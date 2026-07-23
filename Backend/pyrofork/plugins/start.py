import re
import asyncio
from typing import Dict
from asyncio import create_task, sleep as asleep, Lock
from urllib.parse import urlparse
from Backend.logger import LOGGER
from Backend import db
from Backend.config import Telegram
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.encrypt import decode_string
from Backend.helper.metadata import extract_codec_from_filename, metadata
from Backend.helper.pyro import clean_filename, get_readable_file_size, remove_urls
from Backend.pyrofork import StreamBot
from pyrogram import filters, Client
from pyrogram.types import Message
from os import path as ospath
from pyrogram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode
from themoviedb import aioTMDb
from asyncio import Queue
from os import execl as osexecl
from asyncio import create_subprocess_exec, gather
from sys import executable
from aiofiles import open as aiopen
from pyrogram import enums
from Backend.config import QueueConfig, TMDBValidation, MediaProbeConfig
from Backend.helper.pyro import validate_tmdb_id_thorough, validate_episode_exists, cleanup_tmdb_cache, get_tmdb_cache_stats
from Backend.helper.media_probe import stream_and_probe, build_media_caption
from collections import defaultdict
import time
import json


tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")
# Initialize database connection
import random
import string
from passlib.context import CryptContext
from datetime import datetime, timedelta

# =============================================================================
# FILE ACCEPTANCE HELPERS
# =============================================================================
# Common video container extensions we accept even when Telegram reports a
# non-standard / missing mime type (some clients send .mkv as application/octet-stream).
VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.ts', '.wmv', '.m4v', '.webm', '.flv', '.mpg', '.mpeg', '.m2ts')

# Match a video filename anywhere in a caption line, allowing for trailing junk.
_VIDEO_NAME_RE = re.compile(
    r'([^\n]*?\.(?:mkv|mp4|avi|mov|ts|wmv|m4v|webm|flv|mpg|mpeg|m2ts))\b',
    re.IGNORECASE,
)


def is_video_message(message: Message) -> bool:
    """Return True if the message carries an acceptable video file.

    Accepts native Telegram videos, documents with a video/* mime type, OR
    documents whose filename ends in a known video container extension. This is
    deliberately more permissive than a strict mime check so non-standard
    uploads (e.g. .mkv sent as application/octet-stream) are not silently dropped.
    """
    if message.video:
        return True
    doc = message.document
    if not doc:
        return False
    mime = (doc.mime_type or "").lower()
    if mime.startswith("video/"):
        return True
    name = (doc.file_name or "").lower()
    return name.endswith(VIDEO_EXTENSIONS)


def extract_title_from_caption(caption: str, fallback: str) -> str:
    """Extract the most likely filename/title from a (possibly messy) caption.

    Handles multi-line captions, leading emojis/brackets, and bold markers by
    first looking for a line containing a video filename, then falling back to
    the first non-empty line, then to the provided fallback.
    """
    if not caption:
        return fallback
    lines = [ln.strip() for ln in caption.splitlines() if ln.strip()]
    # 1) Prefer a line that actually contains a video filename.
    for line in lines:
        match = _VIDEO_NAME_RE.search(line)
        if match:
            return match.group(1).strip().strip('[](){}<>*_`"\'').strip()
    # 2) Otherwise, fall back to the first meaningful line (strip leading emojis/symbols).
    for line in lines:
        cleaned = re.sub(r'^[^\w\[(]+', '', line).strip()
        if cleaned:
            return cleaned
    return fallback


# =============================================================================
# ENHANCED QUEUE SYSTEM - SLOW & THOROUGH MODE
# =============================================================================

# Enhanced queue item structure
class QueueItem:
    def __init__(self, metadata_info, hash_val, channel, msg_id, size, title, source, 
                 retry_count=0, probe_result=None):
        self.metadata_info = metadata_info
        self.hash = hash_val
        self.channel = channel
        self.msg_id = msg_id
        self.size = size
        self.title = title
        self.source = source
        self.retry_count = retry_count
        self.added_at = datetime.utcnow()
        self.probe_result = probe_result  # Store ffmpeg probe results


# Enhanced queue configuration
file_queue = Queue()
db_lock = Lock()
is_queue_paused = False

# MongoDB Queue Processor State
channel_processors: Dict[int, asyncio.Task] = {}
channel_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
last_edit_time: Dict[int, float] = {}

# Track files currently in queue to prevent duplicates (with timestamps)
queued_files: dict = {}  # {(channel, msg_id): timestamp}
queue_stats = {
    "processed": 0,
    "failed": 0,
    "skipped": 0,
    "retried": 0,
    "replaced": 0,  # Track replacements
}

# Batch processing tracking
processed_count_in_batch = 0
batch_lock = Lock()

# Rate limiting per channel
channel_rate_limit = defaultdict(list)

# Processing time tracking for metrics
total_processing_time = 0.0
processing_count = 0

# Higher values are preferred when the resolution is the same.
CODEC_PRIORITY = {
    'hevc': 3, 'h265': 3, 'h.265': 3, 'x265': 3,
    'h264': 2, 'avc': 2, 'h.264': 2, 'x264': 2,
    'vp9': 1,
}

RIP_PRIORITY = {
    'bluray': 7, '4k': 6, 'remux': 5, 'web-dl': 4, 'webrip': 3,
    'hdtv': 2, 'dvd': 1, 'tvrip': 0, 'hdrip': 0, 'cam': -1,
}


def _codec_priority(codec: str | None) -> int:
    return CODEC_PRIORITY.get(str(codec or '').lower().replace(' ', ''), 0)


def _audio_track_count(metadata_info: dict, probe_result: dict | None = None) -> int:
    tracks = (probe_result or {}).get('audio_tracks') or metadata_info.get('audio_tracks') or []
    return len(tracks) if isinstance(tracks, list) else 0


