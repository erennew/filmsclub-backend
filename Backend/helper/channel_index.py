"""Durable full-channel indexing triggered from a t.me post link.

The scan walks history newest -> oldest, persists its cursor after every
batch, and feeds files through the existing parse/queue pipeline so indexed
media lands in the same MongoDB queue and frontend collections as live
uploads. Only channels listed in AUTH_CHANNEL are accepted.
"""

import asyncio
import html
import re
import time
from datetime import datetime
from typing import Awaitable, Callable, Dict, Optional, Tuple

from wzgram import enums
from wzgram.errors import FloodWait, MessageIdInvalid, MessageNotModified
from wzgram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Backend import db
from Backend.config import IndexConfig, QueueConfig, Telegram
from Backend.logger import LOGGER


LINK_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/"
    r"(?:(c)/)?([A-Za-z0-9_]+)/(\d+)/?(?:\?[^\s]*)?$",
    re.IGNORECASE,
)

STAT_KEYS = ("checked", "queued", "existing", "non_video", "deleted", "errors")
VIDEO_EXTENSIONS = {"mkv", "mp4", "avi", "mov", "ts", "wmv", "m4v", "webm", "flv", "3gp"}

QueueFileCallback = Callable[[Message, str], Awaitable[bool]]

_active_task: Optional[asyncio.Task] = None
_active_channel_id: Optional[int] = None
_resume_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None
_start_lock = asyncio.Lock()
_storage_check_ts = 0.0


class IndexTargetError(Exception):
    """Link could not be resolved to an indexable channel."""


class IndexBusyError(Exception):
    """Another channel scan is already running."""


def parse_channel_link(text: str) -> Optional[Tuple[bool, str, int]]:
    """Return (is_private_link, channel_ref, message_id) or None."""
    match = LINK_RE.match((text or "").strip())
    if not match:
        return None
    return bool(match.group(1)), match.group(2), int(match.group(3))


def clean_channel_id(chat_id: int) -> str:
    value = str(chat_id)
    return value[4:] if value.startswith("-100") else value.lstrip("-")


def is_auth_channel(chat) -> bool:
    chat_id = str(chat.id)
    bare_id = clean_channel_id(chat.id)
    username = (getattr(chat, "username", None) or "").lower()

    for entry in Telegram.AUTH_CHANNEL:
        entry = entry.strip().strip('"').strip("'")
        if not entry:
            continue
        if entry == chat_id or entry == bare_id:
            return True
        if username and entry.lstrip("@").lower() == username:
            return True
    return False


async def resolve_link_target(client, text: str):
    """Resolve a t.me link to (chat, linked_message_id)."""
    parsed = parse_channel_link(text)
    if not parsed:
        raise IndexTargetError("Invalid Telegram message link.")

    is_private, ref, message_id = parsed
    try:
        if is_private or ref.isdigit():
            chat = await client.get_chat(int(f"-100{ref}"))
        else:
            chat = await client.get_chat(ref)
    except Exception as e:
        raise IndexTargetError(
            "Could not open that channel. Make sure the bot is a member/admin there."
        ) from e

    if chat.type not in (enums.ChatType.CHANNEL, enums.ChatType.SUPERGROUP):
        raise IndexTargetError("The link must point to a channel or supergroup.")

    try:
        linked = await client.get_messages(chat.id, message_id)
    except FloodWait as e:
        await asyncio.sleep(int(e.value) + 2)
        linked = await client.get_messages(chat.id, message_id)
    except Exception as e:
        raise IndexTargetError(
            "Could not read that post. Make sure the bot is a member/admin of the channel."
        ) from e

    if not linked or getattr(linked, "empty", False):
        raise IndexTargetError("The linked post was not found in that channel.")

    return chat, message_id


def is_scan_running() -> bool:
    return _active_task is not None and not _active_task.done()


