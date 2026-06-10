import asyncio
import PTN
import re
from Backend.helper.imdb import get_detail, get_season, search_title
from Backend.helper.pyro import extract_tmdb_id, normalize_languages
from Backend.helper.anilist import anilist_service
from Backend.helper.jikan import jikan_api_service
from themoviedb import aioTMDb
from Backend.config import Config
import Backend
from Backend.logger import LOGGER
import traceback
import httpx

DELAY = 2

tmdb = aioTMDb(key=Config.TMDB_API, language="en-US", region="US")


async def fetch_anime_from_anilist(title: str) -> dict:
    """Fetch anime metadata from AniList API"""
    try:
        await asyncio.sleep(DELAY)
        search_results = await anilist_service.search_anime(title, page=1, per_page=5)
        
        if not search_results or not search_results.get('results'):
            LOGGER.debug(f"No AniList results for: {title}")
            return None
        
        # Get the first (best) match
        anime_data = search_results['results'][0]
        if not anime_data:
            return None
        
        # Get detailed information
        await asyncio.sleep(DELAY)
        detailed_anime = await anilist_service.get_anime_details(anime_data.id)
        
        if not detailed_anime:
            return anime_data
        
        return detailed_anime
        
    except Exception as e:
        LOGGER.debug(f"AniList fetch failed for '{title}': {e}")
        return None


async def fetch_anime_from_jikan(title: str) -> dict:
    """Fetch anime metadata from Jikan (MyAnimeList) API"""
    try:
        await asyncio.sleep(DELAY)
        search_results = await jikan_api_service.search_anime(title, page=1, limit=5)
        
        if not search_results or not search_results.get('results'):
            LOGGER.debug(f"No Jikan results for: {title}")
            return None
        
        # Get the first (best) match
        anime_data = search_results['results'][0]
        if not anime_data:
            return None
        
        # Get detailed information
        await asyncio.sleep(DELAY)
        detailed_anime = await jikan_api_service.get_anime_by_id(anime_data.id)
        
        if not detailed_anime:
            return anime_data
        
        # Fetch characters separately
        await asyncio.sleep(DELAY)
        characters = await jikan_api_service.get_anime_characters(anime_data.id)
        
        # Attach characters to the anime data
        if characters:
            detailed_anime.characters = characters
        
        return detailed_anime
        
    except Exception as e:
        LOGGER.debug(f"Jikan fetch failed for '{title}': {e}")
        return None


def format_anilist_to_standard(anime_data, season_number: int, episode_number: int, episode_title: str = None, group_name: str = None, has_season: bool = False) -> dict:
    """Format AniList data to standard metadata format"""
    if not anime_data:
        return None
    
    try:
        title_obj = anime_data.title if hasattr(anime_data, 'title') else None
        show_title = title_obj.english if title_obj and title_obj.english else (title_obj.romaji if title_obj else "Unknown")
        
        images = anime_data.images if hasattr(anime_data, 'images') else None
        poster = images.cover if images else ''
        backdrop = images.banner if images else ''
        
        ratings = anime_data.ratings if hasattr(anime_data, 'ratings') else None
        rate = ratings.average if ratings else 0
        
        genres = anime_data.genres if hasattr(anime_data, 'genres') else []
        # Ensure "Anime" is always added as a genre tag for AniList sourced content
        if genres and "Anime" not in genres:
            genres.append("Anime")
        elif not genres:
            genres = ["Anime"]

        # Format cast data
        cast = []
        if hasattr(anime_data, 'characters') and anime_data.characters:
            for char_data in anime_data.characters[:16]:  # Limit to 16 cast members
                character = char_data.character if hasattr(char_data, 'character') else None
                if character:
                    cast.append({
                        "id": character.id if hasattr(character, 'id') else 0,
                        "name": character.name if hasattr(character, 'name') else "Unknown",
                        "profile": character.image if hasattr(character, 'image') else "",
                        "character": char_data.role if hasattr(char_data, 'role') else "",
                        "tmdb_id": character.id if hasattr(character, 'id') else 0
                    })
        
        # Use prefixed AniList ID to avoid polluting TMDB ID space
        anilist_id = anime_data.id if hasattr(anime_data, 'id') else 0
        tmdb_id = f"al_{anilist_id}" if anilist_id else 0

        return {
            "tmdb_id": tmdb_id,
            "anilist_id": anilist_id,
            "title": show_title,
            "year": anime_data.season_year if hasattr(anime_data, 'season_year') else 0,
            "rate": rate,
            "description": anime_data.description if hasattr(anime_data, 'description') else '',
            "total_seasons": anime_data.season_year if has_season else 1,
            "total_episodes": anime_data.episodes if hasattr(anime_data, 'episodes') else episode_number,
            "poster": poster,
            "backdrop": backdrop,
            "status": anime_data.status if hasattr(anime_data, 'status') else 'Ongoing',
            "genres": genres,
            "media_type": "tv",
            "season_number": season_number,
            "episode_number": episode_number,
            "episode_title": episode_title or f"Episode {episode_number}",
            "episode_backdrop": backdrop,
            "quality": "1080p",
            "languages": ['ja', 'en'],  # Default for anime
            "rip": 'Blu-ray',
            "source": "AniList",
            "imdb_url": None,
            "tmdb_url": f"https://anilist.co/anime/{anilist_id}" if anilist_id else None,
            "is_anime": True,
            "group": group_name,
            "release_group": group_name,
            "has_seasonal_format": has_season,
            "cast": cast,
            "next_episode_to_air": None,
            "last_episode_to_air": None,
        }
    except Exception as e:
        LOGGER.error(f"Error formatting AniList data: {e}")
        return None