def _size_in_bytes(size: str | int | float | None) -> int:
    """Convert the stored human-readable Telegram file size into comparable bytes."""
    if isinstance(size, (int, float)):
        return int(size)
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B)?', str(size or ''), re.IGNORECASE)
    if not match:
        return 0
    units = {'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3, 'TB': 1024 ** 4}
    return int(float(match.group(1)) * units.get((match.group(2) or 'B').upper(), 1))


def _compare_same_quality(current: dict, existing: dict, probe_result: dict | None, current_size: str | None) -> tuple[int, str]:
    """Compare same-resolution releases in the requested deterministic order.
    Priority: codec (h265 > h264 > vp9) > audio track count > file size > language count > rip source
    """
    comparisons = (
        ('codec', _codec_priority(current.get('codec')), _codec_priority(existing.get('codec'))),
        ('audio track count', _audio_track_count(current, probe_result), _audio_track_count(existing)),
        ('file size', _size_in_bytes(current_size), _size_in_bytes(existing.get('size'))),
        ('language count', len(current.get('languages') or []), len(existing.get('languages') or [])),
        ('rip source', RIP_PRIORITY.get(str(current.get('rip') or '').lower(), 0), RIP_PRIORITY.get(str(existing.get('rip') or '').lower(), 0)),
    )
    for label, current_value, existing_value in comparisons:
        if current_value != existing_value:
            return (1 if current_value > existing_value else -1), label
    return 0, 'equal metadata'

# Clear queue confirmation states
clear_queue_state = {
    'waiting_for_confirmation': False,
    'timestamp': 0,
    'pending_count': 0
}

clear_mongodb_state = {
    'waiting_for_confirmation': False,
    'timestamp': 0
}


# Note: asyncio.create_task calls moved to startup to prevent event loop issues
# They will be started when the bot initializes

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

@StreamBot.on_message(filters.command("user") & filters.private & CustomFilters.admin)
async def create_user(bot: Client, message: Message):
    """Create a new authentication user with enhanced formatting."""
    try:
        args = message.text.split()
        
        # Show help if no arguments
        if len(args) == 1:
            help_text = """
👤 <b>Create User Command Help</b>

<b>Usage:</b>
<code>/user &lt;username&gt; &lt;expiry_days&gt;</code>

<b>Example:</b>
<code>/user john 30</code> - Creates user 'john' valid for 30 days

<b>Notes:</b>
• Username must be unique
• Expiry days must be a positive number
• Password will be auto-generated
            """
            await message.reply_text(help_text, parse_mode=enums.ParseMode.HTML)
            return
        
        if len(args) != 3:
            await message.reply_text("❌ Usage: `/user <username> <expiry_days>`", parse_mode=ParseMode.MARKDOWN)
            return

        username = args[1]
        
        # Validate username
        if not username or len(username) < 3:
            await message.reply_text("❌ Username must be at least 3 characters long")
            return
        
        try:
            expiry_days = int(args[2])
            if expiry_days <= 0:
                await message.reply_text("❌ Expiry days must be a positive number")
                return
        except ValueError:
            await message.reply_text("❌ Expiry days must be a valid number")
            return

        users_collection = db.db["auth_users"]  # Use the Tracking database

        # Check if username already exists
        existing_user = await users_collection.find_one({"username": username})
        if existing_user:
            await message.reply_text(f"❌ User `{username}` already exists!", parse_mode=ParseMode.MARKDOWN)
            return

        password = generate_password()
        hashed_password = pwd_ctx.hash(password)
        expires_at = datetime.utcnow() + timedelta(days=expiry_days)

        user_data = {
            "username": username,
            "password": hashed_password,
            "expires_at": expires_at
        }
        await users_collection.insert_one(user_data)

        await message.reply_text(
            f"✅ <b>User Created Successfully</b>\n\n"
            f"👤 <b>Username:</b> <code>{username}</code>\n"
            f"🔑 <b>Password:</b> <code>{password}</code>\n"
            f"🕒 <b>Expires in:</b> {expiry_days} days\n"
            f"📅 <b>Expiry Date:</b> {expires_at.strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            f"⚠️ Please save these credentials securely!",
            parse_mode=enums.ParseMode.HTML
        )

    except Exception as e:
        LOGGER.error(f"Error in /user command: {e}")
        await message.reply_text("❌ An error occurred while creating the user.")

@StreamBot.on_message(filters.command('restart') & filters.private & CustomFilters.admin)
async def restart(bot: Client, message: Message):
    try:
        # Notify the user that the bot is restarting
        
        restart_message = await message.reply_text(
    '<blockquote>⚙️ Restarting Backend API... \n\n✨ Please wait as we bring everything back online! 🚀</blockquote>',
        quote=True,
        parse_mode=enums.ParseMode.HTML
        )
        LOGGER.info("Restart initiated by owner.")

        # Run the update script
        proc1 = await create_subprocess_exec('python3', 'update.py')
        await gather(proc1.wait())

        # Save restart message details for notification after restart
        async with aiopen(".restartmsg", "w") as f:
            await f.write(f"{restart_message.chat.id}\n{restart_message.id}\n")

        # Restart the bot process
        osexecl(executable, executable, "-m", "Backend")

    except Exception as e:
        LOGGER.error(f"Error during restart: {e}")
        await message.reply_text("❌ <b>Failed to restart. Check logs for details.</b>", parse_mode=enums.ParseMode.HTML)




async def delete_messages_after_delay(messages):
    await asleep(300)  
    for msg in messages:
        try:
            await msg.delete()
        except Exception as e:
            LOGGER.error(f"Error deleting message {msg.id}: {e}")
        await asleep(2)  

@StreamBot.on_message(filters.command('start') & filters.private)
async def start(bot: Client, message: Message):
    LOGGER.info(f"Received command: {message.text}")
    
    command_part = message.text.split('start ')[-1]
    
    if command_part.startswith("file_"):
        usr_cmd = command_part[len("file_"):].strip()
        
        parts = usr_cmd.split("_")
        
        if len(parts) == 2:
            try:
                tmdb_id, quality = parts
                tmdb_id = int(tmdb_id)
                season = None
                quality_details = await db.get_quality_details(tmdb_id, quality)
            except ValueError:
                LOGGER.error(f"Error parsing movie command: {usr_cmd}")
                await message.reply_text("Invalid command format for movie.")
                return
        
        elif len(parts) == 3:
            try:
                tmdb_id, season, quality = parts
                tmdb_id = int(tmdb_id)
                season = int(season)
                quality_details = await db.get_quality_details(tmdb_id, quality, season)
            except ValueError:
                LOGGER.error(f"Error parsing TV show command: {usr_cmd}")
                await message.reply_text("Invalid command format for TV show.")
                return
        elif len(parts) == 4:
            try:
                tmdb_id, season, episode, quality = parts
                tmdb_id = int(tmdb_id)
                season = int(season)
                episode = int(episode)
                quality_details = await db.get_quality_details(tmdb_id, quality, season, episode)
            except ValueError:
                LOGGER.error(f"Error parsing TV show command: {usr_cmd}")
                await message.reply_text("Invalid command format for TV show.")
                return

        else:
            await message.reply_text("Invalid command format.")
            return

        sent_messages = []
        for detail in quality_details:
            decoded_data = await decode_string(detail['id'])
            channel = f"-100{decoded_data['chat_id']}"
            msg_id = decoded_data['msg_id']
            name = detail['name']
            if "\\n" in name and name.endswith(".mkv"):
                name = name.rsplit(".mkv", 1)[0].replace("\\n", "\n")
            try:
                file = await bot.get_messages(int(channel), int(msg_id))
                media = file.document or file.video
                if media:
                    sent_msg = await message.reply_cached_media(
                        file_id=media.file_id,
                        caption=f'{name}'
                    )
                    sent_messages.append(sent_msg)
                    await asleep(1)
            except FloodWait as e:
                LOGGER.info(f"Sleeping for {e.value}s")
                await asleep(e.value)
                await message.reply_text(f"Got Floodwait of {e.value}s")
            except Exception as e:
                LOGGER.error(f"Error retrieving/sending media: {e}")
                await message.reply_text("Error retrieving media.")

        if sent_messages:
            warning_msg = await message.reply_text(
                "Forward these files to your saved messages. These files will be deleted from the bot within 5 minutes."
            )
            sent_messages.append(warning_msg)
            create_task(delete_messages_after_delay(sent_messages))
    else:
        await message.reply_text("HI i am FileStore Bot For MovieFlixs")



@StreamBot.on_message(filters.command('log') & filters.private & CustomFilters.admin)
async def start(bot: Client, message: Message):
    try:
        path = ospath.abspath('log.txt')
        return await message.reply_document(
        document=path, quote=True, disable_notification=True
        )
    except Exception as e:
        print(f"An error occurred: {e}")




# (Queue configuration moved to top of file - see above)


async def validate_file_before_insert(item: QueueItem) -> tuple:
    """
    Thorough validation with TMDB checks.
    This is SLOW but ensures data quality.

    Returns:
        (True, None) when all validations pass.
        (False, reason) with a short human-readable reason on failure.
    """
    try:
        LOGGER.info(f"🔍 Starting thorough validation for: {item.title[:50]}...")
        
        # Step 1: Verify file exists in Telegram (2 sec)
        await asyncio.sleep(2)
        channel_id = f"-100{item.channel}"
        try:
            message = await StreamBot.get_messages(int(channel_id), item.msg_id)
            if message.empty:
                LOGGER.warning(f"❌ File {item.msg_id} no longer exists")
                return False, "File no longer exists"
        except Exception as e:
            LOGGER.warning(f"❌ Could not verify message: {e}")
            return False, f"Could not verify file: {e}"
        
        # Step 2: Verify file size (quick check)
        file = message.video or message.document
        if not file or file.file_size == 0:
            LOGGER.warning(f"❌ File has zero size")
            return False, "File has zero size"
        
        LOGGER.info(f"✅ File exists: {get_readable_file_size(file.file_size)}")
        
        # Step 3: Validate metadata structure
        if not item.metadata_info:
            LOGGER.warning(f"❌ Invalid metadata")
            return False, "Invalid metadata"
        
        # Step 4: THOROUGH TMDB VALIDATION (5-10 seconds)
        tmdb_id = item.metadata_info.get('tmdb_id')
        media_type = item.metadata_info.get('media_type')
        
        if tmdb_id and media_type:
            LOGGER.info(f"🔄 Validating TMDB ID {tmdb_id}...")
            
            # Validate main media exists
            tmdb_valid = await validate_tmdb_id_thorough(tmdb_id, media_type)
            
            if not tmdb_valid:
                LOGGER.error(f"❌ TMDB validation FAILED for ID {tmdb_id}")
                return False, f"TMDB ID {tmdb_id} invalid"
            
            # For TV shows, validate episode exists
            if media_type == "tv":
                season = item.metadata_info.get('season_number')
                episode = item.metadata_info.get('episode_number')
                
                if season and episode:
                    episode_valid = await validate_episode_exists(tmdb_id, season, episode)
                    if not episode_valid:
                        LOGGER.error(f"❌ Episode S{season}E{episode} does not exist in TMDB")
                        return False, f"Episode S{season}E{episode} does not exist in TMDB"
        
        # Step 5: Additional delay for safety
        await asyncio.sleep(QueueConfig.FILE_VALIDATION_DELAY)
        
        LOGGER.info(f"✅ ALL VALIDATIONS PASSED for: {item.title[:50]}")
        return True, None
        
    except Exception as e:
        LOGGER.error(f"❌ Validation error: {e}")
        return False, f"Validation error: {e}"


def detect_remastered_version(title: str) -> bool:
    """Detect if the file is a remastered version from filename/title."""
    if not title:
        return False
    
    remastered_keywords = [
        'remastered', 'remaster', 'restored', '4k restore',
        'restoration', 'anniversary', 'special edition',
        'collector', 'ultimate', 'director cut', 'extended'
    ]
    
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in remastered_keywords)


def calculate_file_score(metadata_info: dict, probe_result: dict = None) -> dict:
    """
    Calculate comprehensive score for a file based on multiple factors.
    
    Scoring factors (weighted):
    - Audio track count: 30 points per track
    - Language count: 15 points per language
    - Codec preference: 25 points (h264=25, vp9=20, h265=15, others=10)
    - Rip source: 20 points (based on quality)
    - Remastered: 30 bonus points
    - HDR: 15 bonus points
    - Subtitle tracks: 5 points per track
    
    Returns dict with score and breakdown
    """
    score = 0
    breakdown = {}
    
    # Audio track count (highest priority)
    audio_tracks = metadata_info.get('audio_tracks', [])
    if probe_result and probe_result.get('audio_tracks'):
        audio_tracks = probe_result['audio_tracks']
    
    audio_count = len(audio_tracks) if audio_tracks else 0
    audio_score = audio_count * 30
    score += audio_score
    breakdown['audio_tracks'] = audio_count
    breakdown['audio_score'] = audio_score
    
    # Language count
    languages = metadata_info.get('languages', [])
    lang_count = len(languages) if languages else 0
    lang_score = lang_count * 15
    score += lang_score
    breakdown['languages'] = lang_count
    breakdown['lang_score'] = lang_score
    
    # Codec preference
    codec = metadata_info.get('codec') or 'other'
    from Backend.helper.database import CODEC_PRIORITY
    codec_priority_score = CODEC_PRIORITY.get(codec.lower(), 0) * 25
    score += codec_priority_score
    breakdown['codec'] = codec
    breakdown['codec_score'] = codec_priority_score
    
    # Rip source
    rip = metadata_info.get('rip', 'Unknown')
    rip_priority = {
        'BluRay': 20, '4K': 19, 'Remux': 18,
        'WEB-DL': 15, 'WEBRip': 12, 'HDTV': 10,
        'DVD': 8, 'TVRip': 5, 'HDRip': 3,
        'CAM': 1, 'Unknown': 0
    }
    rip_score = rip_priority.get(rip, 0)
    score += rip_score
    breakdown['rip'] = rip
    breakdown['rip_score'] = rip_score
    
    # Remastered detection
    title = metadata_info.get('title', '')
    is_remastered = detect_remastered_version(title)
    if is_remastered:
        score += 30
        breakdown['remastered'] = True
        breakdown['remastered_bonus'] = 30
    else:
        breakdown['remastered'] = False
        breakdown['remastered_bonus'] = 0
    
    # HDR detection
    if probe_result and probe_result.get('hdr'):
        score += 15
        breakdown['hdr'] = True
        breakdown['hdr_bonus'] = 15
    else:
        breakdown['hdr'] = False
        breakdown['hdr_bonus'] = 0
    
    # Subtitle tracks
    subtitle_tracks = 0
    if probe_result and probe_result.get('subtitle_tracks'):
        subtitle_tracks = len(probe_result['subtitle_tracks'])
    subtitle_score = subtitle_tracks * 5
    score += subtitle_score
    breakdown['subtitle_tracks'] = subtitle_tracks
    breakdown['subtitle_score'] = subtitle_score
    
    breakdown['total_score'] = score
    return breakdown


