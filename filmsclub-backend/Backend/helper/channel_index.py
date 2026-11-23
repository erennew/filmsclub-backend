"""Durable full-channel indexing triggered from a t.me post link.

The scan walks history newest -> oldest, persists its cursor after every
batch, and feeds files through the existing parse/queue pipeline so indexed
media lands in the same MongoDB queue and frontend collections as live
uploads. Only channels listed in AUTH_CHANNEL are accepted.

Several channels can be scanned at once. Each running scan owns its own
stop event and its own progress message, so N concurrent scans need no extra
progress plumbing. Because every scan reads history through the same user
session, a module-level token bucket caps the *aggregate* read rate — N scans
share one budget instead of multiplying the pressure on Telegram.
"""

import asyncio
import html
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from wzgram import enums
from wzgram.errors import FloodWait, MessageIdInvalid, MessageNotModified
from wzgram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Backend.config import IndexConfig, QueueConfig, Telegram
from Backend.helper.runtime_config import runtime_config
from Backend.logger import LOGGER


def _db():
    """Late import: ``Backend.__init__`` builds the Database instance.

    Resolved on every call so the module never holds a stale handle — the same
    pattern ``audit``, ``runtime_config`` and ``alerts`` use. It also keeps the
    module importable during ``Backend.__init__``, when ``Backend.db`` does not
    exist yet.
    """
    from Backend import db
    return db


LINK_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/"
    r"(?:(c)/)?([A-Za-z0-9_-]+)/(\d+)/?(?:\?[^\s]*)?$",
    re.IGNORECASE,
)

# Trigger for the DM link handler. ``LINK_RE`` is anchored and used to parse
# one link at a time; the handler accepts several links, one per line, so the
# message filter needs MULTILINE as well. It is passed to ``filters.regex`` as a
# compiled pattern — passing ``LINK_RE.pattern`` (a string) would recompile it
# with flags=0 and silently drop IGNORECASE.
LINK_TRIGGER_RE = re.compile(LINK_RE.pattern, LINK_RE.flags | re.MULTILINE)

STAT_KEYS = (
    "checked", "queued", "existing", "non_video", "deleted", "errors", "orphans",
    "skipped",
)
VIDEO_EXTENSIONS = {"mkv", "mp4", "avi", "mov", "ts", "wmv", "m4v", "webm", "flv", "3gp"}
# Upper bound for "skip the newest N posts". Large enough for any real channel,
# small enough that a typo cannot park a scan reading history forever.
MAX_SKIP_FIRST_MESSAGES = 100000

QueueFileCallback = Callable[[Message, str], Awaitable[bool]]

# Scan modes. "index" feeds files into the ingestion pipeline; "audit" only
# reports which posts have no database match (the orphan report).
MODE_INDEX = "index"
MODE_AUDIT = "audit"


class _ScanHandle:
    """Everything the registry needs to know about one running scan."""

    __slots__ = ("channel_id", "task", "stop_event", "requester_id",
                 "status_message_id", "mode")

    def __init__(self, channel_id: int, task: asyncio.Task, stop_event: asyncio.Event,
                 requester_id: Optional[int] = None,
                 status_message_id: Optional[int] = None,
                 mode: str = MODE_INDEX):
        self.channel_id = channel_id
        self.task = task
        self.stop_event = stop_event
        self.requester_id = requester_id
        self.status_message_id = status_message_id
        self.mode = mode


# One registry entry per running scan, keyed by channel id.
_scans: Dict[int, _ScanHandle] = {}
# Context needed to launch a scan that is currently held back as `waiting`.
_pending_context: Dict[int, dict] = {}
# Fallback launch context, set by the startup resume path. A `waiting` row
# written before a restart has no in-memory per-channel context, so promotion
# uses this instead of abandoning the scan.
_default_context: Dict[str, Any] = {}
_resume_task: Optional[asyncio.Task] = None
_start_lock = asyncio.Lock()
_storage_check_ts = 0.0


class IndexTargetError(Exception):
    """Link could not be resolved to an indexable channel."""


class IndexBusyError(Exception):
    """The scan concurrency cap is already reached."""


def parse_channel_link(text: str) -> Optional[Tuple[bool, str, int]]:
    """Return (is_private_link, channel_ref, message_id) or None."""
    match = LINK_RE.match((text or "").strip())
    if not match:
        return None
    return bool(match.group(1)), match.group(2), int(match.group(3))


def clean_channel_id(chat_id: int) -> str:
    value = str(chat_id)
    return value[4:] if value.startswith("-100") else value.lstrip("-")


def _chat_ref_to_id(ref: str) -> int:
    """Normalize the numeric part of a private ``t.me/c/...`` link.

    Telegram links normally carry the bare channel id (``3728019705``), which
    needs the ``-100`` supergroup/channel prefix. Some clients already include
    ``-100`` or a minus sign; don't add a second prefix in that case.
    """
    value = str(ref).strip()
    if value.startswith("-"):
        return int(value)
    return int(f"-100{value}")