async def request_index_scan(
    client,
    chat,
    requester_id: int,
    queue_file: QueueFileCallback,
    status_message_id: Optional[int] = None,
) -> None:
    """Start a fresh full-channel scan after confirmation."""
    if is_scan_running():
        raise IndexBusyError("A channel scan is already running.")

    doc = {
        "channel": clean_channel_id(chat.id),
        "title": getattr(chat, "title", None) or str(chat.id),
        "requester_id": requester_id,
        "status": "running",
        "next_offset_id": None,
        "checked": 0,
        "queued": 0,
        "existing": 0,
        "non_video": 0,
        "deleted": 0,
        "errors": 0,
        "elapsed_seconds": 0.0,
        "started_at": datetime.utcnow(),
        "finished_at": None,
        "resumed_at": None,
        "status_message_id": status_message_id,
        "error": None,
    }
    if not await db.upsert_index_scan(chat.id, doc):
        raise IndexTargetError("Could not save scan state. Is the database connected?")

    await _launch(client, chat.id, queue_file, is_resume=False)
    LOGGER.info(f"Index scan started for {chat.id} by {requester_id}")


async def stop_index_scan() -> Optional[int]:
    """Ask the active scan to stop. Returns its channel id, if any."""
    global _active_channel_id
    channel_id = _active_channel_id
    if channel_id is None:
        return None
    await db.update_index_scan(
        channel_id,
        {"status": "cancelled", "finished_at": datetime.utcnow()},
    )
    if _stop_event:
        _stop_event.set()
    LOGGER.info(f"Index scan stop requested for {channel_id}")
    return channel_id


async def resume_index_scans(client, queue_file: QueueFileCallback) -> None:
    """Resume scans that were mid-run when the bot stopped."""
    if not IndexConfig.RESUME_ON_START:
        return
    scans = await db.get_running_index_scans()
    if not scans:
        return

    LOGGER.info(f"Resuming {len(scans)} interrupted channel index scan(s)")

    async def _resume_all():
        for scan in scans:
            channel_id = scan.get("channel_id")
            if not channel_id:
                continue
            try:
                while is_scan_running():
                    await asyncio.sleep(5)
                await db.update_index_scan(
                    channel_id,
                    {"status": "running", "resumed_at": datetime.utcnow()},
                )
                await _launch(client, channel_id, queue_file, is_resume=True)
                if _active_task:
                    await _active_task
            except IndexBusyError:
                LOGGER.warning(f"Index scan resume skipped (busy): {channel_id}")
            except Exception as e:
                LOGGER.error(f"Failed to resume index scan {channel_id}: {e}")

    global _resume_task
    _resume_task = asyncio.create_task(_resume_all())


async def _launch(client, channel_id: int, queue_file: QueueFileCallback, is_resume: bool) -> None:
    global _active_task, _active_channel_id
    async with _start_lock:
        if is_scan_running():
            raise IndexBusyError("A channel scan is already running.")
        _active_channel_id = channel_id
        task = asyncio.create_task(_run_scan(client, channel_id, queue_file, is_resume))
        _active_task = task
        task.add_done_callback(_on_task_done)


def _on_task_done(task: asyncio.Task) -> None:
    global _active_task, _active_channel_id
    if _active_task is task:
        _active_task = None
        _active_channel_id = None
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        LOGGER.error(f"Index scan task crashed: {exc}")