async def smart_replace_or_skip(metadata_info: dict, probe_result: dict = None, current_size: str = None) -> tuple:
    """
    Compare only files of the same resolution. The first differing attribute wins:
    language count, codec (H265/HEVC > H264/AVC > VP9), rip source, audio
    track count, then file size. TV episodes are compared independently.
    
    Returns:
        ('ADD', None) - No existing version of this quality
        ('SKIP', reason) - Existing version is better or equal
        ('REPLACE', old_version_data) - Replace existing with new
    """
    try:
        tmdb_id = metadata_info.get('tmdb_id')
        media_type = metadata_info.get('media_type')
        current_quality = metadata_info.get('quality', '720p')
        
        # Check movies collection
        if media_type == "movie":
            existing_movie = await db.movie_collection.find_one({"tmdb_id": tmdb_id})
            
            if existing_movie and existing_movie.get('telegram'):
                for existing_quality in existing_movie['telegram']:
                    existing_quality_name = existing_quality.get('quality', '720p')
                    
                    # Only compare same quality
                    if existing_quality_name != current_quality:
                        continue
                    
                    existing_metadata = {
                        **existing_quality,
                        'languages': existing_quality.get('languages') or existing_movie.get('languages', []),
                        'rip': existing_quality.get('rip') or existing_movie.get('rip', 'Unknown'),
                    }
                    comparison, reason = _compare_same_quality(metadata_info, existing_metadata, probe_result, current_size)
                    if comparison > 0:
                        LOGGER.info(f"🔄 Current movie is better by {reason} - Will replace existing")
                        return 'REPLACE', existing_quality
                    elif comparison < 0:
                        LOGGER.info(f"⏭️ Existing movie is better by {reason} - Skipping current")
                        return 'SKIP', f"Existing is better by {reason}"
                    else:
                        LOGGER.info("⏭️ Matching movie metadata - Keeping existing")
                        return 'SKIP', 'Matching metadata'
        
        # Similar logic for TV shows...
        elif media_type == "tv":
            season = metadata_info.get('season_number')
            episode = metadata_info.get('episode_number')
            if season and episode:
                existing_tv = await db.tv_collection.find_one({"tmdb_id": tmdb_id})
                if existing_tv:
                    # Check for existing episode in the same season
                    for s in existing_tv.get('seasons', []):
                        if s.get('season_number') == season:
                            for ep in s.get('episodes', []):
                                if ep.get('episode_number') == episode:
                                    for existing_quality in ep.get('telegram', []):
                                        existing_quality_name = existing_quality.get('quality', '720p')
                                        
                                        # Only compare same quality
                                        if existing_quality_name != current_quality:
                                            continue
                                        
                                        existing_metadata = {
                                            **existing_quality,
                                            'languages': existing_quality.get('languages') or existing_tv.get('languages', []),
                                            'rip': existing_quality.get('rip') or existing_tv.get('rip', 'Unknown'),
                                        }
                                        comparison, reason = _compare_same_quality(metadata_info, existing_metadata, probe_result, current_size)
                                        if comparison > 0:
                                            LOGGER.info(f"🔄 Current episode is better by {reason} - Will replace existing")
                                            return 'REPLACE', existing_quality
                                        elif comparison < 0:
                                            LOGGER.info(f"⏭️ Existing episode is better by {reason} - Skipping current")
                                            return 'SKIP', f"Existing is better by {reason}"
                                        else:
                                            LOGGER.info("⏭️ Matching episode metadata - Keeping existing")
                                            return 'SKIP', 'Matching metadata'
        
        LOGGER.info(f"➕ No existing version found - Adding new file")
        return 'ADD', None
        
    except Exception as e:
        LOGGER.error(f"Error in smart_replace_or_skip: {e}")
        return 'ADD', None  # Default to add on error


async def process_file(worker_id: int):
    """Slow, thorough worker with batch gaps."""
    global processed_count_in_batch, total_processing_time, processing_count, is_queue_paused
    
    while True:
        if is_queue_paused:
            await asyncio.sleep(1)
            continue
            
        item = await file_queue.get()
        processing_start = time.time()
        
        try:
            # Add delay between files (8 seconds)
            LOGGER.info(f"⏳ Waiting {QueueConfig.FILE_QUEUE_DELAY}s before processing next file...")
            await asyncio.sleep(QueueConfig.FILE_QUEUE_DELAY)
            
            # Batch gap: after every 10 files, wait 5 seconds
            async with batch_lock:
                processed_count_in_batch += 1
                if processed_count_in_batch >= QueueConfig.BATCH_SIZE:
                    LOGGER.info(f"📦 Processed {QueueConfig.BATCH_SIZE} files. Taking {QueueConfig.BATCH_GAP_SECONDS}s gap...")
                    await asyncio.sleep(QueueConfig.BATCH_GAP_SECONDS)
                    processed_count_in_batch = 0
            
            LOGGER.info(f"[Worker {worker_id}] Processing ({item.retry_count + 1}/{QueueConfig.MAX_RETRY_COUNT}): {item.title[:50]}...")
            
            # Thorough validation (includes TMDB checks - 5-10 seconds)
            validation_passed, validation_reason = await validate_file_before_insert(item)
            
            if not validation_passed:
                if item.retry_count < QueueConfig.MAX_RETRY_COUNT:
                    item.retry_count += 1
                    queue_stats["retried"] += 1
                    LOGGER.warning(f"⚠️ Validation failed, retrying in {QueueConfig.VALIDATION_DELAY}s...")
                    await asyncio.sleep(QueueConfig.VALIDATION_DELAY)
                    await file_queue.put(item)
                else:
                    queue_stats["failed"] += 1
                    LOGGER.error(f"❌ Max retries reached, giving up: {item.title[:50]}")
                    await db.log_failed_file(
                        title=item.title,
                        filename=item.title,
                        reason=f"Max retries reached ({validation_reason or 'validation failed'})",
                        metadata_info=item.metadata_info,
                        channel=item.channel,
                        msg_id=item.msg_id,
                        retry_count=item.retry_count,
                    )
                continue
            
            # Smart replacement decision with probe results
            action, old_version = await smart_replace_or_skip(item.metadata_info, item.probe_result, item.size)
            
            async with db_lock:
                if action == 'ADD':
                    updated_id = await db.insert_media(
                        item.metadata_info, 
                        hash=item.hash, 
                        channel=item.channel, 
                        msg_id=item.msg_id, 
                        size=item.size, 
                        name=item.title,
                        codec=item.metadata_info.get('codec')
                    )
                    if updated_id:
                        queue_stats["processed"] += 1
                        LOGGER.info(f"✅ ADDED: {item.metadata_info.get('quality')} - {item.title[:50]}")
                    else:
                        queue_stats["failed"] += 1
                        await db.log_failed_file(
                            title=item.title,
                            filename=item.title,
                            reason="Database insert failed",
                            metadata_info=item.metadata_info,
                            channel=item.channel,
                            msg_id=item.msg_id,
                            retry_count=item.retry_count,
                        )
                
                elif action == 'REPLACE':
                    # Backup old version before replacing
                    await db.backup_replaced_version(
                        item.metadata_info, 
                        old_version, 
                        reason=f"Replaced with version having more languages"
                    )
                    
                    # Remove old version and add new
                    media_type = item.metadata_info.get('media_type')
                    tmdb_id = item.metadata_info.get('tmdb_id')
                    current_quality = item.metadata_info.get('quality', '720p')
                    
                    if media_type == "movie":
                        await db.movie_collection.update_one(
                            {"tmdb_id": tmdb_id},
                            {"$pull": {"telegram": {"quality": current_quality}}}
                        )
                    elif media_type == "tv":
                        season = item.metadata_info.get('season_number')
                        episode = item.metadata_info.get('episode_number')
                        await db.tv_collection.update_one(
                            {"tmdb_id": tmdb_id},
                            {"$pull": {"seasons.$[s].episodes.$[e].telegram": {"quality": current_quality}}},
                            array_filters=[
                                {"s.season_number": season},
                                {"e.episode_number": episode}
                            ]
                        )
                    
                    # Insert new version
                    updated_id = await db.insert_media(
                        item.metadata_info, 
                        hash=item.hash, 
                        channel=item.channel, 
                        msg_id=item.msg_id, 
                        size=item.size, 
                        name=item.title,
                        codec=item.metadata_info.get('codec')
                    )
                    if updated_id:
                        queue_stats["processed"] += 1
                        queue_stats["replaced"] += 1
                        LOGGER.info(f"🔄 REPLACED: {item.metadata_info.get('quality')} - {item.title[:50]}")
                    else:
                        queue_stats["failed"] += 1
                        await db.log_failed_file(
                            title=item.title,
                            filename=item.title,
                            reason="Database insert failed (replace)",
                            metadata_info=item.metadata_info,
                            channel=item.channel,
                            msg_id=item.msg_id,
                            retry_count=item.retry_count,
                        )
                
                else:  # SKIP
                    queue_stats["skipped"] += 1
                    LOGGER.info(f"⏭️ SKIPPED: {item.title[:50]} - {old_version}")
                    await db.log_failed_file(
                        title=item.title,
                        filename=item.title,
                        reason=f"Skipped ({old_version})" if old_version else "Skipped",
                        metadata_info=item.metadata_info,
                        channel=item.channel,
                        msg_id=item.msg_id,
                        retry_count=item.retry_count,
                    )
            
            # Extra delay after successful processing
            await asyncio.sleep(1)
            
            # Track processing time for metrics
            processing_time = time.time() - processing_start
            total_processing_time += processing_time
            processing_count += 1
                    
        except Exception as e:
            queue_stats["failed"] += 1
            LOGGER.error(f"❌ Error processing: {e}")
            try:
                await db.log_failed_file(
                    title=getattr(item, "title", "Unknown"),
                    filename=getattr(item, "title", "Unknown"),
                    reason=f"Processing error: {e}",
                    metadata_info=getattr(item, "metadata_info", None),
                    channel=getattr(item, "channel", None),
                    msg_id=getattr(item, "msg_id", None),
                    retry_count=getattr(item, "retry_count", 0),
                )
            except Exception as log_err:
                LOGGER.error(f"Failed to log processing error: {log_err}")
        finally:
            cache_key = (item.channel, item.msg_id)
            queued_files.pop(cache_key, None)
            file_queue.task_done()
            LOGGER.info(f"📊 Queue stats - Processed: {queue_stats['processed']}, Failed: {queue_stats['failed']}, Skipped: {queue_stats['skipped']}")