def is_auth_channel_id(chat_id: int) -> bool:
    """AUTH_CHANNEL membership by numeric id only (no username available)."""
    value = str(chat_id)
    bare = clean_channel_id(chat_id)
    for entry in Telegram.AUTH_CHANNEL:
        entry = entry.strip().strip('"').strip("'")
        if not entry:
            continue
        # Accept every way an admin may have written the ID: -100…, -…, bare
        # digits.
        if entry == value or entry.lstrip("-") == bare:
            return True
    return False


def is_auth_channel(chat) -> bool:
    username = (getattr(chat, "username", None) or "").lower()

    if is_auth_channel_id(chat.id):
        return True
    if username:
        for entry in Telegram.AUTH_CHANNEL:
            entry = entry.strip().strip('"').strip("'")
            if not entry:
                continue
            if entry.lstrip("@").lower() == username:
                return True
    return False


def _client_label(client) -> str:
    name = getattr(client, "name", None)
    return str(name) if name else type(client).__name__


async def _get_chat_from(client, chat_ref):
    """Return (chat, error_label) with one FloodWait retry."""
    try:
        return await client.get_chat(chat_ref), None
    except FloodWait as e:
        await asyncio.sleep(int(e.value) + 2)
        try:
            return await client.get_chat(chat_ref), None
        except Exception as e:
            return None, f"{_client_label(client)}: {type(e).__name__}"
    except Exception as e:
        return None, f"{_client_label(client)}: {type(e).__name__}"


async def _get_post_from(client, chat_id, message_id):
    """Return (post, error_label) with one FloodWait retry."""
    try:
        return await client.get_messages(chat_id, message_id), None
    except FloodWait as e:
        await asyncio.sleep(int(e.value) + 2)
        try:
            return await client.get_messages(chat_id, message_id), None
        except Exception as e:
            return None, f"{_client_label(client)}: {type(e).__name__}"
    except Exception as e:
        return None, f"{_client_label(client)}: {type(e).__name__}"


async def resolve_link_target(client, text: str, fallbacks=None):
    """Resolve a t.me link to ``(chat, linked_message_id)``.

    A private channel can only be opened by a client that is already a member.
    The user session is the preferred client for history reads, but the bot may
    be the account that was added to the channel, so every available client is
    tried before reporting failure. Callers pass extra clients via
    ``fallbacks``; ``None`` entries are ignored.
    """
    parsed = parse_channel_link(text)
    if not parsed:
        raise IndexTargetError("Invalid Telegram message link.")

    is_private, ref, message_id = parsed
    candidates = []
    for candidate in [client, *(fallbacks or [])]:
        if candidate is not None and not any(candidate is c for c in candidates):
            candidates.append(candidate)
    if not candidates:
        raise IndexTargetError("No Telegram client is configured to open that link.")

    # Public usernames stay as strings; numeric refs (private or numeric public)
    # are normalized to the -100 channel/supergroup id.
    chat_ref = ref if (not is_private and not ref.isdigit()) else _chat_ref_to_id(ref)

    chat = None
    chat_errors = []
    for candidate in candidates:
        chat, error = await _get_chat_from(candidate, chat_ref)
        if chat is not None:
            break
        if error:
            chat_errors.append(error)
    if chat is None:
        raise IndexTargetError(
            "Could not open that channel with any available client ("
            + ", ".join(chat_errors) + "). Make sure the indexing user session "
            "or bot is a member/admin there."
        )

    if chat.type not in (enums.ChatType.CHANNEL, enums.ChatType.SUPERGROUP):
        raise IndexTargetError("The link must point to a channel or supergroup.")

    linked = None
    post_errors = []
    for candidate in candidates:
        linked, error = await _get_post_from(candidate, chat.id, message_id)
        if linked is not None and not getattr(linked, "empty", False):
            break
        if error:
            post_errors.append(error)
    if linked is None or getattr(linked, "empty", False):
        detail = f" ({', '.join(post_errors)})" if post_errors else ""
        raise IndexTargetError(
            "Could not read that post. Make sure the indexing account "
            "(user session or bot) is a member/admin of the channel" + detail + "."
        )

    return chat, message_id


# =============================================================================
# CONCURRENCY CAP
# =============================================================================
def scan_cap() -> int:
    """How many channel scans may run at the same time (default 1)."""
    try:
        return max(1, int(runtime_config.get("index_max_concurrent_scans")))
    except Exception:
        return 1


