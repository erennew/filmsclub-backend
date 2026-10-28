"""
TV Series Validation Module
Implements recommended validation flow for TV series uploads with fallback handling
"""

import asyncio
import re
import PTN
from Backend.config import Config
from Backend.logger import LOGGER
from themoviedb import aioTMDb
import httpx
from typing import Optional, Dict, Tuple, List

# Initialize TMDB client
tmdb = aioTMDb(api_key=Config.TMDB_API, language="en-US", region="US")

# Retry configuration
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
NON_RETRYABLE_STATUS_CODES = {401, 403, 404}
MAX_RETRIES = 3
RETRY_DELAY = 2


class TVValidationError(Exception):
    """Custom exception for TV validation errors"""
    pass


def parse_filename(filename: str) -> Optional[Dict]:
    """
    Extract title, season, and episode from filename
    Supports various naming patterns
    """
    try:
        # Try anime parser first
        from Backend.helper.metadata import parse_anime_episode
        anime_result = parse_anime_episode(filename)
        if anime_result:
            return {
                'title': anime_result['title'],
                'season': anime_result.get('season', 1),
                'episode': anime_result['episode'],
                'episode_title': anime_result.get('episode_title'),
                'group': anime_result.get('group'),
                'has_season': anime_result.get('has_season', False),
                'is_anime': True
            }
        
        # Fall back to PTN parser
        parsed = PTN.parse(filename)
        if 'excess' in parsed and any('combined' in item.lower() for item in parsed['excess']):
            LOGGER.warning(f"Skipping {filename} due to 'combined' in excess")
            return None
        
        title = parsed.get('title')
        season = parsed.get('season')
        episode = parsed.get('episode')
        
        if not title:
            LOGGER.warning(f"No title parsed from: {filename}")
            return None
        
        if season and not episode:
            LOGGER.warning(f"Missing episode for season: {filename}")
            return None
        
        return {
            'title': title,
            'season': season or 1,
            'episode': episode,
            'episode_title': None,
            'group': parsed.get('group'),
            'has_season': bool(season),
            'is_anime': False
        }
        
    except Exception as e:
        LOGGER.error(f"Error parsing filename {filename}: {e}")
        return None


