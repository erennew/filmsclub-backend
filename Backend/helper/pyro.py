import pycountry
from pyrogram.file_id import FileId
from typing import Optional
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
import re
import os

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


def clean_filename(filename):
    # 1. Strip channel branding prefixes at start: "• @name • -", "[@name] -", "@name -"
    cleaned_filename = re.sub(
        r'^[•·\s]*[\[\(]?\s*@[A-Za-z0-9_]+\s*[\]\)]?\s*[•·]?\s*[-–—]?\s*',
        ' ', filename
    )
    # 2. Remove Telegram usernames with underscores, dashes, brackets, spaces
    # Handles: @WANTED_Originals, @PH_FILES, [@Animes2u], @TG_Movies4u_, etc.
    pattern = r'[\s\[\]_-]*@[A-Za-z0-9_]+[\s\[\]_-]*|[\s\[\]_-]*@[A-Za-z0-9-]+[\s\[\]_-]*'
    cleaned_filename = re.sub(pattern, ' ', cleaned_filename)
    # Remove common uploader prefixes like "mj_link_4u -"
    cleaned_filename = re.sub(r'^[A-Za-z0-9_]+\s*-\s*', ' ', cleaned_filename)
    cleaned_filename = re.sub(r'(?<=\W)(org|AMZN|DDP|DD|NF|AAC|TVDL|5\.1|2\.1|2\.0|7\.0|7\.1|5\.0|~|\b\w+kbps\b)(?=\W)', '', cleaned_filename, flags=re.IGNORECASE)
    cleaned_filename = re.sub(r'\\[ntr]', ' ', cleaned_filename)
    # Replace dots and underscores with spaces for readability, but preserve file extension
    cleaned_filename = re.sub(r'(?<!\.[a-z0-9]{3,4})[._]', ' ', cleaned_filename, flags=re.IGNORECASE)
    cleaned_filename = re.sub(r'\s+', ' ', cleaned_filename).strip()
    return cleaned_filename


def normalize_filename(filename: str) -> str:
    """Normalize filename before PTN.parse to handle edge cases."""
    # 1. Fix malformed extensions (missing closing bracket/paren before ext)
    # e.g. "[Telugu (DD.mkv" -> "[Telugu (DD)].mkv"
    # e.g. "[Telugu (Org .mkv" -> "[Telugu (Org)].mkv"
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

    # 3. Remove trailing dash/space before extension: "HDRip -.mkv" -> "HDRip.mkv"
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
        return tmdb_match.group(2)  # Returns the TMDB ID

    # Match IMDb URLs
    imdb_match = re.search(r'/title/(tt\d+)', url)
    if imdb_match:
        return imdb_match.group(1)  # Returns the IMDb ID

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
        # Check if the restart message file exists
        if await aiopath.exists(".restartmsg"):
            async with aiopen(".restartmsg", "r") as f:
                # Read the chat ID and message ID from the file
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

            # Remove the restart message file
            await aioremove(".restartmsg")

    except Exception as e:
        LOGGER.error(f"Error in restart_notification: {e}")