# ============= MONGODB QUEUE PROCESSOR =============

async def process_mongodb_queue(channel_id: int = None):
    """Process files from MongoDB queue with FloodWait handling and persistence."""
    global processed_count_in_batch, total_processing_time, processing_count
    
    from pyrogram.errors import FloodWait
    
    while True:
        try:
            # Get next batch from MongoDB
            batch = await db.get_next_batch(channel_id, QueueConfig.BATCH_SIZE)
            if not batch:
                # No more items, wait and check again
                await asyncio.sleep(5)
                continue
            
            for item in batch:
                try:
                    # Apply rate limiting
                    now = asyncio.get_event_loop().time()
                    last = last_edit_time.get(channel_id or 0, 0)
                    if now - last < QueueConfig.FILE_QUEUE_DELAY:
                        await asyncio.sleep(QueueConfig.FILE_QUEUE_DELAY - (now - last))
                    
                    # Reconstruct QueueItem from MongoDB data
                    queue_item = QueueItem(
                        metadata_info=item.get('metadata_info', {}),
                        hash_val=item.get('hash'),
                        channel=item.get('channel'),
                        msg_id=item.get('msg_id'),
                        size=item.get('size'),
                        title=item.get('title'),
                        source='mongodb_queue',
                        retry_count=item.get('retry_count', 0),
                        probe_result=item.get('probe_result')
                    )
                    
                    LOGGER.info(f"[MongoDB Queue] Processing ({queue_item.retry_count + 1}/{QueueConfig.MAX_RETRY_COUNT}): {queue_item.title[:50]}...")
                    
                    # Thorough validation with FloodWait handling
                    validation_passed = False
                    for attempt in range(QueueConfig.FLOODWAIT_MAX_RETRIES):
                        try:
                            validation_passed = await validate_file_before_insert(queue_item)
                            break
                        except FloodWait as e:
                            LOGGER.warning(f"FloodWait {e.value}s during validation, attempt {attempt + 1}/{QueueConfig.FLOODWAIT_MAX_RETRIES}")
                            if attempt < QueueConfig.FLOODWAIT_MAX_RETRIES - 1:
                                await asyncio.sleep(e.value)
                            else:
                                LOGGER.error(f"Max FloodWait retries reached for validation")
                                await db.mark_failed(str(item['_id']), f"FloodWait: {e.value}s")
                                queue_stats["failed"] += 1
                                break
                        except Exception as e:
                            LOGGER.error(f"Error during validation: {e}")
                            break
                    
                    if not validation_passed:
                        if queue_item.retry_count < QueueConfig.MAX_RETRY_COUNT:
                            await db.mark_failed(str(item['_id']), "Validation failed")
                            queue_stats["retried"] += 1
                            LOGGER.warning(f"⚠️ Validation failed, will retry later")
                        else:
                            await db.mark_failed(str(item['_id']), "Max retries reached")
                            queue_stats["failed"] += 1
                            LOGGER.error(f"❌ Max retries reached: {queue_item.title[:50]}")
                        continue
                    
                    # Smart replacement decision
                    action, old_version = await smart_replace_or_skip(queue_item.metadata_info, queue_item.probe_result, queue_item.size)
                    
                    # Database operations with FloodWait handling
                    db_success = False
                    for attempt in range(QueueConfig.FLOODWAIT_MAX_RETRIES):
                        try:
                            async with db_lock:
                                if action == 'ADD':
                                    updated_id = await db.insert_media(
                                        queue_item.metadata_info, 
                                        hash=queue_item.hash, 
                                        channel=queue_item.channel, 
                                        msg_id=queue_item.msg_id, 
                                        size=queue_item.size, 
                                        name=queue_item.title,
                                        codec=queue_item.metadata_info.get('codec')
                                    )
                                    if updated_id:
                                        queue_stats["processed"] += 1
                                        await db.mark_completed(str(item['_id']), item['channel_id'], item['message_id'])
                                        await db.update_checkpoint(item['channel_id'], item['message_id'])
                                        LOGGER.info(f"✅ ADDED: {queue_item.metadata_info.get('quality')} - {queue_item.title[:50]}")
                                        db_success = True
                                    else:
                                        await db.mark_failed(str(item['_id']), "Insert failed")
                                        queue_stats["failed"] += 1
                                
                                elif action == 'REPLACE':
                                    # Backup old version
                                    await db.backup_replaced_version(
                                        queue_item.metadata_info, 
                                        old_version, 
                                        reason="Replaced with better version"
                                    )
                                    
                                    # Remove old and add new
                                    media_type = queue_item.metadata_info.get('media_type')
                                    tmdb_id = queue_item.metadata_info.get('tmdb_id')
                                    current_quality = queue_item.metadata_info.get('quality', '720p')
                                    
                                    if media_type == "movie":
                                        await db.movie_collection.update_one(
                                            {"tmdb_id": tmdb_id},
                                            {"$pull": {"telegram": {"quality": current_quality}}}
                                        )
                                    elif media_type == "tv":
                                        season = queue_item.metadata_info.get('season_number')
                                        episode = queue_item.metadata_info.get('episode_number')
                                        await db.tv_collection.update_one(
                                            {"tmdb_id": tmdb_id},
                                            {"$pull": {"seasons.$[s].episodes.$[e].telegram": {"quality": current_quality}}},
                                            array_filters=[
                                                {"s.season_number": season},
                                                {"e.episode_number": episode}
                                            ]
                                        )
                                    
                                    # Insert new version
                                    updated_id = await db.insert_media(
                                        queue_item.metadata_info, 
                                        hash=queue_item.hash, 
                                        channel=queue_item.channel, 
                                        msg_id=queue_item.msg_id, 
                                        size=queue_item.size, 
                                        name=queue_item.title,
                                        codec=queue_item.metadata_info.get('codec')
                                    )
                                    if updated_id:
                                        queue_stats["replaced"] += 1
                                        queue_stats["processed"] += 1
                                        await db.mark_completed(str(item['_id']), item['channel_id'], item['message_id'])
                                        await db.update_checkpoint(item['channel_id'], item['message_id'])
                                        LOGGER.info(f"🔄 REPLACED: {queue_item.metadata_info.get('quality')} - {queue_item.title[:50]}")
                                        db_success = True
                                    else:
                                        await db.mark_failed(str(item['_id']), "Insert failed")
                                        queue_stats["failed"] += 1
                                
                                elif action == 'SKIP':
                                    queue_stats["skipped"] += 1
                                    await db.mark_completed(str(item['_id']), item['channel_id'], item['message_id'])
                                    LOGGER.info(f"⏭️ SKIPPED: {old_version if old_version else 'existing version'}")
                                    db_success = True
                            
                            break  # Success, exit retry loop
                            
                        except FloodWait as e:
                            LOGGER.warning(f"FloodWait {e.value}s during DB operation, attempt {attempt + 1}/{QueueConfig.FLOODWAIT_MAX_RETRIES}")
                            if attempt < QueueConfig.FLOODWAIT_MAX_RETRIES - 1:
                                await asyncio.sleep(e.value)
                            else:
                                LOGGER.error(f"Max FloodWait retries reached for DB operation")
                                await db.mark_failed(str(item['_id']), f"FloodWait: {e.value}s")
                                queue_stats["failed"] += 1
                                break
                        except Exception as e:
                            LOGGER.error(f"Error during DB operation: {e}")
                            await db.mark_failed(str(item['_id']), str(e))
                            queue_stats["failed"] += 1
                            break
                    
                    if db_success:
                        # Update last edit time
                        last_edit_time[channel_id or 0] = asyncio.get_event_loop().time()
                        
                        # Batch gap
                        processed_count_in_batch += 1
                        if processed_count_in_batch >= QueueConfig.BATCH_SIZE:
                            LOGGER.info(f"📦 Processed {QueueConfig.BATCH_SIZE} files. Taking {QueueConfig.BATCH_GAP_SECONDS}s gap...")
                            await asyncio.sleep(QueueConfig.BATCH_GAP_SECONDS)
                            processed_count_in_batch = 0
                
                except Exception as e:
                    LOGGER.error(f"❌ Error processing MongoDB queue item {item.get('_id')}: {e}")
                    await db.mark_failed(str(item['_id']), str(e))
                    queue_stats["failed"] += 1
            
            # Reset failed messages periodically
            await db.reset_failed_messages(channel_id)
            
        except Exception as e:
            LOGGER.error(f"MongoDB queue processor error: {e}")
            await asyncio.sleep(10)

# =============================================================================
# STARTUP FUNCTION - Initialize background tasks
# =============================================================================
async def start_background_tasks():
    """Initialize all background tasks after bot starts."""
    # Start queue workers (in-memory queue)
    for w in range(QueueConfig.QUEUE_WORKERS):
        create_task(process_file(w + 1))
    LOGGER.info(f"👷 Started {QueueConfig.QUEUE_WORKERS} queue workers")
    
    # Start MongoDB queue processor if enabled
    if QueueConfig.ENABLE_MONGODB_QUEUE:
        create_task(process_mongodb_queue())
        LOGGER.info("🗄️ MongoDB queue processor started")
        
        # Resume processing if enabled
        if QueueConfig.RESUME_ON_START:
            LOGGER.info("🔄 Resuming queue processing from MongoDB...")
            # The processor will automatically pick up pending items
    
    # Start TMDB cache cleanup task
    create_task(cleanup_tmdb_cache())
    LOGGER.info("🧹 TMDB cache cleanup task started")
    LOGGER.info("📂 Queue state loaded")


# NOTE: Call start_background_tasks() in your bot's startup function
# Example: await start_background_tasks()