def format_jikan_to_standard(anime_data, season_number: int, episode_number: int, episode_title: str = None, group_name: str = None, has_season: bool = False) -> dict:
    """Format Jikan data to standard metadata format"""
    if not anime_data:
        return None
    
    try:
        show_title = anime_data.title_english if anime_data.title_english else anime_data.title

        # Safe image field access with fallback chain
        images = anime_data.images if hasattr(anime_data, 'images') else None
        poster = ''
        if images:
            poster = getattr(images, 'jpg_large', None) or getattr(images, 'jpg_image', None) or getattr(images, 'jpg', None) or getattr(images, 'webp_large', None) or getattr(images, 'webp_image', None) or ''

        genres = anime_data.genres if hasattr(anime_data, 'genres') else []
        # Ensure "Anime" is always added as a genre tag for Jikan sourced content
        if genres and "Anime" not in genres:
            genres.append("Anime")
        elif not genres:
            genres = ["Anime"]

        # Format cast data
        cast = []
        if hasattr(anime_data, 'characters') and anime_data.characters:
            for char_data in anime_data.characters[:16]:  # Limit to 16 cast members
                if char_data:
                    cast.append({
                        "id": char_data.id if hasattr(char_data, 'id') else 0,
                        "name": char_data.name if hasattr(char_data, 'name') else "Unknown",
                        "profile": char_data.image if hasattr(char_data, 'image') else "",
                        "character": char_data.role if hasattr(char_data, 'role') else "",
                        "tmdb_id": char_data.id if hasattr(char_data, 'id') else 0
                    })
        
        return {
            "tmdb_id": anime_data.id if hasattr(anime_data, 'id') else 0,
            "title": show_title,
            "year": anime_data.year if hasattr(anime_data, 'year') else 0,
            "rate": anime_data.score if hasattr(anime_data, 'score') else 0,
            "description": anime_data.synopsis if hasattr(anime_data, 'synopsis') else '',
            "total_seasons": 1 if not has_season else season_number,
            "total_episodes": anime_data.episodes if hasattr(anime_data, 'episodes') else episode_number,
            "poster": poster,
            "backdrop": '',
            "status": anime_data.status if hasattr(anime_data, 'status') else 'Ongoing',
            "genres": anime_data.genres if hasattr(anime_data, 'genres') else [],
            "media_type": "tv",
            "season_number": season_number,
            "episode_number": episode_number,
            "episode_title": episode_title or f"Episode {episode_number}",
            "episode_backdrop": '',
            "quality": "1080p",
            "languages": ['ja', 'en'],  # Default for anime
            "rip": 'Blu-ray',
            "source": "MyAnimeList",
            "imdb_url": f"https://myanimelist.net/anime/{anime_data.id}" if hasattr(anime_data, 'id') else None,
            "tmdb_url": None,
            "is_anime": True,
            "group": group_name,
            "release_group": group_name,
            "has_seasonal_format": has_season,
            "cast": cast,
            "next_episode_to_air": None,
            "last_episode_to_air": None,
        }
    except Exception as e:
        LOGGER.error(f"Error formatting Jikan data: {e}")
        return None


def _tmdb_image_url(path: str | None, size: str = "w500") -> str:
    if not path:
        return ""
    if str(path).startswith("http"):
        return str(path)
    return f"https://image.tmdb.org/t/p/{size}{path}"


def _compact_episode_payload(episode: dict | None) -> dict | None:
    if not episode:
        return None
    return {
        "id": episode.get("id"),
        "name": episode.get("name") or f"Episode {episode.get('episode_number') or ''}".strip(),
        "overview": episode.get("overview") or "",
        "air_date": episode.get("air_date") or "",
        "season_number": episode.get("season_number"),
        "episode_number": episode.get("episode_number"),
        "runtime": episode.get("runtime"),
        "still": _tmdb_image_url(episode.get("still_path"), "w780"),
    }


async def get_tmdb_people_and_schedule(tmdb_id: int | str | None, media_type: str) -> dict:
    """Return compact cast data and next/last episode info directly from TMDB.

    This is intentionally small so details pages stay fast and frontend payloads stay clean.
    """
    if not Config.TMDB_API or not tmdb_id:
        return {"cast": [], "next_episode_to_air": None, "last_episode_to_air": None}

    tmdb_kind = "movie" if media_type == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/{tmdb_kind}/{tmdb_id}"
    params = {
        "api_key": Config.TMDB_API,
        "language": "en-US",
        "append_to_response": "credits",
    }

    try:
        async with httpx.AsyncClient(timeout=20) as session:
            response = await session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        LOGGER.debug(f"TMDB compact people/schedule fetch failed for {media_type} {tmdb_id}: {exc}")
        return {"cast": [], "next_episode_to_air": None, "last_episode_to_air": None}

    cast = []
    for person in (data.get("credits") or {}).get("cast", [])[:18]:
        name = (person.get("name") or "").strip()
        if not name:
            continue
        cast.append({
            "id": person.get("id"),
            "name": name,
            "character": person.get("character") or "",
            "profile": _tmdb_image_url(person.get("profile_path"), "w185"),
            "known_for_department": person.get("known_for_department") or "Acting",
            "order": person.get("order", len(cast)),
        })

    return {
        "cast": cast,
        "next_episode_to_air": _compact_episode_payload(data.get("next_episode_to_air")),
        "last_episode_to_air": _compact_episode_payload(data.get("last_episode_to_air")),
    }

def parse_anime_episode(filename: str) -> dict:
    """
    Enhanced parser for anime episodes that handles various naming patterns
    """
    # Common anime patterns - ORDER MATTERS, most specific patterns first
    patterns = [
        # [Group] Title - S##E## format (seasonal anime with full season/episode format)
        r'\[([^\]]+)\]\s*(.+?)\s*-\s*S(\d{1,2})E(\d{1,4})\s*(?:\[.*?\])*.*?\.(\w+)$',
        
        # [Group] Title - #### - Episode Title.ext (with episode title, 3-4 digits)
        r'\[([^\]]+)\]\s*(.+?)\s*-\s*(\d{3,4})\s*-\s*(.+?)(?:\[.*?\])*.*?\.(\w+)$',
        
        # [Group] Title - #### [quality/other info].ext (with quality info in brackets, 3-4 digits)
        r'\[([^\]]+)\]\s*(.+?)\s*-\s*(\d{3,4})\s*(?:\[.*?\])+.*?\.(\w+)$',
        
        # [Group] Title - ####.ext (basic 3-4 digit episode format)
        r'\[([^\]]+)\]\s*(.+?)\s*-\s*(\d{3,4})(?:\s*-\s*(.+?))?\.(\w+)$',
        
        # [Group] Title - ## - Episode Title.ext (2-digit episodes with title)
        r'\[([^\]]+)\]\s*(.+?)\s*-\s*(\d{1,2})\s*-\s*(.+?)(?:\[.*?\])*.*?\.(\w+)$',
        
        # [Group] Title - ##.ext (2-digit episodes)
        r'\[([^\]]+)\]\s*(.+?)\s*-\s*(\d{1,2})(?:\s*-\s*(.+?))?\.(\w+)$',
        
        # Title - S##E## format (without group, seasonal)
        r'^(.+?)\s*-\s*S(\d{1,2})E(\d{1,4})\s*(?:\[.*?\])*.*?\.(\w+)$',
        
        # Title - #### - Episode Title.ext (without group, 3-4 digits)
        r'^(.+?)\s*-\s*(\d{3,4})\s*-\s*(.+?)(?:\[.*?\])*.*?\.(\w+)$',
        
        # Title - #### [quality info].ext (without group, 3-4 digits)
        r'^(.+?)\s*-\s*(\d{3,4})\s*(?:\[.*?\])+.*?\.(\w+)$',
        
        # Title - ####.ext (without group, basic format, 3-4 digits)
        r'^(.+?)\s*-\s*(\d{3,4})(?:\s*-\s*(.+?))?\.(\w+)$',
        
        # Title - ## - Episode Title.ext (without group, 2-digit with title)
        r'^(.+?)\s*-\s*(\d{1,2})\s*-\s*(.+?)(?:\[.*?\])*.*?\.(\w+)$',
        
        # Title - ##.ext (without group, 2-digit)
        r'^(.+?)\s*-\s*(\d{1,2})(?:\s*-\s*(.+?))?\.(\w+)$',
        
        # Title Episode.ext (space separated, no hyphen)
        r'^(.+?)\s+(\d{1,4})\s*(?:-\s*(.+?))?\s*\.(\w+)$',
    ]
    
    for i, pattern in enumerate(patterns):
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            groups = match.groups()
            
            # Pattern 0: [Group] Title - S##E## format (seasonal anime)
            if i == 0:
                group, title, season, episode, ext = groups
                return {
                    'group': group,
                    'title': title.strip(),
                    'season': int(season),
                    'episode': int(episode),
                    'episode_title': None,
                    'has_season': True
                }
            
            # Pattern 6: Title - S##E## format (without group, seasonal)
            elif i == 6:
                title, season, episode, ext = groups
                return {
                    'title': title.strip(),
                    'season': int(season),
                    'episode': int(episode),
                    'episode_title': None,
                    'has_season': True
                }
            
            # Patterns with group name and episode titles (1, 4)
            elif i in [1, 4] and len(groups) >= 5:
                group, title, episode, episode_title, ext = groups
                return {
                    'group': group,
                    'title': title.strip(),
                    'season': 1,  # Default season for non-seasonal anime
                    'episode': int(episode),
                    'episode_title': episode_title.strip() if episode_title else None,
                    'has_season': False
                }
            
            # Patterns with group name, potentially with episode title (2, 3, 5)
            elif i in [2, 3, 5] and len(groups) >= 4:
                group, title, episode = groups[0], groups[1], groups[2]
                # Check if there's an episode title (some patterns have optional episode title)
                episode_title = None
                if len(groups) > 4 and groups[3] and groups[3].strip():
                    episode_title = groups[3].strip()
                elif len(groups) == 5 and groups[3] and groups[3].strip():
                    episode_title = groups[3].strip()
                
                return {
                    'group': group,
                    'title': title.strip(),
                    'season': 1,  # Default season for non-seasonal anime
                    'episode': int(episode),
                    'episode_title': episode_title,
                    'has_season': False
                }
            
            # Patterns without group name, with episode titles (7, 10)
            elif i in [7, 10] and len(groups) >= 4:
                title, episode, episode_title, ext = groups
                return {
                    'title': title.strip(),
                    'season': 1,  # Default season for non-seasonal anime
                    'episode': int(episode),
                    'episode_title': episode_title.strip() if episode_title else None,
                    'has_season': False
                }
            
            # Patterns without group name, potentially with episode title (8, 9, 11, 12)
            elif i in [8, 9, 11, 12] and len(groups) >= 3:
                title, episode = groups[0], groups[1]
                # Check if there's an episode title
                episode_title = None
                if len(groups) > 3 and groups[2] and groups[2].strip():
                    episode_title = groups[2].strip()
                
                return {
                    'title': title.strip(),
                    'season': 1,  # Default season for non-seasonal anime
                    'episode': int(episode),
                    'episode_title': episode_title,
                    'has_season': False
                }
    
    return None

