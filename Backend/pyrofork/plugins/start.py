import re
import asyncio
from asyncio import create_task, sleep as asleep, Lock
from urllib.parse import urlparse
from Backend.logger import LOGGER
from Backend import db
from Backend.config import Telegram
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.encrypt import decode_string
from Backend.helper.metadata import metadata
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


# Note: asyncio.create_task calls moved to startup to prevent event loop issues
# They will be started when the bot initializes

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

@StreamBot.on_message(filters.command("user") & filters.private & CustomFilters.owner)
async def create_user(bot: Client, message: Message):
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.reply_text("❌ Usage: `/user <username> <expiry_days>`", parse_mode=ParseMode.MARKDOWN)
            return

        username = args[1]
        expiry_days = int(args[2])

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
            f"✅ User created!\n\n"
            f"👤 Username: `{username}`\n"
            f"🔑 Password: `{password}`\n"
            f"🕒 Expires in: `{expiry_days}` days\n"
            f"📅 Expiry Date: `{expires_at.strftime('%Y-%m-%d %H:%M:%S')} UTC`",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        LOGGER.error(f"Error in /user command: {e}")
        await message.reply_text("❌ An error occurred while creating the user.")

@StreamBot.on_message(filters.command('restart') & filters.private & CustomFilters.owner)
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
        await message.reply_text("**❌ Failed to restart. Check logs for details.**")




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



@StreamBot.on_message(filters.command('log') & filters.private & CustomFilters.owner)
async def start(bot: Client, message: Message):
    try:
        path = ospath.abspath('log.txt')
        return await message.reply_document(
        document=path, quote=True, disable_notification=True
        )
    except Exception as e:
        print(f"An error occurred: {e}")




# (Queue configuration moved to top of file - see above)


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


async def smart_replace_or_skip(metadata_info: dict) -> tuple:
    """
    Smart replacement decision engine.
    
    Rules:
    1. Compare ONLY same quality (720p vs 720p, 1080p vs 1080p)
    2. Priority: Language Count > Quality Score > Rip Source
    3. TV shows: Compare at episode level only
    4. Automatic replacement with safety backups
    5. Detailed logging for all replacements
    
    Returns:
        ('ADD', None) - No existing version of this quality
        ('SKIP', reason) - Existing version is better or equal
        ('REPLACE', old_version_data) - Replace existing with new
    """
    try:
        tmdb_id = metadata_info.get('tmdb_id')
        media_type = metadata_info.get('media_type')
        current_quality = metadata_info.get('quality', '720p')
        current_languages = set(metadata_info.get('languages', []))
        current_rip = metadata_info.get('rip', 'Unknown')
        
        # Rip priority mapping
        rip_priority = {
            'BluRay': 100, '4K': 95, 'Remux': 90,
            'WEB-DL': 80, 'WEBRip': 70, 'HDTV': 60,
            'DVD': 50, 'TVRip': 40, 'HDRip': 30,
            'CAM': 10, 'Unknown': 0
        }
        
        # Quality priority (numeric)
        quality_priority = {
            '4320p': 8, '2160p': 7, '1440p': 6, '1080p': 5,
            '720p': 4, '480p': 3, '360p': 2, '240p': 1
        }
        
        current_quality_score = quality_priority.get(current_quality, 0)
        current_rip_score = rip_priority.get(current_rip, 0)
        current_lang_count = len(current_languages)
        
        # Check movies collection
        if media_type == "movie":
            existing_movie = await db.movie_collection.find_one({"tmdb_id": tmdb_id})
            
            if existing_movie and existing_movie.get('telegram'):
                for existing_quality in existing_movie['telegram']:
                    existing_quality_name = existing_quality.get('quality', '720p')
                    
                    # Only compare same quality
                    if existing_quality_name != current_quality:
                        continue
                    
                    existing_languages = set(existing_quality.get('languages', []))
                    existing_rip = existing_quality.get('rip', 'Unknown')
                    
                    existing_quality_score = quality_priority.get(existing_quality_name, 0)
                    existing_rip_score = rip_priority.get(existing_rip, 0)
                    existing_lang_count = len(existing_languages)
                    
                    # Compare: More languages is better
                    if existing_lang_count > current_lang_count:
                        LOGGER.info(f"📊 Existing file has more languages ({existing_lang_count} vs {current_lang_count}) - Skipping current")
                        return 'SKIP', f"Existing has more languages ({existing_lang_count} vs {current_lang_count})"
                    elif existing_lang_count == current_lang_count:
                        # Same language count, check quality
                        if existing_quality_score > current_quality_score:
                            LOGGER.info(f"📊 Existing file has better quality ({existing_quality_name} vs {current_quality}) - Skipping current")
                            return 'SKIP', f"Existing has better quality"
                        elif existing_quality_score == current_quality_score:
                            # Same quality, check rip source
                            if existing_rip_score > current_rip_score:
                                LOGGER.info(f"📊 Existing file has better rip source ({existing_rip} vs {current_rip}) - Skipping current")
                                return 'SKIP', f"Existing has better rip source"
                            elif existing_rip_score == current_rip_score:
                                # Equal - keep existing (don't replace)
                                LOGGER.info(f"📊 Equal file quality, keeping existing")
                                return 'SKIP', "Equal quality"
                    else:
                        # Current file has more languages - mark existing for replacement
                        LOGGER.info(f"🔄 Current file has more languages ({current_lang_count} vs {existing_lang_count}) - Will replace existing")
                        return 'REPLACE', existing_quality
        
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
                                        
                                        existing_languages = set(existing_quality.get('languages', []))
                                        existing_rip = existing_quality.get('rip', 'Unknown')
                                        
                                        existing_lang_count = len(existing_languages)
                                        existing_rip_score = rip_priority.get(existing_rip, 0)
                                        
                                        if existing_lang_count > current_lang_count:
                                            return 'SKIP', f"Existing has more languages"
                                        elif existing_lang_count == current_lang_count:
                                            if existing_rip_score > current_rip_score:
                                                return 'SKIP', f"Existing has better rip source"
                                            elif existing_rip_score == current_rip_score:
                                                return 'SKIP', "Equal quality"
                                        else:
                                            LOGGER.info(f"🔄 Current file has more languages ({current_lang_count} vs {existing_lang_count}) - Will replace existing")
                                            return 'REPLACE', existing_quality
        
        return 'ADD', None
        
    except Exception as e:
        LOGGER.error(f"Error in smart_replace_or_skip: {e}")
        return 'ADD', None  # Default to add on error