def _format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class _Progress:
    def __init__(self, client, requester_id, channel_id, title, message_id=None):
        self.client = client
        self.requester_id = requester_id
        self.channel_id = channel_id
        self.title = title
        self.message_id = message_id
        self._last_edit = 0.0

    def _text(self, stats: Dict[str, int], cursor, elapsed: float, note: str) -> str:
        cursor_text = f"#{cursor}" if cursor else "from newest"
        lines = [
            f"<b>Indexing {html.escape(self.title)}</b>",
            f"Channel: <code>{self.channel_id}</code>",
            f"Status: {html.escape(note)}",
            "",
            f"Checked: <code>{stats['checked']:,}</code> messages",
            f"Queued: <code>{stats['queued']:,}</code>",
            f"Already in DB: <code>{stats['existing']:,}</code>",
            f"Non-video: <code>{stats['non_video']:,}</code>",
            f"Deleted/empty: <code>{stats['deleted']:,}</code>",
            f"Errors: <code>{stats['errors']:,}</code>",
            f"Cursor: <code>{cursor_text}</code>",
            f"Elapsed: <code>{_format_elapsed(elapsed)}</code>",
            "",
            f"Speed: {IndexConfig.SCAN_BATCH_SIZE} msgs every {IndexConfig.SCAN_BATCH_DELAY}s; "
            f"pauses at queue backlog {IndexConfig.MAX_QUEUE_BACKLOG:,}",
        ]
        return "\n".join(lines)

    async def update(
        self,
        stats: Dict[str, int],
        cursor,
        elapsed: float,
        note: str,
        force: bool = False,
        done: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_edit < IndexConfig.PROGRESS_EDIT_INTERVAL:
            return

        text = self._text(stats, cursor, elapsed, note)
        keyboard = InlineKeyboardMarkup([])
        if not done:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Stop indexing", callback_data="idx:stop")]]
            )

        if self.message_id:
            try:
                await self.client.edit_message_text(
                    self.requester_id,
                    self.message_id,
                    text,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=keyboard,
                )
                self._last_edit = now
                return
            except MessageNotModified:
                self._last_edit = now
                return
            except MessageIdInvalid:
                self.message_id = None
            except FloodWait as e:
                await asyncio.sleep(int(e.value) + 2)
                return
            except Exception as e:
                LOGGER.debug(f"Index progress edit failed, sending new message: {e}")
                self.message_id = None

        try:
            message = await self.client.send_message(
                self.requester_id,
                text,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=keyboard,
            )
            self.message_id = message.id
            self._last_edit = now
            await db.update_index_scan(self.channel_id, {"status_message_id": message.id})
        except FloodWait as e:
            await asyncio.sleep(int(e.value) + 2)
        except Exception as e:
            LOGGER.warning(f"Could not send index progress message: {e}")


def _is_video_message(message: Message):
    """Return the media object if this message contains an indexable video."""
    if message.video:
        return message.video
    document = message.document
    if not document:
        return None
    if document.mime_type and document.mime_type.startswith("video/"):
        return document
    if document.file_name:
        ext = document.file_name.lower().rsplit(".", 1)[-1]
        if ext in VIDEO_EXTENSIONS:
            return document
    return None


async def _fetch_batch(client, channel_id: int, offset_id: Optional[int]):
    last_error = None
    for attempt in range(1, IndexConfig.FLOODWAIT_MAX_RETRIES + 1):
        try:
            kwargs = {"limit": IndexConfig.SCAN_BATCH_SIZE}
            if offset_id is not None:
                kwargs["offset_id"] = int(offset_id)
            return [message async for message in client.get_chat_history(channel_id, **kwargs)]
        except FloodWait as e:
            wait_seconds = int(getattr(e, "value", 0) or 0) + 5
            last_error = e
            LOGGER.warning(
                f"Index scan FloodWait {wait_seconds}s "
                f"(attempt {attempt}/{IndexConfig.FLOODWAIT_MAX_RETRIES})"
            )
            await asyncio.sleep(wait_seconds)
        except Exception as e:
            last_error = e
            LOGGER.warning(f"Index scan batch fetch failed: {e}")
            await asyncio.sleep(min(60, 5 * attempt))
    raise RuntimeError(f"History fetch failed: {last_error}")