async def get_english_episode_title(tv_id: int, season: int, episode: int, imdb_id: str = None, show_title: str = None) -> str:
    """
    Enhanced function to get English episode titles with comprehensive fallback options
    Prioritizes original English titles from multiple sources
    """
    episode_title = f"Episode {episode}"  # Default fallback
    
    try:
        # Method 1: Try TMDB with English language first
        try:
            # Create English-specific TMDB client
            tmdb_en = aioTMDb(key=Config.TMDB_API, language="en-US", region="US")
            await asyncio.sleep(DELAY)
            ep_details = await tmdb_en.episode(tv_id, season, episode).details()
            
            if ep_details and hasattr(ep_details, 'name') and ep_details.name and ep_details.name.strip():
                candidate_title = ep_details.name.strip()
                # Validate it's not just a generic title
                if candidate_title and candidate_title != f"Episode {episode}" and len(candidate_title) > 1:
                    episode_title = candidate_title
                    LOGGER.info(f"Got English episode title from TMDB (en-US): '{episode_title}'")
                    return episode_title
            
        except Exception as e:
            LOGGER.debug(f"TMDB English (en-US) episode fetch failed: {e}")
        
        # Method 2: Try TMDB with just 'en' language code
        try:
            tmdb_en_simple = aioTMDb(key=Config.TMDB_API, language="en", region="US")
            await asyncio.sleep(DELAY)
            ep_details_en = await tmdb_en_simple.episode(tv_id, season, episode).details()
            
            if ep_details_en and hasattr(ep_details_en, 'name') and ep_details_en.name and ep_details_en.name.strip():
                candidate_title = ep_details_en.name.strip()
                if candidate_title and candidate_title != f"Episode {episode}" and len(candidate_title) > 1:
                    episode_title = candidate_title
                    LOGGER.info(f"Got English episode title from TMDB (en): '{episode_title}'")
                    return episode_title
                
        except Exception as e:
            LOGGER.debug(f"TMDB English (en) episode fetch failed: {e}")
        
        # Method 3: Try original TMDB client (default language)
        try:
            await asyncio.sleep(DELAY)
            ep_details_orig = await tmdb.episode(tv_id, season, episode).details()
            
            if ep_details_orig and hasattr(ep_details_orig, 'name') and ep_details_orig.name and ep_details_orig.name.strip():
                candidate_title = ep_details_orig.name.strip()
                if candidate_title and candidate_title != f"Episode {episode}" and len(candidate_title) > 1:
                    episode_title = candidate_title
                    LOGGER.info(f"Got episode title from TMDB (default): '{episode_title}'")
                    return episode_title
                
        except Exception as e:
            LOGGER.debug(f"TMDB default language episode fetch failed: {e}")
        
        # Method 4: Try IMDb episode data if we have an IMDb ID
        if imdb_id:
            try:
                await asyncio.sleep(DELAY)
                ep_details_imdb = await get_season(imdb_id=imdb_id, season_id=season, episode_id=episode)
                
                if ep_details_imdb and isinstance(ep_details_imdb, dict):
                    imdb_title = ep_details_imdb.get('title', '').strip()
                    if imdb_title and imdb_title != f"Episode {episode}" and len(imdb_title) > 1:
                        episode_title = imdb_title
                        LOGGER.info(f"Got episode title from IMDb: '{episode_title}'")
                        return episode_title
                        
            except Exception as e:
                LOGGER.debug(f"IMDb episode fetch failed: {e}")
        
        # Method 5: Try TMDB translations API for English titles
        try:
            await asyncio.sleep(DELAY)
            translations = await tmdb.episode(tv_id, season, episode).translations()
            
            if translations and hasattr(translations, 'translations'):
                # Look for English translations first (US, UK, etc.)
                english_variants = ['en-US', 'en-GB', 'en-CA', 'en-AU', 'en']
                
                for lang_code in english_variants:
                    for trans in translations.translations:
                        if (hasattr(trans, 'iso_639_1') and trans.iso_639_1 == 'en' and 
                            hasattr(trans, 'iso_3166_1') and f"en-{trans.iso_3166_1}" == lang_code):
                            if (hasattr(trans, 'data') and hasattr(trans.data, 'name') and 
                                trans.data.name and trans.data.name.strip()):
                                candidate_title = trans.data.name.strip()
                                if candidate_title != f"Episode {episode}" and len(candidate_title) > 1:
                                    episode_title = candidate_title
                                    LOGGER.info(f"Got English episode title from TMDB translations ({lang_code}): '{episode_title}'")
                                    return episode_title
                
                # If no specific English variant found, try any English translation
                for trans in translations.translations:
                    if hasattr(trans, 'iso_639_1') and trans.iso_639_1 == 'en':
                        if (hasattr(trans, 'data') and hasattr(trans.data, 'name') and 
                            trans.data.name and trans.data.name.strip()):
                            candidate_title = trans.data.name.strip()
                            if candidate_title != f"Episode {episode}" and len(candidate_title) > 1:
                                episode_title = candidate_title
                                LOGGER.info(f"Got English episode title from TMDB translations: '{episode_title}'")
                                return episode_title
                        
        except Exception as e:
            LOGGER.debug(f"TMDB translations fetch failed: {e}")
        
        # Method 6: Try getting season details and extract episode name
        try:
            await asyncio.sleep(DELAY)
            season_details = await tmdb.season(tv_id, season).details()
            
            if season_details and hasattr(season_details, 'episodes'):
                for ep in season_details.episodes:
                    if hasattr(ep, 'episode_number') and ep.episode_number == episode:
                        if hasattr(ep, 'name') and ep.name and ep.name.strip():
                            candidate_title = ep.name.strip()
                            if candidate_title != f"Episode {episode}" and len(candidate_title) > 1:
                                episode_title = candidate_title
                                LOGGER.info(f"Got episode title from season details: '{episode_title}'")
                                return episode_title
                        break
                        
        except Exception as e:
            LOGGER.debug(f"TMDB season details fetch failed: {e}")
    
    except Exception as e:
        LOGGER.warning(f"Error in get_english_episode_title: {e}")
    
    # Final fallback - create a more descriptive default if we have show title
    if show_title:
        episode_title = f"{show_title} - Episode {episode}"
    
    LOGGER.info(f"Using fallback episode title: '{episode_title}'")
    return episode_title