async def process_file(worker_id: int):
    """Slow, thorough worker with batch gaps."""
    global processed_count_in_batch, total_processing_time, processing_count
    
    while True:
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
            
            # Smart replacement decision
            action, old_version = await smart_replace_or_skip(item.metadata_info)
            
            async with db_lock:
                if action == 'ADD':
                    updated_id = await db.insert_media(
                        item.metadata_info, 
                        hash=item.hash, 
                        channel=item.channel, 
                        msg_id=item.msg_id, 
                        size=item.size, 
                        name=item.title
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
                        name=item.title
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

# =============================================================================
# STARTUP FUNCTION - Initialize background tasks
# =============================================================================
async def start_background_tasks():
    """Initialize all background tasks after bot starts."""
    # Start queue workers
    for w in range(QueueConfig.QUEUE_WORKERS):
        create_task(process_file(w + 1))
    LOGGER.info(f"👷 Started {QueueConfig.QUEUE_WORKERS} queue workers")
    
    # Start TMDB cache cleanup task
    create_task(cleanup_tmdb_cache())
    LOGGER.info("🧹 TMDB cache cleanup task started")
    
    # Start queue persistence task
    create_task(save_queue_state())
    LOGGER.info("💾 Queue persistence task started")
    
    # Load previous queue state
    await load_queue_state()
    LOGGER.info("📂 Queue state loaded")


# NOTE: Call start_background_tasks() in your bot's startup function
# Example: await start_background_tasks()


# =============================================================================
# SHARED FILE PARSING LOGIC (used by both live handler and rescan)
# =============================================================================
async def parse_and_queue_file(message: Message, channel: str, is_rescan: bool = False):
    """Parse a message's video/document and add to queue. Returns True if queued/skipped, False if error."""
    try:
        if not (message.video or (message.document and message.document.mime_type and message.document.mime_type.startswith("video/"))):
            return False

        file = message.video or message.document
        if not file:
            return False

        # Extract title from caption or filename
        if message.caption:
            title = None
            for line in message.caption.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                if re.search(r'\.(mkv|mp4|avi|mov)\b', stripped, re.IGNORECASE):
                    title = stripped
                    break
            if not title:
                title = file.file_name or file.file_id
        else:
            title = file.file_name or file.file_id

        msg_id = message.id
        hash_val = file.file_unique_id[:6] if file.file_unique_id else ""
        size = get_readable_file_size(file.file_size)
        channel_int = int(channel)

        # ========== UPDATED: Clean filename and extract languages ==========
        cleaned_title, detected_langs = clean_filename(title)
        title = cleaned_title
        
        LOGGER.info(f"Original title: {title[:100]}...")
        LOGGER.info(f"Cleaned title: {cleaned_title[:100]}...")
        if detected_langs:
            LOGGER.info(f"Detected languages: {detected_langs}")
        
        metadata_info = await metadata(title, file)
        
        # If metadata_info exists and we detected languages, use them
        if metadata_info and detected_langs and not metadata_info.get('languages'):
            metadata_info['languages'] = detected_langs
            
        if metadata_info is None:
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
                            
                    # Add audio tracks from probe
                    if probe_result.get('audio_tracks') and not metadata_info.get('languages'):
                        audio_langs = [t['language'] for t in probe_result['audio_tracks']]
                        metadata_info['languages'] = audio_langs
                        LOGGER.info(f"📢 Added audio languages from probe: {audio_langs}")
                    
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
        
        await file_queue.put(queue_item)
        LOGGER.info(f"📥 Queued ({file_queue.qsize()} pending): {title}")
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


@StreamBot.on_message(filters.command("queue") & filters.private & CustomFilters.owner)
async def queue_status(bot: Client, message: Message):
    pending = file_queue.qsize()
    active = len(queued_files)
    
    # Get TMDB cache stats
    tmdb_stats = get_tmdb_cache_stats()
    
    text = f"""
📊 <b>QUEUE SYSTEM STATUS</b>
━━━━━━━━━━━━━━━━━━━━━

⏳ <b>Queue Size:</b> {pending} files
🔧 <b>Workers:</b> {QueueConfig.QUEUE_WORKERS} (sequential)

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

⏱️ <b>Estimated completion:</b> {calculate_estimated_time(pending)}

💾 <b>TMDB Cache:</b> {tmdb_stats['cache_size']} entries
🎯 <b>Cache hit rate:</b> {tmdb_stats['hit_rate']}%

━━━━━━━━━━━━━━━━━━━━━
<i>🐢 Running in SLOW & THOROUGH mode for maximum reliability</i>
    """
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


# =============================================================================
# EMERGENCY CLEAR QUEUE COMMAND
# =============================================================================
# State for clear queue confirmation
clear_queue_state = {
    'waiting_for_confirmation': False,
    'timestamp': None
}

@StreamBot.on_message(filters.command("clearqueue") & filters.private & CustomFilters.owner)
async def clear_queue(bot: Client, message: Message):
    """Emergency command to clear stuck queue."""
    global queued_files, file_queue
    
    pending = file_queue.qsize()
    
    if pending == 0:
        await message.reply_text("✅ Queue is already empty!")
        return
    
    # Set confirmation state
    clear_queue_state['waiting_for_confirmation'] = True
    clear_queue_state['timestamp'] = time.time()
    
    confirm_msg = await message.reply_text(
        f"⚠️ <b>WARNING: Clear Queue Confirmation</b>\n\n"
        f"This will clear <b>{pending}</b> pending files from the queue.\n\n"
        f"Type <code>/confirmclear</code> to proceed\n"
        f"Type <code>/cancel</code> to abort\n\n"
        f"⏱️ You have 30 seconds to confirm.",
        parse_mode=enums.ParseMode.HTML
    )
    
    # Wait for confirmation with timeout
    try:
        await asyncio.wait_for(
            wait_for_clear_confirmation(),
            timeout=30.0
        )
        
        # Clear the queue
        # Note: We can't directly clear asyncio.Queue, so we drain it
        cleared_count = 0
        while not file_queue.empty():
            try:
                file_queue.get_nowait()
                file_queue.task_done()
                cleared_count += 1
            except asyncio.QueueEmpty:
                break
        
        # Clear queued_files tracking
        queued_files.clear()
        
        LOGGER.warning(f"🗑️ Queue cleared by owner: {cleared_count} files removed")
        await message.reply_text(
            f"✅ <b>Queue Cleared</b>\n\n"
            f"Removed <b>{cleared_count}</b> pending files from queue.\n"
            f"Queue is now empty.",
            parse_mode=enums.ParseMode.HTML
        )
        
    except asyncio.TimeoutError:
        clear_queue_state['waiting_for_confirmation'] = False
        await message.reply_text(
            "⏰ <b>Confirmation timed out</b>\n"
            "Queue was not cleared.",
            parse_mode=enums.ParseMode.HTML
        )


async def wait_for_clear_confirmation():
    """Wait for clear queue confirmation."""
    while clear_queue_state['waiting_for_confirmation']:
        await asyncio.sleep(0.1)


@StreamBot.on_message(filters.command("confirmclear") & filters.private & CustomFilters.owner)
async def confirm_clear(bot: Client, message: Message):
    """Confirm clear queue."""
    if clear_queue_state['waiting_for_confirmation']:
        # Check if within timeout
        if time.time() - clear_queue_state['timestamp'] <= 30:
            clear_queue_state['waiting_for_confirmation'] = False
            # Confirmation accepted, main function will handle the clearing
            await message.reply_text("✅ Confirmation received. Clearing queue...")
        else:
            await message.reply_text("⏰ Confirmation expired. Please run /clearqueue again.")
    else:
        await message.reply_text("No pending clear queue request. Use /clearqueue first.")


@StreamBot.on_message(filters.command("cancel") & filters.private & CustomFilters.owner)
async def cancel_clear(bot: Client, message: Message):
    """Cancel clear queue."""
    if clear_queue_state['waiting_for_confirmation']:
        clear_queue_state['waiting_for_confirmation'] = False
        await message.reply_text("❌ Queue clear cancelled.")
    else:
        # Just a general cancel, no active operation
        pass


# =============================================================================
# ADMIN /rescan COMMAND
# =============================================================================
rescan_lock = Lock()
rescan_running = False

@StreamBot.on_message(filters.regex(r"^/rescan\d+$") & filters.private & CustomFilters.owner)
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
            if not (msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"))):
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
@StreamBot.on_message(filters.command("rescanhelp") & filters.private & CustomFilters.owner)
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


@Client.on_message(filters.command('caption') & filters.private & CustomFilters.owner)
async def toggle_caption(bot: Client, message: Message):
    try:
        Telegram.USE_CAPTION = not Telegram.USE_CAPTION
        await message.reply_text(f"Now Bot Uses {'Caption' if Telegram.USE_CAPTION else 'Filename'}")
    except Exception as e:
        print(f"An error occurred: {e}")

@Client.on_message(filters.command('tmdb') & filters.private & CustomFilters.owner)
async def toggle_tmdb(bot: Client, message: Message):
    try:
        Telegram.USE_TMDB = not Telegram.USE_TMDB
        await message.reply_text(f"Now Bot Uses {'TMDB' if Telegram.USE_TMDB else 'IMDB'}")
    except Exception as e:
        print(f"An error occurred: {e}")

@Client.on_message(filters.command('set') & filters.private & CustomFilters.owner)
async def set_id(bot: Client, message: Message):

    url_part = message.text.split()[1:]  # Skip the command itself

    try:
        if len(url_part) == 1:

            Telegram.USE_DEFAULT_ID = url_part[0]  # Get the first element
            await message.reply_text(f"Now Bot Uses Default URL: {Telegram.USE_DEFAULT_ID}")
        else:
            # Remove the default ID
            Telegram.USE_DEFAULT_ID = None
            await message.reply_text("Removed default ID.")
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")





@Client.on_message(filters.command('delete') & filters.private & CustomFilters.owner)
async def delete(bot: Client, message: Message):
    try:
        split_text = message.text.split()
        if len(split_text) != 2:
            return await message.reply_text("Use this format: /delete https://domain/ser/3123")
        
        url = split_text[1]
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.split('/')
        
        if len(path_parts) >= 3 and path_parts[-2] in ('ser', 'mov') and path_parts[-1].isdigit():
            media_type = path_parts[-2]
            tmdb_id = path_parts[-1]
            delete = await db.delete_document(media_type, int(tmdb_id))
            if delete:
                return await message.reply_text(f"{media_type} with ID {tmdb_id} has been deleted successfully.")
            else:
                return await message.reply_text(f"ID {tmdb_id} wasn't found in the database.")
        else:
            return await message.reply_text("The URL format is incorrect.")
    
    except Exception as e:
        await message.reply_text(f"An error occurred: {str(e)}")
        
