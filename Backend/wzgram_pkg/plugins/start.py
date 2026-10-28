import re
import html
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
from Backend.helper.channel_index import (
    LINK_RE,
    IndexBusyError,
    IndexTargetError,
    clean_channel_id,
    is_auth_channel,
    is_scan_running,
    parse_channel_link,
    request_index_scan,
    resolve_link_target,
    resume_index_scans,
    stop_index_scan,
)
from Backend.wzgram_pkg import StreamBot
from wzgram import filters, Client
from wzgram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from os import path as ospath
from wzgram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode
from themoviedb import aioTMDb
from asyncio import Queue
from os import execl as osexecl
from asyncio import create_subprocess_exec, gather
from sys import executable
from aiofiles import open as aiopen
from wzgram import enums
from Backend.config import QueueConfig, IndexConfig, TMDBValidation, MediaProbeConfig
from Backend.helper.pyro import validate_tmdb_id_thorough, validate_episode_exists, cleanup_tmdb_cache, get_tmdb_cache_stats
from Backend.helper.media_probe import stream_and_probe, build_media_caption
from collections import defaultdict
import time
import json


tmdb = aioTMDb(api_key=Telegram.TMDB_API, language="en-US", region="US")
# Initialize database connection
import random
import string
from passlib.context import CryptContext
from datetime import datetime, timedelta

# =============================================================================
# ENHANCED QUEUE SYSTEM - SLOW & THOROUGH MODE
# =============================================================================

# Enhanced queue item structure
class QueueItem:
    def __init__(self, metadata_info, hash_val, channel, msg_id, size, title, source, 
                 retry_count=0, probe_result=None, file_unique_id=None,
                 size_bytes=None, duration=None, priority=None, needs_review=False):
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
        self.file_unique_id = file_unique_id
        self.size_bytes = size_bytes
        self.duration = duration
        self.priority = QueueConfig.PRIORITY_LIVE if priority is None else int(priority)
        self.needs_review = bool(needs_review)


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

# Global FloodWait gate shared by every queue worker. When Telegram asks us to
# back off, all workers wait together instead of each burning retries.
floodwait_gate = {"until": 0.0}


def _hold_floodwait(seconds) -> None:
    try:
        until = time.time() + max(float(seconds or 0), 1.0)
    except (TypeError, ValueError):
        until = time.time() + QueueConfig.FLOODWAIT_BASE_DELAY
    if until > floodwait_gate["until"]:
        floodwait_gate["until"] = until


async def _wait_floodwait_gate() -> None:
    while True:
        remaining = floodwait_gate["until"] - time.time()
        if remaining <= 0:
            return
        LOGGER.warning(f"⏳ Global FloodWait gate active — waiting {int(remaining)}s")
        await asyncio.sleep(min(remaining, 30))


def floodwait_remaining() -> int:
    """Seconds until the shared FloodWait gate reopens (admin display)."""
    return max(0, int(floodwait_gate["until"] - time.time()))

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

# Multi-part and multi-episode archives need a human decision: ingesting them
# as a single movie/episode silently corrupts the library.
PACK_PATTERNS = [
    (re.compile(r'\bcd[\s._-]*[12]\b', re.IGNORECASE), 'multi-part (CD1/CD2)'),
    (re.compile(r'\b(?:disc|disk)[\s._-]*[1-9]\b', re.IGNORECASE), 'multi-part (disc N)'),
    (re.compile(r'\bs\d{1,2}[\s._-]*e[\s._-]*\d{1,3}[\s._-]*[-–][\s._-]*(?:e[\s._-]*)?\d{1,3}\b', re.IGNORECASE), 'multi-episode range'),
    (re.compile(r'\bs\d{1,2}[\s._-]*e\d{1,3}[\s._-]*e\d{1,3}\b', re.IGNORECASE), 'multi-episode concatenated'),
    (re.compile(r'\b\d{1,2}x\d{1,3}[\s._-]*[-–][\s._-]*\d{1,2}x?\d{1,3}\b', re.IGNORECASE), 'multi-episode range'),
    (re.compile(r'\b(?:complete|full)[\s._-]+(?:season|series|show)\b', re.IGNORECASE), 'season/series pack'),
    (re.compile(r'\bs\d{1,2}[\s._-]*(?:complete|full)\b', re.IGNORECASE), 'season pack'),
    (re.compile(r'\bseason[\s._-]*\d{1,2}[\s._-]*(?:complete|full)\b', re.IGNORECASE), 'season pack'),
]

# "Part 1" is ambiguous ("The Godfather Part 2" is a real title), so it only
# flags an item for review: the worker will never replace an existing quality
# with a possible half-file.
PART_PATTERN = re.compile(r'\b(?:part|pt)[\s._-]*([1-9])\b', re.IGNORECASE)


def detect_pack_or_multipart(*texts) -> str | None:
    """Return a human-readable reason when the file looks like a pack/multi-part."""
    for text in texts:
        if not text:
            continue
        for pattern, reason in PACK_PATTERNS:
            if pattern.search(str(text)):
                return reason
    return None


def _part_numbers(text) -> set:
    return {m.group(1) for m in PART_PATTERN.finditer(str(text or ""))}


def detect_part_file(original_filename=None, title=None, caption=None) -> bool:
    """True when the upload looks like one piece of a multi-part release.

    A part number that is also part of the parsed title (e.g. "The Godfather
    Part 2") is treated as the real title, not as a split file.
    """
    upload_parts = _part_numbers(original_filename) | _part_numbers(caption)
    return bool(upload_parts - _part_numbers(title))


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

        users_collection = db.db["auth_users"]  # ops cluster (primary)

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


async def _deep_probe_queue_item(item: QueueItem) -> dict:
    """Full-stream probe used only when a same-quality comparison is tied.

    Quick probes stop at ~1 MB; when two releases compare equal we pay for a
    deeper look (audio track count is the tiebreaker) instead of paying it for
    every ingested file.
    """
    try:
        channel_id = f"-100{item.channel}"
        message = await StreamBot.get_messages(int(channel_id), item.msg_id)
        file = message.video or message.document
        if not file:
            return {}
        LOGGER.info(f"🔬 Deep probe (tie-break) for {item.title[:50]}...")
        return await stream_and_probe(
            file, item.msg_id, max_size_mb=MediaProbeConfig.MAX_PROBE_SIZE_MB
        )
    except FloodWait as e:
        _hold_floodwait(e.value)
        return {}
    except Exception as e:
        LOGGER.warning(f"Deep probe failed for message {item.msg_id}: {e}")
        return {}