async def metadata(filename: str, media) -> dict:
    """
    Main metadata parsing function that handles both anime and regular content
    Enhanced to prioritize default ID when set via /set command
    """
    try:
        # First try the enhanced anime parser
        anime_parsed = parse_anime_episode(filename)
        
        if anime_parsed:
            title = anime_parsed['title']
            season_number = anime_parsed.get('season', 1)
            episode_number = anime_parsed['episode']
            episode_title = anime_parsed.get('episode_title')
            group_name = anime_parsed.get('group')
            has_season = anime_parsed.get('has_season', False)
            
            LOGGER.info(f"Parsed anime: {title} - S{season_number}E{episode_number:03d} (Group: {group_name})")
            
            # Pass season information to the anime metadata fetcher
            return await fetch_anime_metadata(title, season_number, episode_number, episode_title, group_name, has_season)
        
        # Fall back to PTN parser for regular content
        parsed = PTN.parse(filename)
        if 'excess' in parsed and any('combined' in item.lower() for item in parsed['excess']):
            LOGGER.info(f"Skipping {filename} due to 'combined' in excess")
            return None

        title = parsed.get('title')
        season = parsed.get('season')
        episode = parsed.get('episode')
        year = parsed.get('year')
        quality = parsed.get('resolution')
        languages = normalize_languages(parsed.get('language'))
        rip = parsed.get('quality')
        
        # Extract group from PTN parsing if available
        group_name = None
        if 'group' in parsed:
            group_name = parsed['group']
        elif 'excess' in parsed:
            # Sometimes group names end up in excess
            for item in parsed['excess']:
                if item.startswith('[') and item.endswith(']'):
                    group_name = item.strip('[]')
                    break

        if isinstance(season, list) or isinstance(episode, list):
            LOGGER.warning(f"Invalid format: Season/Episode is list {filename}, parsed: {parsed}")
            return None

        if season and not episode:
            LOGGER.warning(f"Missing episode for season: {filename}, parsed: {parsed}")
            return None

        # Enhanced default ID extraction with better logging
        LOGGER.info(f"Backend.USE_DEFAULT_ID: {Backend.USE_DEFAULT_ID}")
        
        default_id = None
        if Backend.USE_DEFAULT_ID:
            try:
                default_id = extract_tmdb_id(Backend.USE_DEFAULT_ID)
                LOGGER.info(f"Using default ID from /set command: {default_id}")
            except Exception as e:
                LOGGER.warning(f"Failed to extract default ID from USE_DEFAULT_ID '{Backend.USE_DEFAULT_ID}': {e}")
                # Fall back to filename extraction
                try:
                    default_id = extract_tmdb_id(filename)
                    LOGGER.info(f"Fallback: Extracted ID from filename: {default_id}")
                except Exception as e2:
                    LOGGER.debug(f"Failed to extract ID from filename {filename}: {e2}")
                    default_id = None
        else:
            # No default set, try to extract from filename
            try:
                default_id = extract_tmdb_id(filename)
                LOGGER.info(f"Extracted ID from filename: {default_id}")
            except Exception as e:
                LOGGER.debug(f"No ID found in filename {filename}: {e}")
                default_id = None

        LOGGER.info(f"Final ID being used: {default_id}")

        if title:
            if season and episode:
                LOGGER.info(f"Fetching TV metadata: {title} S{season}E{episode} (Group: {group_name}) with ID: {default_id}")
                return await fetch_tv_metadata(title, season, episode, year, quality, default_id, languages, rip, group_name)
            else:
                LOGGER.info(f"Fetching movie metadata: {title} ({year}) (Group: {group_name}) with ID: {default_id}")
                return await fetch_movie_metadata(title, year, quality, default_id, languages, rip, group_name)

        LOGGER.warning(f"No title parsed from: {filename} (parsed: {parsed})")
        return None

    except Exception as e:
        LOGGER.error(f"Unhandled error while parsing metadata for {filename}: {e}")
        LOGGER.debug(f"Full traceback: {traceback.format_exc()}")
        return None

