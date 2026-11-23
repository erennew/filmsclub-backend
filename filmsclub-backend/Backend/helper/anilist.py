"""
AniList GraphQL API Service - Premium Anime Data Fetching
Netflix/Disney+ Style API Integration for Python
"""

import asyncio
import aiohttp
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from functools import lru_cache
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
ANILIST_API_URL = 'https://graphql.anilist.co'
ANILIST_CDN_URL = 'https://img.anili.st'

# GraphQL Queries
TRENDING_ANIME_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    media(type: ANIME, sort: TRENDING_DESC, isAdult: false) {
      id
      idMal
      title {
        romaji
        english
        native
      }
      coverImage {
        extraLarge
        large
        medium
        color
      }
      bannerImage
      description
      episodes
      status
      startDate {
        year
        month
        day
      }
      endDate {
        year
        month
        day
      }
      season
      seasonYear
      averageScore
      meanScore
      popularity
      favourites
      genres
      synonyms
      duration
      format
      source
      hashtag
      countryOfOrigin
      isAdult
      isLicensed
      nextAiringEpisode {
        airingAt
        timeUntilAiring
        episode
      }
      studios {
        edges {
          isMain
          node {
            id
            name
          }
        }
      }
      trailer {
        id
        site
        thumbnail
      }
      characters(perPage: 12) {
        edges {
          role
          node {
            id
            name {
              full
              native
            }
            image {
              large
              medium
            }
          }
          voiceActors(language: JAPANESE) {
            id
            name {
              full
              native
            }
            image {
              large
              medium
            }
          }
        }
      }
      relations {
        edges {
          relationType
          node {
            id
            title {
              romaji
              english
            }
            coverImage {
              large
            }
            format
            type
          }
        }
      }
      recommendations(perPage: 10) {
        edges {
          node {
            mediaRecommendation {
              id
              title {
                romaji
                english
              }
              coverImage {
                large
              }
              averageScore
            }
          }
        }
      }
    }
  }
}
"""

POPULAR_ANIME_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {
      id
      idMal
      title {
        romaji
        english
        native
      }
      coverImage {
        extraLarge
        large
        medium
        color
      }
      bannerImage
      description
      episodes
      status
      seasonYear
      averageScore
      popularity
      favourites
      genres
      format
      duration
    }
  }
}
"""

TOP_RATED_ANIME_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    media(type: ANIME, sort: SCORE_DESC, isAdult: false) {
      id
      idMal
      title {
        romaji
        english
        native
      }
      coverImage {
        extraLarge
        large
        medium
        color
      }
      bannerImage
      description
      episodes
      status
      seasonYear
      averageScore
      meanScore
      popularity
      favourites
      genres
      format
      duration
      studios {
        edges {
          isMain
          node {
            name
          }
        }
      }
    }
  }
}
"""

ANIME_DETAILS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    title {
      romaji
      english
      native
      userPreferred
    }
    coverImage {
      extraLarge
      large
      medium
      color
    }
    bannerImage
    description
    episodes
    chapters
    volumes
    status
    startDate {
      year
      month
      day
    }
    endDate {
      year
      month
      day
    }
    season
    seasonYear
    averageScore
    meanScore
    popularity
    favourites
    trending
    genres
    synonyms
    duration
    format
    source
    hashtag
    countryOfOrigin
    isAdult
    isLicensed
    isFavourite
    nextAiringEpisode {
      airingAt
      timeUntilAiring
      episode
    }
    studios {
      edges {
        isMain
        node {
          id
          name
        }
      }
    }
    trailer {
      id
      site
      thumbnail
    }
    characters(perPage: 20) {
      edges {
        role
        node {
          id
          name {
            full
            native
            userPreferred
          }
          image {
            large
            medium
          }
          description
        }
        voiceActors(language: JAPANESE) {
          id
          name {
            full
            native
            userPreferred
          }
          image {
            large
            medium
          }
          language
        }
      }
    }
    staff(perPage: 15) {
      edges {
        role
        node {
          id
          name {
            full
            native
          }
          image {
            large
            medium
          }
          primaryOccupations
        }
      }
    }
    relations {
      edges {
        relationType
        node {
          id
          title {
            romaji
            english
          }
          coverImage {
            large
          }
          format
          type
          status
          averageScore
        }
      }
    }
    recommendations(perPage: 12) {
      edges {
        node {
          mediaRecommendation {
            id
            title {
              romaji
              english
            }
            coverImage {
              large
            }
            averageScore
            format
            episodes
          }
        }
      }
    }
    reviews(perPage: 5) {
      edges {
        node {
          id
          summary
          score
          rating
          createdAt
          user {
            id
            name
            avatar {
              large
            }
          }
        }
      }
    }
    streamingEpisodes {
      title
      thumbnail
      url
      site
    }
    rankings {
      rank
      type
      format
      year
      season
      allTime
      context
    }
    stats {
      scoreDistribution {
        score
        amount
      }
      statusDistribution {
        status
        amount
      }
    }
    externalLinks {
      id
      url
      site
      type
    }
  }
}
"""

