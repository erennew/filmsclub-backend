import re
import os
import pycountry
from typing import Optional, List, Dict, Tuple
from pyrogram.file_id import FileId
from Backend.logger import LOGGER
from Backend import __version__, now, timezone
from Backend.config import Telegram
from Backend.helper.exceptions import FIleNotFound
from asyncio import create_subprocess_exec, create_subprocess_shell
from aiofiles import open as aiopen
from aiofiles.os import path as aiopath, remove as aioremove
from asyncio.subprocess import PIPE
from pyrogram import Client
from Backend.pyrofork import StreamBot

from pyrogram import enums


def is_media(message):
    return next((getattr(message, attr) for attr in ["document", "photo", "video", "audio", "voice", "video_note", "sticker", "animation"] if getattr(message, attr)), None)


async def get_file_ids(client: Client, chat_id: int, message_id: int) -> Optional[FileId]:
    message = await client.get_messages(chat_id, message_id)
    if message.empty:
        raise FIleNotFound
    file_id = file_unique_id = None
    if media := is_media(message):
        file_id, file_unique_id = FileId.decode(
            media.file_id), media.file_unique_id
    setattr(file_id, 'file_name', getattr(media, 'file_name', ''))
    setattr(file_id, 'file_size', getattr(media, 'file_size', 0))
    setattr(file_id, 'mime_type', getattr(media, 'mime_type', ''))
    setattr(file_id, 'unique_id', file_unique_id)
    return file_id


def get_readable_file_size(size_in_bytes):
    size_in_bytes = int(size_in_bytes) if str(size_in_bytes).isdigit() else 0
    if not size_in_bytes:
        return '0B'
    index, SIZE_UNITS = 0, ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1
    return f'{size_in_bytes:.2f}{SIZE_UNITS[index]}' if index > 0 else f'{size_in_bytes:.2f}B'


def extract_languages_from_filename(filename: str) -> List[str]:
    """
    Extract language codes from filename patterns like [Tel + Tam + Mal + Kan]
    Returns list of ISO 639-1 language codes.
    """
    language_map = {
        # Indian languages
        'tel': 'te', 'telugu': 'te',
        'tam': 'ta', 'tamil': 'ta',
        'mal': 'ml', 'malayalam': 'ml',
        'kan': 'kn', 'kannada': 'kn',
        'hin': 'hi', 'hindi': 'hi',
        'ben': 'bn', 'bengali': 'bn',
        'mar': 'mr', 'marathi': 'mr',
        'guj': 'gu', 'gujarati': 'gu',
        'pun': 'pa', 'punjabi': 'pa',
        'urd': 'ur', 'urdu': 'ur',
        
        # International
        'eng': 'en', 'english': 'en',
        'spa': 'es', 'spanish': 'es',
        'fre': 'fr', 'french': 'fr',
        'ger': 'de', 'german': 'de',
        'ita': 'it', 'italian': 'it',
        'jpn': 'ja', 'japanese': 'ja',
        'kor': 'ko', 'korean': 'ko',
        'chi': 'zh', 'chinese': 'zh',
        'rus': 'ru', 'russian': 'ru',
        'ara': 'ar', 'arabic': 'ar',
    }
    
    # Pattern 1: [Tel + Tam + Mal + Kan] or [Tel+Tam+Mal+Kan]
    pattern1 = r'\[([^\]]+)\]'
    match = re.search(pattern1, filename, re.IGNORECASE)
    
    if match:
        content = match.group(1)
        # Split by + or | or , or space
        parts = re.split(r'[\+\|\s,]+', content)
        languages = []
        for part in parts:
            part_clean = part.strip().lower()
            if part_clean in language_map:
                languages.append(language_map[part_clean])
            elif len(part_clean) <= 3 and part_clean.isalpha():
                # Try to find by 3-letter code
                for key, value in language_map.items():
                    if key.startswith(part_clean):
                        languages.append(value)
                        break
        if languages:
            return list(dict.fromkeys(languages))  # Remove duplicates
    
    # Pattern 2: (Telugu + Tamil) or (Hindi, English)
    pattern2 = r'\(([^\)]+)\)'
    match = re.search(pattern2, filename, re.IGNORECASE)
    if match:
        content = match.group(1)
        if '+' in content or ',' in content:
            parts = re.split(r'[\+\|\s,]+', content)
            languages = []
            for part in parts:
                part_clean = part.strip().lower()
                if part_clean in language_map:
                    languages.append(language_map[part_clean])
            if languages:
                return list(dict.fromkeys(languages))
    
    return []