async def fetch_anime_metadata(title: str, season_number: int, episode_number: int, episode_title: str = None, group_name: str = None, has_season: bool = False) -> dict:
    """
    Special handler for anime series - preserves season information for seasonal anime
    Enhanced to use AniList/Jikan as primary sources with IMDb/TMDB as fallback
    """
    try:
        # Try AniList first (best for anime)
        LOGGER.info(f"Trying AniList for anime: {title}")
        anilist_data = await fetch_anime_from_anilist(title)
        if anilist_data:
            formatted = format_anilist_to_standard(anilist_data, season_number, episode_number, episode_title, group_name, has_season)
            if formatted:
                group_info = f" by {group_name}" if group_name else ""
                LOGGER.info(f"✅ Anime metadata fetched from AniList for '{formatted['title']}' S{season_number}E{episode_number:03d}{group_info}")
                return formatted
        
        # Try Jikan (MyAnimeList) as second option
        LOGGER.info(f"Trying Jikan for anime: {title}")
        jikan_data = await fetch_anime_from_jikan(title)
        if jikan_data:
            formatted = format_jikan_to_standard(jikan_data, season_number, episode_number, episode_title, group_name, has_season)
            if formatted:
                group_info = f" by {group_name}" if group_name else ""
                LOGGER.info(f"✅ Anime metadata fetched from MyAnimeList for '{formatted['title']}' S{season_number}E{episode_number:03d}{group_info}")
                return formatted
        
        # Fall back to original IMDb/TMDB approach
        LOGGER.info(f"Falling back to IMDb/TMDB for anime: {title}")
        imdb_id = None
        
        # Check if we have a default ID set via /set command
        if Backend.USE_DEFAULT_ID:
            try:
                default_id = extract_tmdb_id(Backend.USE_DEFAULT_ID)
                if default_id and default_id.startswith("tt"):
                    imdb_id = default_id
                    LOGGER.info(f"Using default IMDb ID for anime: {imdb_id}")
            except Exception as e:
                LOGGER.warning(f"Failed to extract default ID for anime: {e}")
        
        # Search for the anime series only if no default ID
        if not imdb_id:
            result = await search_title(query=title, type="tvSeries")
            imdb_id = result['id'] if result else None
        
        tv_details, ep_details = None, None
        
        if imdb_id:
            try:
                await asyncio.sleep(DELAY)
                tv_details = await get_detail(imdb_id=imdb_id)
                LOGGER.info(f"Successfully fetched IMDb data for {title}")
            except Exception as e:
                LOGGER.warning(f"IMDb fetch failed for ID {imdb_id}: {e}")
        
        # Try TMDB as fallback
        if not tv_details:
            try:
                await asyncio.sleep(DELAY)
                tmdb_results = await tmdb.search().tv(query=title)
                if tmdb_results:
                    tmdb_tv_id = tmdb_results[0].id
                    tv_details = await tmdb.tv(tmdb_tv_id).details()
                    use_tmdb = True
                else:
                    LOGGER.warning(f"No results found for anime '{title}'")
                    return None
            except Exception as e:
                LOGGER.error(f"TMDB search failed for anime '{title}': {e}")
                return None
        else:
            use_tmdb = False
        
        # Generate URLs
        imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None
        tmdb_url = None
        
        if use_tmdb:
            tmdb_url = f"https://www.themoviedb.org/tv/{tv_details.id}"
            show_title = tv_details.name
            show_year = tv_details.first_air_date.year if tv_details.first_air_date else 0
            rate = tv_details.vote_average or 0
            description = tv_details.overview or ''
            total_seasons = tv_details.number_of_seasons or (season_number if has_season else 1)
            total_episodes = tv_details.number_of_episodes or episode_number
            poster = f"https://image.tmdb.org/t/p/w500{tv_details.poster_path}" if tv_details.poster_path else ''
            backdrop = f"https://image.tmdb.org/t/p/original{tv_details.backdrop_path}" if tv_details.backdrop_path else ''
            genres = [genre.name for genre in tv_details.genres] if tv_details.genres else []
            status = tv_details.status if hasattr(tv_details, 'status') else 'Ongoing'
            
            # Get English episode title using the enhanced function
            ep_title = await get_english_episode_title(tv_details.id, season_number, episode_number, imdb_id, show_title)
            
        else:
            # Using IMDb data
            show_title = tv_details.get('title', title)
            show_year = tv_details.get('releaseDetailed', {}).get('year', 0)
            rate = tv_details.get('rating', {}).get('star', 0)
            description = tv_details.get('plot', '')
            total_seasons = season_number if has_season else 1  # Use actual season if seasonal format
            total_episodes = episode_number  # Assume current episode is part of total
            poster = tv_details.get('image', '')
            backdrop = ''
            genres = tv_details.get('genre', [])
            status = 'Ongoing'
            
            # Try to get TMDB data for better images and episode title
            try:
                await asyncio.sleep(DELAY)
                tmdb_results = await tmdb.search().tv(query=show_title)
                if tmdb_results:
                    tmdb_tv_id = tmdb_results[0].id
                    tmdb_url = f"https://www.themoviedb.org/tv/{tmdb_tv_id}"
                    tmdb_details = await tmdb.tv(tmdb_tv_id).details()
                    backdrop = f"https://image.tmdb.org/t/p/original{tmdb_details.backdrop_path}" if tmdb_details.backdrop_path else ''
                    if not poster:
                        poster = f"https://image.tmdb.org/t/p/w500{tmdb_details.poster_path}" if tmdb_details.poster_path else ''
                    # Update total episodes and seasons from TMDB if available and larger
                    if tmdb_details.number_of_episodes and tmdb_details.number_of_episodes > episode_number:
                        total_episodes = tmdb_details.number_of_episodes
                    if tmdb_details.number_of_seasons and has_season:
                        total_seasons = max(total_seasons, tmdb_details.number_of_seasons)
                    if hasattr(tmdb_details, 'status'):
                        status = tmdb_details.status
                    
                    # Get English episode title using the enhanced function
                    ep_title = await get_english_episode_title(tmdb_tv_id, season_number, episode_number, imdb_id, show_title)
                else:
                    # Fallback to filename episode title or default
                    ep_title = episode_title or f"Episode {episode_number}"
            except Exception as e:
                LOGGER.debug(f"Failed to fetch supplemental TMDB data: {e}")
                ep_title = episode_title or f"Episode {episode_number}"
        
        # Get original language from metadata if not provided
        original_languages = ['ja', 'en']  # Default for anime (Japanese first, then English)
        if use_tmdb:
            # Get original language from TMDB
            if hasattr(tv_details, 'original_language') and tv_details.original_language:
                original_languages = [tv_details.original_language]
                # Add English as secondary if original is not English
                if tv_details.original_language != 'en':
                    original_languages.append('en')
        else:
            # For IMDb data, try to get language info
            if 'language' in tv_details and tv_details['language']:
                # IMDb language format might be different, handle common cases
                imdb_langs = tv_details['language']
                if isinstance(imdb_langs, list):
                    original_languages = [lang.lower()[:2] if len(lang) > 2 else lang.lower() for lang in imdb_langs[:2]]
                elif isinstance(imdb_langs, str):
                    # Convert common language names to ISO codes
                    lang_map = {
                        'japanese': 'ja', 'english': 'en', 'korean': 'ko', 
                        'chinese': 'zh', 'spanish': 'es', 'french': 'fr'
                    }
                    lang_lower = imdb_langs.lower()
                    mapped_lang = lang_map.get(lang_lower, lang_lower[:2])
                    original_languages = [mapped_lang]
                    if mapped_lang != 'en':
                        original_languages.append('en')
        
        compact_info = await get_tmdb_people_and_schedule(tv_details.id if use_tmdb else (tmdb_tv_id if 'tmdb_tv_id' in locals() else None), "tv")

        result = {
            "tmdb_id": tv_details.id if use_tmdb else tv_details['id'].replace("tt", ""),
            "title": show_title,
            "year": show_year,
            "rate": rate,
            "description": description,
            "total_seasons": total_seasons,
            "total_episodes": total_episodes,
            "poster": poster,
            "backdrop": backdrop,
            "status": status,
            "genres": genres,
            "media_type": "tv",
            "season_number": season_number,  # Use the actual parsed season number
            "episode_number": episode_number,  # Keep original episode number
            "episode_title": ep_title,  # Use the English episode title we fetched
            "episode_backdrop": backdrop,
            "quality": "1080p",
            "languages": original_languages,  # Use detected original languages
            "rip": 'Blu-ray',
            "source": "TMDb" if use_tmdb else "IMDb",
            "imdb_url": imdb_url,
            "tmdb_url": tmdb_url,
            "is_anime": True,  # Flag to identify anime series
            "group": group_name,  # Add group information
            "release_group": group_name,  # Alternative field name for compatibility
            "has_seasonal_format": has_season,  # Flag to indicate if this uses S##E## format
            "cast": compact_info.get("cast", []),
            "next_episode_to_air": compact_info.get("next_episode_to_air"),
            "last_episode_to_air": compact_info.get("last_episode_to_air"),
        }

        source = "TMDb" if use_tmdb else "IMDb"
        group_info = f" by {group_name}" if group_name else ""
        default_info = " [DEFAULT ID]" if Backend.USE_DEFAULT_ID else ""
        LOGGER.info(f"Anime metadata fetched from {source} for '{show_title}' S{season_number}E{episode_number:03d}{group_info}{default_info} - Episode: '{ep_title}'")
        
        return result

    except Exception as e:
        LOGGER.error(f"Error fetching anime metadata for '{title}' S{season_number}E{episode_number}: {e}")
        LOGGER.debug(f"Full traceback: {traceback.format_exc()}")
        return None