# =============================================================================
# SHARED FILE PARSING LOGIC (used by both live handler and rescan)
# =============================================================================
async def parse_and_queue_file(message: Message, channel: str, is_rescan: bool = False):
    """Parse a message's video/document and add to queue. Returns True if queued/skipped, False if error."""
    try:
        if not is_video_message(message):
            return False

        file = message.video or message.document
        if not file:
            return False

        # Extract title from caption (robust to emojis/brackets/multi-line) or filename
        fallback_name = file.file_name or file.file_id
        title = extract_title_from_caption(message.caption, fallback_name) if message.caption else fallback_name

        msg_id = message.id
        hash_val = file.file_unique_id[:6] if file.file_unique_id else ""
        size = get_readable_file_size(file.file_size)
        channel_int = int(channel)

        # ========== UPDATED: Clean filename and extract languages ==========
        original_filename = file.file_name or title
        cleaned_title, detected_langs = clean_filename(title)
        title = cleaned_title
        
        LOGGER.info(f"Original title: {title[:100]}...")
        LOGGER.info(f"Cleaned title: {cleaned_title[:100]}...")
        if detected_langs:
            LOGGER.info(f"Detected languages: {detected_langs}")
        
        metadata_info = await metadata(title, file)

        # ``clean_filename`` deliberately removes technical release tags before
        # metadata parsing. Preserve codec preference data from the unmodified
        # Telegram filename so H265/H264/VP9 comparisons remain available even
        # when media probing is disabled or unavailable.
        if metadata_info:
            filename_codec = extract_codec_from_filename(original_filename)
            if filename_codec:
                metadata_info['codec'] = filename_codec
        
        # If metadata_info exists and we detected languages, use them
        if metadata_info and detected_langs and not metadata_info.get('languages'):
            metadata_info['languages'] = detected_langs
            
        if metadata_info is None:
            await db.log_failed_file(
                title=title,
                filename=fallback_name,
                reason="Metadata not found",
                channel=channel_int,
                msg_id=msg_id,
            )
            if not is_rescan:
                await message.reply_text("> Not added — check log")
            return False
        
        # ========== NEW: Media Probing with ffmpeg ==========
        probe_result = None
        try:
            if MediaProbeConfig.ENABLE_MEDIA_PROBE:
                LOGGER.info(f"🔍 Starting media probe for: {title[:50]}...")
                
                # Update progress message if in private chat
                progress_msg = None
                if message.chat.type == "private":
                    progress_msg = await message.reply_text("🔍 Analyzing media with ffmpeg...")
                
                # Stream and probe the media
                probe_result = await stream_and_probe(file, msg_id, max_size_mb=MediaProbeConfig.MAX_PROBE_SIZE_MB)
                
                if progress_msg:
                    await progress_msg.delete()
                    
                if probe_result:
                    LOGGER.info(f"✅ Media probe successful: {probe_result.get('height', 'Unknown')}p, "
                               f"{len(probe_result.get('audio_tracks', []))} audio tracks")
                    
                    # Update metadata with probe results
                    if probe_result.get('height'):
                        from Backend.helper.media_probe import get_quality_from_height
                        quality = get_quality_from_height(probe_result['height'])
                        if quality:
                            metadata_info['quality'] = quality
                    
                    # Add codec information
                    if probe_result.get('codec'):
                        from Backend.helper.media_probe import get_codec_name
                        codec = get_codec_name(probe_result['codec'])
                        if codec:
                            metadata_info['codec'] = codec
                            LOGGER.info(f"🎬 Detected codec: {codec}")
                            
                    # Add audio tracks from probe
                    if probe_result.get('audio_tracks') and not metadata_info.get('languages'):
                        audio_langs = [t['language'] for t in probe_result['audio_tracks']]
                        metadata_info['languages'] = audio_langs
                        LOGGER.info(f"📢 Added audio languages from probe: {audio_langs}")

                    # Persist track data so future same-quality replacements can compare it.
                    metadata_info['audio_tracks'] = probe_result.get('audio_tracks', [])
                    metadata_info['subtitle_tracks'] = probe_result.get('subtitle_tracks', [])
                    
                    # Add subtitle info
                    if probe_result.get('has_subtitles'):
                        metadata_info['has_subtitles'] = True
                    
                    # Build enhanced caption if needed
                    if Telegram.USE_CAPTION:
                        enhanced_caption = build_media_caption(
                            title=metadata_info.get('title', title),
                            probe_result=probe_result,
                            quality=metadata_info.get('quality'),
                            languages=metadata_info.get('languages', detected_langs)
                        )
                        # Store caption for later use
                        metadata_info['enhanced_caption'] = enhanced_caption
                else:
                    LOGGER.warning(f"⚠️ Media probe returned no results")
            else:
                LOGGER.debug(f"Media probing disabled by config")
                
        except Exception as e:
            LOGGER.warning(f"Media probe failed: {e}")
        
        # Use detected languages from filename if probe didn't find any
        if detected_langs and not metadata_info.get('languages'):
            metadata_info['languages'] = detected_langs
            LOGGER.info(f"📢 Using filename languages: {detected_langs}")

        title = remove_urls(title)
        if not title.endswith(('.mkv', '.mp4')):
            title += '.mkv'

        cache_key = (channel_int, msg_id)
        if cache_key in queued_files:
            LOGGER.info(f"Skipping duplicate in queue: {title} ({channel_int}, {msg_id})")
            return True

        # For live handler: also check DB to avoid re-adding same file
        if not is_rescan:
            exists = await db.is_file_exists(channel_int, msg_id, hash_val)
            if exists:
                queue_stats["skipped"] += 1
                # Log skipped file to DB
                await db.log_failed_file(
                    title=title,
                    filename=file.file_name or file.file_id,
                    reason="File already exists in database - skipped",
                    metadata_info={"title": title, "channel": channel, "msg_id": msg_id, "hash": hash_val}
                )
                LOGGER.info(f"File already in DB, skipping: {title}")
                return True

        queued_files[cache_key] = datetime.utcnow()
        
        # Create queue item with enhanced structure including probe results
        queue_item = QueueItem(
            metadata_info=metadata_info,
            hash_val=hash_val,
            channel=channel_int,
            msg_id=msg_id,
            size=size,
            title=title,
            source="live" if not is_rescan else "rescan",
            retry_count=0,
            probe_result=probe_result  # Store ffmpeg probe results
        )
        
        # If MongoDB queue is enabled, only add to MongoDB (not in-memory)
        # This prevents duplicate processing
        if QueueConfig.ENABLE_MONGODB_QUEUE:
            added_to_mongodb = await db.add_to_queue(
                channel_id=channel_int,
                message_id=msg_id,
                metadata_info=metadata_info,
                hash_val=hash_val,
                channel=channel,
                msg_id=msg_id,
                size=size,
                title=title,
                probe_result=probe_result
            )
            if added_to_mongodb:
                LOGGER.info(f"🗄️ Added to MongoDB queue: {title}")
            else:
                LOGGER.info(f"⏭️ File already in MongoDB queue or processed: {title}")
                queued_files.pop(cache_key, None)
                return True
        else:
            # Add to in-memory queue only if MongoDB queue is disabled
            await file_queue.put(queue_item)
        
        LOGGER.info(f"📥 Queued ({file_queue.qsize() if not QueueConfig.ENABLE_MONGODB_QUEUE else 'MongoDB'} pending): {title}")
        return True
    except Exception as e:
        LOGGER.error(f"Error parsing file: {e}")
        return False


# =============================================================================
# QUEUE PERSISTENCE FUNCTIONS
# =============================================================================
async def save_queue_state():
    """Save current queue to file for recovery after restart."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            # Get current queue items
            items = []
            # Note: We can't directly iterate asyncio.Queue, so we track via queued_files
            queue_data = {
                'queued_files_count': len(queued_files),
                'stats': queue_stats,
                'timestamp': datetime.utcnow().isoformat()
            }
            # Save to file
            async with aiopen('.queue_state.json', 'w') as f:
                await f.write(json.dumps(queue_data, default=str))
            LOGGER.debug(f"💾 Saved queue state: {queue_data['queued_files_count']} files pending")
        except Exception as e:
            LOGGER.error(f"Error saving queue state: {e}")


async def load_queue_state():
    """Restore queue state after restart."""
    try:
        async with aiopen('.queue_state.json', 'r') as f:
            data = json.loads(await f.read())
            LOGGER.info(f"📂 Restored queue state: {data.get('queued_files_count', 0)} files were pending at last save")
            LOGGER.info(f"📊 Previous stats: {data.get('stats', {})}")
    except FileNotFoundError:
        LOGGER.info("No previous queue state found")
    except Exception as e:
        LOGGER.error(f"Error loading queue state: {e}")


# =============================================================================
# LIVE CHANNEL HANDLER WITH RATE LIMITING
# =============================================================================
@StreamBot.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(bot: Client, message: Message):
    if str(message.chat.id) in Telegram.AUTH_CHANNEL:
        # Rate limiting: MAX 5 messages per minute per channel
        channel_id = message.chat.id
        now = time.time()
        
        # Clean old timestamps
        channel_rate_limit[channel_id] = [
            t for t in channel_rate_limit[channel_id] 
            if now - t < QueueConfig.RATE_LIMIT_SECONDS
        ]
        
        if len(channel_rate_limit[channel_id]) >= QueueConfig.MAX_MESSAGES_PER_MINUTE:
            LOGGER.warning(f"🚫 Rate limit exceeded for channel {channel_id} ({QueueConfig.MAX_MESSAGES_PER_MINUTE}/min)")
            LOGGER.info(f"⏳ Waiting 60 seconds before accepting more messages...")
            await asyncio.sleep(60)  # Wait a full minute
            # Retry after wait
            channel_rate_limit[channel_id] = []
        
        channel_rate_limit[channel_id].append(now)
        
        # Add delay between channel messages
        await asyncio.sleep(3)  # 3 second delay between messages from same channel
        
        try:
            channel = str(message.chat.id).replace("-100", "")
            await parse_and_queue_file(message, channel, is_rescan=False)
        except FloodWait as e:
            LOGGER.warning(f"🚫 FloodWait: sleeping for {e.value}s")
            await asleep(e.value)
            await message.reply_text(
                text=f"Got Floodwait of {e.value}s",
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            LOGGER.error(f"Error in file_receive_handler: {e}")
    else:
        await message.reply(text="> Channel is not in AUTH_CHANNEL")


# =============================================================================
# ADMIN /queue COMMAND
# =============================================================================
def calculate_estimated_time(pending: int) -> str:
    """Calculate estimated completion time."""
    if pending <= 0:
        return "0m 0s"
    
    # Estimate time per file (delay + processing)
    est_time_per_file = QueueConfig.FILE_QUEUE_DELAY + 10  # 8s delay + 10s processing
    est_batch_gap = (pending // QueueConfig.BATCH_SIZE) * QueueConfig.BATCH_GAP_SECONDS
    est_total_seconds = (pending * est_time_per_file) + est_batch_gap
    est_minutes = est_total_seconds // 60
    est_seconds = est_total_seconds % 60
    
    return f"{est_minutes}m {est_seconds}s"


@StreamBot.on_message(filters.command("queue") & filters.private & CustomFilters.admin)
async def queue_status(bot: Client, message: Message):
    """Show comprehensive queue system status."""
    pending = file_queue.qsize()
    active = len(queued_files)
    
    # Get TMDB cache stats
    tmdb_stats = get_tmdb_cache_stats()
    
    # Get MongoDB queue stats if enabled
    mongodb_stats = ""
    if QueueConfig.ENABLE_MONGODB_QUEUE:
        mongo_queue_stats = await db.get_queue_stats()
        mongodb_stats = f"""