async def validate_file_before_insert(item: QueueItem) -> bool:
    """
    Thorough validation with TMDB checks.
    This is SLOW but ensures data quality.
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
                return False
        except Exception as e:
            LOGGER.warning(f"❌ Could not verify message: {e}")
            return False
        
        # Step 2: Verify file size (quick check)
        file = message.video or message.document
        if not file or file.file_size == 0:
            LOGGER.warning(f"❌ File has zero size")
            return False
        
        LOGGER.info(f"✅ File exists: {get_readable_file_size(file.file_size)}")
        
        # Step 3: Validate metadata structure
        if not item.metadata_info:
            LOGGER.warning(f"❌ Invalid metadata")
            return False
        
        # Step 4: THOROUGH TMDB VALIDATION (5-10 seconds)
        tmdb_id = item.metadata_info.get('tmdb_id')
        media_type = item.metadata_info.get('media_type')
        
        if tmdb_id and media_type:
            LOGGER.info(f"🔄 Validating TMDB ID {tmdb_id}...")
            
            # Validate main media exists
            tmdb_valid = await validate_tmdb_id_thorough(tmdb_id, media_type)
            
            if not tmdb_valid:
                LOGGER.error(f"❌ TMDB validation FAILED for ID {tmdb_id}")
                return False
            
            # For TV shows, validate episode exists
            if media_type == "tv":
                season = item.metadata_info.get('season_number')
                episode = item.metadata_info.get('episode_number')
                
                if season and episode:
                    episode_valid = await validate_episode_exists(tmdb_id, season, episode)
                    if not episode_valid:
                        LOGGER.error(f"❌ Episode S{season}E{episode} does not exist in TMDB")
                        return False
        
        # Step 5: Additional delay for safety
        await asyncio.sleep(QueueConfig.FILE_VALIDATION_DELAY)
        
        LOGGER.info(f"✅ ALL VALIDATIONS PASSED for: {item.title[:50]}")
        return True
        
    except Exception as e:
        LOGGER.error(f"❌ Validation error: {e}")
        return False


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
            existing_movie = await db.get_movie_doc(tmdb_id)

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
                existing_tv = await db.get_tv_doc(tmdb_id)
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
            validation_passed = await validate_file_before_insert(item)
            
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
                continue
            
            # Smart replacement decision with probe results
            action, old_version = await smart_replace_or_skip(item.metadata_info, item.probe_result, item.size)
            if action == 'REPLACE' and item.needs_review:
                LOGGER.warning(f"🛡️ Refusing to replace existing quality with part file: {item.title[:60]}")
                await db.log_failed_file(
                    title=item.title,
                    filename=item.title,
                    reason="Part file detected — replacement blocked, needs manual review",
                    metadata_info=item.metadata_info,
                    parse_data={
                        "title": item.title,
                        "original_filename": item.title,
                        "channel": item.channel,
                        "msg_id": item.msg_id,
                        "file_unique_id": item.file_unique_id,
                    },
                )
                action, old_version = 'SKIP', 'part file — replacement blocked'
            
            async with db_lock:
                if action == 'ADD':
                    updated_id = await db.insert_media(
                        item.metadata_info, 
                        hash=item.hash, 
                        channel=item.channel, 
                        msg_id=item.msg_id, 
                        size=item.size, 
                        name=item.title,
                        codec=item.metadata_info.get('codec'),
                        file_unique_id=item.file_unique_id,
                        size_bytes=item.size_bytes,
                        duration=item.duration
                    )
                    if updated_id:
                        queue_stats["processed"] += 1
                        LOGGER.info(f"✅ ADDED: {item.metadata_info.get('quality')} - {item.title[:50]}")
                    else:
                        queue_stats["failed"] += 1
                
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
                    season = item.metadata_info.get('season_number')
                    episode = item.metadata_info.get('episode_number')
                    await db.pull_quality(media_type, tmdb_id, current_quality, season, episode)
                    
                    # Insert new version
                    updated_id = await db.insert_media(
                        item.metadata_info, 
                        hash=item.hash, 
                        channel=item.channel, 
                        msg_id=item.msg_id, 
                        size=item.size, 
                        name=item.title,
                        codec=item.metadata_info.get('codec'),
                        file_unique_id=item.file_unique_id,
                        size_bytes=item.size_bytes,
                        duration=item.duration
                    )
                    if updated_id:
                        queue_stats["processed"] += 1
                        queue_stats["replaced"] += 1
                        LOGGER.info(f"🔄 REPLACED: {item.metadata_info.get('quality')} - {item.title[:50]}")
                    else:
                        queue_stats["failed"] += 1
                
                else:  # SKIP
                    queue_stats["skipped"] += 1
                    LOGGER.info(f"⏭️ SKIPPED: {item.title[:50]} - {old_version}")
            
            # Extra delay after successful processing
            await asyncio.sleep(1)
            
            # Track processing time for metrics
            processing_time = time.time() - processing_start
            total_processing_time += processing_time
            processing_count += 1
                    
        except Exception as e:
            queue_stats["failed"] += 1
            LOGGER.error(f"❌ Error processing: {e}")
        finally:
            cache_key = (item.channel, item.msg_id)
            queued_files.pop(cache_key, None)
            file_queue.task_done()
            LOGGER.info(f"📊 Queue stats - Processed: {queue_stats['processed']}, Failed: {queue_stats['failed']}, Skipped: {queue_stats['skipped']}")


# ============= MONGODB QUEUE PROCESSOR =============

async def process_mongodb_queue(worker_id: int = 1, channel_id: int = None):
    """Worker loop over the MongoDB queue.

    Each worker claims exactly one item at a time, honours the persisted pause
    flag and the global FloodWait gate, and round-robins across channels via
    ``db.claim_next_item``. Live uploads sort ahead of rescan backfill.
    """
    global processed_count_in_batch

    last_housekeeping = 0.0
    LOGGER.info(f"🗄️ MongoDB queue worker {worker_id} started")

    while True:
        try:
            if await db.is_queue_paused():
                await asyncio.sleep(QueueConfig.QUEUE_STATE_POLL_SECONDS)
                continue

            now = time.time()
            if worker_id == 1 and now - last_housekeeping > 60:
                await db.reset_failed_messages(channel_id)
                last_housekeeping = now

            await _wait_floodwait_gate()

            item = await db.claim_next_item(
                channel_id=channel_id, claimed_by=f"worker:{worker_id}"
            )
            if not item:
                await asyncio.sleep(QueueConfig.QUEUE_STATE_POLL_SECONDS)
                continue

            await asyncio.sleep(QueueConfig.FILE_QUEUE_DELAY)

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
                probe_result=item.get('probe_result'),
                file_unique_id=item.get('file_unique_id'),
                size_bytes=item.get('size_bytes'),
                duration=item.get('duration'),
                priority=item.get('priority'),
                needs_review=item.get('needs_review', False),
            )

            LOGGER.info(
                f"[Queue worker {worker_id}] Processing "
                f"({queue_item.retry_count + 1}/{QueueConfig.MAX_RETRY_COUNT}): "
                f"{queue_item.title[:50]}..."
            )

            try:
                validation_passed = await validate_file_before_insert(queue_item)
            except FloodWait as e:
                _hold_floodwait(e.value)
                await db.mark_failed(str(item['_id']), f"FloodWait: {e.value}s")
                queue_stats["retried"] += 1
                continue
            except Exception as e:
                LOGGER.error(f"Error during validation: {e}")
                validation_passed = False

            if not validation_passed:
                await db.mark_failed(str(item['_id']), "Validation failed")
                if queue_item.retry_count + 1 >= QueueConfig.MAX_RETRY_COUNT:
                    queue_stats["failed"] += 1
                    LOGGER.error(f"❌ Max retries reached: {queue_item.title[:50]}")
                else:
                    queue_stats["retried"] += 1
                    LOGGER.warning("⚠️ Validation failed, will retry later")
                continue

            # Smart replacement decision
            action, old_version = await smart_replace_or_skip(
                queue_item.metadata_info, queue_item.probe_result, queue_item.size
            )

            # A "Part N" upload must never overwrite an existing quality —
            # a half-file replacing a complete one is silent data loss.
            if action == 'REPLACE' and queue_item.needs_review:
                LOGGER.warning(
                    f"🛡️ Refusing to replace existing quality with part file: {queue_item.title[:60]}"
                )
                await db.log_failed_file(
                    title=queue_item.title,
                    filename=queue_item.title,
                    reason="Part file detected — replacement blocked, needs manual review",
                    metadata_info=queue_item.metadata_info,
                    parse_data={
                        "title": queue_item.title,
                        "original_filename": queue_item.title,
                        "channel": queue_item.channel,
                        "msg_id": queue_item.msg_id,
                        "file_unique_id": queue_item.file_unique_id,
                    },
                )
                action, old_version = 'SKIP', 'part file — replacement blocked'

            # Deep probe only on an otherwise-tied same-quality comparison.
            if (
                action == 'SKIP'
                and old_version == 'Matching metadata'
                and MediaProbeConfig.DEEP_PROBE_ON_TIE
                and not (queue_item.probe_result or {}).get('audio_tracks')
            ):
                deep_probe = await _deep_probe_queue_item(queue_item)
                if deep_probe:
                    queue_item.probe_result = deep_probe
                    action, old_version = await smart_replace_or_skip(
                        queue_item.metadata_info, queue_item.probe_result, queue_item.size
                    )
                    if action == 'REPLACE' and queue_item.needs_review:
                        action, old_version = 'SKIP', 'part file — replacement blocked'

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
                            codec=queue_item.metadata_info.get('codec'),
                            file_unique_id=queue_item.file_unique_id,
                            size_bytes=queue_item.size_bytes,
                            duration=queue_item.duration,
                        )
                        if updated_id:
                            queue_stats["processed"] += 1
                            await db.mark_completed(str(item['_id']), item['channel_id'], item['message_id'])
                            await db.update_checkpoint(item['channel_id'], item['message_id'])
                            LOGGER.info(f"✅ ADDED: {queue_item.metadata_info.get('quality')} - {queue_item.title[:50]}")
                        else:
                            await db.mark_failed(str(item['_id']), "Insert failed")
                            queue_stats["failed"] += 1

                    elif action == 'REPLACE':
                        await db.backup_replaced_version(
                            queue_item.metadata_info,
                            old_version,
                            reason="Replaced with better version",
                        )

                        media_type = queue_item.metadata_info.get('media_type')
                        tmdb_id = queue_item.metadata_info.get('tmdb_id')
                        current_quality = queue_item.metadata_info.get('quality', '720p')
                        season = queue_item.metadata_info.get('season_number')
                        episode = queue_item.metadata_info.get('episode_number')
                        await db.pull_quality(media_type, tmdb_id, current_quality, season, episode)

                        updated_id = await db.insert_media(
                            queue_item.metadata_info,
                            hash=queue_item.hash,
                            channel=queue_item.channel,
                            msg_id=queue_item.msg_id,
                            size=queue_item.size,
                            name=queue_item.title,
                            codec=queue_item.metadata_info.get('codec'),
                            file_unique_id=queue_item.file_unique_id,
                            size_bytes=queue_item.size_bytes,
                            duration=queue_item.duration,
                        )
                        if updated_id:
                            queue_stats["replaced"] += 1
                            queue_stats["processed"] += 1
                            await db.mark_completed(str(item['_id']), item['channel_id'], item['message_id'])
                            await db.update_checkpoint(item['channel_id'], item['message_id'])
                            LOGGER.info(f"🔄 REPLACED: {queue_item.metadata_info.get('quality')} - {queue_item.title[:50]}")
                        else:
                            await db.mark_failed(str(item['_id']), "Insert failed")
                            queue_stats["failed"] += 1

                    elif action == 'SKIP':
                        queue_stats["skipped"] += 1
                        await db.mark_completed(str(item['_id']), item['channel_id'], item['message_id'])
                        LOGGER.info(f"⏭️ SKIPPED: {old_version if old_version else 'existing version'}")
            except FloodWait as e:
                _hold_floodwait(e.value)
                await db.mark_failed(str(item['_id']), f"FloodWait: {e.value}s")
                queue_stats["retried"] += 1
                continue
            except Exception as e:
                LOGGER.error(f"Error during DB operation: {e}")
                await db.mark_failed(str(item['_id']), str(e))
                queue_stats["failed"] += 1
                continue

            # Batch gap (global across workers)
            async with batch_lock:
                processed_count_in_batch += 1
                if processed_count_in_batch >= QueueConfig.BATCH_SIZE:
                    LOGGER.info(
                        f"📦 Processed {QueueConfig.BATCH_SIZE} files. "
                        f"Taking {QueueConfig.BATCH_GAP_SECONDS}s gap..."
                    )
                    await asyncio.sleep(QueueConfig.BATCH_GAP_SECONDS)
                    processed_count_in_batch = 0

        except asyncio.CancelledError:
            LOGGER.info(f"🗄️ MongoDB queue worker {worker_id} stopped")
            raise
        except Exception as e:
            LOGGER.error(f"MongoDB queue worker {worker_id} error: {e}")
            await asyncio.sleep(10)


# =============================================================================
# STARTUP FUNCTION - Initialize background tasks
# =============================================================================
async def _index_queue_file(message: Message, channel: str) -> bool:
    """Queue callback used by the full-channel indexer."""
    return await parse_and_queue_file(
        message, channel, is_rescan=True, allow_probe=IndexConfig.ENABLE_PROBE
    )


async def start_background_tasks():
    """Initialize all background tasks after bot starts."""
    global is_queue_paused

    if QueueConfig.ENABLE_MONGODB_QUEUE:
        # Exactly one queue engine runs: N MongoDB consumers, each claiming a
        # single item. Starting the in-memory workers too would double-process.
        try:
            is_queue_paused = await db.is_queue_paused(max_age=0)
        except Exception:
            is_queue_paused = False
        for w in range(max(1, QueueConfig.QUEUE_WORKERS)):
            create_task(process_mongodb_queue(w + 1))
        LOGGER.info(
            f"🗄️ MongoDB queue: {max(1, QueueConfig.QUEUE_WORKERS)} worker(s) started"
            f"{' (paused)' if is_queue_paused else ''}"
        )
        if QueueConfig.RESUME_ON_START:
            LOGGER.info("🔄 Resuming queue processing from MongoDB...")
    else:
        for w in range(max(1, QueueConfig.QUEUE_WORKERS)):
            create_task(process_file(w + 1))
        LOGGER.info(f"👷 Started {max(1, QueueConfig.QUEUE_WORKERS)} in-memory queue workers")

    # Resume interrupted full-channel scans
    create_task(resume_index_scans(StreamBot, _index_queue_file))
    
    # Start TMDB cache cleanup task
    create_task(cleanup_tmdb_cache())
    LOGGER.info("🧹 TMDB cache cleanup task started")
    LOGGER.info("📂 Queue state loaded")


# NOTE: Call start_background_tasks() in your bot's startup function
# Example: await start_background_tasks()


# =============================================================================
# SHARED FILE PARSING LOGIC (used by both live handler and rescan)
# =============================================================================
async def parse_and_queue_file(message: Message, channel: str, is_rescan: bool = False, allow_probe: bool = True,
                               notify: bool = True):
    """Parse a message's video/document and add to queue. Returns True if queued/skipped, False if error."""
    try:
        # Relaxed mime-type check: accept video/* OR common video extensions
        file = message.video or message.document
        if not file:
            return False

        # Check if it's a video by mime-type or extension
        is_video = False
        if message.video:
            is_video = True
        elif message.document:
            if message.document.mime_type and message.document.mime_type.startswith("video/"):
                is_video = True
            elif message.document.file_name:
                # Check common video extensions
                ext = message.document.file_name.lower().split('.')[-1]
                is_video = ext in ['mkv', 'mp4', 'avi', 'mov', 'ts', 'wmv', 'm4v', 'webm', 'flv', '3gp']

        if not is_video:
            return False

        # ── Title extraction: caption (if USE_CAPTION) then filename fallback ──
        # We pick the best candidate line from the caption and run it through
        # the same clean_filename() pipeline used for filenames, so @channels,
        # technical tags, uploader prefixes, etc. are all stripped consistently.
        raw_title = None
        caption_langs = []   # languages extracted from caption info lines (🔊 etc.)

        if message.caption and Telegram.USE_CAPTION:
            caption_lines = message.caption.split("\n")

            # ── Scan ALL lines for language info (emoji info lines like "🔊 Telugu, Hindi") ──
            lang_line_pattern = re.compile(
                r'(?:Telugu|Tamil|Malayalam|Kannada|Hindi|English|Bengali|Marathi|Punjabi|Urdu)',
                re.IGNORECASE
            )
            lang_name_map = {
                'telugu': 'te', 'tamil': 'ta', 'malayalam': 'ml', 'kannada': 'kn',
                'hindi': 'hi', 'english': 'en', 'bengali': 'bn', 'marathi': 'mr',
                'punjabi': 'pa', 'urdu': 'ur',
            }
            for line in caption_lines:
                stripped = line.strip()
                # Target lines that are clearly language-info lines (contain 🔊 or multiple lang names)
                found = lang_line_pattern.findall(stripped)
                if len(found) >= 2 or (found and any(c in stripped for c in ['🔊', '🔉', '🎙', 'Audio', 'Lang'])):
                    for lang_name in found:
                        code = lang_name_map.get(lang_name.lower())
                        if code and code not in caption_langs:
                            caption_langs.append(code)

            # Pass 0 – skip [@ChannelName] / channel header lines at the top
            # e.g. "[@CloudMovieX]" or "[ChannelName]"
            non_header_lines = [
                l for l in caption_lines
                if not re.match(r'^\s*\[@?[A-Za-z0-9_]+\]\s*$', l.strip())
            ]

            # Pass 1 – prefer a line that already has a video extension
            for line in non_header_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if re.search(r'\.(mkv|mp4|avi|mov|ts|wmv|m4v|webm|flv|3gp)\b', stripped, re.IGNORECASE):
                    raw_title = stripped
                    break

            # Pass 2 – line with year or quality marker (movie/series identifier)
            if not raw_title:
                for line in non_header_lines:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if re.search(r'\(\d{4}\)|\b(1080p|720p|480p|4K|2160p|1440p|2K|4k)\b', stripped, re.IGNORECASE):
                        raw_title = stripped
                        break

            # Pass 3 – first substantial non-hashtag, non-URL, non-emoji-info line
            if not raw_title:
                for line in non_header_lines:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Skip hashtag-only, pure URLs, emoji info lines, very short tokens
                    if (len(stripped) > 5
                            and not stripped.startswith('#')
                            and not stripped.startswith('http')
                            and not re.match(r'^[\U00010000-\U0010ffff🔊💬⏳]', stripped)):
                        raw_title = stripped
                        break

            if raw_title:
                LOGGER.info(f"Caption candidate: {raw_title[:100]}")
            if caption_langs:
                LOGGER.info(f"Caption languages detected: {caption_langs}")

        # Fall back to the actual filename if caption gave nothing
        if not raw_title:
            raw_title = file.file_name or file.file_id
            LOGGER.info(f"Using filename as title source: {raw_title[:100] if raw_title else 'none'}")

        # Ensure a video extension is present (inherit from real filename if missing)
        if raw_title and not re.search(r'\.(mkv|mp4|avi|mov|ts|wmv|m4v|webm|flv|3gp)\b', raw_title, re.IGNORECASE):
            if file.file_name:
                ext_match = re.search(r'\.[a-z0-9]{3,4}$', file.file_name, re.IGNORECASE)
                if ext_match:
                    raw_title = raw_title + ext_match.group()

        title = raw_title or file.file_id


        msg_id = message.id
        hash_val = file.file_unique_id[:6] if file.file_unique_id else ""
        size = get_readable_file_size(file.file_size)
        channel_int = int(channel)
        file_unique_id = file.file_unique_id or None
        size_bytes = file.file_size or None
        duration = getattr(file, "duration", None) or None

        # ========== UPDATED: Clean filename and extract languages ==========
        original_filename = file.file_name or title
        cleaned_title, detected_langs = clean_filename(title)
        title = cleaned_title
        
        # Merge languages detected from caption emoji/info lines
        if caption_langs:
            for lang in caption_langs:
                if lang not in detected_langs:
                    detected_langs.append(lang)

        LOGGER.info(f"Original title: {title[:100]}...")
        LOGGER.info(f"Cleaned title: {cleaned_title[:100]}...")
        if detected_langs:
            LOGGER.info(f"Detected languages: {detected_langs}")

        # Keep the cheap parse context so failures can be re-processed later
        # without another Telegram download.
        parse_data = {
            "title": title,
            "original_filename": original_filename,
            "caption": (message.caption or "")[:800] or None,
            "languages": detected_langs,
            "file_unique_id": file_unique_id,
            "size_bytes": size_bytes,
            "duration": duration,
            "channel": channel,
            "msg_id": msg_id,
        }

        if QueueConfig.ENABLE_PACK_DETECTION:
            pack_reason = detect_pack_or_multipart(original_filename, title, message.caption)
            if pack_reason:
                LOGGER.warning(f"📦 Skipping {pack_reason}: {title[:80]}")
                await db.log_failed_file(
                    title=title,
                    filename=file.file_name or file.file_id,
                    reason=f"Pack/multi-part detected ({pack_reason}) — needs manual review",
                    metadata_info={"title": title, "channel": channel, "msg_id": msg_id},
                    parse_data=parse_data,
                )
                queue_stats["skipped"] += 1
                if notify and not is_rescan:
                    await message.reply_text(
                        f"> Not added automatically — {pack_reason} needs manual review"
                    )
                return False

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
            # Log failure to DB
            await db.log_failed_file(
                title=title,
                filename=file.file_name or file.file_id,
                reason="Metadata fetch failed - no metadata found from any source",
                metadata_info={"title": title, "channel": channel, "msg_id": msg_id},
                parse_data=parse_data
            )
            if notify and not is_rescan:
                await message.reply_text("> Not added — check log")
            return False
        
        # ========== NEW: Media Probing with ffmpeg ==========
        probe_result = None
        try:
            if MediaProbeConfig.ENABLE_MEDIA_PROBE and allow_probe:
                LOGGER.info(f"🔍 Starting media probe for: {title[:50]}...")
                
                # Update progress message if in private chat
                progress_msg = None
                if message.chat.type == "private":
                    progress_msg = await message.reply_text("🔍 Analyzing media with ffmpeg...")
                
                # Stream and probe the media (quick probe by default; a deep
                # probe only runs later if a same-quality comparison ties).
                probe_result = await stream_and_probe(
                    file, msg_id,
                    max_size_mb=MediaProbeConfig.MAX_PROBE_SIZE_MB,
                    client=getattr(message, '_client', None),
                    max_steps=MediaProbeConfig.QUICK_STEPS,
                )
                
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

        # Telegram's file_unique_id is stable across channels/re-uploads.
        # (Mongo mode re-checks this inside add_to_queue.)
        if (
            not QueueConfig.ENABLE_MONGODB_QUEUE
            and file_unique_id
            and await db.is_file_unique_id_known(file_unique_id)
        ):
            queued_files.pop(cache_key, None)
            queue_stats["skipped"] += 1
            LOGGER.info(f"File {file_unique_id} already in library, skipping: {title[:60]}")
            return True

        # Create queue item with enhanced structure including probe results
        needs_review = detect_part_file(original_filename, title, message.caption)
        if needs_review:
            LOGGER.warning(f"📝 Part-file detected (will not replace existing quality): {title[:80]}")
        queue_item = QueueItem(
            metadata_info=metadata_info,
            hash_val=hash_val,
            channel=channel_int,
            msg_id=msg_id,
            size=size,
            title=title,
            source="live" if not is_rescan else "rescan",
            retry_count=0,
            probe_result=probe_result,  # Store ffmpeg probe results
            file_unique_id=file_unique_id,
            size_bytes=size_bytes,
            duration=duration,
            priority=QueueConfig.PRIORITY_RESCAN if is_rescan else QueueConfig.PRIORITY_LIVE,
            needs_review=needs_review,
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
                probe_result=probe_result,
                file_unique_id=file_unique_id,
                size_bytes=size_bytes,
                duration=duration,
                priority=queue_item.priority,
                needs_review=needs_review,
            )
            if added_to_mongodb:
                # MongoDB's unique queue index is the dedup source of truth in
                # this mode; keeping every entry in memory leaks on lakhs of files.
                queued_files.pop(cache_key, None)
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
        # Do not leave a stale in-memory dedup marker behind if queueing blew
        # up (e.g. DB write failure); otherwise this message is skipped forever.
        try:
            queued_files.pop((int(channel), message.id), None)
        except Exception:
            pass
        return False


