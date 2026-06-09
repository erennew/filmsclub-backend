"""
Jikan API Service (Unofficial MyAnimeList API) v4
Netflix/Disney+ Style API Integration for Python
Documentation: https://jikan.moe
"""

import asyncio
import aiohttp
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from functools import lru_cache
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
JIKAN_API_URL = 'https://api.jikan.moe/v4'
JIKAN_CDN_URL = 'https://cdn.myanimelist.net'

# Rate limiting configuration
RATE_LIMIT = {
    'requests_per_minute': 60,
    'requests_per_second': 3,
    'delay_between_requests': 0.35  # seconds
}


@dataclass
class PageInfo:
    """Page information for paginated responses"""
    last_visible_page: int
    has_next_page: bool
    current_page: int
    items: Dict


@dataclass
class AnimeImages:
    """Anime image URLs"""
    jpg_image: Optional[str] = None
    jpg_small: Optional[str] = None
    jpg_large: Optional[str] = None
    webp_image: Optional[str] = None
    webp_small: Optional[str] = None
    webp_large: Optional[str] = None


@dataclass
class Anime:
    """Complete anime data structure"""
    id: int
    title: str
    title_english: Optional[str] = None
    title_japanese: Optional[str] = None
    title_synonyms: List[str] = field(default_factory=list)
    type: Optional[str] = None
    source: Optional[str] = None
    episodes: Optional[int] = None
    status: Optional[str] = None
    airing: bool = False
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    season: Optional[str] = None
    year: Optional[int] = None
    duration: Optional[str] = None
    rating: Optional[str] = None
    score: float = 0.0
    scored_by: Optional[int] = None
    rank: Optional[int] = None
    popularity: Optional[int] = None
    members: Optional[int] = None
    favorites: Optional[int] = None
    synopsis: Optional[str] = None
    background: Optional[str] = None
    trailer_url: Optional[str] = None
    images: Optional[AnimeImages] = None
    genres: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)
    demographics: List[str] = field(default_factory=list)
    studios: List[str] = field(default_factory=list)
    producers: List[str] = field(default_factory=list)
    licensors: List[str] = field(default_factory=list)


@dataclass
class Character:
    """Character information"""
    id: int
    name: str
    name_kanji: Optional[str] = None
    nicknames: List[str] = field(default_factory=list)
    about: Optional[str] = None
    favorites: Optional[int] = None
    image: Optional[str] = None
    animeography: List[Dict] = field(default_factory=list)
    mangaography: List[Dict] = field(default_factory=list)
    voice_actors: List[Dict] = field(default_factory=list)


@dataclass
class Person:
    """Person (staff/voice actor) information"""
    id: int
    name: str
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    alternate_names: List[str] = field(default_factory=list)
    birthday: Optional[str] = None
    website_url: Optional[str] = None
    favorites: Optional[int] = None
    about: Optional[str] = None
    image: Optional[str] = None
    anime: List[Dict] = field(default_factory=list)
    manga: List[Dict] = field(default_factory=list)
    voice_acting_roles: List[Dict] = field(default_factory=list)


@dataclass
class Episode:
    """Episode information"""
    id: int
    title: str
    title_japanese: Optional[str] = None
    title_romanji: Optional[str] = None
    duration: Optional[int] = None
    aired: Optional[str] = None
    filler: bool = False
    recap: bool = False
    forum_url: Optional[str] = None


@dataclass
class Review:
    """User review"""
    id: int
    url: str
    type: str
    votes: int
    date: str
    review: str
    score: int
    tags: List[str] = field(default_factory=list)
    username: Optional[str] = None
    user_image: Optional[str] = None