def skip_first_default() -> int:
    """Live default for the 'skip newest N messages' prompt (0 = index all)."""
    try:
        return max(0, int(runtime_config.get("index_skip_first_messages")))
    except Exception:
        return max(0, int(IndexConfig.INDEX_SKIP_FIRST_MESSAGES))


def normalize_skip_first(value, default: Optional[int] = None) -> int:
    """Validate a requested skip count.

    ``None``/blank means "use the current default". The value is clamped to the
    documented range rather than silently wrapping a negative into a resume
    that skips nothing.
    """
    if value is None or value == "":
        value = skip_first_default() if default is None else default
    try:
        number = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError("Skip count must be a whole number.") from e
    if number < 0 or number > MAX_SKIP_FIRST_MESSAGES:
        raise ValueError(
            f"Skip count must be between 0 and {MAX_SKIP_FIRST_MESSAGES}."
        )
    return number


def running_count() -> int:
    """Number of scans currently running."""
    return len([h for h in _scans.values() if not h.task.done()])


def is_scan_running(channel_id: int) -> bool:
    """True when that specific channel is being scanned."""
    handle = _scans.get(channel_id)
    return handle is not None and not handle.task.done()


def running_channels() -> List[int]:
    return sorted(h.channel_id for h in _scans.values() if not h.task.done())


def scan_status() -> Dict:
    """Live registry snapshot for the panel and the bot commands."""
    cap = scan_cap()
    running = running_channels()
    retiring = sorted(h.channel_id for h in _scans.values() if h.stop_event.is_set())
    return {
        "cap": cap,
        "running": running,
        "running_count": len(running),
        "retiring": retiring,
        "scans": [
            {
                "channel_id": h.channel_id,
                "mode": h.mode,
                "requester_id": h.requester_id,
                "status_message_id": h.status_message_id,
                "stopping": h.stop_event.is_set(),
            }
            for h in sorted(_scans.values(), key=lambda x: x.channel_id)
        ],
    }


def _busy_message(cap: int) -> str:
    if cap <= 1:
        # Today's exact wording — the single-scan default must not change.
        return "A channel scan is already running."
    return (
        f"Too many channel scans are running (limit {cap}). "
        "Stop one or raise index_max_concurrent_scans."
    )