async def check_episode_exists(tv_id: int, season: int, episode: int) -> bool:
    """Check if a specific episode exists in TMDB"""
    try:
        await asyncio.sleep(DELAY)
        season_details = await tmdb.season(tv_id, season).details()
        
        if not season_details or not hasattr(season_details, 'episodes'):
            return False
            
        # Check if the episode number exists in this season
        episode_numbers = [ep.episode_number for ep in season_details.episodes if hasattr(ep, 'episode_number')]
        return episode in episode_numbers
        
    except Exception as e:
        LOGGER.debug(f"Error checking episode existence for TV ID {tv_id} S{season}E{episode}: {e}")
        return False

async def get_available_seasons(tv_id: int) -> list:
    """Get list of available season numbers"""
    try:
        await asyncio.sleep(DELAY)
        tv_details = await tmdb.tv(tv_id).details()
        
        if not tv_details or not hasattr(tv_details, 'seasons'):
            return []
            
        return [season.season_number for season in tv_details.seasons if hasattr(season, 'season_number')]
        
    except Exception as e:
        LOGGER.debug(f"Error getting available seasons for TV ID {tv_id}: {e}")
        return []

async def fetch_tv_metadata(title: str, season: int, episode: int, year=None, quality=None, default_id=None, languages=None, rip=None, group_name=None) -> dict:
    """
    Enhanced TV metadata fetcher with improved default ID handling and English episode titles
    """
    try:
        # Store original values to preserve them in the final result
        original_season = season
        original_episode = episode
        
        tv_details, ep_details, use_tmdb = None, None, False
        imdb_id = default_id if default_id and default_id.startswith("tt") else None
        tmdb_tv_id = None

        # Enhanced logging for default ID usage
        if Backend.USE_DEFAULT_ID:
            LOGGER.info(f"Using default ID from /set command: {default_id}")
        else:
            LOGGER.info(f"Using ID from filename/search: {default_id}")

        # Only search if no default ID is available
        if not imdb_id:
            try:
                result = await search_title(query=f"{title} {year}" if year else title, type="tvSeries")
                imdb_id = result['id'] if result else None
                LOGGER.info(f"IMDb search result for '{title}': {imdb_id}")
            except Exception as e:
                LOGGER.warning(f"IMDb search failed for '{title}': {e}")

        if imdb_id:
            try:
                await asyncio.sleep(DELAY)
                tv_details = await get_detail(imdb_id=imdb_id)
                LOGGER.info(f"Successfully fetched IMDb data for {title} S{season}E{episode} using ID: {imdb_id}")
            except Exception as e:
                LOGGER.warning(f"IMDb TV fetch failed for ID {imdb_id}: {e}")
                tv_details = None

        # Only use TMDB if we have no data from IMDb at all
        if not tv_details:
            use_tmdb = True
            await asyncio.sleep(DELAY)
            tmdb_results = await tmdb.search().tv(query=title)
            if not tmdb_results:
                LOGGER.warning(f"No TMDb results found for title '{title}'")
                return None
                
            tmdb_tv_id = tmdb_results[0].id
            LOGGER.debug(f"TMDb ID found: {tmdb_tv_id}")
            
            # Get TV show details first
            tv_details = await tmdb.tv(tmdb_tv_id).details()
            
            # Check if the requested season exists
            available_seasons = await get_available_seasons(tmdb_tv_id)
            if available_seasons and season not in available_seasons:
                LOGGER.warning(f"Season {season} not available for '{title}' in TMDB. Available seasons: {available_seasons}")
                # Don't modify season/episode if we have IMDb data - TMDB might just be outdated
                if not imdb_id:
                    season = max(available_seasons) if available_seasons else 1
                    LOGGER.info(f"No IMDb data available, using TMDB season {season} instead")
            
            # Only check episode existence if we're actually using TMDB data (no IMDb data)
            if not imdb_id:
                episode_exists = await check_episode_exists(tmdb_tv_id, season, episode)
                if not episode_exists:
                    LOGGER.warning(f"Episode {episode} not found in season {season} for '{title}' in TMDB")
                    try:
                        await asyncio.sleep(DELAY)
                        season_details = await tmdb.season(tmdb_tv_id, season).details()
                        if season_details and hasattr(season_details, 'episodes') and season_details.episodes:
                            last_episode = max(ep.episode_number for ep in season_details.episodes if hasattr(ep, 'episode_number'))
                            episode = min(episode, last_episode)
                            LOGGER.info(f"Using episode {episode} instead (last available: {last_episode})")
                        else:
                            episode = 1
                            LOGGER.info(f"Falling back to episode 1")
                    except Exception as e:
                        LOGGER.warning(f"Error getting season details, using episode 1: {e}")
                        episode = 1
        
        elif not imdb_id:
            # We have TV details from IMDb but no episode details, try TMDB for episode info only
            try:
                await asyncio.sleep(DELAY)
                tmdb_results = await tmdb.search().tv(query=title)
                if tmdb_results:
                    tmdb_tv_id = tmdb_results[0].id
                    LOGGER.info(f"Got TMDB ID for supplemental episode data: {tmdb_tv_id}")
            except Exception as e:
                LOGGER.warning(f"Could not get TMDB ID for episode details: {e}")

        # Generate URLs
        imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None
        tmdb_url = f"https://www.themoviedb.org/tv/{tmdb_tv_id or (tv_details.id if use_tmdb else None)}" if (use_tmdb and tv_details) or tmdb_tv_id else None

        if use_tmdb:
            # Using TMDB data for both show and episode
            tmdb_id = tv_details.id
            show_title = tv_details.name
            show_year = tv_details.first_air_date.year if tv_details.first_air_date else 0
            rate = tv_details.vote_average or 0
            description = tv_details.overview or ''
            total_seasons = tv_details.number_of_seasons or 0
            total_episodes = tv_details.number_of_episodes or 0
            poster = f"https://image.tmdb.org/t/p/w500{tv_details.poster_path}" if tv_details.poster_path else ''
            backdrop = f"https://image.tmdb.org/t/p/original{tv_details.backdrop_path}" if tv_details.backdrop_path else ''
            status = tv_details.status if hasattr(tv_details, 'status') else 'Unknown'
            genres = [genre.name for genre in tv_details.genres] if tv_details.genres else []
            
            # Get English episode title using the enhanced function
            ep_title = await get_english_episode_title(tv_details.id, season, episode, imdb_id, show_title)
            ep_backdrop = backdrop  # Use show backdrop as fallback
            
            # Try to get episode-specific backdrop
            try:
                await asyncio.sleep(DELAY)
                ep_details = await tmdb.episode(tv_details.id, season, episode).details()
                if ep_details and hasattr(ep_details, 'still_path') and ep_details.still_path:
                    ep_backdrop = f"https://image.tmdb.org/t/p/original{ep_details.still_path}"
            except Exception as e:
                LOGGER.debug(f"Could not fetch episode backdrop: {e}")
                
        else:
            # Using IMDb data for show info
            tmdb_id = tv_details['id'].replace("tt", "")
            show_title = tv_details.get('title', title)
            show_year = tv_details.get('releaseDetailed', {}).get('year', 0)
            rate = tv_details.get('rating', {}).get('star', 0)
            description = tv_details.get('plot', '')
            total_seasons = len(tv_details.get('all_seasons', []))
            total_episodes = sum(len(season.get('episodes', [])) for season in tv_details.get('seasons', []))
            poster = tv_details.get('image', '')
            backdrop = ''
            genres = tv_details.get('genre', [])
            
            # Get English episode title - prioritize enhanced function over IMDb data
            if tmdb_tv_id:
                ep_title = await get_english_episode_title(tmdb_tv_id, season, episode, imdb_id, show_title)
            else:
                # Try to get episode title from IMDb
                try:
                    await asyncio.sleep(DELAY)
                    ep_details = await get_season(imdb_id=imdb_id, season_id=season, episode_id=episode)
                    if ep_details and isinstance(ep_details, dict):
                        ep_title = ep_details.get('title', f"Episode {episode}")
                    else:
                        ep_title = f"Episode {episode}"
                except Exception as e:
                    LOGGER.debug(f"Could not fetch IMDb episode title: {e}")
                    ep_title = f"Episode {episode}"
            
            ep_backdrop = ''
            
            # Try to get backdrop and episode backdrop from TMDB as fallback for IMDb data
            try:
                await asyncio.sleep(DELAY)
                if not tmdb_tv_id:
                    fallback_results = await tmdb.search().tv(query=show_title)
                    if fallback_results:
                        tmdb_tv_id = fallback_results[0].id
                        tmdb_url = f"https://www.themoviedb.org/tv/{tmdb_tv_id}"
                
                if tmdb_tv_id:
                    fallback_detail = await tmdb.tv(tmdb_tv_id).details()
                    backdrop = f"https://image.tmdb.org/t/p/original{fallback_detail.backdrop_path}" if fallback_detail.backdrop_path else ''
                    status = fallback_detail.status if hasattr(fallback_detail, 'status') else 'Unknown'
                    
                    # Try to get episode backdrop
                    try:
                        ep_details = await tmdb.episode(tmdb_tv_id, season, episode).details()
                        if ep_details and hasattr(ep_details, 'still_path') and ep_details.still_path:
                            ep_backdrop = f"https://image.tmdb.org/t/p/original{ep_details.still_path}"
                    except Exception as e:
                        LOGGER.debug(f"Could not fetch episode backdrop from TMDB: {e}")
                        
                else:
                    status = 'Unknown'
            except Exception as e:
                LOGGER.warning(f"Fallback TMDb metadata fetch failed: {e}")
                status = 'Unknown'

        # Get original language from metadata if not provided from filename
        final_languages = languages or ['en']  # Default fallback
        
        if not languages:  # Only if no language was detected from filename
            if use_tmdb:
                # Get original language from TMDB
                if hasattr(tv_details, 'original_language') and tv_details.original_language:
                    final_languages = [tv_details.original_language]
                    # Add English as secondary if original is not English
                    if tv_details.original_language != 'en':
                        final_languages.append('en')
            else:
                # For IMDb data, try to get language info
                if 'language' in tv_details and tv_details['language']:
                    imdb_langs = tv_details['language']
                    if isinstance(imdb_langs, list):
                        final_languages = [lang.lower()[:2] if len(lang) > 2 else lang.lower() for lang in imdb_langs[:2]]
                    elif isinstance(imdb_langs, str):
                        # Convert common language names to ISO codes
                        lang_map = {
                            'japanese': 'ja', 'english': 'en', 'korean': 'ko', 
                            'chinese': 'zh', 'spanish': 'es', 'french': 'fr',
                            'german': 'de', 'italian': 'it', 'portuguese': 'pt'
                        }
                        lang_lower = imdb_langs.lower()
                        final_languages = [lang_map.get(lang_lower, lang_lower[:2])]
                        if final_languages[0] != 'en':
                            final_languages.append('en')

        compact_info = await get_tmdb_people_and_schedule(tmdb_id, "tv")

        result = {
            "tmdb_id": tmdb_id,
            "title": show_title,
            "year": show_year,
            "rate": rate,
            "description": description,
            "total_seasons": total_seasons,
            "total_episodes": total_episodes,
            "poster": poster,
            "backdrop": backdrop,
            "status": status,
            "genres": genres,
            "media_type": "tv",
            "season_number": original_season,  # Use original requested season
            "episode_number": original_episode,  # Use original requested episode
            "episode_title": ep_title,  # Use the English episode title we fetched
            "episode_backdrop": ep_backdrop,
            "quality": quality or "1080p",  # Default to 1080p if None
            "languages": final_languages,  # Use detected or original languages
            "rip": rip or 'Blu-ray',
            "source": "TMDb" if use_tmdb else "IMDb",
            "imdb_url": imdb_url,
            "tmdb_url": tmdb_url,
            "group": group_name,  # Add group information
            "release_group": group_name,  # Alternative field name for compatibility
            "using_default_id": bool(Backend.USE_DEFAULT_ID),  # Flag to indicate if default ID was used
            "cast": compact_info.get("cast", []),
            "next_episode_to_air": compact_info.get("next_episode_to_air"),
            "last_episode_to_air": compact_info.get("last_episode_to_air"),
        }

        source = "TMDb" if use_tmdb else "IMDb"
        group_info = f" by {group_name}" if group_name else ""
        default_info = " [DEFAULT ID]" if Backend.USE_DEFAULT_ID else ""
        LOGGER.info(f"TV metadata fetched from {source} for '{show_title}' S{original_season}E{original_episode}{group_info}{default_info} - Episode: '{ep_title}'")

        return result

    except Exception as e:
        LOGGER.error(f"Error fetching TV metadata for '{title}' S{season}E{episode}: {e}")
        LOGGER.debug(f"Full traceback: {traceback.format_exc()}")
        return None