# =============================================================================
# QUEUE PERSISTENCE FUNCTIONS
# =============================================================================
async def queue_message_links(raw_links: str, preview: bool = False) -> dict:
    """Queue explicit t.me post links through the normal ingestion pipeline.

    Used by the admin panel bulk-add tool. Only channels listed in
    AUTH_CHANNEL are accepted; results are returned per link.
    """
    links = []
    for token in re.split(r"[\s,]+", str(raw_links or "")):
        token = token.strip()
        if token and token not in links:
            links.append(token)
    links = links[:500]

    results = []
    queued = failed = 0
    for link in links:
        try:
            chat, message_id = await resolve_link_target(StreamBot, link)
            if not is_auth_channel(chat):
                raise IndexTargetError("Channel is not listed in AUTH_CHANNEL")
            message = await StreamBot.get_messages(chat.id, message_id)
            if not message or getattr(message, "empty", False):
                raise IndexTargetError("Post not found")

            file = message.video or message.document
            entry = {
                "link": link,
                "channel": clean_channel_id(chat.id),
                "message_id": message_id,
                "file_name": getattr(file, "file_name", None) if file else None,
                "size": get_readable_file_size(file.file_size) if file else None,
                "file_unique_id": getattr(file, "file_unique_id", None) if file else None,
                "caption": (message.caption or "")[:240] or None,
            }

            if preview:
                entry["ok"] = bool(file)
                if not file:
                    entry["error"] = "No video/document in this post"
            else:
                if not file:
                    raise IndexTargetError("No video/document in this post")
                ok = await parse_and_queue_file(
                    message,
                    clean_channel_id(chat.id),
                    is_rescan=False,
                    allow_probe=True,
                    notify=False,
                )
                entry["ok"] = bool(ok)
                entry["queued"] = bool(ok)

            results.append(entry)
            if entry.get("ok"):
                queued += 1
            else:
                failed += 1
        except FloodWait as e:
            _hold_floodwait(e.value)
            results.append({"link": link, "ok": False, "error": f"FloodWait {e.value}s"})
            failed += 1
        except Exception as e:
            results.append({"link": link, "ok": False, "error": str(e)})
            failed += 1

    return {
        "preview": preview,
        "total": len(results),
        "queued": queued,
        "failed": failed,
        "results": results,
    }


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