SEARCH_ANIME_QUERY = """
query ($search: String, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    media(type: ANIME, search: $search, isAdult: false) {
      id
      idMal
      title {
        romaji
        english
        native
      }
      coverImage {
        large
        medium
        color
      }
      bannerImage
      description(asHtml: false)
      episodes
      status
      seasonYear
      averageScore
      popularity
      genres
      format
      duration
    }
  }
}
"""

SEASONAL_ANIME_QUERY = """
query ($season: MediaSeason, $year: Int, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    media(type: ANIME, season: $season, seasonYear: $year, sort: POPULARITY_DESC, isAdult: false) {
      id
      idMal
      title {
        romaji
        english
        native
      }
      coverImage {
        extraLarge
        large
        medium
        color
      }
      bannerImage
      description
      episodes
      status
      season
      seasonYear
      averageScore
      popularity
      favourites
      genres
      format
      duration
      nextAiringEpisode {
        episode
        airingAt
      }
    }
  }
}
"""


@dataclass
class PageInfo:
    """Page information for paginated responses"""
    total: int
    current_page: int
    last_page: int
    has_next_page: bool


@dataclass
class AnimeTitle:
    """Anime title in different languages"""
    romaji: Optional[str] = None
    english: Optional[str] = None
    native: Optional[str] = None
    preferred: Optional[str] = None


@dataclass
class AnimeImages:
    """Anime image URLs"""
    cover: Optional[str] = None
    poster: Optional[str] = None
    banner: Optional[str] = None
    color: Optional[str] = None


@dataclass
class AnimeRating:
    """Anime rating and popularity metrics"""
    average: Optional[int] = None
    mean: Optional[int] = None
    popularity: Optional[int] = None
    favourites: Optional[int] = None
    trending: Optional[int] = None


@dataclass
class Character:
    """Character information"""
    id: int
    name: str
    native_name: Optional[str] = None
    image: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None


@dataclass
class VoiceActor:
    """Voice actor information"""
    id: int
    name: str
    native_name: Optional[str] = None
    image: Optional[str] = None
    language: Optional[str] = None


@dataclass
class CharacterWithVoiceActor:
    """Character with their voice actor"""
    character: Character
    voice_actor: Optional[VoiceActor] = None
    role: Optional[str] = None


@dataclass
class Staff:
    """Staff member information"""
    id: int
    name: str
    image: Optional[str] = None
    role: Optional[str] = None
    occupations: Optional[List[str]] = None


@dataclass
class RelatedMedia:
    """Related media information"""
    id: int
    title: str
    poster: Optional[str] = None
    relation_type: Optional[str] = None
    format: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    rating: Optional[int] = None


@dataclass
class Recommendation:
    """Recommended media"""
    id: int
    title: str
    poster: Optional[str] = None
    rating: Optional[int] = None
    format: Optional[str] = None
    episodes: Optional[int] = None