async def search_tmdb_by_title(title: str, retry_count: int = 0) -> Optional[int]:
    """
    Search TMDB by title and return the best matching TMDB ID
    Implements retry logic for network errors only
    """
    try:
        await asyncio.sleep(1)  # Rate limiting
        
        async with httpx.AsyncClient(timeout=20) as session:
            url = f"https://api.themoviedb.org/3/search/tv"
            params = {
                "api_key": Config.TMDB_API,
                "query": title,
                "language": "en-US"
            }
            
            response = await session.get(url, params=params)
            
            # Handle retryable errors
            if response.status_code in RETRYABLE_STATUS_CODES and retry_count < MAX_RETRIES:
                LOGGER.warning(f"TMDB search failed with status {response.status_code}, retrying ({retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
                return await search_tmdb_by_title(title, retry_count + 1)
            
            # Handle non-retryable errors
            if response.status_code in NON_RETRYABLE_STATUS_CODES:
                if response.status_code == 404:
                    LOGGER.debug(f"TMDB search returned 404 for: {title}")
                    return None
                elif response.status_code == 401:
                    LOGGER.error("TMDB API key is invalid")
                    raise TVValidationError("Invalid TMDB API key")
                elif response.status_code == 403:
                    LOGGER.error("TMDB API access forbidden")
                    raise TVValidationError("TMDB API access forbidden")
            
            response.raise_for_status()
            data = response.json()
            
            if not data.get('results'):
                LOGGER.debug(f"No TMDB results found for: {title}")
                return None
            
            # Pick highest score / first_air_date
            results = data['results']
            best_result = max(results, key=lambda x: (x.get('vote_average', 0), x.get('first_air_date', '')))
            
            LOGGER.info(f"Found TMDB ID {best_result['id']} for '{title}' (score: {best_result.get('vote_average', 0)})")
            return best_result['id']
            
    except TVValidationError:
        raise
    except Exception as e:
        if retry_count < MAX_RETRIES:
            LOGGER.warning(f"TMDB search failed with error, retrying ({retry_count + 1}/{MAX_RETRIES}): {e}")
            await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
            return await search_tmdb_by_title(title, retry_count + 1)
        LOGGER.error(f"TMDB search failed after retries for '{title}': {e}")
        return None


async def search_tvmaze_by_title(title: str, retry_count: int = 0) -> Optional[str]:
    """
    Search TVMaze by title as fallback
    Returns TVMaze ID
    """
    try:
        await asyncio.sleep(1)
        
        async with httpx.AsyncClient(timeout=20) as session:
            url = f"http://api.tvmaze.com/search/shows"
            params = {"q": title}
            
            response = await session.get(url, params=params)
            
            if response.status_code in RETRYABLE_STATUS_CODES and retry_count < MAX_RETRIES:
                LOGGER.warning(f"TVMaze search failed, retrying ({retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
                return await search_tvmaze_by_title(title, retry_count + 1)
            
            if response.status_code == 404:
                LOGGER.debug(f"TVMaze search returned 404 for: {title}")
                return None
            
            response.raise_for_status()
            data = response.json()
            
            if not data:
                LOGGER.debug(f"No TVMaze results found for: {title}")
                return None
            
            # Pick first result (TVMaze doesn't have scores)
            best_result = data[0]['show']
            tvmaze_id = best_result.get('id')
            
            LOGGER.info(f"Found TVMaze ID {tvmaze_id} for '{title}'")
            return str(tvmaze_id)
            
    except Exception as e:
        if retry_count < MAX_RETRIES:
            LOGGER.warning(f"TVMaze search failed, retrying ({retry_count + 1}/{MAX_RETRIES}): {e}")
            await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
            return await search_tvmaze_by_title(title, retry_count + 1)
        LOGGER.error(f"TVMaze search failed after retries for '{title}': {e}")
        return None


async def search_imdb_by_title(title: str, retry_count: int = 0) -> Optional[str]:
    """
    Search IMDb by title as fallback
    Returns IMDb ID (tt format)
    """
    try:
        from Backend.helper.imdb import search_title
        
        if retry_count < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY * retry_count)
        
        result = await search_title(query=title, type="tvSeries")
        
        if result:
            imdb_id = result.get('id')
            LOGGER.info(f"Found IMDb ID {imdb_id} for '{title}'")
            return imdb_id
        
        LOGGER.debug(f"No IMDb results found for: {title}")
        return None
        
    except Exception as e:
        if retry_count < MAX_RETRIES:
            LOGGER.warning(f"IMDb search failed, retrying ({retry_count + 1}/{MAX_RETRIES}): {e}")
            await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
            return await search_imdb_by_title(title, retry_count + 1)
        LOGGER.error(f"IMDb search failed after retries for '{title}': {e}")
        return None


async def validate_tmdb_season_episode(tmdb_id: int, season: int, episode: int, retry_count: int = 0) -> Tuple[bool, Optional[str]]:
    """
    Validate season and episode exist for a TMDB ID
    Returns (is_valid, error_message)
    Implements Indian serial fallback: if season endpoint 404 but TV exists, allow with warning
    """
    try:
        await asyncio.sleep(1)
        
        async with httpx.AsyncClient(timeout=20) as session:
            # First check if the TV show exists
            tv_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
            params = {"api_key": Config.TMDB_API}
            
            response = await session.get(tv_url, params=params)
            
            # Handle retryable errors
            if response.status_code in RETRYABLE_STATUS_CODES and retry_count < MAX_RETRIES:
                LOGGER.warning(f"TMDB TV check failed, retrying ({retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
                return await validate_tmdb_season_episode(tmdb_id, season, episode, retry_count + 1)
            
            # Handle non-retryable errors
            if response.status_code == 404:
                LOGGER.warning(f"TMDB TV ID {tmdb_id} not found - invalid ID")
                return False, "Invalid TMDB ID - TV show not found"
            elif response.status_code in {401, 403}:
                LOGGER.error(f"TMDB API error: {response.status_code}")
                return False, f"TMDB API error: {response.status_code}"
            
            response.raise_for_status()
            tv_data = response.json()
            
            # TV exists, now check season
            season_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}"
            response = await session.get(season_url, params=params)
            
            # Handle retryable errors for season check
            if response.status_code in RETRYABLE_STATUS_CODES and retry_count < MAX_RETRIES:
                LOGGER.warning(f"TMDB season check failed, retrying ({retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
                return await validate_tmdb_season_episode(tmdb_id, season, episode, retry_count + 1)
            
            # Indian serial fallback: if season 404 but TV exists, allow with warning
            if response.status_code == 404:
                LOGGER.warning(f"Season {season} not found for TMDB ID {tmdb_id}, but TV exists - allowing with warning (Indian serial fallback)")
                return True, f"Season {season} validation unavailable - episode data incomplete on TMDB"
            
            if response.status_code in {401, 403}:
                LOGGER.error(f"TMDB API error: {response.status_code}")
                return False, f"TMDB API error: {response.status_code}"
            
            response.raise_for_status()
            season_data = response.json()
            
            # Check if episode exists
            episodes = season_data.get('episodes', [])
            episode_exists = any(ep.get('episode_number') == episode for ep in episodes)
            
            if not episode_exists:
                LOGGER.warning(f"Episode {episode} not found in season {season} for TMDB ID {tmdb_id}")
                # Indian serial fallback: allow with warning if episode not found
                return True, f"Episode {episode} validation unavailable - episode data incomplete on TMDB"
            
            LOGGER.info(f"✅ Season {season} Episode {episode} validated for TMDB ID {tmdb_id}")
            return True, None
            
    except Exception as e:
        if retry_count < MAX_RETRIES:
            LOGGER.warning(f"TMDB validation failed, retrying ({retry_count + 1}/{MAX_RETRIES}): {e}")
            await asyncio.sleep(RETRY_DELAY * (retry_count + 1))
            return await validate_tmdb_season_episode(tmdb_id, season, episode, retry_count + 1)
        LOGGER.error(f"TMDB validation failed after retries: {e}")
        return False, f"Validation error: {str(e)}"


async def validate_tv_series(filename: str, provided_tmdb_id: Optional[int] = None) -> Dict:
    """
    Main validation function implementing the recommended flow
    
    Flow:
    1. Parse filename to extract title, season, episode
    2. If provided TMDB ID, validate it (don't trust it forever)
    3. If no ID or validation fails, search TMDB by title
    4. If TMDB fails, fallback to TVMaze
    5. If TVMaze fails, fallback to IMDb
    6. Validate season/episode with Indian serial fallback
    7. Return validation result with warnings if applicable
    """
    result = {
        'success': False,
        'tmdb_id': None,
        'tvmaze_id': None,
        'imdb_id': None,
        'title': None,
        'season': None,
        'episode': None,
        'warnings': [],
        'errors': []
    }
    
    # Step 1: Parse filename
    parsed = parse_filename(filename)
    if not parsed:
        result['errors'].append("Failed to parse filename")
        return result
    
    result['title'] = parsed['title']
    result['season'] = parsed['season']
    result['episode'] = parsed['episode']
    result['is_anime'] = parsed.get('is_anime', False)
    
    LOGGER.info(f"Parsed: {parsed['title']} S{parsed['season']}E{parsed['episode']}")
    
    # Step 2: If provided TMDB ID, validate it
    if provided_tmdb_id:
        LOGGER.info(f"Validating provided TMDB ID: {provided_tmdb_id}")
        is_valid, error_msg = await validate_tmdb_season_episode(provided_tmdb_id, parsed['season'], parsed['episode'])
        
        if is_valid:
            result['tmdb_id'] = provided_tmdb_id
            if error_msg:
                result['warnings'].append(error_msg)
            result['success'] = True
            return result
        else:
            LOGGER.warning(f"Provided TMDB ID {provided_tmdb_id} invalid: {error_msg}")
            result['warnings'].append(f"Provided TMDB ID invalid: {error_msg}")
            # Continue to search by title
    
    # Step 3: Search TMDB by title
    LOGGER.info(f"Searching TMDB by title: {parsed['title']}")
    tmdb_id = await search_tmdb_by_title(parsed['title'])
    
    if tmdb_id:
        result['tmdb_id'] = tmdb_id
        
        # Validate season/episode
        is_valid, error_msg = await validate_tmdb_season_episode(tmdb_id, parsed['season'], parsed['episode'])
        
        if is_valid:
            if error_msg:
                result['warnings'].append(error_msg)
            result['success'] = True
            return result
        else:
            result['errors'].append(error_msg)
            # Continue to fallbacks
    
    # Step 4: Fallback to TVMaze
    LOGGER.info(f"TMDB search failed, trying TVMaze for: {parsed['title']}")
    tvmaze_id = await search_tvmaze_by_title(parsed['title'])
    
    if tvmaze_id:
        result['tvmaze_id'] = tvmaze_id
        result['warnings'].append("Using TVMaze data - TMDB not available")
        result['success'] = True
        return result
    
    # Step 5: Fallback to IMDb
    LOGGER.info(f"TVMaze search failed, trying IMDb for: {parsed['title']}")
    imdb_id = await search_imdb_by_title(parsed['title'])
    
    if imdb_id:
        result['imdb_id'] = imdb_id
        result['warnings'].append(f"Using IMDb data - TMDB/TVMaze not available. Accept with warning")
        result['success'] = True
        return result
    
    # All searches failed
    result['errors'].append("No matching TV series found on TMDB, TVMaze, or IMDb")
    return result


async def refresh_tmdb_id(old_tmdb_id: int, title: str) -> Optional[int]:
    """
    Refresh a cached TMDB ID by searching by title
    Use when cached IDs may be wrong or outdated
    """
    LOGGER.info(f"Refreshing TMDB ID for '{title}' (old ID: {old_tmdb_id})")
    new_tmdb_id = await search_tmdb_by_title(title)
    
    if new_tmdb_id and new_tmdb_id != old_tmdb_id:
        LOGGER.info(f"TMDB ID refreshed: {old_tmdb_id} -> {new_tmdb_id}")
    elif new_tmdb_id == old_tmdb_id:
        LOGGER.info(f"TMDB ID unchanged: {old_tmdb_id}")
    else:
        LOGGER.warning(f"Failed to refresh TMDB ID for '{title}'")
    
    return new_tmdb_id