async def _background_parse_and_queue(message: Message, channel: str):
    """Background task: parse and queue a file without blocking the event loop."""
    try:
        await parse_and_queue_file(message, channel, is_rescan=False)
    except FloodWait as e:
        LOGGER.warning(f"⚠️ FloodWait in background parser: sleeping for {e.value}s")
        await asleep(e.value)
    except Exception as e:
        LOGGER.error(f"Error in background parse_and_queue: {e}")


@StreamBot.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(bot: Client, message: Message):
    """Live channel handler — NEVER blocks the event loop.
    
    All heavy work (TMDB lookup, ffmpeg probe, DB insert) is offloaded to a
    background asyncio task so that even lakhs of incoming files cannot stall
    MongoDB connections or the FastAPI server.
    """
    if str(message.chat.id) in Telegram.AUTH_CHANNEL:
        channel_id = message.chat.id
        now = time.time()

        # ── Rate-limit tracking (non-blocking) ──────────────────────────────
        # Clean timestamps older than the rate-limit window.
        channel_rate_limit[channel_id] = [
            t for t in channel_rate_limit[channel_id]
            if now - t < QueueConfig.RATE_LIMIT_SECONDS
        ]

        if len(channel_rate_limit[channel_id]) >= QueueConfig.MAX_MESSAGES_PER_MINUTE:
            # DO NOT sleep — just log and let the MongoDB queue absorb the burst.
            # The queue processor applies its own pacing; sleeping here would
            # block the entire event loop and disconnect MongoDB.
            LOGGER.warning(
                f"⚠️ Rate limit hit for channel {channel_id} "
                f"({len(channel_rate_limit[channel_id])}/{QueueConfig.MAX_MESSAGES_PER_MINUTE} per {QueueConfig.RATE_LIMIT_SECONDS}s) "
                f"— queuing directly without delay"
            )
            channel_rate_limit[channel_id] = []  # reset window, keep accepting

        channel_rate_limit[channel_id].append(now)

        # ── Fire-and-forget: never await the heavy work inline ───────────────
        channel = str(message.chat.id).replace("-100", "")
        create_task(_background_parse_and_queue(message, channel))
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
        paused = await db.is_queue_paused()
        state = "⏸️ Paused" if paused else "▶️ Running"
        mongodb_stats = f"""
🗄️ <b>MongoDB Queue:</b> {state}
• ⏳ Pending: {mongo_queue_stats['pending']}
• ❌ Failed: {mongo_queue_stats['failed']}
• 🔧 Processing: {mongo_queue_stats['processing']}
• 🔄 Total Retries: {mongo_queue_stats['total_retries']}
• 👷 Workers: {QueueConfig.QUEUE_WORKERS}
"""
        engine_line = ""
    else:
        engine_line = (
            f"⏳ <b>In-Memory Queue:</b> {pending} files\n"
            f"🔧 <b>Workers:</b> {QueueConfig.QUEUE_WORKERS} (sequential)"
        )
    
    text = f"""
📊 <b>QUEUE SYSTEM STATUS</b>
━━━━━━━━━━━━━━━━━━━━━

{engine_line}
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
        summary = await db.get_stats_summary()
        movie_count = summary["movies"]
        tv_count = summary["tv_shows"]
        total_movie_files = summary["total_movie_files"]
        total_tv_files = summary["total_tv_files"]
        views_count = summary["views"]
        replaced_count = summary["replaced"]
        failed_count = summary["failed"]

        # Get recent activity
        from datetime import datetime, timedelta
        yesterday = datetime.utcnow() - timedelta(days=1)
        recent_views = await db.views_collection.count_documents({'date': {'$gte': yesterday.strftime('%Y-%m-%d')}})

        text = f"""