🗄️ <b>MongoDB Queue:</b>
• ⏳ Pending: {mongo_queue_stats['pending']}
• ❌ Failed: {mongo_queue_stats['failed']}
• 🔧 Processing: {mongo_queue_stats['processing']}
• 🔄 Total Retries: {mongo_queue_stats['total_retries']}
"""
    
    text = f"""
📊 <b>QUEUE SYSTEM STATUS</b>
━━━━━━━━━━━━━━━━━━━━━

⏳ <b>In-Memory Queue:</b> {pending} files
🔧 <b>Workers:</b> {QueueConfig.QUEUE_WORKERS} (sequential)
{mongodb_stats}
📈 <b>Statistics:</b>
• ✅ Processed: {queue_stats['processed']}
• ❌ Failed: {queue_stats['failed']}
• ⏭️ Skipped: {queue_stats['skipped']}
• 🔄 Retried: {queue_stats['retried']}
• 🔁 Replaced: {queue_stats.get('replaced', 0)}

⚙️ <b>Current Settings:</b>
• File delay: {QueueConfig.FILE_QUEUE_DELAY}s
• Batch: {QueueConfig.BATCH_SIZE} files / {QueueConfig.BATCH_GAP_SECONDS}s gap
• TMDB: {'✅ Active' if TMDBValidation.ENABLE_VALIDATION else '❌ Inactive'}
• Rate limit: {QueueConfig.MAX_MESSAGES_PER_MINUTE}/min per channel
• MongoDB Queue: {'✅ Enabled' if QueueConfig.ENABLE_MONGODB_QUEUE else '❌ Disabled'}
• Resume on Start: {'✅ Enabled' if QueueConfig.RESUME_ON_START else '❌ Disabled'}

⏱️ <b>Estimated completion:</b> {calculate_estimated_time(pending)}

💾 <b>TMDB Cache:</b> {tmdb_stats['cache_size']} entries
🎯 <b>Cache hit rate:</b> {tmdb_stats['hit_rate']}%

━━━━━━━━━━━━━━━━━━━━━
<i>🐢 Running in SLOW & THOROUGH mode for maximum reliability</i>
    """
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@StreamBot.on_message(filters.command("stats") & filters.private & CustomFilters.admin)
async def database_stats(bot: Client, message: Message):
    """Show comprehensive database statistics."""
    try:
        # Get collection counts
        movie_count = await db.movie_collection.count_documents({})
        tv_count = await db.tv_collection.count_documents({})
        views_count = await db.views_collection.count_documents({})
        replaced_count = await db.replaced_versions.count_documents({})
        failed_count = await db.failed_files_collection.count_documents({})
        
        # Get total file counts (sum of all quality versions)
        total_movie_files = 0
        total_tv_files = 0
        
        if movie_count > 0:
            movie_sample = await db.movie_collection.find_one({})
            if movie_sample and 'telegram' in movie_sample:
                docs = await db.movie_collection.find({}, {'telegram': 1}).to_list(length=None)
                total_movie_files = sum(len(doc.get('telegram', [])) for doc in docs)
        
        if tv_count > 0:
            total_tv_files = 0
            async for doc in db.tv_collection.find({}, {'seasons': 1}):
                for season in doc.get('seasons', []):
                    for episode in season.get('episodes', []):
                        total_tv_files += len(episode.get('telegram', []))
        
        # Get recent activity
        from datetime import datetime, timedelta
        yesterday = datetime.utcnow() - timedelta(days=1)
        recent_views = await db.views_collection.count_documents({'date': {'$gte': yesterday.strftime('%Y-%m-%d')}})
        
        text = f"""
📊 <b>DATABASE STATISTICS</b>
━━━━━━━━━━━━━━━━━━━━━

📁 <b>Media Collections:</b>
• 🎬 Movies: {movie_count} titles ({total_movie_files} files)
• 📺 TV Shows: {tv_count} titles ({total_tv_files} files)
• 📊 Total Media: {movie_count + tv_count} titles

📈 <b>Activity Tracking:</b>
• 👁️ Total Views: {views_count} records
• 🔥 Recent Views (24h): {recent_views}

🗄️ <b>System Collections:</b>
• 🔄 Replaced Versions: {replaced_count}
• ❌ Failed Files: {failed_count}

⚙️ <b>Configuration:</b>
• Database: {db.db_name}
• TMDB API: {'✅ Configured' if Telegram.TMDB_API else '❌ Not configured'}
• IMDB API: {'✅ Configured' if Telegram.IMDB_API else '❌ Not configured'}

━━━━━━━━━━━━━━━━━━━━━
<i>📡 Use /queue for processing status</i>
        """
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    
    except Exception as e:
        LOGGER.error(f"Error in /stats command: {e}")
        await message.reply_text(f"❌ Error retrieving statistics: {e}")


@StreamBot.on_message(filters.command("status") & filters.private & CustomFilters.admin)
async def bot_status(bot: Client, message: Message):
    """Show comprehensive bot status and health."""
    try:
        # Bot info
        bot_info = await StreamBot.get_me()
        bot_username = bot_info.username if bot_info else "Unknown"
        
        # Check database connection
        db_status = "✅ Connected" if db._conn else "❌ Disconnected"
        
        # Get channel info
        channels = Telegram.AUTH_CHANNEL
        channel_count = len(channels) if channels else 0
        
        # Get multi-client status
        multi_client_status = "✅ Enabled" if Telegram.MULTI_CLIENT else "❌ Disabled"
        
        # Calculate uptime (approximate)
        uptime = "Unknown"  # Would need to track start time
        
        text = f"""
🤖 <b>BOT STATUS & HEALTH</b>
━━━━━━━━━━━━━━━━━━━━━

📡 <b>Bot Information:</b>
• Username: @{bot_username}
• ID: {bot_info.id if bot_info else 'Unknown'}
• Status: ✅ Online

🗄️ <b>Database:</b>
• Connection: {db_status}
• Name: {db.db_name}

📺 <b>Channels:</b>
• Monitored: {channel_count} channels
• Multi-client: {multi_client_status}

⚙️ <b>Settings:</b>
• Caption: {'✅ Enabled' if Telegram.USE_CAPTION else '❌ Disabled'}
• TMDB: {'✅ Enabled' if Telegram.USE_TMDB else '❌ Disabled'}
• Media Probe: {'✅ Enabled' if MediaProbeConfig.ENABLE_MEDIA_PROBE else '❌ Disabled'}
• TMDB Validation: {'✅ Enabled' if TMDBValidation.ENABLE_VALIDATION else '❌ Disabled'}

👥 <b>Admins:</b>
• Owner: {Telegram.OWNER_ID}
• Admin Count: {len(Telegram.ADMIN_IDS)}

━━━━━━━━━━━━━━━━━━━━━
<i>📊 Use /stats for database statistics</i>
<i>📡 Use /queue for processing status</i>
        """
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    
    except Exception as e:
        LOGGER.error(f"Error in /status command: {e}")
        await message.reply_text(f"❌ Error retrieving status: {e}")


@StreamBot.on_message(filters.command("help") & filters.private & CustomFilters.admin)
async def admin_help(bot: Client, message: Message):
    """Show comprehensive admin help."""
    help_text = """
📚 <b>Admin Commands Help</b>
━━━━━━━━━━━━━━━━━━━━━

📊 <b>Status & Statistics:</b>
• <code>/status</code> - Show bot status and health
• <code>/stats</code> - Show database statistics
• <code>/queue</code> - Show queue processing status

🗄️ <b>Database Management:</b>
• <code>/resetdb</code> - Reset entire database (⚠️ DANGEROUS)
• <code>/delete</code> - Delete specific media by TMDB ID
• <code>/delete mov 12345</code> - Delete movie
• <code>/delete ser 12345</code> - Delete TV show

📁 <b>Content Management:</b>
• <code>/rescanhelp</code> - Show available rescan commands
• <code>/rescan0</code> - Rescan first channel
• <code>/rescan1</code> - Rescan second channel
• <code>/rescan0 100</code> - Rescan with limit

⚙️ <b>Queue Management:</b>
• <code>/clearqueue</code> - Clear pending queue
• <code>/confirmclear</code> - Confirm queue clear
• <code>/cancel</code> - Cancel pending operation

👤 <b>User Management:</b>
• <code>/user &lt;username&gt; &lt;days&gt;</code> - Create auth user

🔧 <b>Settings:</b>
• <code>/caption</code> - Toggle caption/filename
• <code>/tmdb</code> - Toggle TMDB/IMDB
• <code>/set &lt;id&gt;</code> - Set default URL ID

🔄 <b>System:</b>
• <code>/restart</code> - Restart the bot
• <code>/log</code> - Get log file

