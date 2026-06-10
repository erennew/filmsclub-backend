from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
from bson import ObjectId
from fastapi import HTTPException
import motor.motor_asyncio
from pydantic import ValidationError
from pymongo import ASCENDING, DESCENDING

from Backend.logger import LOGGER
from Backend.config import Telegram
from Backend.helper.encrypt import encode_string
from Backend.helper.modal import Episode, MovieSchema, QualityDetail, Season, TVShowSchema


class Database:
    def __init__(self, connection_uri: str = Telegram.DATABASE, db_name: str = "projectS"):
        self._conn = None
        self.db = None
        self.tv_collection = None
        self.movie_collection = None
        self.deploy_config = None
        self.failed_files = None
        self.connection_uri = connection_uri
        self.db_name = db_name

    async def connect(self):
        """Establish a connection to the database."""
        if not self.connection_uri or not self.connection_uri.strip():
            LOGGER.error("DATABASE connection URI is empty. Set the DATABASE environment variable on Heroku.")
            self._conn = None
            self.db = None
            return
        
        try:
            if self._conn is not None:
                await self._conn.close()

            self._conn = motor.motor_asyncio.AsyncIOMotorClient(self.connection_uri)
            self.db = self._conn[self.db_name]

            # Ensure collections are assigned
            self.tv_collection = self.db["tv"]
            self.movie_collection = self.db["movie"]
            self.deploy_config = self.db["deploy_config"]
            self.views_collection = self.db["views"]
            self.replaced_versions = self.db["replaced_versions"]  # Backup collection for replaced files
            self.failed_files = self.db["failed_files"]  # Persistent log of failed/skipped ingestions

            # Create index for efficient trending queries
            await self.views_collection.create_index([("date", DESCENDING), ("count", DESCENDING)])
            await self.views_collection.create_index([("tmdb_id", ASCENDING), ("media_type", ASCENDING), ("date", ASCENDING)])

            # Create index for replaced_versions collection
            await self.replaced_versions.create_index([("tmdb_id", ASCENDING)])
            await self.replaced_versions.create_index([("replaced_at", DESCENDING)])

            # Create indexes for failed_files collection
            await self.failed_files.create_index([("timestamp", DESCENDING)])
            await self.failed_files.create_index([("reason", ASCENDING)])

            LOGGER.info("Database connection established")
        
            # Debug: Print available collections
           # collections = await self.db.list_collection_names()
           # LOGGER.info(f"Available collections: {collections}")

        except Exception as e:
            LOGGER.error(f"Error connecting to the database: {e}")
            self._conn = None
            self.db = None
        

    async def disconnect(self):
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            LOGGER.info("Database connection closed")
        self._conn = None
        self.db = None
        self.tv_collection = None
        self.movie_collection = None

    @staticmethod
    def _convert_object_id(document: dict) -> dict:
        """Convert MongoDB ObjectId to string."""
        if "_id" in document:
            document["_id"] = str(document["_id"])
        return document

    
    async def update_tv_show(self, tv_show_data: TVShowSchema) -> Optional[ObjectId]:
        try:
            tv_show_dict = tv_show_data.model_dump()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None

        existing_media = await self.tv_collection.find_one({
            "$or": [
                {"tmdb_id": tv_show_dict["tmdb_id"]},
                {"title": tv_show_dict["title"], "release_year": tv_show_dict["release_year"]}
            ]
        })

        if not existing_media:
            result = await self.tv_collection.insert_one(tv_show_dict)
            return result.inserted_id

        updated = False
        for season in tv_show_dict["seasons"]:
            existing_season = next(
                (s for s in existing_media["seasons"] 
                 if s["season_number"] == season["season_number"]), None)
            
            if existing_season:
                for episode in season["episodes"]:
                    existing_episode = next(
                        (e for e in existing_season["episodes"] 
                         if e["episode_number"] == episode["episode_number"]), None)
                    
                    if existing_episode:
                        for quality in episode["telegram"]:
                            existing_quality = next(
                                (q for q in existing_episode["telegram"] 
                                 if q["quality"] == quality["quality"]), None)
                            
                            if existing_quality:
                                existing_quality.update(quality)
                                updated = True
                            else:
                                existing_episode["telegram"].append(quality)
                                updated = True
                    else:
                        existing_season["episodes"].append(episode)
                        updated = True
            else:
                existing_media["seasons"].append(season)
                updated = True

        if updated:
            existing_media["updated_on"] = datetime.utcnow()
            existing_media["languages"] = tv_show_dict["languages"]
            existing_media["rip"] = tv_show_dict["rip"]
            await self.tv_collection.replace_one(
                {"tmdb_id": tv_show_dict["tmdb_id"]}, existing_media)
            return existing_media["_id"]
        else:
            LOGGER.info(f"No updates made for: {tv_show_dict['tmdb_id']}")
            return existing_media["_id"]

    async def update_movie(self, movie_data: MovieSchema) -> Optional[ObjectId]:
        if self.movie_collection is None:
            LOGGER.error("Database collection is not initialized. Did you call db.connect()?")
            return None
        try:
            movie_dict = movie_data.model_dump()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None

        existing_media = await self.movie_collection.find_one({
            "$or": [
                {"tmdb_id": movie_dict["tmdb_id"]},
                {"title": movie_dict["title"], "release_year": movie_dict["release_year"]}
            ]
        })

        if not existing_media:
            result = await self.movie_collection.insert_one(movie_dict)
            return result.inserted_id

        updated = False
        for quality in movie_dict["telegram"]:
            existing_quality = next(
                (q for q in existing_media["telegram"] 
                 if q["quality"] == quality["quality"]), None)
            
            if existing_quality:
                existing_quality.update(quality)
                updated = True
            else:
                existing_media["telegram"].append(quality)
                updated = True

        if updated:
            existing_media["updated_on"] = datetime.utcnow()
            existing_media["languages"] = movie_dict["languages"]
            existing_media["rip"] = movie_dict["rip"]
            await self.movie_collection.replace_one(
                {"tmdb_id": movie_dict["tmdb_id"]}, existing_media)
            return existing_media["_id"]
        else:
            LOGGER.info(f"No updates made for: {movie_dict['tmdb_id']}")
            return existing_media["_id"]

    async def insert_media(
        self,
        metadata_info: dict,
        hash: str,
        channel: int,
        msg_id: int,
        size: str,
        name: str
    ) -> Optional[ObjectId]:
        data = {"chat_id": channel, "msg_id": msg_id, "hash": hash}
        encoded_string = await encode_string(data)

        if metadata_info['media_type'] == "movie":
            media = MovieSchema(
                tmdb_id=metadata_info['tmdb_id'],
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                runtime=metadata_info['runtime'],
                media_type=metadata_info['media_type'],
                languages=metadata_info['languages'],
                rip=metadata_info['rip'],
                telegram=[
                    QualityDetail(
                        quality=metadata_info['quality'],
                        id=encoded_string,
                        name=name,
                        size=size
                    )]
            )
            return await self.update_movie(media)
        else:
            tv_show = TVShowSchema(
                tmdb_id=metadata_info['tmdb_id'],
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                media_type=metadata_info['media_type'],
                status=metadata_info['status'],
                total_seasons=metadata_info['total_seasons'],
                total_episodes=metadata_info['total_episodes'],
                languages=metadata_info['languages'],
                rip=metadata_info['rip'],
                seasons=[
                    Season(
                        season_number=metadata_info['season_number'],
                        episodes=[
                            Episode(
                                episode_number=metadata_info['episode_number'],
                                title=metadata_info['episode_title'],
                                episode_backdrop=metadata_info['episode_backdrop'],
                                telegram=[
                                    QualityDetail(
                                        quality=metadata_info['quality'],
                                        id=encoded_string,
                                        name=name,
                                        size=size
                                    )
                                ]
                            )
                        ]
                    )
                ]
            )
            return await self.update_tv_show(tv_show)

    async def is_file_exists(self, channel: int, msg_id: int, hash: str) -> bool:
        """Check if a file (by channel + msg_id + hash) already exists in the database."""
        try:
            data = {"chat_id": channel, "msg_id": msg_id, "hash": hash}
            encoded_id = await encode_string(data)
            # Check movies
            movie = await self.movie_collection.find_one(
                {"telegram.id": encoded_id},
                {"_id": 1}
            )
            if movie:
                return True
            # Check TV shows
            tv = await self.tv_collection.find_one(
                {"seasons.episodes.telegram.id": encoded_id},
                {"_id": 1}
            )
            if tv:
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error checking file existence: {e}")
            return False

    async def sort_tv_shows(
        self, 
        sort_params: List[Tuple[str, str]], 
        page: int, 
        page_size: int
    ) -> dict:
        skip = (page - 1) * page_size
        sort_criteria = [(field, ASCENDING if direction == "asc" else DESCENDING) 
                        for field, direction in sort_params]
        
        pipeline = [
            {"$sort": dict(sort_criteria)},
            {"$facet": {
                "metadata": [{"$count": "total_count"}],
                "data": [{"$skip": skip}, {"$limit": page_size}]
            }}
        ]
        
        result = await self.tv_collection.aggregate(pipeline).to_list(1)
        total_count = result[0]["metadata"][0]["total_count"] if result[0]["metadata"] else 0
        sorted_shows = [TVShowSchema(**doc) for doc in result[0]["data"]]
        return {"total_count": total_count, "tv_shows": sorted_shows}

    async def sort_movies(
        self, 
        sort_params: List[Tuple[str, str]], 
        page: int, 
        page_size: int
    ) -> dict:
        skip = (page - 1) * page_size
        sort_criteria = [(field, ASCENDING if direction == "asc" else DESCENDING) 
                        for field, direction in sort_params]
        
        pipeline = [
            {"$sort": dict(sort_criteria)},
            {"$facet": {
                "metadata": [{"$count": "total_count"}],
                "data": [{"$skip": skip}, {"$limit": page_size}]
            }}
        ]
        
        result = await self.movie_collection.aggregate(pipeline).to_list(1)
        total_count = result[0]["metadata"][0]["total_count"] if result[0]["metadata"] else 0
        sorted_movies = [MovieSchema(**doc) for doc in result[0]["data"]]
        return {"total_count": total_count, "movies": sorted_movies}

    async def find_similar_media(
        self,
        tmdb_id: int,
        media_type: str,
        page: int = 1,
        page_size: int = 10
    ) -> dict:
        collection = self.movie_collection if media_type == "movie" else self.tv_collection
        parent_media = await collection.find_one({"tmdb_id": tmdb_id})
        
        if not parent_media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        parent_genres = parent_media.get("genres", [])
        if not parent_genres:
            return {"total_count": 0, "similar_media": []}

        skip = (page - 1) * page_size
        pipeline = [
            {"$match": {
                "tmdb_id": {"$ne": tmdb_id},
                "genres": {"$in": parent_genres}
            }},
            {"$addFields": {
                "genreMatchCount": {"$size": {"$setIntersection": ["$genres", parent_genres]}}
            }},
            {"$sort": {"genreMatchCount": -1, "rating": -1}},
            {"$facet": {
                "metadata": [{"$count": "total_count"}],
                "data": [{"$skip": skip}, {"$limit": page_size}]
            }}
        ]
        
        result = await collection.aggregate(pipeline).to_list(1)
        total_count = result[0]["metadata"][0]["total_count"] if result[0]["metadata"] else 0
        similar_media = [self._convert_object_id(doc) for doc in result[0]["data"]]
        return {"total_count": total_count, "similar_media": similar_media}

    async def search_documents(
        self, 
        query: str, 
        page: int, 
        page_size: int
    ) -> dict:
        skip = (page - 1) * page_size
        words = query.split()
        regex_query = {'$regex': '.*' + '.*'.join(words) + '.*', '$options': 'i'}
        
        tv_pipeline = [
            {"$match": {"$or": [
                {"title": regex_query},
                {"seasons.episodes.telegram.name": regex_query}
            ]}},
            {"$project": {
                "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1,
                "release_year": 1, "poster": 1, "backdrop": 1, "description": 1,
                "total_seasons": 1, "total_episodes": 1, "media_type": 1
            }}
        ]
        
        movie_pipeline = [
            {"$match": {"$or": [
                {"title": regex_query},
                {"telegram.name": regex_query}
            ]}},
            {"$project": {
                "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1,
                "release_year": 1, "poster": 1, "backdrop": 1, "description": 1,
                "media_type": 1
            }}
        ]
        
        tv_results = await self.tv_collection.aggregate(tv_pipeline).to_list(None)
        movie_results = await self.movie_collection.aggregate(movie_pipeline).to_list(None)
        combined = tv_results + movie_results
        
        return {
            "total_count": len(combined),
            "results": [self._convert_object_id(doc) for doc in combined[skip:skip+page_size]]
        }

    async def get_media_details(
        self,
        tmdb_id: int,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None
    ) -> Optional[dict]:
        if episode_number is not None and season_number is not None:
            tv_show = await self.tv_collection.find_one({"tmdb_id": tmdb_id})
            if not tv_show:
                return None
            for season in tv_show.get("seasons", []):
                if season.get("season_number") == season_number:
                    for episode in season.get("episodes", []):
                        if episode.get("episode_number") == episode_number:
                            details = self._convert_object_id(episode)
                            details.update({
                                "tmdb_id": tmdb_id,
                                "type": "tv",
                                "season_number": season_number,
                                "episode_number": episode_number,
                                "backdrop": episode.get("episode_backdrop")
                            })
                            return details
            return None

        elif season_number is not None:
            tv_show = await self.tv_collection.find_one({"tmdb_id": tmdb_id})
            if not tv_show:
                return None
            for season in tv_show.get("seasons", []):
                if season.get("season_number") == season_number:
                    details = self._convert_object_id(season)
                    details.update({
                        "tmdb_id": tmdb_id,
                        "type": "tv",
                        "season_number": season_number
                    })
                    return details
            return None

        else:
            tv_doc = await self.tv_collection.find_one({"tmdb_id": tmdb_id})
            if tv_doc:
                tv_doc = self._convert_object_id(tv_doc)
                tv_doc["type"] = "tv"
                return tv_doc
            
            movie_doc = await self.movie_collection.find_one({"tmdb_id": tmdb_id})
            if movie_doc:
                movie_doc = self._convert_object_id(movie_doc)
                movie_doc["type"] = "movie"
                return movie_doc
            
            return None

    async def get_quality_details(
        self,
        tmdb_id: int,
        quality: str,
        season: Optional[int] = None,
        episode: Optional[int] = None
    ) -> List[Dict[str, int]]:
        if season is None:
            # Movie case
            doc = await self.movie_collection.find_one(
                {"tmdb_id": tmdb_id},
                {"telegram": 1}
            )
            if not doc:
                return []
            return [
                {"id": item["id"], "name": item["name"]}
                for item in doc.get("telegram", [])
                if item["quality"] == quality
            ]
        else:
            # TV show case
            doc = await self.tv_collection.find_one(
                {"tmdb_id": tmdb_id},
                {"seasons": 1}
            )
            if not doc:
                return []
            
            results = []
            for s in doc.get("seasons", []):
                if s["season_number"] == season:
                    episodes = s.get("episodes", [])
                    
                    # Filter by specific episode if provided
                    if episode is not None:
                        episodes = [ep for ep in episodes if ep["episode_number"] == episode]
                    
                    for ep in episodes:
                        results.extend([
                            {"id": t["id"], "name": t["name"]}
                            for t in ep.get("telegram", [])
                            if t["quality"] == quality
                        ])
            return results


    async def track_view(self, tmdb_id: int, media_type: str) -> None:
        """Increment the daily view count for a movie or TV show."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        await self.views_collection.update_one(
            {"tmdb_id": tmdb_id, "media_type": media_type, "date": today},
            {"$inc": {"count": 1}, "$setOnInsert": {"tmdb_id": tmdb_id, "media_type": media_type, "date": today}},
            upsert=True
        )

    async def get_trending_today(self, limit: int = 10) -> dict:
        """Get top movies and TV shows by today's view count."""
        today = datetime.utcnow().strftime("%Y-%m-%d")

        pipeline = [
            {"$match": {"date": today}},
            {"$sort": {"count": DESCENDING}},
            {"$limit": limit * 2},
            {"$group": {
                "_id": {"tmdb_id": "$tmdb_id", "media_type": "$media_type"},
                "count": {"$max": "$count"}
            }},
            {"$sort": {"count": DESCENDING}},
            {"$limit": limit}
        ]

        trending_views = await self.views_collection.aggregate(pipeline).to_list(None)

        movies = []
        tv_shows = []

        for item in trending_views:
            tmdb_id = item["_id"]["tmdb_id"]
            media_type = item["_id"]["media_type"]
            count = item["count"]

            if media_type == "movie":
                doc = await self.movie_collection.find_one({"tmdb_id": tmdb_id})
                if doc:
                    doc = self._convert_object_id(doc)
                    doc["view_count"] = count
                    movies.append(doc)
            else:
                doc = await self.tv_collection.find_one({"tmdb_id": tmdb_id})
                if doc:
                    doc = self._convert_object_id(doc)
                    doc["view_count"] = count
                    tv_shows.append(doc)

        return {"movies": movies, "tv_shows": tv_shows}

    async def get_most_viewed(self, limit: int = 10) -> dict:
        """Get top movies and TV shows by all-time view count."""
        pipeline = [
            {"$group": {
                "_id": {"tmdb_id": "$tmdb_id", "media_type": "$media_type"},
                "total_count": {"$sum": "$count"}
            }},
            {"$sort": {"total_count": DESCENDING}},
            {"$limit": limit}
        ]

        trending_views = await self.views_collection.aggregate(pipeline).to_list(None)

        movies = []
        tv_shows = []

        for item in trending_views:
            tmdb_id = item["_id"]["tmdb_id"]
            media_type = item["_id"]["media_type"]
            count = item["total_count"]

            if media_type == "movie":
                doc = await self.movie_collection.find_one({"tmdb_id": tmdb_id})
                if doc:
                    doc = self._convert_object_id(doc)
                    doc["view_count"] = count
                    movies.append(doc)
            else:
                doc = await self.tv_collection.find_one({"tmdb_id": tmdb_id})
                if doc:
                    doc = self._convert_object_id(doc)
                    doc["view_count"] = count
                    tv_shows.append(doc)

        return {"movies": movies, "tv_shows": tv_shows}

    async def get_anime(self, limit: int = 20, page: int = 1) -> dict:
        """Get anime (Animation/Anime genre) movies and TV shows."""
        skip = (page - 1) * limit

        anime_pipeline = [
            {"$match": {"genres": {"$in": ["Animation", "Anime", "anime", "animation"]}}},
            {"$sort": {"rating": DESCENDING, "release_year": DESCENDING}},
            {"$facet": {
                "metadata": [{"$count": "total_count"}],
                "data": [{"$skip": skip}, {"$limit": limit}]
            }}
        ]

        movie_result = await self.movie_collection.aggregate(anime_pipeline).to_list(1)
        tv_result = await self.tv_collection.aggregate(anime_pipeline).to_list(1)

        movies = [self._convert_object_id(doc) for doc in movie_result[0]["data"]] if movie_result else []
        tv_shows = [self._convert_object_id(doc) for doc in tv_result[0]["data"]] if tv_result else []

        total_count = 0
        if movie_result and movie_result[0]["metadata"]:
            total_count += movie_result[0]["metadata"][0]["total_count"]
        if tv_result and tv_result[0]["metadata"]:
            total_count += tv_result[0]["metadata"][0]["total_count"]

        combined = (movies + tv_shows)
        combined.sort(key=lambda x: (x.get("rating") or 0), reverse=True)

        return {"total_count": total_count, "results": combined[:limit]}

    async def get_kdrama(self, limit: int = 20, page: int = 1) -> dict:
        """Get K-Drama TV shows (Korean language + Drama genre)."""
        skip = (page - 1) * limit

        kdrama_pipeline = [
            {"$match": {
                "languages": {"$in": ["Korean", "ko", "korean", "kor"]},
                "genres": {"$in": ["Drama", "Korean Drama", "drama", "K-Drama", "Kdrama"]}
            }},
            {"$sort": {"rating": DESCENDING, "release_year": DESCENDING}},
            {"$facet": {
                "metadata": [{"$count": "total_count"}],
                "data": [{"$skip": skip}, {"$limit": limit}]
            }}
        ]

        tv_result = await self.tv_collection.aggregate(kdrama_pipeline).to_list(1)

        tv_shows = [self._convert_object_id(doc) for doc in tv_result[0]["data"]] if tv_result else []
        total_count = tv_result[0]["metadata"][0]["total_count"] if tv_result and tv_result[0]["metadata"] else 0

        return {"total_count": total_count, "tv_shows": tv_shows}

    async def delete_document(
        self,
        media_type: str,
        tmdb_id: int
    ) -> bool:
        if media_type == "mov":
            result = await self.movie_collection.delete_one({"tmdb_id": tmdb_id})
        else:
            result = await self.tv_collection.delete_one({"tmdb_id": tmdb_id})

        if result.deleted_count > 0:
            LOGGER.info(f"{media_type} with tmdb_id {tmdb_id} deleted successfully.")
            return True
        LOGGER.info(f"No document found with tmdb_id {tmdb_id}.")
        return False

    async def update_movie_audio_tracks(
        self,
        tmdb_id: int,
        quality: str,
        audio_tracks: List[Dict]
    ) -> bool:
        """Update audio tracks for a specific movie quality."""
        try:
            result = await self.movie_collection.update_one(
                {
                    "tmdb_id": tmdb_id,
                    "telegram.quality": quality
                },
                {
                    "$set": {"telegram.$.audio_tracks": audio_tracks}
                }
            )
            if result.modified_count > 0:
                LOGGER.info(f"Cached audio tracks for movie {tmdb_id}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating movie audio tracks: {e}")
            return False

    async def update_tv_episode_audio_tracks(
        self,
        tmdb_id: int,
        season_number: int,
        episode_number: int,
        quality: str,
        audio_tracks: List[Dict]
    ) -> bool:
        """Update audio tracks for a specific TV episode quality."""
        try:
            result = await self.tv_collection.update_one(
                {
                    "tmdb_id": tmdb_id,
                    "seasons.season_number": season_number,
                    "seasons.episodes.episode_number": episode_number,
                    "seasons.episodes.telegram.quality": quality
                },
                {
                    "$set": {"seasons.$[s].episodes.$[e].telegram.$[t].audio_tracks": audio_tracks}
                },
                array_filters=[
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                    {"t.quality": quality}
                ]
            )
            if result.modified_count > 0:
                LOGGER.info(f"Cached audio tracks for TV {tmdb_id} S{season_number}E{episode_number}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating TV episode audio tracks: {e}")
            return False

    async def update_movie_subtitle_tracks(
        self,
        tmdb_id: int,
        quality: str,
        subtitle_tracks: List[Dict]
    ) -> bool:
        """Update subtitle tracks for a specific movie quality."""
        try:
            result = await self.movie_collection.update_one(
                {
                    "tmdb_id": tmdb_id,
                    "telegram.quality": quality
                },
                {
                    "$set": {"telegram.$.subtitle_tracks": subtitle_tracks}
                }
            )
            if result.modified_count > 0:
                LOGGER.info(f"Cached subtitle tracks for movie {tmdb_id}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating movie subtitle tracks: {e}")
            return False

    async def update_tv_episode_subtitle_tracks(
        self,
        tmdb_id: int,
        season_number: int,
        episode_number: int,
        quality: str,
        subtitle_tracks: List[Dict]
    ) -> bool:
        """Update subtitle tracks for a specific TV episode quality."""
        try:
            result = await self.tv_collection.update_one(
                {
                    "tmdb_id": tmdb_id,
                    "seasons.season_number": season_number,
                    "seasons.episodes.episode_number": episode_number,
                    "seasons.episodes.telegram.quality": quality
                },
                {
                    "$set": {"seasons.$[s].episodes.$[e].telegram.$[t].subtitle_tracks": subtitle_tracks}
                },
                array_filters=[
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                    {"t.quality": quality}
                ]
            )
            if result.modified_count > 0:
                LOGGER.info(f"Cached subtitle tracks for TV {tmdb_id} S{season_number}E{episode_number}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating TV episode subtitle tracks: {e}")
            return False

    async def get_media_with_tracks(
        self,
        file_id: str
    ) -> Optional[Dict]:
        """Find media document containing a specific file_id and return its audio and subtitle tracks."""
        try:
            # Check movies
            movie = await self.movie_collection.find_one(
                {"telegram.id": file_id},
                {"telegram.$": 1, "tmdb_id": 1, "title": 1, "media_type": 1}
            )
            if movie and movie.get("telegram"):
                quality_detail = movie["telegram"][0]
                return {
                    "media_type": "movie",
                    "tmdb_id": movie.get("tmdb_id"),
                    "title": movie.get("title"),
                    "quality": quality_detail.get("quality"),
                    "file_name": quality_detail.get("name"),
                    "audio_tracks": quality_detail.get("audio_tracks"),
                    "subtitle_tracks": quality_detail.get("subtitle_tracks")
                }

            # Check TV shows
            tv_show = await self.tv_collection.find_one(
                {"seasons.episodes.telegram.id": file_id},
                {
                    "seasons.episodes": 1,
                    "tmdb_id": 1,
                    "title": 1,
                    "media_type": 1
                }
            )
            if tv_show:
                for season in tv_show.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for quality in episode.get("telegram", []):
                            if quality.get("id") == file_id:
                                return {
                                    "media_type": "tv",
                                    "tmdb_id": tv_show.get("tmdb_id"),
                                    "title": tv_show.get("title"),
                                    "season_number": season.get("season_number"),
                                    "episode_number": episode.get("episode_number"),
                                    "quality": quality.get("quality"),
                                    "file_name": quality.get("name"),
                                    "audio_tracks": quality.get("audio_tracks"),
                                    "subtitle_tracks": quality.get("subtitle_tracks")
                                }
            return None
        except Exception as e:
            LOGGER.error(f"Error getting media with tracks: {e}")
            return None

    # Backward compatibility alias
    async def get_media_with_audio_tracks(self, file_id: str) -> Optional[Dict]:
        """Backward compatibility - calls get_media_with_tracks."""
        return await self.get_media_with_tracks(file_id)

    async def backup_replaced_version(self, metadata_info: dict, old_version: dict, reason: str) -> bool:
        """
        Backup a version that is being replaced to the replaced_versions collection.
        This provides a safety net for rollback if needed.
        """
        try:
            backup_data = {
                "tmdb_id": metadata_info.get("tmdb_id"),
                "media_type": metadata_info.get("media_type"),
                "title": metadata_info.get("title"),
                "season_number": metadata_info.get("season_number"),
                "episode_number": metadata_info.get("episode_number"),
                "quality": old_version.get("quality"),
                "old_version": old_version,
                "replacement_reason": reason,
                "replaced_at": datetime.utcnow(),
                "backup_status": "success"
            }
            
            result = await self.replaced_versions.insert_one(backup_data)
            if result.inserted_id:
                LOGGER.info(f"💾 Backed up replaced version for TMDB ID {metadata_info.get('tmdb_id')}")
                return True
            else:
                LOGGER.error(f"Failed to backup replaced version for TMDB ID {metadata_info.get('tmdb_id')}")
                return False
        except Exception as e:
            LOGGER.error(f"Error backing up replaced version: {e}")
            return False

    # =========================================================================
    # FAILED FILE LOGGING
    # =========================================================================
    async def log_failed_file(
        self,
        title: str,
        filename: str,
        reason: str,
        metadata_info: Optional[dict] = None,
        channel: Optional[int] = None,
        msg_id: Optional[int] = None,
        retry_count: int = 0,
    ) -> Optional[ObjectId]:
        """Persist a failed/skipped ingestion to the failed_files collection.

        Returns the inserted ObjectId, or None when the DB is unavailable so
        callers never crash the queue worker because of a logging failure.
        """
        if self.failed_files is None:
            LOGGER.warning("Cannot log failed file: database not connected")
            return None

        metadata_info = metadata_info or {}
        tmdb_id = metadata_info.get("tmdb_id")
        media_type = metadata_info.get("media_type")
        try:
            doc = {
                "title": title or metadata_info.get("title") or filename or "Unknown",
                "filename": filename or "",
                "reason": reason or "Unknown",
                "tmdb_id": tmdb_id,
                "media_type": media_type,
                "season_number": metadata_info.get("season_number"),
                "episode_number": metadata_info.get("episode_number"),
                "channel": channel,
                "msg_id": msg_id,
                "retry_count": retry_count,
                "metadata_info": metadata_info or None,
                "timestamp": datetime.utcnow(),
            }
            result = await self.failed_files.insert_one(doc)
            LOGGER.info(f"📝 Logged failed file '{doc['title'][:50]}' (reason: {reason})")
            return result.inserted_id
        except Exception as e:
            LOGGER.error(f"Error logging failed file: {e}")
            return None

    async def get_failed_files(
        self,
        page: int = 1,
        page_size: int = 30,
        reason_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> dict:
        """Return paginated failed files, newest first, with optional filters."""
        if self.failed_files is None:
            return {"items": [], "total_count": 0, "page": page, "page_size": page_size}

        query: dict = {}
        if reason_filter:
            query["reason"] = reason_filter

        date_query: dict = {}
        for bound, op in ((date_from, "$gte"), (date_to, "$lte")):
            if not bound:
                continue
            try:
                date_query[op] = datetime.fromisoformat(bound)
            except (TypeError, ValueError):
                LOGGER.warning(f"Ignoring invalid failed-files date filter: {bound}")
        if date_query:
            query["timestamp"] = date_query

        try:
            page = max(page, 1)
            page_size = max(min(page_size, 200), 1)
            skip = (page - 1) * page_size
            total_count = await self.failed_files.count_documents(query)
            cursor = (
                self.failed_files.find(query)
                .sort("timestamp", DESCENDING)
                .skip(skip)
                .limit(page_size)
            )
            items = [self._convert_object_id(doc) for doc in await cursor.to_list(length=page_size)]
            return {
                "items": items,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
            }
        except Exception as e:
            LOGGER.error(f"Error fetching failed files: {e}")
            return {"items": [], "total_count": 0, "page": page, "page_size": page_size}

    async def get_failed_file(self, file_id: str) -> Optional[dict]:
        """Return a single failed file log entry by its string ObjectId."""
        if self.failed_files is None:
            return None
        try:
            doc = await self.failed_files.find_one({"_id": ObjectId(file_id)})
            return self._convert_object_id(doc) if doc else None
        except Exception as e:
            LOGGER.error(f"Error fetching failed file {file_id}: {e}")
            return None

    async def delete_failed_file(self, file_id: str) -> bool:
        """Delete a single failed file log entry by its string ObjectId."""
        if self.failed_files is None:
            return False
        try:
            result = await self.failed_files.delete_one({"_id": ObjectId(file_id)})
            return result.deleted_count > 0
        except Exception as e:
            LOGGER.error(f"Error deleting failed file {file_id}: {e}")
            return False

    async def clear_failed_files(self) -> int:
        """Delete all failed file log entries. Returns the number removed."""
        if self.failed_files is None:
            return 0
        try:
            result = await self.failed_files.delete_many({})
            LOGGER.info(f"🧹 Cleared {result.deleted_count} failed file log entries")
            return result.deleted_count
        except Exception as e:
            LOGGER.error(f"Error clearing failed files: {e}")
            return 0

    async def count_failed_files(self, reason_filter: Optional[str] = None) -> int:
        """Return the number of failed file log entries (optionally by reason)."""
        if self.failed_files is None:
            return 0
        try:
            query = {"reason": reason_filter} if reason_filter else {}
            return await self.failed_files.count_documents(query)
        except Exception as e:
            LOGGER.error(f"Error counting failed files: {e}")
            return 0

    async def prune_failed_files(self, max_age_days: int = 30) -> int:
        """Delete failed file logs older than max_age_days. Returns count removed.

        max_age_days <= 0 disables pruning (retain forever).
        """
        if self.failed_files is None or max_age_days <= 0:
            return 0
        try:
            cutoff = datetime.utcnow() - timedelta(days=max_age_days)
            result = await self.failed_files.delete_many({"timestamp": {"$lt": cutoff}})
            if result.deleted_count:
                LOGGER.info(f"🧹 Pruned {result.deleted_count} failed file logs older than {max_age_days}d")
            return result.deleted_count
        except Exception as e:
            LOGGER.error(f"Error pruning failed files: {e}")
            return 0
