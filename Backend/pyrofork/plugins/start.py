import re
from asyncio import create_task, sleep as asleep
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
from asyncio import Queue, create_task
from os import execl as osexecl
from asyncio import create_subprocess_exec, gather
from sys import executable
from aiofiles import open as aiopen
from pyrogram import enums


tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")
# Initialize database connection
import random
import string
from passlib.context import CryptContext
from datetime import datetime, timedelta

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




# =============================================================================
# FILE PROCESSING QUEUE SYSTEM
# =============================================================================
from asyncio import Lock

file_queue = Queue()
db_lock = Lock()

# Track files currently in queue to prevent duplicates
queued_files: set = set()          # {(channel, msg_id)}
queue_stats = {
    "processed": 0,
    "failed": 0,
    "skipped": 0,
}
QUEUE_WORKERS = 3


async def process_file(worker_id: int):
    """Worker that pulls from queue and inserts into DB one by one."""
    while True:
        item = await file_queue.get()
        metadata_info, hash, channel, msg_id, size, title, source = item
        cache_key = (channel, msg_id)

        try:
            async with db_lock:
                updated_id = await db.insert_media(
                    metadata_info, hash=hash, channel=channel, msg_id=msg_id, size=size, name=title
                )
                if updated_id:
                    queue_stats["processed"] += 1
                    LOGGER.info(f"[Worker {worker_id}] {metadata_info['media_type']} updated with ID: {updated_id}")
                else:
                    queue_stats["failed"] += 1
                    LOGGER.warning(f"[Worker {worker_id}] Update failed for {title}")
        except Exception as e:
            queue_stats["failed"] += 1
            LOGGER.error(f"[Worker {worker_id}] Error processing {title}: {e}")
        finally:
            queued_files.discard(cache_key)
            file_queue.task_done()

# Start multiple worker tasks
for w in range(QUEUE_WORKERS):
    create_task(process_file(w + 1))


# =============================================================================
# SHARED FILE PARSING LOGIC (used by both live handler and rescan)
# =============================================================================
async def parse_and_queue_file(message: Message, channel: str, is_rescan: bool = False):
    """Parse a message's video/document and add to queue. Returns True if queued/skipped, False if error."""
    try:
        if not (message.video or (message.document and message.document.mime_type and message.document.mime_type.startswith("video/"))):
            return False  # Not a video

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

        title = clean_filename(title)
        metadata_info = await metadata(title, file)
        if metadata_info is None:
            if not is_rescan:
                await message.reply_text("> Not added — check log")
            return False

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

        queued_files.add(cache_key)
        await file_queue.put((metadata_info, hash_val, channel_int, msg_id, size, title, "live" if not is_rescan else "rescan"))
        LOGGER.info(f"Queued ({file_queue.qsize()} pending): {title}")
        return True
    except Exception as e:
        LOGGER.error(f"Error parsing file: {e}")
        return False


# =============================================================================
# LIVE CHANNEL HANDLER
# =============================================================================
@StreamBot.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(bot: Client, message: Message):
    if str(message.chat.id) in Telegram.AUTH_CHANNEL:
        try:
            channel = str(message.chat.id).replace("-100", "")
            await parse_and_queue_file(message, channel, is_rescan=False)
        except FloodWait as e:
            LOGGER.info(f"Sleeping for {str(e.value)}s")
            await asleep(e.value)
            await message.reply_text(
                text=f"Got Floodwait of {str(e.value)}s",
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        await message.reply(text="> Channel is not in AUTH_CHANNEL")


# =============================================================================
# ADMIN /queue COMMAND
# =============================================================================
@StreamBot.on_message(filters.command("queue") & filters.private & CustomFilters.owner)
async def queue_status(bot: Client, message: Message):
    pending = file_queue.qsize()
    active = len(queued_files)
    processed = queue_stats["processed"]
    failed = queue_stats["failed"]
    skipped = queue_stats["skipped"]
    workers = QUEUE_WORKERS

    text = (
        f"📊 <b>Queue Status</b>\n\n"
        f"⏳ <b>Pending:</b> {pending}\n"
        f"🔄 <b>Active:</b> {active}\n"
        f"✅ <b>Processed:</b> {processed}\n"
        f"❌ <b>Failed:</b> {failed}\n"
        f"⏭ <b>Skipped (duplicates):</b> {skipped}\n"
        f"👷 <b>Workers:</b> {workers}\n"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


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
        