async def _process_message(
    channel_bare: str,
    message: Message,
    stats: Dict[str, int],
    queue_file: QueueFileCallback,
    stop_event: asyncio.Event,
) -> None:
    if getattr(message, "empty", False):
        stats["deleted"] += 1
        return

    media = _is_video_message(message)
    if not media:
        stats["non_video"] += 1
        return

    hash_val = (getattr(media, "file_unique_id", None) or "")[:6]
    try:
        exists = await db.is_file_exists_strict(int(channel_bare), message.id, hash_val)
    except Exception as e:
        # A failed check must not be treated as "not indexed": retry the batch
        # instead of skipping files while the database is unavailable.
        raise RuntimeError(f"DB check failed for message {message.id}: {e}") from e
    if exists:
        stats["existing"] += 1
        return

    for attempt in range(1, IndexConfig.FLOODWAIT_MAX_RETRIES + 1):
        if stop_event.is_set():
            return
        try:
            queued = await queue_file(message, channel_bare)
            if queued:
                stats["queued"] += 1
            else:
                stats["errors"] += 1
            break
        except FloodWait as e:
            if attempt >= IndexConfig.FLOODWAIT_MAX_RETRIES:
                stats["errors"] += 1
                break
            wait_seconds = int(getattr(e, "value", 0) or 0) + 2
            LOGGER.warning(f"Index scan FloodWait {wait_seconds}s while queueing message {message.id}")
            await asyncio.sleep(wait_seconds)
        except Exception as e:
            LOGGER.error(f"Index scan failed to queue message {message.id}: {e}")
            raise

    if IndexConfig.FILE_DELAY > 0:
        await asyncio.sleep(IndexConfig.FILE_DELAY)


async def _wait_for_backlog(progress: _Progress, stats, cursor, elapsed, stop_event) -> bool:
    if not QueueConfig.ENABLE_MONGODB_QUEUE or IndexConfig.MAX_QUEUE_BACKLOG <= 0:
        return True

    while not stop_event.is_set():
        try:
            queue_stats = await db.get_queue_stats()
        except Exception:
            return True
        backlog = queue_stats.get("pending", 0) + queue_stats.get("processing", 0)
        if backlog < IndexConfig.MAX_QUEUE_BACKLOG:
            return True
        await progress.update(
            stats,
            cursor,
            elapsed,
            note=f"queue backlog {backlog:,} - waiting {IndexConfig.BACKLOG_WAIT_SECONDS}s",
            force=True,
        )
        await asyncio.sleep(IndexConfig.BACKLOG_WAIT_SECONDS)
    return False


async def _storage_limit_note(force: bool = False) -> Optional[str]:
    """Describe clusters that reached the configured quota, if any.

    Checked periodically so a long backfill pauses before the database starts
    rejecting inserts (which would flood the queue with terminal failures).
    """
    global _storage_check_ts
    now = time.monotonic()
    if not force and now - _storage_check_ts < IndexConfig.STORAGE_CHECK_SECONDS:
        return None
    _storage_check_ts = now
    try:
        usage = await db.get_storage_usage()
    except Exception:
        return None
    full = [u for u in usage if u.get("used_percent", 0) >= IndexConfig.STORAGE_STOP_PERCENT]
    if not full:
        return None
    return ", ".join(
        f"cluster {u['cluster']} at {u['used_percent']}% ({u['used_mb']}/{u['quota_mb']} MB)"
        for u in full
    )


async def _finish(progress: _Progress, stats, cursor, elapsed, status: str, note: str) -> None:
    await progress.update(stats, cursor, elapsed, note=note, force=True, done=True)
    await db.update_index_scan(
        progress.channel_id,
        {
            "status": status,
            "finished_at": datetime.utcnow(),
            "elapsed_seconds": elapsed,
        },
    )
    LOGGER.info(f"Index scan {status} for {progress.channel_id}: {stats}")