# =============================================================================
# SHARED READ GOVERNOR
# =============================================================================
class _ReadGovernor:
    """Aggregate read budget shared by every concurrent scan.

    All scans read channel history through one user session, so N scans would
    multiply the ``get_chat_history`` pressure and a Telegram FloodWait would
    stall the whole bot rather than just the indexing. This token bucket caps
    the *aggregate* throughput at ``scan_batch_size / scan_batch_delay``
    messages per second with a burst capacity of one batch.

    With a single scan the bucket is transparent: it hands out exactly the
    batch it is about to request, and the existing inter-batch delay supplies
    the pacing. With N scans they share the same envelope instead of asking
    for N times as much.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tokens = 0.0
        self._last = time.monotonic()
        self._penalty_until = 0.0

    # ── shape of the bucket, recomputed from the live knobs ──
    def _capacity(self) -> float:
        return float(max(1, int(runtime_config.get("scan_batch_size"))))

    def _rate(self) -> float:
        size = float(max(1, int(runtime_config.get("scan_batch_size"))))
        delay = float(runtime_config.get("scan_batch_delay"))
        if delay <= 0:
            # No pacing configured: one batch per refill tick.
            return size
        return size / delay

    def _refill(self) -> float:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last)
        self._last = now
        capacity = self._capacity()
        self._tokens = min(capacity, self._tokens + elapsed * self._rate())
        return now

    def _prime(self) -> None:
        """Start with a full bucket so the first batch is never delayed."""
        self._tokens = self._capacity()
        self._last = time.monotonic()

    async def acquire(self, tokens: float) -> None:
        """Wait until ``tokens`` messages may be read."""
        wanted = max(1.0, float(tokens))
        while True:
            async with self._lock:
                now = self._refill()
                penalty = self._penalty_until - now
                if penalty > 0:
                    wait = penalty
                elif self._tokens >= wanted:
                    self._tokens -= wanted
                    return
                else:
                    wait = (wanted - self._tokens) / max(self._rate(), 1e-6)
            await asyncio.sleep(max(wait, 0.05))

    async def penalty(self, seconds: float) -> None:
        """Block every scan for ``seconds`` (one channel hit a rate limit)."""
        async with self._lock:
            self._penalty_until = max(
                self._penalty_until, time.monotonic() + max(0.0, float(seconds))
            )

    def state(self) -> Dict:
        return {
            "tokens": round(self._tokens, 1),
            "capacity": self._capacity(),
            "rate_per_second": round(self._rate(), 1),
            "penalty_seconds": round(max(0.0, self._penalty_until - time.monotonic()), 1),
        }


_governor = _ReadGovernor()
_governor._prime()


# =============================================================================
# SCAN LIFECYCLE
# =============================================================================
def _new_scan_doc(chat, requester_id: int, status: str,
                  status_message_id: Optional[int], skip_first: int = 0) -> dict:
    return {
        "channel": clean_channel_id(chat.id),
        "title": getattr(chat, "title", None) or str(chat.id),
        "requester_id": requester_id,
        "status": status,
        "next_offset_id": None,
        "checked": 0,
        "queued": 0,
        "existing": 0,
        "non_video": 0,
        "deleted": 0,
        "errors": 0,
        "orphans": 0,
        "skipped": 0,
        # Requested number of newest posts to ignore. Persisted with the scan
        # so a restart or a `waiting` promotion cannot lose the admin's answer.
        "skip_first": int(skip_first or 0),
        "elapsed_seconds": 0.0,
        "started_at": datetime.utcnow(),
        "finished_at": None,
        "resumed_at": None,
        "status_message_id": status_message_id,
        "error": None,
    }


async def request_index_scan(
    client,
    chat,
    requester_id: int,
    queue_file: QueueFileCallback,
    status_message_id: Optional[int] = None,
    notify_client=None,
    mode: str = MODE_INDEX,
    skip_first: Optional[int] = None,
) -> None:
    """Start a fresh full-channel scan after confirmation.

    ``skip_first`` is the number of newest posts to ignore (asked before the
    scan starts). ``None`` uses the live ``index_skip_first_messages`` default.

    Raises ``IndexBusyError`` when this channel is already being scanned or the
    concurrency cap is reached.
    """
    channel_id = chat.id
    if is_scan_running(channel_id):
        # Overwriting the live scan doc would reset its counters while the
        # original task kept walking history, and the new task would race it.
        raise IndexBusyError("This channel is already being scanned.")

    cap = scan_cap()
    if running_count() >= cap:
        raise IndexBusyError(_busy_message(cap))

    skip = normalize_skip_first(skip_first)
    doc = _new_scan_doc(chat, requester_id, "running", status_message_id,
                        skip_first=skip)
    doc["mode"] = mode
    if not await _db().upsert_index_scan(channel_id, doc):
        raise IndexTargetError("Could not save scan state. Is the database connected?")

    await _launch(client, channel_id, queue_file, is_resume=False,
                  notify_client=notify_client, mode=mode)
    LOGGER.info(f"Index scan started for {channel_id} by {requester_id}")


async def request_index_scans(
    client,
    chats: List,
    requester_id: int,
    queue_file: QueueFileCallback,
    notify_client=None,
    status_message_id: Optional[int] = None,
    mode: str = MODE_INDEX,
    skip_first: Optional[int] = None,
) -> Dict:
    """Start several scans at once.

    Scans beyond the concurrency cap are not lost — they are recorded with
    status ``waiting`` and promoted automatically when a slot frees. Every scan
    in the batch uses the same ``skip_first`` answer from the confirmation.
    """
    cap = scan_cap()
    skip = normalize_skip_first(skip_first)
    started: List[int] = []
    waiting: List[int] = []
    skipped: List[int] = []

    for chat in chats:
        channel_id = chat.id
        if is_scan_running(channel_id):
            # Re-requesting a live channel must not overwrite its scan doc with
            # a fresh `waiting` row — that would reset its progress counters
            # while the original task is still walking history.
            skipped.append(channel_id)
            continue
        # ``_launch`` registers the handle in ``_scans`` before it returns, so
        # ``running_count()`` already includes every scan started by this loop.
        # Adding ``len(started)`` on top would double-count and park the last
        # channel as `waiting` even though a slot is free.
        if running_count() < cap:
            doc = _new_scan_doc(chat, requester_id, "running", status_message_id,
                                skip_first=skip)
            doc["mode"] = mode
            if not await _db().upsert_index_scan(channel_id, doc):
                raise IndexTargetError(
                    "Could not save scan state. Is the database connected?"
                )
            await _launch(client, channel_id, queue_file, is_resume=False,
                          notify_client=notify_client, mode=mode)
            started.append(channel_id)
        else:
            doc = _new_scan_doc(chat, requester_id, "waiting", status_message_id,
                                skip_first=skip)
            doc["mode"] = mode
            await _db().upsert_index_scan(channel_id, doc)
            _pending_context[channel_id] = {
                "client": client,
                "queue_file": queue_file,
                "notify_client": notify_client,
                "mode": mode,
            }
            waiting.append(channel_id)

    return {"started": started, "waiting": waiting,
            "skipped": skipped, "cap": cap, "skip_first": skip}


async def stop_index_scan(channel_id: Optional[int] = None) -> List[int]:
    """Ask one scan — or every scan — to stop.

    Returns the affected channel ids. The scan finishes its current batch and
    then stops; it is resumable, so no confirmation is needed. Queued
    (``waiting``) scans are cancelled as well when stopping everything,
    otherwise they would be promoted the instant a stopped scan finishes.
    """
    if channel_id is None:
        targets = running_channels()
    elif channel_id in _scans:
        targets = [channel_id]
    else:
        targets = []

    stopped: List[int] = []
    for cid in targets:
        handle = _scans.get(cid)
        if handle is None:
            continue
        await _db().update_index_scan(
            cid, {"status": "cancelled", "finished_at": datetime.utcnow()}
        )
        handle.stop_event.set()
        stopped.append(cid)
        LOGGER.info(f"Index scan stop requested for {cid}")

    # Cancel queued scans too. Without this, "stop all" would immediately be
    # undone by _on_task_done promoting the waiting queue into the freed slots.
    waiting = await _db().get_waiting_index_scans()
    for scan in waiting:
        cid = scan.get("channel_id")
        if cid is None or (channel_id is not None and cid != channel_id):
            continue
        if cid in _scans:
            continue
        await _db().update_index_scan(
            cid, {"status": "cancelled", "finished_at": datetime.utcnow(),
                  "error": "cancelled by admin before it started"}
        )
        _pending_context.pop(cid, None)
        if channel_id is None or cid == channel_id:
            stopped.append(cid)
            LOGGER.info(f"Queued index scan {cid} cancelled")

    return stopped


async def resume_index_scans(client, queue_file: QueueFileCallback, notify_client=None) -> None:
    """Resume scans that were mid-run or queued when the bot stopped."""
    global _default_context
    _default_context = {
        "client": client,
        "queue_file": queue_file,
        "notify_client": notify_client,
        "mode": MODE_INDEX,
    }
    if not IndexConfig.RESUME_ON_START:
        return

    # Both interrupted scans and scans held behind the concurrency cap must
    # survive a restart. Reading only `running` rows would strand every
    # `waiting` one, because its in-memory launch context is gone.
    running = await _db().get_running_index_scans()
    waiting = await _db().get_waiting_index_scans()
    scans = list(running) + list(waiting)
    seen: set = set()
    unique: List[Dict] = []
    for scan in scans:
        channel_id = scan.get("channel_id")
        if not channel_id or channel_id in seen:
            continue
        seen.add(channel_id)
        unique.append(scan)
    scans = unique
    if not scans:
        return

    LOGGER.info(f"Resuming {len(scans)} interrupted/queued channel index scan(s)")

    async def _resume_all():
        cap = scan_cap()
        for scan in scans:
            channel_id = scan.get("channel_id")
            if not channel_id:
                continue
            if not is_auth_channel_id(channel_id):
                LOGGER.warning(
                    f"Index scan {channel_id} skipped: channel is no longer in AUTH_CHANNEL"
                )
                await _db().update_index_scan(
                    channel_id,
                    {"status": "error", "error": "Channel is no longer in AUTH_CHANNEL",
                     "finished_at": datetime.utcnow()},
                )
                continue

            mode = scan.get("mode") or MODE_INDEX
            if running_count() < cap:
                await _db().update_index_scan(
                    channel_id, {"status": "running", "resumed_at": datetime.utcnow()}
                )
                try:
                    await _launch(client, channel_id, queue_file, is_resume=True,
                                  notify_client=notify_client, mode=mode)
                except IndexBusyError:
                    LOGGER.warning(f"Index scan resume skipped (busy): {channel_id}")
                    await _db().update_index_scan(channel_id, {"status": "waiting"})
                    _pending_context[channel_id] = {
                        "client": client, "queue_file": queue_file,
                        "notify_client": notify_client, "mode": mode,
                    }
                except Exception as e:
                    LOGGER.error(f"Failed to resume index scan {channel_id}: {e}")
            else:
                LOGGER.info(f"Index scan {channel_id} waiting for a free slot")
                await _db().update_index_scan(channel_id, {"status": "waiting"})
                _pending_context[channel_id] = {
                    "client": client, "queue_file": queue_file,
                    "notify_client": notify_client, "mode": mode,
                }

    global _resume_task
    _resume_task = asyncio.create_task(_resume_all())


async def _launch(
    client,
    channel_id: int,
    queue_file: QueueFileCallback,
    is_resume: bool,
    notify_client=None,
    mode: str = MODE_INDEX,
) -> None:
    async with _start_lock:
        cap = scan_cap()
        if running_count() >= cap:
            raise IndexBusyError(_busy_message(cap))

        stop_event = asyncio.Event()
        requester_id = None
        status_message_id = None
        scan = await _db().get_index_scan(channel_id)
        if scan:
            requester_id = scan.get("requester_id")
            status_message_id = scan.get("status_message_id")

        task = asyncio.create_task(
            _run_scan(client, notify_client or client, channel_id, queue_file,
                      is_resume, stop_event, mode)
        )
        handle = _ScanHandle(
            channel_id=channel_id,
            task=task,
            stop_event=stop_event,
            requester_id=requester_id,
            status_message_id=status_message_id,
            mode=mode,
        )
        _scans[channel_id] = handle
        task.add_done_callback(_on_task_done)
        _pending_context.pop(channel_id, None)


def _on_task_done(task: asyncio.Task) -> None:
    """Remove only this scan's entry, then promote a waiting scan if possible."""
    for channel_id, handle in list(_scans.items()):
        if handle.task is task:
            _scans.pop(channel_id, None)
            break
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        exc = None
    if exc:
        LOGGER.error(f"Index scan task crashed: {exc}")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — callbacks always run in the loop
        return
    loop.create_task(_promote_waiting())