async def fetch_movie_metadata(title: str, year=None, quality=None, default_id=None, languages=None, rip=None, group_name=None) -> dict:
    """
    Enhanced movie metadata fetcher with improved default ID handling
    """
    try:
        movie_details, use_tmdb = None, False
        imdb_id = default_id if default_id and default_id.startswith("tt") else None
        tmdb_movie_id = None

        # Enhanced logging for default ID usage
        if Backend.USE_DEFAULT_ID:
            LOGGER.info(f"Using default ID from /set command: {default_id}")
        else:
            LOGGER.info(f"Using ID from filename/search: {default_id}")

        # Only search if no default ID is available
        if not imdb_id:
            try:
                result = await search_title(query=f"{title} {year}" if year else title, type="movie")
                imdb_id = result['id'] if result else None
                LOGGER.info(f"IMDb search result for '{title}': {imdb_id}")
                
            except Exception as e:
                LOGGER.warning(f"IMDb search failed for '{title}': {e}")
                imdb_id = None

        if imdb_id:
            try:
                # Do NOT remove 'tt' prefix; send full imdb_id
                LOGGER.debug(f"Fetching IMDb details using ID: {imdb_id}")
                await asyncio.sleep(DELAY)
                movie_details = await get_detail(imdb_id=imdb_id)
                LOGGER.info(f"Successfully fetched IMDb data for '{title}' using ID: {imdb_id}")
                
            except Exception as e:
                LOGGER.warning(f"IMDb movie fetch failed for '{title}': {e}")
                movie_details = None

        if not movie_details:
            use_tmdb = True
            try:
                await asyncio.sleep(DELAY)
                tmdb_results = await tmdb.search().movies(query=title, year=year) if year else await tmdb.search().movies(query=title)
                if not tmdb_results:
                    LOGGER.warning(f"No TMDB results found for '{title}'")
                    return None
                tmdb_movie_id = tmdb_results[0].id
                movie_details = await tmdb.movie(tmdb_movie_id).details()
            except Exception as e:
                LOGGER.error(f"TMDB search failed for '{title}': {e}")
                return None

        # Generate URLs
        imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None
        tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_movie_id or (movie_details.id if use_tmdb else None)}" if (use_tmdb and movie_details) or tmdb_movie_id else None

        if use_tmdb:
            tmdb_id = movie_details.id
            movie_title = movie_details.title
            movie_year = movie_details.release_date.year if movie_details.release_date else 0
            rate = movie_details.vote_average or 0
            description = movie_details.overview or ''
            poster = f"https://image.tmdb.org/t/p/w500{movie_details.poster_path}" if movie_details.poster_path else ''
            backdrop = f"https://image.tmdb.org/t/p/original{movie_details.backdrop_path}" if movie_details.backdrop_path else ''
            runtime = movie_details.runtime or 0
            genres = [genre.name for genre in movie_details.genres] if movie_details.genres else []
        else:
            description = movie_details.get('plot', '')
            tmdb_id = movie_details['id'].replace("tt", "")
            movie_title = movie_details.get('title', title)
            movie_year = movie_details.get('releaseDetailed', {}).get('year', 0)
            rate = movie_details.get('rating', {}).get('star', 0)
            runtime = movie_details.get('runtimeSeconds', 0) // 60
            genres = movie_details.get('genre', [])
            
            # Try to get supplemental TMDB data for better images
            try:
                await asyncio.sleep(DELAY)
                force_tmdb_results = await tmdb.search().movies(query=movie_title, year=movie_year)
                if force_tmdb_results:
                    force_movie_id = force_tmdb_results[0].id
                    if not tmdb_movie_id:  # Only set if we don't have it already
                        tmdb_movie_id = force_movie_id
                        tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_movie_id}"
                    force_movie_details = await tmdb.movie(force_movie_id).details()
                    backdrop = f"https://image.tmdb.org/t/p/original{force_movie_details.backdrop_path}" if force_movie_details.backdrop_path else ''
                    poster = movie_details.get('image', '') or \
                             (f"https://image.tmdb.org/t/p/w500{force_movie_details.poster_path}" if force_movie_details.poster_path else '')
            except Exception as e:
                LOGGER.debug(f"Failed to fetch supplemental TMDB data for movie: {e}")
                backdrop = ''
                poster = movie_details.get('image', '')

        # Get original language from metadata if not provided from filename
        final_languages = languages or ['en']  # Default fallback
        
        if not languages:  # Only if no language was detected from filename
            if use_tmdb:
                # Get original language from TMDB
                if hasattr(movie_details, 'original_language') and movie_details.original_language:
                    final_languages = [movie_details.original_language]
                    # Add English as secondary if original is not English
                    if movie_details.original_language != 'en':
                        final_languages.append('en')
            else:
                # For IMDb data, try to get language info
                if 'language' in movie_details and movie_details['language']:
                    imdb_langs = movie_details['language']
                    if isinstance(imdb_langs, list):
                        final_languages = [lang.lower()[:2] if len(lang) > 2 else lang.lower() for lang in imdb_langs[:2]]
                    elif isinstance(imdb_langs, str):
                        # Convert common language names to ISO codes
                        lang_map = {
                            'japanese': 'ja', 'english': 'en', 'korean': 'ko', 
                            'chinese': 'zh', 'spanish': 'es', 'french': 'fr',
                            'german': 'de', 'italian': 'it', 'portuguese': 'pt'
                        }
                        lang_lower = imdb_langs.lower()
                        final_languages = [lang_map.get(lang_lower, lang_lower[:2])]
                        if final_languages[0] != 'en':
                            final_languages.append('en')

        source = "TMDb" if use_tmdb else "IMDb"
        group_info = f" by {group_name}" if group_name else ""
        default_info = " [DEFAULT ID]" if Backend.USE_DEFAULT_ID else ""
        LOGGER.info(f"Movie metadata fetched from {source} for '{movie_title}' ({movie_year}){group_info}{default_info}")

        movie_compact_info = await get_tmdb_people_and_schedule(tmdb_id, "movie")

        return {
            "tmdb_id": tmdb_id,
            "title": movie_title,
            "year": movie_year,
            "rate": rate,
            "description": description,
            "poster": poster,
            "backdrop": backdrop,
            "media_type": "movie",
            "genres": genres,
            "runtime": runtime,
            "quality": quality or "1080p",  # Default to 1080p if None
            "languages": final_languages,  # Use detected or original languages
            "rip": rip or 'Blu-ray',
            "source": "TMDb" if use_tmdb else "IMDb",
            "imdb_url": imdb_url,
            "tmdb_url": tmdb_url,
            "group": group_name,  # Add group information
            "release_group": group_name,  # Alternative field name for compatibility
            "using_default_id": bool(Backend.USE_DEFAULT_ID),  # Flag to indicate if default ID was used
            "cast": movie_compact_info.get("cast", []),
        }

    except Exception as e:
        LOGGER.error(f"Unhandled error in fetch_movie_metadata for '{title}': {e}")
        LOGGER.debug(f"Full traceback: {traceback.format_exc()}")
        return None