async def _run_scan(client, channel_id: int, queue_file: QueueFileCallback, is_resume: bool) -> None:
    global _stop_event
    _stop_event = asyncio.Event()
    stop_event = _stop_event

    scan = await db.get_index_scan(channel_id)
    if not scan or scan.get("status") != "running":
        return

    title = scan.get("title") or str(channel_id)
    requester_id = scan.get("requester_id")
    progress = _Progress(client, requester_id, channel_id, title, scan.get("status_message_id"))
    stats = {key: int(scan.get(key) or 0) for key in STAT_KEYS}
    cursor = scan.get("next_offset_id")
    elapsed_base = float(scan.get("elapsed_seconds") or 0)
    run_started = time.monotonic()
    channel_bare = scan.get("channel") or clean_channel_id(channel_id)

    def elapsed() -> float:
        return elapsed_base + (time.monotonic() - run_started)

    try:
        await client.get_chat(channel_id)
    except FloodWait as e:
        await asyncio.sleep(int(e.value) + 5)
    except Exception as e:
        # Best-effort warmup only: get_chat_history resolves the peer itself,
        # and after a restart the entity may simply not be cached yet.
        LOGGER.warning(f"Index scan get_chat warmup failed for {channel_id}: {e}")

    if is_resume:
        await progress.update(stats, cursor, elapsed(), note="resumed after restart", force=True)
    else:
        await progress.update(stats, cursor, elapsed(), note="scanning history", force=True)

    consecutive_failures = 0

    while True:
        if stop_event.is_set():
            await _finish(progress, stats, cursor, elapsed(), "cancelled", "stopped by admin")
            return

        if not await _wait_for_backlog(progress, stats, cursor, elapsed(), stop_event):
            await _finish(progress, stats, cursor, elapsed(), "cancelled", "stopped by admin")
            return

        storage_note = await _storage_limit_note()
        while storage_note and not stop_event.is_set():
            await progress.update(
                stats, cursor, elapsed(),
                note=f"storage limit reached ({storage_note}) - free space, add a cluster, or /indexstop",
                force=True,
            )
            await asyncio.sleep(IndexConfig.BACKLOG_WAIT_SECONDS)
            storage_note = await _storage_limit_note(force=True)
        if stop_event.is_set():
            await _finish(progress, stats, cursor, elapsed(), "cancelled", "stopped by admin")
            return

        try:
            batch = await _fetch_batch(client, channel_id, cursor)
        except Exception as e:
            consecutive_failures += 1
            LOGGER.error(
                f"Index scan batch failure ({consecutive_failures}/"
                f"{IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES}): {e}"
            )
            if consecutive_failures >= IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES:
                await db.update_index_scan(channel_id, {"error": str(e)[:300]})
                await _finish(progress, stats, cursor, elapsed(), "error", f"history fetch failed: {e}")
                return
            await progress.update(
                stats, cursor, elapsed(),
                note=f"fetch failed, retrying ({consecutive_failures}/"
                     f"{IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES})",
                force=True,
            )
            await asyncio.sleep(IndexConfig.BACKLOG_WAIT_SECONDS)
            continue

        if not batch:
            await _finish(progress, stats, cursor, elapsed(), "completed", "channel fully indexed")
            return

        last_processed = None
        batch_error = None
        for message in batch:
            if stop_event.is_set():
                break
            try:
                await _process_message(channel_bare, message, stats, queue_file, stop_event)
            except Exception as e:
                batch_error = e
                break
            stats["checked"] += 1
            last_processed = message.id

        if batch_error is not None and not stop_event.is_set():
            consecutive_failures += 1
            LOGGER.error(
                f"Index scan processing failure ({consecutive_failures}/"
                f"{IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES}): {batch_error}"
            )
            if consecutive_failures >= IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES:
                await db.update_index_scan(channel_id, {"error": str(batch_error)[:300]})
                await _finish(progress, stats, cursor, elapsed(), "error", f"processing failed: {batch_error}")
                return
            await progress.update(
                stats, cursor, elapsed(),
                note=f"processing failed, retrying ({consecutive_failures}/"
                     f"{IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES})",
                force=True,
            )
            await asyncio.sleep(IndexConfig.BACKLOG_WAIT_SECONDS)
            continue

        if stop_event.is_set():
            if last_processed is not None:
                cursor = last_processed
        else:
            cursor = min(message.id for message in batch)
        consecutive_failures = 0
        await db.update_index_scan(
            channel_id,
            {
                "next_offset_id": cursor,
                "checked": stats["checked"],
                "queued": stats["queued"],
                "existing": stats["existing"],
                "non_video": stats["non_video"],
                "deleted": stats["deleted"],
                "errors": stats["errors"],
                "elapsed_seconds": elapsed(),
                "status": "running",
            },
        )
        await progress.update(stats, cursor, elapsed(), note="scanning history")

        if stop_event.is_set():
            await _finish(progress, stats, cursor, elapsed(), "cancelled", "stopped by admin")
            return

        delay = IndexConfig.SCAN_BATCH_DELAY
        if delay > 0:
            await asyncio.sleep(delay)