async def _promote_waiting() -> None:
    """Launch the oldest waiting scan when a concurrency slot frees up."""
    cap = scan_cap()
    if running_count() >= cap:
        return
    waiting = await _db().get_waiting_index_scans()
    if not waiting:
        return

    for scan in waiting:
        if running_count() >= cap:
            return
        channel_id = scan.get("channel_id")
        if not channel_id or channel_id in _scans:
            continue
        if not is_auth_channel_id(channel_id):
            await _db().update_index_scan(
                channel_id,
                {"status": "error", "error": "Channel is no longer in AUTH_CHANNEL",
                 "finished_at": datetime.utcnow()},
            )
            continue

        context = _pending_context.get(channel_id) or _default_context
        client = context.get("client")
        queue_file = context.get("queue_file")
        if client is None or queue_file is None:
            # No launch context at all — leave the row waiting so an operator
            # can see it in /indexstatus rather than silently dropping it.
            LOGGER.warning(
                f"Waiting index scan {channel_id} has no launch context; "
                "it will be picked up when the bot restarts"
            )
            continue

        await _db().update_index_scan(
            channel_id, {"status": "running", "resumed_at": datetime.utcnow()}
        )
        try:
            await _launch(client, channel_id, queue_file, is_resume=True,
                          notify_client=context.get("notify_client"),
                          mode=context.get("mode") or MODE_INDEX)
            LOGGER.info(f"Promoted waiting index scan {channel_id}")
        except IndexBusyError:
            return
        except Exception as e:
            LOGGER.error(f"Failed to promote waiting index scan {channel_id}: {e}")
            await _db().update_index_scan(channel_id, {"status": "error", "error": str(e)[:300]})
            return


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
    def __init__(self, client, requester_id, channel_id, title, message_id=None,
                 mode: str = MODE_INDEX):
        self.client = client
        self.requester_id = requester_id
        self.channel_id = channel_id
        self.title = title
        self.message_id = message_id
        self.mode = mode
        self._last_edit = 0.0

    def _text(self, stats: Dict[str, int], cursor, elapsed: float, note: str) -> str:
        cursor_text = f"#{cursor}" if cursor else "from newest"
        heading = "Auditing" if self.mode == MODE_AUDIT else "Indexing"
        lines = [
            f"<b>{heading} {html.escape(self.title)}</b>",
            f"Channel: <code>{self.channel_id}</code>",
            f"Status: {html.escape(note)}",
            "",
            f"Checked: <code>{stats['checked']:,}</code> messages",
            f"Queued: <code>{stats['queued']:,}</code>",
            f"Already in DB: <code>{stats['existing']:,}</code>",
            f"Non-video: <code>{stats['non_video']:,}</code>",
            f"Deleted/empty: <code>{stats['deleted']:,}</code>",
            f"Errors: <code>{stats['errors']:,}</code>",
        ]
        if self.mode == MODE_AUDIT:
            lines.insert(-1, f"Orphans: <code>{stats.get('orphans', 0):,}</code>")
        if stats.get("skipped", 0):
            lines.insert(-1, f"Skipped newest: <code>{stats['skipped']:,}</code>")
        lines.extend([
            f"Cursor: <code>{cursor_text}</code>",
            f"Elapsed: <code>{_format_elapsed(elapsed)}</code>",
            "",
            f"Speed: {int(runtime_config.get('scan_batch_size'))} msgs every "
            f"{runtime_config.get('scan_batch_delay')}s; "
            f"pauses at queue backlog {int(runtime_config.get('max_queue_backlog')):,}",
        ])
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
        if not self.requester_id:
            # Panel and orphan scans have no Telegram user to report to.
            # Telegram rejects peer id 0, so skip the edit/send entirely.
            return

        text = self._text(stats, cursor, elapsed, note)
        keyboard = InlineKeyboardMarkup([])
        if not done:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(
                    "Stop indexing",
                    callback_data=f"idx:stop:{self.channel_id}",
                )]]
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
            await _db().update_index_scan(self.channel_id, {"status_message_id": message.id})
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