📊 <b>DATABASE STATISTICS</b>
━━━━━━━━━━━━━━━━━━━━━

📁 <b>Media Collections ({summary['clusters']} cluster(s)):</b>
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
• Database: {db.db_name} ({summary['clusters']} cluster(s))
• TMDB API: {'✅ Configured' if Telegram.TMDB_API else '❌ Not configured'}
• IMDB API: {'✅ Configured' if Telegram.IMDB_API else '❌ Not configured'}

━━━━━━━━━━━━━━━━━━━━━
<i>📡 Use /queue for processing status</i>
        """
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    
    except Exception as e:
        LOGGER.error(f"Error in /stats command: {e}")
        await message.reply_text(f"❌ Error retrieving statistics: {e}")


@StreamBot.on_message(filters.command("storage") & filters.private & CustomFilters.admin)
async def storage_status(bot: Client, message: Message):
    """Per-cluster quota usage and ops collection sizes."""
    try:
        report = await db.get_storage_report()
        lines = ["💾 <b>STORAGE REPORT</b>", "━━━━━━━━━━━━━━━━━━━━━"]
        for c in report.get("clusters", []):
            if str(c.get("status", "")).startswith("error"):
                lines.append(f"• Cluster {c.get('cluster')}: ❌ {c.get('status')}")
                continue
            icon = "⚠️" if c.get("status") == "alert" else "✅"
            lines.append(
                f"{icon} <b>Cluster {c.get('cluster')}</b>: "
                f"{c.get('used_mb')} / {c.get('quota_mb')} MB ({c.get('used_percent')}%)\n"
                f"   🎬 {c.get('movies')} movies · 📺 {c.get('tv')} shows · 📦 {c.get('objects')} docs\n"
                f"   data {c.get('data_mb')} MB + index {c.get('index_mb')} MB"
            )
        ops = report.get("ops") or {}
        if ops:
            lines.append("\n🗄️ <b>Ops:</b> " + " · ".join(f"{k} {v}" for k, v in ops.items()))
        if report.get("alerts"):
            lines.append("\n🚨 <b>Alerts:</b>\n" + "\n".join(f"• {a}" for a in report["alerts"]))
        lines.append("\n<i>Quota per cluster: CLUSTER_QUOTA_MB (default 512 MB).</i>")
        await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        LOGGER.error(f"Error in /storage command: {e}")
        await message.reply_text(f"❌ Error retrieving storage report: {e}")


@StreamBot.on_message(filters.command("mergeduplicates") & filters.private & CustomFilters.admin)
async def merge_duplicates(bot: Client, message: Message):
    """Dry-run or apply a merge of media docs duplicated across clusters."""
    try:
        confirm = len(message.command) > 1 and message.command[1].lower() in ("confirm", "yes", "apply")
        summary = await db.merge_duplicate_media(apply=confirm)
        if confirm:
            await message.reply_text(
                "✅ <b>Merge complete</b>\n\n"
                f"• Duplicate titles: {summary['duplicates']}\n"
                f"• Merged: {summary['merged']}\n"
                f"• Deleted extra docs: {summary['deleted']}\n"
                f"• Errors: {summary['errors']}",
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await message.reply_text(
                "🧪 <b>Duplicate scan (dry run)</b>\n\n"
                f"• Titles on >1 cluster: {summary['duplicates']}\n"
                f"• Mergeable: {summary['merged']}\n"
                f"• Errors: {summary['errors']}\n\n"
                "Send <code>/mergeduplicates confirm</code> to write the merged rows.",
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception as e:
        LOGGER.error(f"Error in /mergeduplicates command: {e}")
        await message.reply_text(f"❌ Merge failed: {e}")


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

🔊 <b>Status & Statistics:</b>
• <code>/status</code> - Show bot status and health
• <code>/stats</code> - Show database statistics
• <code>/storage</code> - Per-cluster storage quota report
• <code>/queue</code> - Show queue processing status
• <code>/mergeduplicates</code> - Scan/merge cross-cluster duplicate titles

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
• Send a t.me post link in DM - Index that AUTH_CHANNEL's entire history
• <code>/indexstop</code> - Stop the running full-channel index scan

📝 <b>Queue Management:</b>
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
    """Pause the queue processors (persisted across restarts)."""
    global is_queue_paused
    is_queue_paused = True
    if QueueConfig.ENABLE_MONGODB_QUEUE:
        await db.set_queue_paused(True)
    await message.reply_text("⏸️ <b>Queue Processing Paused</b>\n\nUse /startqueue or /resumequeue to resume.", parse_mode=enums.ParseMode.HTML)


@StreamBot.on_message(filters.command(["startqueue", "resumequeue", "startq"]) & filters.private & CustomFilters.admin)
async def resume_queue(bot: Client, message: Message):
    """Resume the queue processors."""
    global is_queue_paused
    is_queue_paused = False
    if QueueConfig.ENABLE_MONGODB_QUEUE:
        await db.set_queue_paused(False)
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

    if is_scan_running():
        await message.reply_text(
            "A full-channel index scan is running. Stop it with /indexstop first."
        )
        return

    async with rescan_lock:
        if rescan_running:
            await message.reply_text("⚠️ A rescan is already in progress. Use /queue to check status.")
            return
        rescan_running = True

    try:
        # Parse channel index from command (e.g., rescan0 -> index 0)
        command = message.text.split()[0]  # /rescan0, /rescan1, etc.
        channel_index = int(command.replace("/rescan", ""))

        # Parse optional limit argument
        args = message.text.split()
        limit = 0
        if len(args) >= 2:
            try:
                limit = int(args[1])
            except ValueError:
                pass

        if not Telegram.AUTH_CHANNEL:
            await message.reply_text("❌ No AUTH_CHANNEL configured.")
            return

        if channel_index < 0 or channel_index >= len(Telegram.AUTH_CHANNEL):
            await message.reply_text(
                f"❌ Invalid channel index. Use /rescanhelp to see available channels.",
                parse_mode=enums.ParseMode.HTML
            )
            return

        channel_id_str = Telegram.AUTH_CHANNEL[channel_index]
        channel_id = int(channel_id_str)

        status_msg = await message.reply_text(
            f"🔍 <b>Rescan started</b>\n"
            f"Channel: <code>{channel_id}</code> (Index: {channel_index})\n"
            f"Checking all messages one by one...",
            parse_mode=enums.ParseMode.HTML
        )

        async def progress_cb(stats: dict):
            try:
                await status_msg.edit_text(
                    f"🔍 <b>Rescanning...</b>\n"
                    f"Checked: <code>{stats['checked']}</code>\n"
                    f"📥 New queued: <code>{stats['new']}</code>\n"
                    f"✅ Already in DB: <code>{stats['existing']}</code>\n"
                    f"❌ Errors: <code>{stats['errors']}</code>",
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                pass

        result = await perform_rescan(channel_index, limit, progress_cb)

        await status_msg.edit_text(
            f"✅ <b>Rescan Complete</b>\n\n"
            f"📁 Total checked: <code>{result['checked']}</code>\n"
            f"📥 New files queued: <code>{result['new']}</code>\n"
            f"✅ Already in DB: <code>{result['existing']}</code>\n"
            f"❌ Errors: <code>{result['errors']}</code>\n\n"
            f"Use /queue to monitor processing.",
            parse_mode=enums.ParseMode.HTML
        )

    except Exception as e:
        LOGGER.error(f"Rescan failed: {e}")
        await message.reply_text(f"❌ Rescan failed: {e}")
    finally:
        async with rescan_lock:
            rescan_running = False


async def perform_rescan(channel_index: int, limit: int = 0, progress_cb=None) -> dict:
    """
    Scan an AUTH_CHANNEL for video files and queue any not already in the DB.
    Standalone function so both the /rescan bot command and the admin API can
    trigger it. Returns {'checked','new','existing','errors'}.
    """
    if not Telegram.AUTH_CHANNEL:
        raise ValueError("No AUTH_CHANNEL configured")
    if channel_index < 0 or channel_index >= len(Telegram.AUTH_CHANNEL):
        raise ValueError(f"Invalid channel index {channel_index}")

    channel_id_str = Telegram.AUTH_CHANNEL[channel_index]
    channel_id = int(channel_id_str)
    channel_clean = channel_id_str.replace("-100", "")

    stats = {"checked": 0, "new": 0, "existing": 0, "errors": 0}

    async for msg in StreamBot.get_chat_history(channel_id, limit=limit if limit > 0 else None):
        stats["checked"] += 1

        # Only process video/document messages with relaxed mime-type check
        file = msg.video or msg.document
        if not file:
            continue

        is_video = False
        if msg.video:
            is_video = True
        elif msg.document:
            if msg.document.mime_type and msg.document.mime_type.startswith("video/"):
                is_video = True
            elif msg.document.file_name:
                ext = msg.document.file_name.lower().split('.')[-1]
                is_video = ext in ['mkv', 'mp4', 'avi', 'mov', 'ts', 'wmv', 'm4v', 'webm', 'flv', '3gp']

        if not is_video:
            continue

        try:
            hash_val = file.file_unique_id[:6] if file.file_unique_id else ""
            exists = await db.is_file_exists(int(channel_clean), msg.id, hash_val)
            if exists:
                stats["existing"] += 1
                continue

            success = await parse_and_queue_file(msg, channel_clean, is_rescan=True)
            if success:
                stats["new"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            LOGGER.error(f"Rescan error on msg {msg.id}: {e}")
            stats["errors"] += 1

        # Progress callback every 50 messages
        if progress_cb and stats["checked"] % 50 == 0:
            try:
                await progress_cb(stats)
            except Exception:
                pass

        # Small delay to avoid flooding
        if stats["checked"] % 10 == 0:
            await asleep(0.5)

    return stats


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
    text += "⚠️ Only one rescan can run at a time. Use /queue to check status.\n\n"
    text += "💡 To index an entire channel with restart-safe progress, send a t.me post link in DM."

    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


# =============================================================================
# FULL-CHANNEL INDEXING FROM A t.me POST LINK
# =============================================================================
def _user_is_admin(user_id: int) -> bool:
    return user_id == Telegram.OWNER_ID or user_id in Telegram.ADMIN_IDS


@StreamBot.on_message(filters.private & filters.text & CustomFilters.admin & filters.regex(LINK_RE.pattern))
async def index_channel_from_link(bot: Client, message: Message):
    """Send a t.me post link in DM to index that AUTH_CHANNEL's entire history."""
    if not parse_channel_link(message.text):
        return

    if is_scan_running():
        await message.reply_text("A channel scan is already running. Stop it with /indexstop first.")
        return

    if rescan_running:
        await message.reply_text("A /rescan is running. Wait for it to finish first.")
        return

    try:
        chat, linked_message_id = await resolve_link_target(bot, message.text)
    except IndexTargetError as e:
        await message.reply_text(f"Cannot index this link: {e}")
        return

    if not is_auth_channel(chat):
        await message.reply_text(
            "This channel is not listed in AUTH_CHANNEL, so it cannot be indexed.\n"
            "Add its ID to AUTH_CHANNEL and restart the bot."
        )
        return

    previous = await db.get_index_scan(chat.id)
    previous_note = ""
    if previous and previous.get("status") not in (None, "completed"):
        cursor = previous.get("next_offset_id")
        previous_note = (
            f"\n\nPrevious scan: {previous.get('status')} at "
            f"message #{cursor if cursor else 'newest'}. Starting again begins from the "
            "newest message; files already in the database are skipped."
        )

    title = chat.title or str(chat.id)
    await message.reply_text(
        f"<b>Index this entire channel?</b>\n\n"
        f"Channel: {html.escape(title)}\n"
        f"ID: <code>{chat.id}</code>\n"
        f"Linked post: #{linked_message_id}\n\n"
        f"All history is scanned newest to oldest at "
        f"{IndexConfig.SCAN_BATCH_SIZE} messages every {IndexConfig.SCAN_BATCH_DELAY}s. "
        f"Existing files are skipped and the scan continues automatically after a restart."
        f"{previous_note}",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Start indexing", callback_data=f"idx:go:{chat.id}")],
            [InlineKeyboardButton("Cancel", callback_data="idx:no")],
        ]),
    )


