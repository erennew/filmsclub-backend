import asyncio
import PTN
from Backend.helper.imdb import get_detail, get_season, search_title
from Backend.helper.pyro import extract_tmdb_id, normalize_languages, normalize_filename
import re
from themoviedb import aioTMDb
from Backend.config import Telegram
import Backend
from Backend.logger import LOGGER
import traceback


DELAY = 2

tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")

async def metadata(filename: str, media) -> dict:
    try:
        # Normalize filename first
        filename = normalize_filename(filename)
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

        if isinstance(season, list) or isinstance(episode, list):
            LOGGER.warning(f"Invalid format: Season/Episode is list — {filename}, parsed: {parsed}")
            return None

        if season and not episode:
            LOGGER.warning(f"Missing episode for season: {filename}, parsed: {parsed}")
            return None

        # Fallback manual title extraction if PTN couldn't find one
        if not title:
            title = _extract_title_fallback(filename)
            LOGGER.info(f"Fallback title extraction for {filename}: {title}")

        try:
            default_id = extract_tmdb_id(Backend.USE_DEFAULT_ID)
        except Exception as e:
            LOGGER.debug(f"Failed to extract default TMDB ID from USE_DEFAULT_ID: {e}")
            default_id = None

        if not default_id:
            try:
                default_id = extract_tmdb_id(filename)
            except Exception as e:
                LOGGER.debug(f"Failed to extract TMDB ID from filename {filename}: {e}")
                default_id = None

        if title:
            if season and episode:
                LOGGER.info(f"Fetching TV metadata for: {title} S{season}E{episode}")
                return await fetch_tv_metadata(title, season, episode, year, quality, default_id, languages, rip)
            else:
                LOGGER.info(f"Fetching movie metadata for: {title} ({year})")
                return await fetch_movie_metadata(title, year, quality, default_id, languages, rip)

        LOGGER.info(f"No title parsed from: {filename} (parsed: {parsed})")
        return None

    except Exception as e:
        LOGGER.error(f"Unhandled error while parsing metadata for {filename}: {e}")
        return None


def _extract_title_fallback(filename: str) -> str:
    """Manual title extraction when PTN fails."""
    # Remove extension
    name = re.sub(r'\.\w{2,4}$', '', filename)
    # Remove year
    name = re.sub(r'\(\d{4}\)', '', name)
    # Remove season/episode markers
    name = re.sub(r'[Ss]\d{1,3}[Ee]\d{1,4}', '', name)
    name = re.sub(r'[Ss]eason\s*\d+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[Ee]pisode\s*\d+', '', name, flags=re.IGNORECASE)
    # Remove resolution / quality tokens
    name = re.sub(r'\d{3,4}p', '', name)
    name = re.sub(r'(BluRay|HDRip|WEB[-]?DL|HDTV|DVDRip|BRRip|HDTC|CAM|TS|DVDScr|SCR|R5|REMASTERED)', '', name, flags=re.IGNORECASE)
    name = re.sub(r'(x264|x265|H\.?264|HEVC|AVC|10bit|8bit)', '', name, flags=re.IGNORECASE)
    # Remove language tags
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    # Remove common junk
    name = re.sub(r'(?i)(HQ|AMZN|NF|DDP?|AAC|5\.1|2\.0|7\.1|Org|Original|Multi|Telugu|Hindi|Tamil|Malayalam|Kannada|English)', '', name)
    # Clean up
    name = re.sub(r'[\-_]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name if name else None




async def fetch_tv_metadata(title: str, season: int, episode: int, year=None, quality=None, default_id=None, languages=None, rip=None) -> dict:
    try:
        tv_details, ep_details, use_tmdb = None, None, False
        imdb_id = default_id if default_id and default_id.startswith("tt") else None

        if not imdb_id:
            result = await search_title(query=f"{title} {year}" if year else title, type="tvSeries")
            imdb_id = result['id'] if result else None

        if imdb_id:
            try:
                await asyncio.sleep(DELAY)
                tv_details = await get_detail(imdb_id=imdb_id)
                await asyncio.sleep(DELAY)
                ep_details = await get_season(imdb_id=imdb_id, season_id=season, episode_id=episode)
            except Exception as e:
                LOGGER.warning(f"IMDb TV fetch failed for ID {imdb_id}: {e}")
                tv_details, ep_details = None, None

        if not tv_details or not ep_details:
            use_tmdb = True
            await asyncio.sleep(DELAY)
            tmdb_results = await tmdb.search().tv(query=title)
            if not tmdb_results:
                LOGGER.warning(f"No TMDb results found for title '{title}'")
                return None
            tv_id = tmdb_results[0].id
            LOGGER.debug(f"TMDb ID found: {tv_id}")
            tv_details = await tmdb.tv(tv_id).details()
            ep_details = await tmdb.episode(tv_id, season, episode).details()

        if use_tmdb:
            tmdb_id = tv_details.id
            show_title = tv_details.name
            show_year = tv_details.first_air_date.year if tv_details.first_air_date else 0
            rate = tv_details.vote_average or 0
            description = tv_details.overview or ''
            total_seasons = tv_details.number_of_seasons or 0
            total_episodes = tv_details.number_of_episodes or 0
            poster = f"https://image.tmdb.org/t/p/w500{tv_details.poster_path}" if tv_details.poster_path else ''
            backdrop = f"https://image.tmdb.org/t/p/original{tv_details.backdrop_path}" if tv_details.backdrop_path else ''
            status = tv_details.status or 'Unknown'
            genres = [genre.name for genre in tv_details.genres] if tv_details.genres else []
            ep_title = ep_details.name if ep_details and hasattr(ep_details, 'name') else f"S{season}E{episode}"
            ep_backdrop = f"https://image.tmdb.org/t/p/original{ep_details.still_path}" if ep_details and ep_details.still_path else ''
        else:
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
            ep_title = ep_details.get('title', f"S{season}E{episode}") if ep_details else f"S{season}E{episode}"
            ep_backdrop = ep_details.get('image', '') if ep_details else ''
            try:
                await asyncio.sleep(DELAY)
                fallback_results = await tmdb.search().tv(query=show_title)
                if fallback_results:
                    fallback_id = fallback_results[0].id
                    fallback_detail = await tmdb.tv(fallback_id).details()
                    backdrop = f"https://image.tmdb.org/t/p/original{fallback_detail.backdrop_path}" if fallback_detail.backdrop_path else ''
                    status = fallback_detail.status or 'Unknown'
                else:
                    status = 'Unknown'
            except Exception as e:
                LOGGER.warning(f"Fallback TMDb metadata fetch failed: {e}")
                status = 'Unknown'

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
            "season_number": season,
            "episode_number": episode,
            "episode_title": ep_title,
            "episode_backdrop": ep_backdrop,
            "quality": quality,
            "languages": languages or ['hi'],
            "rip": rip or 'Blu-ray'
        }

        LOGGER.info(f"Metadata successfully fetched for {show_title} S{season}E{episode}")
        return result

    except Exception as e:
        LOGGER.error(f"Error fetching TV metadata for '{title}' S{season}E{episode}: {e}", exc_info=True)
        return None