async def _fetch_batch(client, channel_id: int, offset_id: Optional[int],
                       limit: Optional[int] = None):
    """Read one history page. ``limit`` overrides the configured batch size —
    the skip phase needs a page no larger than the number of posts left to
    ignore, otherwise it would walk past the admin's requested count.
    """
    last_error = None
    batch_size = int(limit) if limit else int(runtime_config.get("scan_batch_size"))
    for attempt in range(1, IndexConfig.FLOODWAIT_MAX_RETRIES + 1):
        try:
            await _governor.acquire(batch_size)
            kwargs = {"limit": batch_size}
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
            # One channel hitting a rate limit backs off every scan.
            await _governor.penalty(wait_seconds)
            await asyncio.sleep(wait_seconds)
        except Exception as e:
            last_error = e
            text = str(e)
            if "BOT_METHOD_INVALID" in text or "can't be used by bots" in text:
                raise RuntimeError(
                    "Channel history cannot be read with a bot account "
                    "(BOT_METHOD_INVALID). Set USER_SESSION_STRING to a user-account "
                    "session that is a member of the channel and restart."
                ) from e
            if any(code in text for code in ("CHANNEL_PRIVATE", "USER_NOT_PARTICIPANT")):
                raise RuntimeError(
                    "The indexing account cannot read this channel. Join the channel "
                    "with the USER_SESSION_STRING account (or add the bot as admin) "
                    "and retry."
                ) from e
            LOGGER.warning(f"Index scan batch fetch failed: {e}")
            await asyncio.sleep(min(60, 5 * attempt))
    raise RuntimeError(f"History fetch failed: {last_error}")