@StreamBot.on_callback_query(filters.regex(r"^idx:go:-?\d+$"))
async def index_start_callback(bot: Client, query):
    if not _user_is_admin(query.from_user.id):
        return await query.answer("Admins only.", show_alert=True)
    if rescan_running:
        return await query.answer("A /rescan is running. Try again after it finishes.", show_alert=True)
    if is_scan_running():
        return await query.answer("A channel scan is already running.", show_alert=True)

    try:
        channel_id = int(query.data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return await query.answer("Invalid request.", show_alert=True)

    try:
        chat = await bot.get_chat(channel_id)
    except Exception as e:
        return await query.answer(f"Cannot open channel: {e}", show_alert=True)

    if not is_auth_channel(chat):
        return await query.answer("This channel is not in AUTH_CHANNEL.", show_alert=True)

    await query.answer("Starting...")
    try:
        await request_index_scan(
            bot, chat, query.from_user.id, _index_queue_file, status_message_id=query.message.id
        )
    except IndexBusyError:
        await query.message.reply_text("A channel scan is already running.")
    except Exception as e:
        await query.message.reply_text(f"Could not start indexing: {e}")


@StreamBot.on_callback_query(filters.regex(r"^idx:no$"))
async def index_cancel_callback(bot: Client, query):
    if not _user_is_admin(query.from_user.id):
        return await query.answer("Admins only.", show_alert=True)
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^idx:stop$"))
async def index_stop_callback(bot: Client, query):
    if not _user_is_admin(query.from_user.id):
        return await query.answer("Admins only.", show_alert=True)
    channel_id = await stop_index_scan()
    if channel_id:
        await query.answer("Stopping after the current batch...", show_alert=True)
    else:
        await query.answer("No channel scan is running.", show_alert=True)


@StreamBot.on_message(filters.command("indexstop") & filters.private & CustomFilters.admin)
async def index_stop_command(bot: Client, message: Message):
    channel_id = await stop_index_scan()
    if channel_id:
        await message.reply_text(
            f"Stopping the scan for <code>{channel_id}</code> after the current batch.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text("No channel scan is running.")


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
            
            # Perform database reset (wipes EVERY cluster)
            try:
                deleted_count, collections = await db.reset_all_databases()
                
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





@StreamBot.on_message(filters.command('delete') & filters.private & CustomFilters.admin)
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
        media_doc = await (db.get_movie_doc(tmdb_id) if db_media_type == "movie" else db.get_tv_doc(tmdb_id))
        
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
        
        # Store deletion request per admin so concurrent admins don't overwrite each other
        pending_deletes[message.from_user.id] = delete_request
        
        return await message.reply_text(confirm_text, parse_mode=enums.ParseMode.HTML)
    
    except Exception as e:
        LOGGER.error(f"Error in delete command: {e}")
        await message.reply_text(f"❌ An error occurred: {str(e)}")


# Pending deletion requests keyed by admin user ID
pending_deletes = {}


@StreamBot.on_message(filters.command('cancel') & filters.private & CustomFilters.admin)
async def cancel_delete(bot: Client, message: Message):
    pending_deletes.pop(message.from_user.id, None)
    await message.reply_text("✅ Pending deletion cancelled.")


@StreamBot.on_message(filters.command('confirmdelete') & filters.private & CustomFilters.admin)
async def confirm_delete(bot: Client, message: Message):
    """Confirm and execute the deletion."""
    pending = pending_deletes.pop(message.from_user.id, None)

    if not pending:
        return await message.reply_text("❌ No pending deletion request. Use /delete first.")

    # Check if request is still valid (5 minutes timeout)
    if time.time() - pending['timestamp'] > 300:
        return await message.reply_text("⏰ Deletion request expired. Please run /delete again.")

    try:
        tmdb_id = pending['tmdb_id']
        media_type = pending['media_type']
        title = pending['title']

        LOGGER.info(f"[/confirmdelete] Attempting to delete {media_type} tmdb_id={tmdb_id} ({title})")

        # Verify the document still exists before deleting
        doc = await (db.get_movie_doc(tmdb_id) if media_type == "movie" else db.get_tv_doc(tmdb_id))
        if not doc:
            LOGGER.error(f"[/confirmdelete] Document not found in DB: {media_type} tmdb_id={tmdb_id}")
            await message.reply_text(
                f"❌ Cannot delete: <b>{title}</b> (TMDB ID {tmdb_id}) was not found in the "
                f"<code>{media_type}</code> collection.\n"
                f"It may have already been deleted.",
                parse_mode=enums.ParseMode.HTML
            )
            return

        # Perform deletion
        result = await db.delete_document(media_type, tmdb_id)

        if not result:
            LOGGER.error(f"[/confirmdelete] delete_document returned False for {media_type} tmdb_id={tmdb_id}")
            await message.reply_text(
                f"❌ Delete operation returned no result for <b>{title}</b> (TMDB ID {tmdb_id}).\n"
                f"The document exists in DB but could not be deleted. Check bot logs.",
                parse_mode=enums.ParseMode.HTML
            )
            return

        LOGGER.info(f"✅ Deleted {media_type} tmdb_id={tmdb_id}: {title}")

        # Invalidate caches so frontend reflects the change immediately
        try:
            from Backend.helper.cache import clear_all_caches
            await clear_all_caches()
        except Exception as ce:
            LOGGER.warning(f"Cache clear after delete failed (non-fatal): {ce}")

        await message.reply_text(
            f"✅ <b>Deleted Successfully</b>\n\n"
            f"• {title}\n"
            f"• TMDB ID: {tmdb_id}\n"
            f"• Type: {media_type}\n\n"
            f"<i>Changes will reflect immediately.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    except Exception as e:
        import traceback
        LOGGER.error(f"[/confirmdelete] Error confirming deletion: {e}\n{traceback.format_exc()}")
        await message.reply_text(f"❌ Error: {str(e)}")