━━━━━━━━━━━━━━━━━━━━━
<i>⚠️ Use commands with caution, especially deletion operations</i>
    """
    await message.reply_text(help_text, parse_mode=enums.ParseMode.HTML)


# =============================================================================
# EMERGENCY CLEAR QUEUE COMMAND
# =============================================================================
# State for clear queue confirmation
clear_queue_state = {
    'waiting_for_confirmation': False,
    'timestamp': None
}

@StreamBot.on_message(filters.command("clearqueue") & filters.private & CustomFilters.admin)
async def clear_queue(bot: Client, message: Message):
    """Emergency command to clear stuck queue."""
    global queued_files, file_queue
    
    pending = file_queue.qsize()
    
    if pending == 0:
        await message.reply_text("✅ Queue is already empty!")
        return
    
    confirm_msg = await message.reply_text(
        f"⚠️ <b>DANGER: Clear Queue Confirmation</b>\n\n"
        f"This will <b>CLEAR ALL {pending} FILES</b> from the in-memory queue:\n"
        f"• All pending files will be lost\n"
        f"• Queue will be reset to empty\n\n"
        f"Type <code>/confirmclear</code> to proceed\n"
        f"Type <code>/cancel</code> to abort\n\n"
        f"⏱️ You have 60 seconds to confirm.",
        parse_mode=enums.ParseMode.HTML
    )
    
    # Set confirmation state
    clear_queue_state['waiting_for_confirmation'] = True
    clear_queue_state['timestamp'] = time.time()
    clear_queue_state['pending_count'] = pending


@StreamBot.on_message(filters.command("confirmclear") & filters.private & CustomFilters.admin)
async def confirm_clear(bot: Client, message: Message):
    """Confirm and clear the queue."""
    global queued_files, file_queue, clear_queue_state
    
    if not clear_queue_state['waiting_for_confirmation']:
        return await message.reply_text("⚠️ No pending clear operation to confirm.")
    
    # Check timeout
    if time.time() - clear_queue_state['timestamp'] > 60:
        clear_queue_state['waiting_for_confirmation'] = False
        return await message.reply_text("⏰ Confirmation timeout. Please use /clearqueue again.")
    
    # Clear the queue
    cleared_count = 0
    while not file_queue.empty():
        try:
            file_queue.get_nowait()
            file_queue.task_done()
            cleared_count += 1
        except asyncio.QueueEmpty:
            break
    
    # Clear queued files tracking
    queued_files.clear()
    
    # Reset confirmation state
    clear_queue_state['waiting_for_confirmation'] = False
    
    await message.reply_text(
        f"✅ <b>Queue Cleared Successfully</b>\n\n"
        f"• Cleared {cleared_count} files from in-memory queue\n"
        f"• Reset queued files tracking\n\n"
        f"Bot will start fresh from now.",
        parse_mode=enums.ParseMode.HTML
    )


@StreamBot.on_message(filters.command(["stopqueue", "pausequeue", "stop"]) & filters.private & CustomFilters.admin)
async def stop_queue(bot: Client, message: Message):
    """Pause the queue processors."""
    global is_queue_paused
    is_queue_paused = True
    await message.reply_text("⏸️ <b>Queue Processing Paused</b>\n\nUse /startqueue or /resumequeue to resume.", parse_mode=enums.ParseMode.HTML)


@StreamBot.on_message(filters.command(["startqueue", "resumequeue", "startq"]) & filters.private & CustomFilters.admin)
async def resume_queue(bot: Client, message: Message):
    """Resume the queue processors."""
    global is_queue_paused
    is_queue_paused = False
    await message.reply_text("▶️ <b>Queue Processing Resumed</b>", parse_mode=enums.ParseMode.HTML)


@StreamBot.on_message(filters.command("clearmongodb") & filters.private & CustomFilters.admin)
async def clear_mongodb_queue(bot: Client, message: Message):
    """Clear MongoDB queue (admin only)."""
    if not QueueConfig.ENABLE_MONGODB_QUEUE:
        return await message.reply_text("❌ MongoDB queue is not enabled.")
    
    confirm_msg = await message.reply_text(
        "⚠️ <b>DANGER: Clear MongoDB Queue Confirmation</b>\n\n"
        "This will <b>DELETE ALL ITEMS</b> from the MongoDB queue:\n"
        "• All pending files will be lost\n"
        "• All failed items will be lost\n"
        "• All processed files will be lost\n"
        "• All checkpoints will be lost\n"
        "• Queue will be completely emptied\n\n"
        "Type <code>/confirmmongodb</code> to proceed\n"
        "Type <code>/cancel</code> to abort\n\n"
        "⏱️ You have 60 seconds to confirm.",
        parse_mode=enums.ParseMode.HTML
    )
    
    # Set confirmation state
    clear_mongodb_state['waiting_for_confirmation'] = True
    clear_mongodb_state['timestamp'] = time.time()


@StreamBot.on_message(filters.command("confirmmongodb") & filters.private & CustomFilters.admin)
async def confirm_clear_mongodb(bot: Client, message: Message):
    """Confirm and clear MongoDB queue."""
    global clear_mongodb_state
    
    if not clear_mongodb_state['waiting_for_confirmation']:
        return await message.reply_text("⚠️ No pending MongoDB clear operation to confirm.")
    
    # Check timeout
    if time.time() - clear_mongodb_state['timestamp'] > 60:
        clear_mongodb_state['waiting_for_confirmation'] = False
        return await message.reply_text("⏰ Confirmation timeout. Please use /clearmongodb again.")
    
    # Clear MongoDB queue
    cleared_count = await db.clear_queue()
    
    # Reset confirmation state
    clear_mongodb_state['waiting_for_confirmation'] = False
    
    await message.reply_text(
        f"✅ <b>MongoDB Queue Completely Cleared</b>\n\n"
        f"• Total items cleared: {cleared_count}\n"
        f"• Queue collection: emptied\n"
        f"• Processed files: cleared\n"
        f"• Checkpoints: cleared\n\n"
        f"Bot will start fresh from now.",
        parse_mode=enums.ParseMode.HTML
    )


@StreamBot.on_message(filters.command("cancel") & filters.private & CustomFilters.admin)
async def cancel_clear(bot: Client, message: Message):
    """Cancel clear queue operation."""
    if clear_queue_state['waiting_for_confirmation']:
        clear_queue_state['waiting_for_confirmation'] = False
        await message.reply_text("❌ Queue clear cancelled.")
    elif clear_mongodb_state['waiting_for_confirmation']:
        clear_mongodb_state['waiting_for_confirmation'] = False
        await message.reply_text("❌ MongoDB queue clear cancelled.")
    else:
        await message.reply_text("No active operation to cancel.")


# =============================================================================
# ADMIN /rescan COMMAND
# =============================================================================
rescan_lock = Lock()
rescan_running = False

@StreamBot.on_message(filters.regex(r"^/rescan\d+$") & filters.private & CustomFilters.admin)
async def rescan_channel(bot: Client, message: Message):
    global rescan_running

    async with rescan_lock:
        if rescan_running:
            await message.reply_text("⚠️ A rescan is already in progress. Use /queue to check status.")
            return
        rescan_running = True

    try:
        # Parse channel index from command (e.g., rescan0 -> index 0)
        command = message.text.split()[0]  # /rescan0, /rescan1, etc.
        channel_index = int(command.replace("/rescan", ""))

        # Validate channel index
        if not Telegram.AUTH_CHANNEL:
            await message.reply_text("❌ No AUTH_CHANNEL configured.")
            return

        if channel_index < 0 or channel_index >= len(Telegram.AUTH_CHANNEL):
            await message.reply_text(
                f"❌ Invalid channel index. Use /rescanhelp to see available channels.",
                parse_mode=enums.ParseMode.HTML
            )
            return

        # Parse optional limit argument
        args = message.text.split()
        limit = 0
        if len(args) >= 2:
            try:
                limit = int(args[1])
            except ValueError:
                pass

        channel_id_str = Telegram.AUTH_CHANNEL[channel_index]
        channel_id = int(channel_id_str)
        channel_clean = channel_id_str.replace("-100", "")

        status_msg = await message.reply_text(
            f"🔍 <b>Rescan started</b>\n"
            f"Channel: <code>{channel_id}</code> (Index: {channel_index})\n"
            f"Checking all messages one by one...",
            parse_mode=enums.ParseMode.HTML
        )

        total_checked = 0
        total_new = 0
        total_existing = 0
        total_errors = 0
        last_update = 0

        async for msg in bot.get_chat_history(channel_id, limit=limit if limit > 0 else None):
            total_checked += 1

            # Only process video/document messages
            if not is_video_message(msg):
                continue

            try:
                file = msg.video or msg.document
                if not file:
                    continue
                hash_val = file.file_unique_id[:6] if file.file_unique_id else ""
                exists = await db.is_file_exists(int(channel_clean), msg.id, hash_val)
                if exists:
                    total_existing += 1
                    continue

                # Not in DB — parse and queue
                success = await parse_and_queue_file(msg, channel_clean, is_rescan=True)
                if success:
                    total_new += 1
                else:
                    total_errors += 1
            except Exception as e:
                LOGGER.error(f"Rescan error on msg {msg.id}: {e}")
                total_errors += 1

            # Update status every 50 messages
            if total_checked % 50 == 0:
                try:
                    await status_msg.edit_text(
                        f"🔍 <b>Rescanning...</b>\n"
                        f"Checked: <code>{total_checked}</code>\n"
                        f"📥 New queued: <code>{total_new}</code>\n"
                        f"✅ Already in DB: <code>{total_existing}</code>\n"
                        f"❌ Errors: <code>{total_errors}</code>",
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass

            # Small delay to avoid flooding
            if total_checked % 10 == 0:
                await asleep(0.5)

        # Final report
        await status_msg.edit_text(
            f"✅ <b>Rescan Complete</b>\n\n"
            f"📁 Total checked: <code>{total_checked}</code>\n"
            f"📥 New files queued: <code>{total_new}</code>\n"
            f"✅ Already in DB: <code>{total_existing}</code>\n"
            f"❌ Errors: <code>{total_errors}</code>\n"
            f"⏳ Pending in queue: <code>{file_queue.qsize()}</code>\n\n"
            f"Use /queue to monitor processing.",
            parse_mode=enums.ParseMode.HTML
        )

    except Exception as e:
        LOGGER.error(f"Rescan failed: {e}")
        await message.reply_text(f"❌ Rescan failed: {e}")
    finally:
        async with rescan_lock:
            rescan_running = False


# =============================================================================
# ADMIN /rescanhelp COMMAND
# =============================================================================
@StreamBot.on_message(filters.command("rescanhelp") & filters.private & CustomFilters.admin)
async def rescan_help(bot: Client, message: Message):
    """Show available rescan commands and their corresponding channels."""
    if not Telegram.AUTH_CHANNEL:
        await message.reply_text("❌ No AUTH_CHANNEL configured.")
        return

    text = "📋 <b>Available Rescan Commands</b>\n\n"
    for idx, channel_id in enumerate(Telegram.AUTH_CHANNEL):
        text += f"• <code>/rescan{idx}</code> → Channel: <code>{channel_id}</code>\n"

    text += "\n💡 <b>Usage:</b>\n"
    text += "• <code>/rescan0</code> - Scan first channel\n"
    text += "• <code>/rescan1</code> - Scan second channel\n"
    text += "• <code>/rescan0 100</code> - Scan first channel with limit of 100 messages\n\n"
    text += "⚠️ Only one rescan can run at a time. Use /queue to check status."

    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


# =============================================================================
# ADMIN /resetdb COMMAND - Reset entire database
# =============================================================================
reset_db_state = {
    'waiting_for_confirmation': False,
    'timestamp': None
}

@StreamBot.on_message(filters.command("resetdb") & filters.private & CustomFilters.admin)
async def reset_database(bot: Client, message: Message):
    """Reset the entire database with confirmation."""
    global reset_db_state
    
    # Set confirmation state
    reset_db_state['waiting_for_confirmation'] = True
    reset_db_state['timestamp'] = time.time()
    
    confirm_msg = await message.reply_text(
        "⚠️ <b>DANGER: Database Reset Confirmation</b>\n\n"
        "This will <b>DELETE ALL DATA</b> from the database:\n"
        "• All movies\n"
        "• All TV shows\n"
        "• All views tracking\n"
        "• All replaced versions\n"
        "• All failed files logs\n\n"
        "Type <code>/confirmreset</code> to proceed\n"
        "Type <code>/cancel</code> to abort\n\n"
        "⏱️ You have 60 seconds to confirm.",
        parse_mode=enums.ParseMode.HTML
    )



@StreamBot.on_message(filters.command("confirmreset") & filters.private & CustomFilters.admin)
async def confirm_reset(bot: Client, message: Message):
    """Confirm database reset."""
    global reset_db_state
    if reset_db_state['waiting_for_confirmation']:
        # Check if within timeout
        if time.time() - reset_db_state['timestamp'] <= 60:
            reset_db_state['waiting_for_confirmation'] = False
            status_msg = await message.reply_text("✅ Confirmation received. Resetting database...")
            
            # Perform database reset
            try:
                # Get all collection names
                collections = await db.db.list_collection_names()
                
                deleted_count = 0
                for collection_name in collections:
                    if collection_name.startswith("system."):
                        continue
                    collection = db.db[collection_name]
                    result = await collection.delete_many({})
                    deleted_count += result.deleted_count
                
                LOGGER.warning(f"🗑️ Database reset by owner: {deleted_count} documents deleted from {len(collections)} collections")
                
                await status_msg.edit_text(
                    f"✅ <b>Database Reset Complete</b>\n\n"
                    f"Deleted <b>{deleted_count}</b> documents from <b>{len(collections)}</b> collections:\n"
                    f"{', '.join(collections)}\n\n"
                    f"Database is now empty. Use /rescan to rebuild.",
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception as e:
                LOGGER.error(f"Error resetting database: {e}")
                await status_msg.edit_text(f"❌ Error resetting database: {e}")
        else:
            reset_db_state['waiting_for_confirmation'] = False
            await message.reply_text("⏰ Confirmation expired. Please run /resetdb again.")
    else:
        await message.reply_text("No pending reset request. Use /resetdb first.")


@Client.on_message(filters.command('caption') & filters.private & CustomFilters.admin)
async def toggle_caption(bot: Client, message: Message):
    """Toggle caption/filename usage with enhanced feedback."""
    try:
        Telegram.USE_CAPTION = not Telegram.USE_CAPTION
        status = "Caption" if Telegram.USE_CAPTION else "Filename"
        await message.reply_text(
            f"✅ <b>Setting Updated</b>\n\n"
            f"Now Bot Uses: <b>{status}</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        LOGGER.error(f"Error in /caption command: {e}")
        await message.reply_text("❌ An error occurred while toggling caption setting.")

@Client.on_message(filters.command('tmdb') & filters.private & CustomFilters.admin)
async def toggle_tmdb(bot: Client, message: Message):
    """Toggle TMDB/IMDB usage with enhanced feedback."""
    try:
        Telegram.USE_TMDB = not Telegram.USE_TMDB
        status = "TMDB" if Telegram.USE_TMDB else "IMDB"
        await message.reply_text(
            f"✅ <b>Setting Updated</b>\n\n"
            f"Now Bot Uses: <b>{status}</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        LOGGER.error(f"Error in /tmdb command: {e}")
        await message.reply_text("❌ An error occurred while toggling TMDB setting.")

@Client.on_message(filters.command('set') & filters.private & CustomFilters.admin)
async def set_id(bot: Client, message: Message):
    """Set default URL ID with enhanced validation."""
    try:
        args = message.text.split()
        
        # Show help if no arguments
        if len(args) == 1:
            help_text = """