@dataclass
class Review:
    """User review"""
    id: int
    summary: Optional[str] = None
    score: Optional[int] = None
    rating: Optional[int] = None
    created_at: Optional[int] = None
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None


@dataclass
class Anime:
    """Complete anime data structure"""
    id: int
    mal_id: Optional[int] = None
    title: Optional[AnimeTitle] = None
    images: Optional[AnimeImages] = None
    description: Optional[str] = None
    episodes: Optional[int] = None
    status: Optional[str] = None
    start_date: Optional[Dict] = None
    end_date: Optional[Dict] = None
    season: Optional[str] = None
    season_year: Optional[int] = None
    ratings: Optional[AnimeRating] = None
    genres: List[str] = field(default_factory=list)
    duration: Optional[int] = None
    format: Optional[str] = None
    source: Optional[str] = None
    country: Optional[str] = None
    is_adult: bool = False
    is_licensed: bool = False
    next_episode: Optional[Dict] = None
    studios: List[Dict] = field(default_factory=list)
    trailer: Optional[Dict] = None
    characters: List[CharacterWithVoiceActor] = field(default_factory=list)
    staff: List[Staff] = field(default_factory=list)
    relations: List[RelatedMedia] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)
    reviews: List[Review] = field(default_factory=list)
    streaming_episodes: List[Dict] = field(default_factory=list)
    rankings: List[Dict] = field(default_factory=list)
    stats: Optional[Dict] = None
    external_links: List[Dict] = field(default_factory=list)