async def fetch_movie_metadata(title: str, year=None, quality=None, default_id=None, languages=None, rip=None) -> dict:
    try:
        movie_details, use_tmdb = None, False
        imdb_id = default_id if default_id and default_id.startswith("tt") else None

        if not imdb_id:
            try:
                result = await search_title(query=f"{title} {year}" if year else title, type="movie")
                imdb_id = result['id'] if result else None
                
            except Exception as e:
                LOGGER.warning(f"IMDb search failed for '{title}': {e}")
                imdb_id = None

        if imdb_id:
            try:
                clean_id = imdb_id[2:] if imdb_id.startswith("tt") else imdb_id
                LOGGER.debug(f"Fetching IMDb details using ID: {clean_id}")
                movie_details = await get_detail(imdb_id=clean_id)
               
            except Exception as e:
                LOGGER.warning(f"IMDb movie fetch failed for '{title}': {e}")
                movie_details = None

        if not movie_details:
            use_tmdb = True
            try:
                tmdb_results = await tmdb.search().movies(query=title, year=year) if year else await tmdb.search().movies(query=title)
                if not tmdb_results:
                    LOGGER.warning(f"No TMDB results found for '{title}'")
                    return None
                movie_id = tmdb_results[0].id
                movie_details = await tmdb.movie(movie_id).details()
            except Exception as e:
                LOGGER.error(f"TMDB search failed for '{title}': {e}")
                return None

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
            try:
                force_tmdb_results = await tmdb.search().movies(query=movie_title, year=movie_year)
                force_movie_id = force_tmdb_results[0].id
                force_movie_details = await tmdb.movie(force_movie_id).details()
                backdrop = f"https://image.tmdb.org/t/p/original{force_movie_details.backdrop_path}" if force_movie_details.backdrop_path else ''
                poster = movie_details.get('image', '') or \
                         (f"https://image.tmdb.org/t/p/w500{force_movie_details.poster_path}" if force_movie_details.poster_path else '')
            except Exception as e:
                backdrop = ''
                poster = ''

        LOGGER.info(f"Metadata fetched successfully for '{movie_title}' ({movie_year})")
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
            "quality": quality,
            "languages": languages or ['hi'],
            "rip": rip or 'Blu-ray'
        }

    except Exception as e:
        LOGGER.error(f"Unhandled error in fetch_movie_metadata for '{title}': {e}")
        return None

        