def clean_filename(filename: str) -> Tuple[str, List[str]]:
    """
    Enhanced filename cleaning that also extracts languages.
    Returns (cleaned_filename, detected_languages)
    """
    original = filename
    
    # Extract languages first
    detected_languages = extract_languages_from_filename(original)
    
    # ========== 1. REMOVE CHANNEL PROMOTIONS ==========
    # Handle [@ChannelName] - format
    cleaned = re.sub(
        r'^\[@[A-Za-z0-9_]+\]\s*[-–—]?\s*',
        '',
        filename
    )
    
    # Handle @ChannelName - format
    cleaned = re.sub(
        r'^@[A-Za-z0-9_]+\s*[-–—]?\s*',
        '',
        cleaned
    )
    
    # Handle • @ChannelName • - format
    cleaned = re.sub(
        r'^[•·]\s*@[A-Za-z0-9_]+\s*[•·]\s*[-–—]?\s*',
        '',
        cleaned
    )
    
    # Handle [@ChannelName] - format (with space after bracket)
    cleaned = re.sub(
        r'^\[@[A-Za-z0-9_]+\]\s*-\s*',
        '',
        cleaned
    )
    
    # 2. Strip channel branding prefixes at start
    cleaned = re.sub(
        r'^[•·\s]*[\[\(]?\s*@[A-Za-z0-9_]+\s*[\]\)]?\s*[•·]?\s*[-–—]?\s*',
        ' ',
        cleaned
    )
    
    # 3. Remove Telegram usernames with underscores, dashes, brackets, spaces
    pattern = r'[\s\[\]_-]*@[A-Za-z0-9_]+[\s\[\]_-]*|[\s\[\]_-]*@[A-Za-z0-9-]+[\s\[\]_-]*'
    cleaned = re.sub(pattern, ' ', cleaned)
    
    # 4. Remove common uploader prefixes
    cleaned = re.sub(r'^[A-Za-z0-9_]+\s*-\s*', ' ', cleaned)
    
    # 5. Remove technical tags
    cleaned = re.sub(r'(?<=\W)(org|AMZN|DDP|DD|NF|AAC|TVDL|WEB-DL|WEBRip|BluRay|HDTV|5\.1|2\.1|2\.0|7\.0|7\.1|5\.0|~|\b\w+kbps\b|x265|x264|HEVC|H\.?264|H\.?265)(?=\W)', '', cleaned, flags=re.IGNORECASE)
    
    # 6. Remove language bracket patterns (preserve the content but remove brackets)
    # Remove [Tel + Tam + Mal + Kan] but keep the text
    cleaned = re.sub(r'\[[^\]]*(?:Tel|Tam|Mal|Kan|Hin|Eng|Tamil|Telugu|Malayalam|Kannada|Hindi|English)[^\]]*\]', '', cleaned, flags=re.IGNORECASE)
    
    # 7. Remove ESubs, Subs tags
    cleaned = re.sub(r'\b(ESubs?|Subs?|HC|Subbed|Dubbed)\b', '', cleaned, flags=re.IGNORECASE)
    
    # 8. Remove newlines/tabs
    cleaned = re.sub(r'\\[ntr]', ' ', cleaned)
    
    # 9. Replace dots and underscores with spaces (preserve extension)
    ext_match = re.search(r'\.[a-z0-9]{3,4}$', cleaned, re.IGNORECASE)
    if ext_match:
        ext = ext_match.group()
        base = cleaned[: -len(ext)]
        base = re.sub(r'[._]', ' ', base, flags=re.IGNORECASE)
        base = re.sub(r'\s+', ' ', base)
        cleaned = base.strip() + ext
    else:
        cleaned = re.sub(r'[._]', ' ', cleaned, flags=re.IGNORECASE)
    
    # 10. Clean up multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # 11. Remove any remaining non-printable or special characters
    cleaned = re.sub(r'[^\w\s\-\(\)\[\]\.]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned, detected_languages


def normalize_filename(filename: str) -> str:
    """Normalize filename before PTN.parse to handle edge cases."""
    # 1. Fix malformed extensions (missing closing bracket/paren before ext)
    ext = os.path.splitext(filename)[1]
    if ext:
        base = filename[: -len(ext)]
        # Close unbalanced brackets/parens before extension
        open_brackets = base.count('[') - base.count(']')
        open_parens = base.count('(') - base.count(')')
        for _ in range(open_brackets):
            base += ']'
        for _ in range(open_parens):
            base += ')'
        filename = base + ext

    # 2. Normalize "S01 - EP07" / "S01 - E07" / "S01 EP07" -> "S01E07"
    filename = re.sub(r'S(\d{1,3})\s*[-_]?\s*EP?(\d{1,4})\b', r'S\1E\2', filename, flags=re.IGNORECASE)

    # 3. Remove trailing dash/space before extension
    filename = re.sub(r'[\s\-]+(?=\.(mkv|mp4|avi|mov)$)', '', filename, flags=re.IGNORECASE)

    # 4. Remove leading dashes/spaces
    filename = re.sub(r'^[\s\-]+', '', filename)

    # 5. Fix double dots and spaces around dots
    filename = re.sub(r'\s+\.', '.', filename)
    filename = re.sub(r'\.\s+', '.', filename)
    filename = re.sub(r'\.+', '.', filename)

    return filename.strip()


def get_readable_time(seconds: int) -> str:
    count = 0
    readable_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", " days"]
    while count < 4:
        count += 1
        if count < 3:
            remainder, result = divmod(seconds, 60)
        else:
            remainder, result = divmod(seconds, 24)
        if seconds == 0 and remainder == 0:
            break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4:
        readable_time += time_list.pop() + ", "
    time_list.reverse()
    readable_time += ": ".join(time_list)
    return readable_time


def extract_tmdb_id(url):
    # Match TMDB URLs
    tmdb_match = re.search(r'/(movie|tv)/(\d+)', url)
    if tmdb_match:
        return tmdb_match.group(2)

    # Match IMDb URLs
    imdb_match = re.search(r'/title/(tt\d+)', url)
    if imdb_match:
        return imdb_match.group(1)

    return None


def remove_urls(text):
    url_pattern = r'\b(?:https?|ftp):\/\/[^\s/$.?#].[^\s]*'
    text_without_urls = re.sub(url_pattern, '', text)
    cleaned_text = re.sub(r'\s+', ' ', text_without_urls).strip()
    return cleaned_text


def normalize_languages(language):
    """
    Normalize the language input(s) to a list of ISO 639-1 codes using pycountry.
    """
    if not language:
        return []

    if isinstance(language, str):
        language = [language]

    normalized_languages = []
    for lang in language:
        try:
            lang_code = pycountry.languages.get(name=lang).alpha_2
            if lang_code:
                normalized_languages.append(lang_code)
        except AttributeError:
            print(f"Language '{lang}' not found or does not have an ISO 639-1 code.")

    return normalized_languages


async def cmd_exec(cmd, shell=False):
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    stdout = stdout.decode(errors='ignore').strip()
    stderr = stderr.decode(errors='ignore').strip()
    return stdout, stderr, proc.returncode


async def restart_notification():
    chat_id, msg_id = 0, 0

    try:
        if await aiopath.exists(".restartmsg"):
            async with aiopen(".restartmsg", "r") as f:
                data = await f.readlines()
                chat_id, msg_id = map(int, data)

            try:
                repo = Telegram.UPSTREAM_REPO.split('/')
                UPSTREAM_REPO = f"https://github.com/{repo[-2]}/{repo[-1]}"
                await StreamBot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"<blockquote>♻️ Restart Successfully...! \n\nDate: {now.strftime('%d/%m/%y')}\nTime: {now.strftime('%I:%M:%S %p')}\nTimeZone: {timezone.zone}\n\nRepo: {UPSTREAM_REPO}\nBranch: {Telegram.UPSTREAM_BRANCH}\nVersion: {__version__}</blockquote>",
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception as e:
                LOGGER.error(f"Failed to edit restart message: {e}")

            await aioremove(".restartmsg")

    except Exception as e:
        LOGGER.error(f"Error in restart_notification: {e}")


# =============================================================================
# TMDB VALIDATION WITH CACHING
# =============================================================================
import asyncio
from datetime import datetime, timedelta
from Backend.config import QueueConfig, TMDBValidation, Telegram
from themoviedb import aioTMDb

# TMDB client for validation
tmdb_validator = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")

# Cache for validated TMDB IDs
tmdb_validation_cache = {}
tmdb_cache_expiry = {}

# Track cache hits/misses for metrics
tmdb_cache_hits = 0
tmdb_cache_misses = 0


async def validate_tmdb_id_thorough(tmdb_id: int, media_type: str, retry_count: int = 0) -> bool:
    """
    Thorough TMDB validation with caching and exponential backoff for timeouts.
    Returns True if ID exists and is valid.
    This is SLOW but ACCURATE.
    """
    global tmdb_cache_hits, tmdb_cache_misses
    
    if not TMDBValidation.ENABLE_VALIDATION:
        return True  # Skip validation if disabled
    
    # Check cache first
    cache_key = f"{media_type}:{tmdb_id}"
    if TMDBValidation.CACHE_VALID_IDS:
        if cache_key in tmdb_validation_cache:
            expiry = tmdb_cache_expiry.get(cache_key, datetime.min)
            if datetime.utcnow() < expiry:
                tmdb_cache_hits += 1
                LOGGER.debug(f"TMDB cache hit: {cache_key} = {tmdb_validation_cache[cache_key]}")
                return tmdb_validation_cache[cache_key]
    
    tmdb_cache_misses += 1
    
    try:
        LOGGER.info(f"🔍 Validating TMDB ID {tmdb_id} ({media_type})...")
        
        # Slow but thorough validation
        if media_type == "movie":
            # Fetch movie details (takes 2-5 seconds)
            result = await asyncio.wait_for(
                tmdb_validator.movie(tmdb_id).details(),
                timeout=QueueConfig.TMDB_VALIDATION_TIMEOUT
            )
            is_valid = result is not None and hasattr(result, 'id')
            
            if is_valid:
                LOGGER.info(f"✅ TMDB validation PASSED for movie {tmdb_id}: {result.title if result else 'Unknown'}")
            else:
                LOGGER.warning(f"❌ TMDB validation FAILED for movie {tmdb_id}")
                
        elif media_type == "tv":
            # Fetch TV details (takes 2-5 seconds)
            result = await asyncio.wait_for(
                tmdb_validator.tv(tmdb_id).details(),
                timeout=QueueConfig.TMDB_VALIDATION_TIMEOUT
            )
            is_valid = result is not None and hasattr(result, 'id')
            
            if is_valid:
                LOGGER.info(f"✅ TMDB validation PASSED for TV {tmdb_id}: {result.name if result else 'Unknown'}")
            else:
                LOGGER.warning(f"❌ TMDB validation FAILED for TV {tmdb_id}")
        else:
            return False
        
        # Cache the result
        if TMDBValidation.CACHE_VALID_IDS:
            tmdb_validation_cache[cache_key] = is_valid
            tmdb_cache_expiry[cache_key] = datetime.utcnow() + timedelta(seconds=QueueConfig.TMDB_CACHE_TTL)
        
        return is_valid
        
    except asyncio.TimeoutError:
        LOGGER.error(f"⏰ TMDB validation TIMEOUT for {media_type} {tmdb_id} after {QueueConfig.TMDB_VALIDATION_TIMEOUT}s")
        
        # Smart exponential backoff: 5s, 10s, 20s
        if retry_count < 3:
            wait_time = 2 ** retry_count * 5
            LOGGER.warning(f"🔄 TMDB timeout, retrying in {wait_time}s (attempt {retry_count + 1}/3)")
            await asyncio.sleep(wait_time)
            return await validate_tmdb_id_thorough(tmdb_id, media_type, retry_count + 1)
        else:
            LOGGER.error(f"❌ TMDB validation failed after 3 retries")
            
        if TMDBValidation.SKIP_ON_TIMEOUT:
            LOGGER.warning(f"⚠️ Skipping validation due to timeout (file will be skipped)")
            return False
        else:
            # Wait and retry (don't skip, just wait longer)
            LOGGER.info(f"🔄 Waiting additional 10 seconds and retrying...")
            await asyncio.sleep(10)
            return await validate_tmdb_id_thorough(tmdb_id, media_type, 0)  # Reset retry count
            
    except Exception as e:
        LOGGER.error(f"❌ TMDB validation ERROR for {media_type} {tmdb_id}: {e}")
        return False


async def validate_episode_exists(tmdb_id: int, season: int, episode: int) -> bool:
    """
    Validate that a specific episode exists in TMDB.
    Very slow but ensures accuracy.
    """
    if not TMDBValidation.ENABLE_VALIDATION:
        return True
    
    cache_key = f"episode:{tmdb_id}:{season}:{episode}"
    
    # Check cache
    if cache_key in tmdb_validation_cache:
        expiry = tmdb_cache_expiry.get(cache_key, datetime.min)
        if datetime.utcnow() < expiry:
            return tmdb_validation_cache[cache_key]
    
    try:
        LOGGER.info(f"🔍 Validating episode S{season}E{episode} for TMDB ID {tmdb_id}...")
        
        # Fetch season details (takes 3-5 seconds)
        season_details = await asyncio.wait_for(
            tmdb_validator.season(tmdb_id, season).details(),
            timeout=QueueConfig.TMDB_VALIDATION_TIMEOUT
        )
        
        if not season_details or not hasattr(season_details, 'episodes'):
            return False
        
        # Check if episode exists
        episode_numbers = [ep.episode_number for ep in season_details.episodes if hasattr(ep, 'episode_number')]
        is_valid = episode in episode_numbers
        
        if is_valid:
            LOGGER.info(f"✅ Episode validation PASSED: S{season}E{episode} exists")
        else:
            LOGGER.warning(f"❌ Episode validation FAILED: S{season}E{episode} not found (available: {episode_numbers})")
        
        # Cache result
        tmdb_validation_cache[cache_key] = is_valid
        tmdb_cache_expiry[cache_key] = datetime.utcnow() + timedelta(seconds=QueueConfig.TMDB_CACHE_TTL)
        
        return is_valid
        
    except asyncio.TimeoutError:
        LOGGER.error(f"⏰ Episode validation TIMEOUT for S{season}E{episode}")
        return False
    except Exception as e:
        LOGGER.error(f"❌ Episode validation ERROR: {e}")
        return False


async def cleanup_tmdb_cache():
    """Remove expired TMDB cache entries every hour."""
    while True:
        await asyncio.sleep(3600)  # Run every hour
        now = datetime.utcnow()
        expired = [k for k, v in tmdb_cache_expiry.items() if now >= v]
        for key in expired:
            tmdb_validation_cache.pop(key, None)
            tmdb_cache_expiry.pop(key, None)
        if expired:
            LOGGER.info(f"🧹 Cleaned {len(expired)} expired TMDB cache entries")


def get_tmdb_cache_stats() -> dict:
    """Get TMDB cache statistics for metrics."""
    total = tmdb_cache_hits + tmdb_cache_misses
    hit_rate = (tmdb_cache_hits / total * 100) if total > 0 else 0
    return {
        "cache_size": len(tmdb_validation_cache),
        "hits": tmdb_cache_hits,
        "misses": tmdb_cache_misses,
        "hit_rate": round(hit_rate, 2)
    }