async def _record_orphan(channel_bare: str, message: Message, media, mode: str) -> None:
    """Store a video post that has no database match."""
    doc = {
        "_id": f"{channel_bare}:{message.id}",
        "channel": channel_bare,
        "message_id": message.id,
        "date": message.date.isoformat() if getattr(message, "date", None) else None,
        "file_unique_id": getattr(media, "file_unique_id", None),
        "size": getattr(media, "file_size", None),
        "title": (getattr(message, "caption", None) or getattr(media, "file_name", None) or "")[:240] or None,
        "scanned_at": datetime.utcnow(),
        "job": channel_bare,
    }
    try:
        await _db().upsert_orphan_file(doc)
    except Exception as e:
        LOGGER.warning(f"Could not record orphan {doc['_id']}: {e}")


async def _process_message(
    channel_bare: str,
    message: Message,
    stats: Dict[str, int],
    queue_file: QueueFileCallback,
    stop_event: asyncio.Event,
    mode: str = MODE_INDEX,
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
        exists = await _db().is_file_exists_strict(int(channel_bare), message.id, hash_val)
    except Exception as e:
        # A failed check must not be treated as "not indexed": retry the batch
        # instead of skipping files while the database is unavailable.
        raise RuntimeError(f"DB check failed for message {message.id}: {e}") from e
    if exists:
        stats["existing"] += 1
        return

    if mode == MODE_AUDIT:
        # Audit mode never enqueues — it only reports the gap.
        stats["orphans"] = int(stats.get("orphans", 0)) + 1
        await _record_orphan(channel_bare, message, media, mode)
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
    max_backlog = int(runtime_config.get("max_queue_backlog"))
    if not QueueConfig.ENABLE_MONGODB_QUEUE or max_backlog <= 0:
        return True

    while not stop_event.is_set():
        try:
            queue_stats = await _db().get_queue_stats()
        except Exception:
            return True
        backlog = queue_stats.get("pending", 0) + queue_stats.get("processing", 0)
        if backlog < max_backlog:
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
        usage = await _db().get_storage_usage()
    except Exception:
        return None
    stop_percent = int(runtime_config.get("storage_stop_percent"))
    full = [u for u in usage if u.get("used_percent", 0) >= stop_percent]
    if not full:
        return None
    return ", ".join(
        f"cluster {u['cluster']} at {u['used_percent']}% ({u['used_mb']}/{u['quota_mb']} MB)"
        for u in full
    )


async def _finish(progress: _Progress, stats, cursor, elapsed, status: str, note: str) -> None:
    await progress.update(stats, cursor, elapsed, note=note, force=True, done=True)
    await _db().update_index_scan(
        progress.channel_id,
        {
            "status": status,
            "finished_at": datetime.utcnow(),
            "elapsed_seconds": elapsed,
        },
    )
    LOGGER.info(f"Index scan {status} for {progress.channel_id}: {stats}")


async def _skip_newest_messages(
    progress: _Progress,
    history_client,
    channel_id: int,
    stats: Dict[str, int],
    cursor,
    elapsed,
    stop_event: asyncio.Event,
    skip_total: int,
):
    """Advance the cursor past the newest posts without indexing them.

    The count and cursor are written after every page. A restart therefore
    resumes at exactly the right post instead of re-skipping (which would eat
    further into history) or forgetting the admin's answer entirely.
    """
    remaining = max(0, int(skip_total) - int(stats.get("skipped", 0)))
    while remaining > 0 and not stop_event.is_set():
        page_size = min(int(runtime_config.get("scan_batch_size")), remaining)
        batch = await _fetch_batch(history_client, channel_id, cursor,
                                   limit=page_size)
        if not batch:
            break

        processed = 0
        for message in batch:
            if remaining <= 0 or stop_event.is_set():
                break
            stats["skipped"] = int(stats.get("skipped", 0)) + 1
            remaining -= 1
            processed += 1

        if processed:
            # History is newest -> oldest; the oldest processed id becomes the
            # offset for the next read so nothing is read twice.
            cursor = min(m.id for m in batch[:processed])
            await _db().update_index_scan(
                channel_id,
                {
                    "next_offset_id": cursor,
                    "skipped": stats["skipped"],
                    "elapsed_seconds": elapsed(),
                    "status": "running",
                },
            )
            await progress.update(
                stats, cursor, elapsed(),
                note=f"skipping newest {skip_total} posts ({stats['skipped']} done)",
                force=True,
            )
    return cursor


async def _run_scan(
    history_client,
    notify_client,
    channel_id: int,
    queue_file: QueueFileCallback,
    is_resume: bool,
    stop_event: asyncio.Event,
    mode: str = MODE_INDEX,
) -> None:
    scan = await _db().get_index_scan(channel_id)
    if not scan or scan.get("status") != "running":
        return

    title = scan.get("title") or str(channel_id)
    requester_id = scan.get("requester_id")
    progress = _Progress(notify_client, requester_id, channel_id, title,
                         scan.get("status_message_id"), mode=mode)
    stats = {key: int(scan.get(key) or 0) for key in STAT_KEYS}
    cursor = scan.get("next_offset_id")
    elapsed_base = float(scan.get("elapsed_seconds") or 0)
    run_started = time.monotonic()
    channel_bare = scan.get("channel") or clean_channel_id(channel_id)

    def elapsed() -> float:
        return elapsed_base + (time.monotonic() - run_started)

    try:
        await history_client.get_chat(channel_id)
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

    # ── "Skip the newest N messages" -----------------------------------------
    # A fresh scan with a skip answer walks past those posts first, persisting
    # the cursor after each page. A resumed scan continues from its saved
    # cursor/count and never re-skips the same posts.
    skip_total = int(scan.get("skip_first") or 0)
    if skip_total > int(stats.get("skipped", 0)):
        try:
            cursor = await _skip_newest_messages(
                progress, history_client, channel_id, stats, cursor,
                elapsed, stop_event, skip_total,
            )
        except Exception as e:
            LOGGER.error(f"Index scan skip phase failed for {channel_id}: {e}")
            await _db().update_index_scan(channel_id, {"error": str(e)[:300]})
            await _finish(progress, stats, cursor, elapsed(), "error",
                          f"could not skip newest posts: {e}")
            return
        if stop_event.is_set():
            await _finish(progress, stats, cursor, elapsed(), "cancelled", "stopped by admin")
            return

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
            batch = await _fetch_batch(history_client, channel_id, cursor)
        except Exception as e:
            consecutive_failures += 1
            LOGGER.error(
                f"Index scan batch failure ({consecutive_failures}/"
                f"{IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES}): {e}"
            )
            if consecutive_failures >= IndexConfig.MAX_CONSECUTIVE_BATCH_FAILURES:
                await _db().update_index_scan(channel_id, {"error": str(e)[:300]})
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
                await _process_message(channel_bare, message, stats, queue_file,
                                       stop_event, mode=mode)
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
                await _db().update_index_scan(channel_id, {"error": str(batch_error)[:300]})
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
        await _db().update_index_scan(
            channel_id,
            {
                "next_offset_id": cursor,
                "checked": stats["checked"],
                "queued": stats["queued"],
                "existing": stats["existing"],
                "non_video": stats["non_video"],
                "deleted": stats["deleted"],
                "errors": stats["errors"],
                "orphans": int(stats.get("orphans", 0)),
                "skipped": int(stats.get("skipped", 0)),
                "elapsed_seconds": elapsed(),
                "status": "running",
            },
        )
        await progress.update(stats, cursor, elapsed(), note="scanning history")

        if stop_event.is_set():
            await _finish(progress, stats, cursor, elapsed(), "cancelled", "stopped by admin")
            return

        delay = float(runtime_config.get("scan_batch_delay"))
        if delay > 0:
            await asyncio.sleep(delay)