🔗 <b>Set Default ID Command Help</b>

<b>Usage:</b>
<code>/set &lt;url_id&gt;</code> - Set default URL ID
<code>/set</code> - Remove default ID

<b>Example:</b>
<code>/set abc123</code> - Sets default ID to 'abc123'
            """
            await message.reply_text(help_text, parse_mode=enums.ParseMode.HTML)
            return

        url_part = args[1:]  # Skip the command itself

        if len(url_part) == 1:
            Telegram.USE_DEFAULT_ID = url_part[0]
            await message.reply_text(
                f"✅ <b>Default ID Set</b>\n\n"
                f"Now Bot Uses Default URL: <code>{Telegram.USE_DEFAULT_ID}</code>",
                parse_mode=enums.ParseMode.HTML
            )
        else:
            # Remove the default ID
            Telegram.USE_DEFAULT_ID = None
            await message.reply_text(
                "✅ <b>Default ID Removed</b>\n\n"
                "Bot will no longer use a default URL ID.",
                parse_mode=enums.ParseMode.HTML
            )
    except Exception as e:
        LOGGER.error(f"Error in /set command: {e}")
        await message.reply_text("❌ An error occurred while setting default ID.")





@Client.on_message(filters.command('delete') & filters.private & CustomFilters.admin)
async def delete(bot: Client, message: Message):
    """Delete media by TMDB ID with enhanced options."""
    try:
        split_text = message.text.split()
        
        # Show help if no arguments
        if len(split_text) == 1:
            help_text = """
🗑️ <b>Delete Command Help</b>

<b>Usage:</b>
• <code>/delete mov 12345</code> - Delete movie with TMDB ID 12345
• <code>/delete ser 12345</code> - Delete TV show with TMDB ID 12345
• <code>/delete https://domain.com/mov/12345</code> - Delete by URL

<b>Examples:</b>
• <code>/delete mov 299534</code> - Delete Avengers: Endgame
• <code>/delete ser 1396</code> - Delete Breaking Bad
• <code>/delete https://example.com/ser/1396</code> - Delete by URL

⚠️ This action cannot be undone!
            """
            return await message.reply_text(help_text, parse_mode=enums.ParseMode.HTML)
        
        # Handle URL format
        if len(split_text) == 2:
            url = split_text[1]
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.split('/')
            
            if len(path_parts) >= 3 and path_parts[-2] in ('ser', 'mov') and path_parts[-1].isdigit():
                media_type = path_parts[-2]
                tmdb_id = int(path_parts[-1])
            else:
                return await message.reply_text("❌ Invalid URL format. Use /delete for help.")
        
        # Handle direct format: /delete mov 12345
        elif len(split_text) == 3:
            media_type = split_text[1]
            tmdb_id_str = split_text[2]
            
            if media_type not in ('mov', 'ser'):
                return await message.reply_text("❌ Media type must be 'mov' or 'ser'")
            
            if not tmdb_id_str.isdigit():
                return await message.reply_text("❌ TMDB ID must be a number")
            
            tmdb_id = int(tmdb_id_str)
        else:
            return await message.reply_text("❌ Invalid format. Use /delete for help.")
        
        # Convert media_type for database
        db_media_type = "movie" if media_type == "mov" else "tv"
        
        # Get media info before deletion for confirmation
        collection = db.movie_collection if db_media_type == "movie" else db.tv_collection
        media_doc = await collection.find_one({"tmdb_id": tmdb_id})
        
        if not media_doc:
            return await message.reply_text(f"❌ No {db_media_type} found with TMDB ID {tmdb_id}")
        
        # Show media info and ask for confirmation
        title = media_doc.get('title', 'Unknown')
        year = media_doc.get('release_year', 'Unknown')
        
        confirm_text = f"""
⚠️ <b>Delete Confirmation</b>

Are you sure you want to delete:
• <b>{title}</b> ({year})
• TMDB ID: {tmdb_id}
• Type: {db_media_type}

Type <code>/confirmdelete</code> to proceed
Type <code>/cancel</code> to abort
        """
        
        # Store deletion request
        delete_request = {
            'tmdb_id': tmdb_id,
            'media_type': db_media_type,
            'title': title,
            'timestamp': time.time()
        }
        
        # Use a simple global dict for tracking (in production, use proper session management)
        global pending_delete
        pending_delete = delete_request
        
        return await message.reply_text(confirm_text, parse_mode=enums.ParseMode.HTML)
    
    except Exception as e:
        LOGGER.error(f"Error in delete command: {e}")
        await message.reply_text(f"❌ An error occurred: {str(e)}")


# Global variable for pending deletion
pending_delete = None


@Client.on_message(filters.command('confirmdelete') & filters.private & CustomFilters.admin)
async def confirm_delete(bot: Client, message: Message):
    """Confirm and execute the deletion."""
    global pending_delete
    
    if not pending_delete:
        return await message.reply_text("❌ No pending deletion request. Use /delete first.")
    
    # Check if request is still valid (5 minutes timeout)
    if time.time() - pending_delete['timestamp'] > 300:
        pending_delete = None
        return await message.reply_text("⏰ Deletion request expired. Please run /delete again.")
    
    try:
        tmdb_id = pending_delete['tmdb_id']
        media_type = pending_delete['media_type']
        title = pending_delete['title']
        
        # Perform deletion
        result = await db.delete_document(media_type, tmdb_id)
        
        if result:
            LOGGER.info(f"Deleted {media_type} {tmdb_id}: {title}")
            await message.reply_text(
                f"✅ <b>Deleted Successfully</b>\n\n"
                f"• {title}\n"
                f"• TMDB ID: {tmdb_id}\n"
                f"• Type: {media_type}",
                parse_mode=enums.ParseMode.HTML
            )
        else:
            await message.reply_text(f"❌ Failed to delete {media_type} with ID {tmdb_id}")
        
        pending_delete = None
    
    except Exception as e:
        LOGGER.error(f"Error confirming deletion: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")
        pending_delete = None
        