class AniListService:
    """AniList API Service for fetching anime data"""
    
    def __init__(self):
        self.base_url = ANILIST_API_URL
        self.cdn_url = ANILIST_CDN_URL
        self.cache = {}
        self.cache_duration = timedelta(hours=24)
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
    
    async def query(self, query: str, variables: Dict[str, Any] = None) -> Dict:
        """Execute GraphQL query"""
        try:
            session = await self.get_session()
            async with session.post(
                self.base_url,
                json={'query': query, 'variables': variables or {}},
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
            ) as response:
                data = await response.json()
                
                if 'errors' in data:
                    logger.error(f"GraphQL errors: {data['errors']}")
                    raise Exception(data['errors'][0].get('message', 'Unknown error'))
                
                return data.get('data', {})
        except Exception as e:
            logger.error(f"AniList API Error: {e}")
            raise
    
    def format_anime_data(self, raw_data: Dict) -> Optional[Anime]:
        """Format raw API data into Anime dataclass"""
        if not raw_data:
            return None
        
        # Format title
        title = AnimeTitle(
            romaji=raw_data.get('title', {}).get('romaji'),
            english=raw_data.get('title', {}).get('english'),
            native=raw_data.get('title', {}).get('native'),
            preferred=raw_data.get('title', {}).get('userPreferred') or 
                      raw_data.get('title', {}).get('english') or 
                      raw_data.get('title', {}).get('romaji')
        )
        
        # Format images
        images = AnimeImages(
            cover=raw_data.get('coverImage', {}).get('extraLarge') or 
                  raw_data.get('coverImage', {}).get('large'),
            poster=raw_data.get('coverImage', {}).get('large'),
            banner=raw_data.get('bannerImage'),
            color=raw_data.get('coverImage', {}).get('color')
        )
        
        # Format ratings
        ratings = AnimeRating(
            average=raw_data.get('averageScore'),
            mean=raw_data.get('meanScore'),
            popularity=raw_data.get('popularity'),
            favourites=raw_data.get('favourites'),
            trending=raw_data.get('trending')
        )
        
        # Format characters
        characters = []
        for edge in raw_data.get('characters', {}).get('edges', []):
            char_node = edge.get('node', {})
            character = Character(
                id=char_node.get('id', 0),
                name=char_node.get('name', {}).get('full', 'Unknown'),
                native_name=char_node.get('name', {}).get('native'),
                image=char_node.get('image', {}).get('large'),
                role=edge.get('role'),
                description=char_node.get('description')
            )
            
            voice_actor = None
            va_list = edge.get('voiceActors', [])
            if va_list:
                va = va_list[0]
                voice_actor = VoiceActor(
                    id=va.get('id', 0),
                    name=va.get('name', {}).get('full', 'Unknown'),
                    native_name=va.get('name', {}).get('native'),
                    image=va.get('image', {}).get('large'),
                    language=va.get('language')
                )
            
            characters.append(CharacterWithVoiceActor(
                character=character,
                voice_actor=voice_actor,
                role=edge.get('role')
            ))
        
        # Format staff
        staff_list = []
        for edge in raw_data.get('staff', {}).get('edges', []):
            staff_node = edge.get('node', {})
            staff_list.append(Staff(
                id=staff_node.get('id', 0),
                name=staff_node.get('name', {}).get('full', 'Unknown'),
                image=staff_node.get('image', {}).get('large'),
                role=edge.get('role'),
                occupations=staff_node.get('primaryOccupations', [])
            ))
        
        # Format relations
        relations = []
        for edge in raw_data.get('relations', {}).get('edges', []):
            node = edge.get('node', {})
            relations.append(RelatedMedia(
                id=node.get('id', 0),
                title=node.get('title', {}).get('english') or node.get('title', {}).get('romaji', 'Unknown'),
                poster=node.get('coverImage', {}).get('large'),
                relation_type=edge.get('relationType'),
                format=node.get('format'),
                type=node.get('type'),
                status=node.get('status'),
                rating=node.get('averageScore')
            ))
        
        # Format recommendations
        recommendations = []
        for edge in raw_data.get('recommendations', {}).get('edges', []):
            node = edge.get('node', {}).get('mediaRecommendation', {})
            recommendations.append(Recommendation(
                id=node.get('id', 0),
                title=node.get('title', {}).get('english') or node.get('title', {}).get('romaji', 'Unknown'),
                poster=node.get('coverImage', {}).get('large'),
                rating=node.get('averageScore'),
                format=node.get('format'),
                episodes=node.get('episodes')
            ))
        
        # Format reviews
        reviews = []
        for edge in raw_data.get('reviews', {}).get('edges', []):
            node = edge.get('node', {})
            user = node.get('user', {})
            reviews.append(Review(
                id=node.get('id', 0),
                summary=node.get('summary'),
                score=node.get('score'),
                rating=node.get('rating'),
                created_at=node.get('createdAt'),
                user_name=user.get('name'),
                user_avatar=user.get('avatar', {}).get('large')
            ))
        
        return Anime(
            id=raw_data.get('id', 0),
            mal_id=raw_data.get('idMal'),
            title=title,
            images=images,
            description=raw_data.get('description', '').replace('<br>', '\n').replace('<i>', '').replace('</i>', ''),
            episodes=raw_data.get('episodes'),
            status=raw_data.get('status'),
            start_date=raw_data.get('startDate'),
            end_date=raw_data.get('endDate'),
            season=raw_data.get('season'),
            season_year=raw_data.get('seasonYear'),
            ratings=ratings,
            genres=raw_data.get('genres', []),
            duration=raw_data.get('duration'),
            format=raw_data.get('format'),
            source=raw_data.get('source'),
            country=raw_data.get('countryOfOrigin'),
            is_adult=raw_data.get('isAdult', False),
            is_licensed=raw_data.get('isLicensed', False),
            next_episode=raw_data.get('nextAiringEpisode'),
            studios=[{'id': e['node']['id'], 'name': e['node']['name'], 'is_main': e['isMain']} 
                    for e in raw_data.get('studios', {}).get('edges', [])],
            trailer=raw_data.get('trailer'),
            characters=characters,
            staff=staff_list,
            relations=relations,
            recommendations=recommendations,
            reviews=reviews,
            streaming_episodes=raw_data.get('streamingEpisodes', []),
            rankings=raw_data.get('rankings', []),
            stats=raw_data.get('stats'),
            external_links=raw_data.get('externalLinks', [])
        )
    
    async def get_trending_anime(self, page: int = 1, per_page: int = 20) -> Dict:
        """Get trending anime"""
        data = await self.query(TRENDING_ANIME_QUERY, {'page': page, 'perPage': per_page})
        page_data = data.get('Page', {})
        return {
            'page_info': page_data.get('pageInfo', {}),
            'results': [self.format_anime_data(media) for media in page_data.get('media', [])]
        }
    
    async def get_popular_anime(self, page: int = 1, per_page: int = 20) -> Dict:
        """Get popular anime"""
        data = await self.query(POPULAR_ANIME_QUERY, {'page': page, 'perPage': per_page})
        page_data = data.get('Page', {})
        return {
            'page_info': page_data.get('pageInfo', {}),
            'results': [self.format_anime_data(media) for media in page_data.get('media', [])]
        }
    
    async def get_top_rated_anime(self, page: int = 1, per_page: int = 20) -> Dict:
        """Get top rated anime"""
        data = await self.query(TOP_RATED_ANIME_QUERY, {'page': page, 'perPage': per_page})
        page_data = data.get('Page', {})
        return {
            'page_info': page_data.get('pageInfo', {}),
            'results': [self.format_anime_data(media) for media in page_data.get('media', [])]
        }
    
    async def get_anime_details(self, anime_id: int) -> Optional[Anime]:
        """Get detailed anime information by ID"""
        data = await self.query(ANIME_DETAILS_QUERY, {'id': anime_id})
        media = data.get('Media')
        return self.format_anime_data(media)
    
    async def search_anime(self, search_query: str, page: int = 1, per_page: int = 20) -> Dict:
        """Search for anime by title"""
        data = await self.query(SEARCH_ANIME_QUERY, {
            'search': search_query,
            'page': page,
            'perPage': per_page
        })
        page_data = data.get('Page', {})
        return {
            'page_info': page_data.get('pageInfo', {}),
            'results': [self.format_anime_data(media) for media in page_data.get('media', [])]
        }
    
    async def get_seasonal_anime(self, season: str, year: int, page: int = 1, per_page: int = 20) -> Dict:
        """Get seasonal anime"""
        data = await self.query(SEASONAL_ANIME_QUERY, {
            'season': season.upper(),
            'year': year,
            'page': page,
            'perPage': per_page
        })
        page_data = data.get('Page', {})
        return {
            'page_info': page_data.get('pageInfo', {}),
            'results': [self.format_anime_data(media) for media in page_data.get('media', [])]
        }
    
    async def get_current_season_anime(self, page: int = 1, per_page: int = 20) -> Dict:
        """Get current season anime"""
        season, year = self.get_current_season()
        return await self.get_seasonal_anime(season, year, page, per_page)
    
    async def get_next_season_anime(self, page: int = 1, per_page: int = 20) -> Dict:
        """Get next season anime"""
        season, year = self.get_current_season()
        seasons = ['WINTER', 'SPRING', 'SUMMER', 'FALL']
        current_index = seasons.index(season)
        next_season = seasons[(current_index + 1) % 4]
        next_year = year + 1 if current_index == 3 else year
        return await self.get_seasonal_anime(next_season, next_year, page, per_page)
    
    @staticmethod
    def get_current_season() -> tuple:
        """Get current season based on date"""
        now = datetime.now()
        month = now.month
        year = now.year
        
        if 1 <= month <= 3:
            season = 'WINTER'
        elif 4 <= month <= 6:
            season = 'SPRING'
        elif 7 <= month <= 9:
            season = 'SUMMER'
        else:
            season = 'FALL'
        
        return season, year
    
    def clear_cache(self):
        """Clear the cache"""
        self.cache.clear()


# Singleton instance
anilist_service = AniListService()