class JikanApiService:
    """Jikan API Service for fetching MyAnimeList data"""
    
    def __init__(self):
        self.base_url = JIKAN_API_URL
        self.cdn_url = JIKAN_CDN_URL
        self.cache = {}
        self.cache_duration = timedelta(hours=24)
        self.last_request_time = datetime.min
        self.session = None
    
    async def get_session(self):
        """Get or create aiohttp session"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def rate_limit(self):
        """Apply rate limiting to respect Jikan's limits"""
        now = datetime.now()
        time_since_last = (now - self.last_request_time).total_seconds()
        
        if time_since_last < RATE_LIMIT['delay_between_requests']:
            delay = RATE_LIMIT['delay_between_requests'] - time_since_last
            await asyncio.sleep(delay)
        
        self.last_request_time = datetime.now()
    
    async def fetch_with_cache(self, endpoint: str, params: Dict = None, ttl: timedelta = None) -> Dict:
        """Fetch data with caching and rate limiting"""
        if ttl is None:
            ttl = self.cache_duration
        
        cache_key = f"{endpoint}:{str(params)}"
        
        # Check cache
        if cache_key in self.cache:
            cached_data, cached_time = self.cache[cache_key]
            if datetime.now() - cached_time < ttl:
                return cached_data
        
        # Apply rate limiting
        await self.rate_limit()
        
        try:
            session = await self.get_session()
            async with session.get(
                f"{self.base_url}{endpoint}",
                params=params,
                headers={'Accept': 'application/json'}
            ) as response:
                
                # Handle rate limiting
                if response.status == 429:
                    retry_after = int(response.headers.get('Retry-After', 1))
                    await asyncio.sleep(retry_after)
                    return await self.fetch_with_cache(endpoint, params, ttl)
                
                if response.status != 200:
                    error_data = await response.json()
                    raise Exception(error_data.get('message', f'HTTP {response.status}'))
                
                data = await response.json()
                
                # Cache the response
                self.cache[cache_key] = (data, datetime.now())
                
                return data
        except Exception as e:
            logger.error(f"Jikan API Error: {e}")
            raise
    
    def format_anime_data(self, raw_data: Dict) -> Optional[Anime]:
        """Format raw API data into Anime dataclass"""
        if not raw_data:
            return None
        
        images = None
        if raw_data.get('images'):
            images = AnimeImages(
                jpg_image=raw_data['images'].get('jpg', {}).get('image_url'),
                jpg_small=raw_data['images'].get('jpg', {}).get('small_image_url'),
                jpg_large=raw_data['images'].get('jpg', {}).get('large_image_url'),
                webp_image=raw_data['images'].get('webp', {}).get('image_url'),
                webp_small=raw_data['images'].get('webp', {}).get('small_image_url'),
                webp_large=raw_data['images'].get('webp', {}).get('large_image_url')
            )
        
        return Anime(
            id=raw_data.get('mal_id', 0),
            title=raw_data.get('title', 'Unknown'),
            title_english=raw_data.get('title_english'),
            title_japanese=raw_data.get('title_japanese'),
            title_synonyms=raw_data.get('title_synonyms', []),
            type=raw_data.get('type'),
            source=raw_data.get('source'),
            episodes=raw_data.get('episodes'),
            status=raw_data.get('status'),
            airing=raw_data.get('airing', False),
            start_date=raw_data.get('aired', {}).get('from'),
            end_date=raw_data.get('aired', {}).get('to'),
            season=raw_data.get('season'),
            year=raw_data.get('year'),
            duration=raw_data.get('duration'),
            rating=raw_data.get('rating'),
            score=raw_data.get('score', 0.0),
            scored_by=raw_data.get('scored_by'),
            rank=raw_data.get('rank'),
            popularity=raw_data.get('popularity'),
            members=raw_data.get('members'),
            favorites=raw_data.get('favorites'),
            synopsis=raw_data.get('synopsis'),
            background=raw_data.get('background'),
            trailer_url=raw_data.get('trailer', {}).get('url'),
            images=images,
            genres=[g.get('name') for g in raw_data.get('genres', [])],
            themes=[t.get('name') for t in raw_data.get('themes', [])],
            demographics=[d.get('name') for d in raw_data.get('demographics', [])],
            studios=[s.get('name') for s in raw_data.get('studios', [])],
            producers=[p.get('name') for p in raw_data.get('producers', [])],
            licensors=[l.get('name') for l in raw_data.get('licensors', [])]
        )
    
    def format_character_data(self, raw_data: Dict) -> Optional[Character]:
        """Format raw API data into Character dataclass"""
        if not raw_data:
            return None
        
        return Character(
            id=raw_data.get('mal_id', 0),
            name=raw_data.get('name', 'Unknown'),
            name_kanji=raw_data.get('name_kanji'),
            nicknames=raw_data.get('nicknames', []),
            about=raw_data.get('about'),
            favorites=raw_data.get('favorites'),
            image=raw_data.get('images', {}).get('jpg', {}).get('image_url'),
            animeography=raw_data.get('animeography', []),
            mangaography=raw_data.get('mangaography', []),
            voice_actors=[{
                'id': va.get('person', {}).get('mal_id'),
                'name': va.get('person', {}).get('name'),
                'image': va.get('person', {}).get('images', {}).get('jpg', {}).get('image_url'),
                'language': va.get('language')
            } for va in raw_data.get('voice_actors', [])]
        )
    
    def format_person_data(self, raw_data: Dict) -> Optional[Person]:
        """Format raw API data into Person dataclass"""
        if not raw_data:
            return None
        
        return Person(
            id=raw_data.get('mal_id', 0),
            name=raw_data.get('name', 'Unknown'),
            given_name=raw_data.get('given_name'),
            family_name=raw_data.get('family_name'),
            alternate_names=raw_data.get('alternate_names', []),
            birthday=raw_data.get('birthday'),
            website_url=raw_data.get('website_url'),
            favorites=raw_data.get('favorites'),
            about=raw_data.get('about'),
            image=raw_data.get('images', {}).get('jpg', {}).get('image_url'),
            anime=raw_data.get('anime', []),
            manga=raw_data.get('manga', []),
            voice_acting_roles=raw_data.get('voice_acting_roles', [])
        )
    
    def format_episode_data(self, raw_data: Dict) -> Optional[Episode]:
        """Format raw API data into Episode dataclass"""
        if not raw_data:
            return None
        
        return Episode(
            id=raw_data.get('mal_id', 0),
            title=raw_data.get('title', 'Unknown'),
            title_japanese=raw_data.get('title_japanese'),
            title_romanji=raw_data.get('title_romanji'),
            duration=raw_data.get('duration'),
            aired=raw_data.get('aired'),
            filler=raw_data.get('filler', False),
            recap=raw_data.get('recap', False),
            forum_url=raw_data.get('forum_url')
        )
    
    # ============= ANIME ENDPOINTS =============
    
    async def get_anime_by_id(self, anime_id: int) -> Optional[Anime]:
        """Get anime by ID"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/full')
        return self.format_anime_data(data.get('data'))
    
    async def get_anime_characters(self, anime_id: int) -> List[Character]:
        """Get anime characters"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/characters')
        return [self.format_character_data(char) for char in data.get('data', [])]
    
    async def get_anime_episodes(self, anime_id: int, page: int = 1) -> Dict:
        """Get anime episodes"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/episodes', {'page': page})
        return {
            'pagination': data.get('pagination', {}),
            'episodes': [self.format_episode_data(ep) for ep in data.get('data', [])]
        }
    
    async def get_anime_staff(self, anime_id: int) -> List[Person]:
        """Get anime staff"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/staff')
        return [self.format_person_data(staff.get('person')) for staff in data.get('data', [])]
    
    async def get_anime_recommendations(self, anime_id: int) -> List[Dict]:
        """Get anime recommendations"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/recommendations')
        return [{
            'id': rec['entry']['mal_id'],
            'title': rec['entry']['title'],
            'image': rec['entry']['images']['jpg']['image_url'],
            'recommendation_count': rec['votes']
        } for rec in data.get('data', [])]
    
    async def get_anime_reviews(self, anime_id: int, page: int = 1) -> List[Review]:
        """Get anime reviews"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/reviews', {'page': page})
        reviews = []
        for review in data.get('data', []):
            reviews.append(Review(
                id=review.get('mal_id', 0),
                url=review.get('url', ''),
                type=review.get('type', ''),
                votes=review.get('votes', 0),
                date=review.get('date', ''),
                review=review.get('review', ''),
                score=review.get('score', 0),
                tags=review.get('tags', []),
                username=review.get('user', {}).get('username'),
                user_image=review.get('user', {}).get('images', {}).get('jpg', {}).get('image_url')
            ))
        return reviews
    
    async def get_anime_themes(self, anime_id: int) -> Dict:
        """Get anime themes (openings and endings)"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/themes')
        return {
            'openings': data.get('data', {}).get('openings', []),
            'endings': data.get('data', {}).get('endings', [])
        }
    
    async def get_anime_external_links(self, anime_id: int) -> List[Dict]:
        """Get anime external links"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/external')
        return [{'name': link.get('name'), 'url': link.get('url')} for link in data.get('data', [])]
    
    async def get_anime_streaming_links(self, anime_id: int) -> List[Dict]:
        """Get anime streaming links"""
        data = await self.fetch_with_cache(f'/anime/{anime_id}/streaming')
        return [{'name': link.get('name'), 'url': link.get('url')} for link in data.get('data', [])]
    
    # ============= SEARCH ENDPOINTS =============
    
    async def search_anime(self, query: str, page: int = 1, limit: int = 25) -> Dict:
        """Search for anime"""
        data = await self.fetch_with_cache('/anime', {
            'q': query,
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': [self.format_anime_data(anime) for anime in data.get('data', [])]
        }
    
    async def search_manga(self, query: str, page: int = 1, limit: int = 25) -> Dict:
        """Search for manga"""
        data = await self.fetch_with_cache('/manga', {
            'q': query,
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': data.get('data', [])
        }
    
    async def search_characters(self, query: str, page: int = 1, limit: int = 25) -> Dict:
        """Search for characters"""
        data = await self.fetch_with_cache('/characters', {
            'q': query,
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': [self.format_character_data(char) for char in data.get('data', [])]
        }
    
    async def search_people(self, query: str, page: int = 1, limit: int = 25) -> Dict:
        """Search for people"""
        data = await self.fetch_with_cache('/people', {
            'q': query,
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': [self.format_person_data(person) for person in data.get('data', [])]
        }
    
    # ============= TOP LISTS =============
    
    async def get_top_anime(self, filter_type: str = 'bypopularity', page: int = 1, limit: int = 25) -> Dict:
        """Get top anime"""
        data = await self.fetch_with_cache('/top/anime', {
            'filter': filter_type,
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': [self.format_anime_data(anime) for anime in data.get('data', [])]
        }
    
    # ============= SEASONAL ENDPOINTS =============
    
    async def get_seasonal_anime(self, year: int, season: str, page: int = 1, limit: int = 25) -> Dict:
        """Get seasonal anime"""
        data = await self.fetch_with_cache(f'/seasons/{year}/{season}', {
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': [self.format_anime_data(anime) for anime in data.get('data', [])]
        }
    
    async def get_current_season_anime(self, page: int = 1, limit: int = 25) -> Dict:
        """Get current season anime"""
        data = await self.fetch_with_cache('/seasons/now', {
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': [self.format_anime_data(anime) for anime in data.get('data', [])]
        }
    
    async def get_upcoming_season_anime(self, page: int = 1, limit: int = 25) -> Dict:
        """Get upcoming season anime"""
        data = await self.fetch_with_cache('/seasons/upcoming', {
            'page': page,
            'limit': limit
        })
        return {
            'pagination': data.get('pagination', {}),
            'results': [self.format_anime_data(anime) for anime in data.get('data', [])]
        }
    
    # ============= CHARACTER/PERSON ENDPOINTS =============
    
    async def get_character_by_id(self, character_id: int) -> Optional[Character]:
        """Get character by ID"""
        data = await self.fetch_with_cache(f'/characters/{character_id}/full')
        return self.format_character_data(data.get('data'))
    
    async def get_person_by_id(self, person_id: int) -> Optional[Person]:
        """Get person by ID"""
        data = await self.fetch_with_cache(f'/people/{person_id}/full')
        return self.format_person_data(data.get('data'))
    
    # ============= GENRE ENDPOINTS =============
    
    async def get_anime_genres(self) -> List[Dict]:
        """Get anime genres"""
        data = await self.fetch_with_cache('/genres/anime')
        return [{'id': genre['mal_id'], 'name': genre['name'], 'count': genre['count']} 
                for genre in data.get('data', [])]
    
    # ============= RANDOM ENDPOINTS =============
    
    async def get_random_anime(self) -> Optional[Anime]:
        """Get random anime"""
        data = await self.fetch_with_cache('/random/anime')
        return self.format_anime_data(data.get('data'))
    
    async def get_random_character(self) -> Optional[Character]:
        """Get random character"""
        data = await self.fetch_with_cache('/random/characters')
        return self.format_character_data(data.get('data'))
    
    async def get_random_person(self) -> Optional[Person]:
        """Get random person"""
        data = await self.fetch_with_cache('/random/people')
        return self.format_person_data(data.get('data'))
    
    # ============= SCHEDULE =============
    
    async def get_schedule(self, filter_day: str = None, page: int = 1, limit: int = 25) -> List[Anime]:
        """Get anime schedule"""
        params = {'page': page, 'limit': limit}
        if filter_day:
            params['filter'] = filter_day
        data = await self.fetch_with_cache('/schedules', params)
        return [self.format_anime_data(anime) for anime in data.get('data', [])]
    
    # ============= HELPER METHODS =============
    
    @staticmethod
    def get_current_season() -> tuple:
        """Get current season based on date"""
        now = datetime.now()
        month = now.month
        year = now.year
        
        if 1 <= month <= 3:
            season = 'winter'
        elif 4 <= month <= 6:
            season = 'spring'
        elif 7 <= month <= 9:
            season = 'summer'
        else:
            season = 'fall'
        
        return season, year
    
    def clear_cache(self):
        """Clear the cache"""
        self.cache.clear()


# Singleton instance
jikan_api_service = JikanApiService()