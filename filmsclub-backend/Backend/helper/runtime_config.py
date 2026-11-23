"""Runtime configuration store: live overrides for queue/index knobs.

Every knob has an env-derived default that already exists in ``QueueConfig`` /
``IndexConfig``. The effective value of a knob is the stored override when one
is present and the env-derived default otherwise, so a deployment that never
writes an override behaves exactly as it does today.

The store keeps a single in-process snapshot refreshed by a background poll.
``get()`` is synchronous so a worker can read a knob without awaiting a Mongo
round-trip on every iteration. A failed refresh keeps the previous snapshot;
if there has never been a snapshot the env default is returned. Config storage
must never stall a worker, so every failure path degrades to the default.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from Backend.config import AdminOpsConfig, IndexConfig, QueueConfig, Telegram
from Backend.logger import LOGGER

# ``app_settings`` document that holds the overrides.
CONFIG_DOC_ID = "runtime_config"

# How long a stored override survives without a refresh.
POLL_SECONDS = max(1.0, float(AdminOpsConfig.RUNTIME_CONFIG_POLL_SECONDS))


class KnobError(ValueError):
    """Raised for an unknown key or a value that fails validation."""


def _cfg_file_queue_delay() -> int:
    return int(QueueConfig.FILE_QUEUE_DELAY)


def _cfg_batch_size() -> int:
    return int(QueueConfig.BATCH_SIZE)


def _cfg_batch_gap_seconds() -> int:
    return int(QueueConfig.BATCH_GAP_SECONDS)


def _cfg_queue_workers() -> int:
    return int(QueueConfig.QUEUE_WORKERS)


def _cfg_max_qualities_per_title() -> int:
    return int(QueueConfig.MAX_QUALITIES_PER_TITLE)


def _cfg_scan_batch_size() -> int:
    return int(IndexConfig.SCAN_BATCH_SIZE)


def _cfg_scan_batch_delay() -> float:
    return float(IndexConfig.SCAN_BATCH_DELAY)


def _cfg_max_queue_backlog() -> int:
    return int(IndexConfig.MAX_QUEUE_BACKLOG)


def _cfg_storage_stop_percent() -> int:
    return int(IndexConfig.STORAGE_STOP_PERCENT)


def _cfg_index_max_concurrent_scans() -> int:
    # New capability: today's behaviour is exactly one scan at a time.
    return 1


def _cfg_index_skip_first_messages() -> int:
    """Default number of newest posts a scan ignores (0 = today's behaviour)."""
    return max(0, int(IndexConfig.INDEX_SKIP_FIRST_MESSAGES))


def _cfg_use_caption() -> bool:
    return bool(Telegram.USE_CAPTION)


def _cfg_use_tmdb() -> bool:
    return bool(Telegram.USE_TMDB)


# ── The knob table ──────────────────────────────────────────────────────────
# key -> {type, min, max, default, live, label}
#
# ``default`` is a callable so the env-derived value is re-read on every call
# (tests and a changed .env are then honoured without a restart).
KNOBS: Dict[str, Dict[str, Any]] = {
    "file_queue_delay": {
        "type": int, "min": 0, "max": 60,
        "default": _cfg_file_queue_delay,
        "live": "live — applies to the next file",
        "label": "Delay between files (s)",
    },
    "batch_size": {
        "type": int, "min": 5, "max": 1000,
        "default": _cfg_batch_size,
        "live": "live — applies to the next batch boundary",
        "label": "Files per batch",
    },
    "batch_gap_seconds": {
        "type": int, "min": 0, "max": 600,
        "default": _cfg_batch_gap_seconds,
        "live": "live — applies to the next batch boundary",
        "label": "Gap between batches (s)",
    },
    "queue_workers": {
        "type": int, "min": 1, "max": 16,
        "default": _cfg_queue_workers,
        "live": "via supervisor — workers are spawned/retired at runtime",
        "label": "Queue workers",
    },
    "max_qualities_per_title": {
        "type": int, "min": 1, "max": 12,
        "default": _cfg_max_qualities_per_title,
        "live": "live — applies to the next insert",
        "label": "Max qualities per title",
    },
    "scan_batch_size": {
        "type": int, "min": 20, "max": 500,
        "default": _cfg_scan_batch_size,
        "live": "live — applies to the next history batch",
        "label": "History messages per batch",
    },
    "scan_batch_delay": {
        "type": float, "min": 0.0, "max": 60.0,
        "default": _cfg_scan_batch_delay,
        "live": "live — applies to the next history batch",
        "label": "Delay between history batches (s)",
    },
    "max_queue_backlog": {
        "type": int, "min": 100, "max": 50000,
        "default": _cfg_max_queue_backlog,
        "live": "live — applies to the next backlog poll",
        "label": "Queue backlog that pauses a scan",
    },
    "storage_stop_percent": {
        "type": int, "min": 50, "max": 99,
        "default": _cfg_storage_stop_percent,
        "live": "live — applies to the next storage check",
        "label": "Cluster usage % that stops a scan",
    },
    "index_max_concurrent_scans": {
        "type": int, "min": 1, "max": 8,
        "default": _cfg_index_max_concurrent_scans,
        "live": "live — applies to the next scan launch",
        "label": "Concurrent channel scans",
    },
    "index_skip_first_messages": {
        "type": int, "min": 0, "max": 100000,
        "default": _cfg_index_skip_first_messages,
        "live": "applies to scans started after the change (running scans keep their own value)",
        "label": "Skip newest messages when indexing",
    },
    "use_caption": {
        "type": bool,
        "default": _cfg_use_caption,
        "live": "live — applies to the next incoming message",
        "label": "Use captions for titles",
    },
    "use_tmdb": {
        "type": bool,
        "default": _cfg_use_tmdb,
        "live": "live — applies to the next incoming message",
        "label": "Use TMDB for metadata",
    },
}

# Knobs whose value cannot be applied without a restart.
RESTART_ONLY_KEYS = {"queue_workers"} if QueueConfig.ENABLE_MONGODB_QUEUE else set()


def knob_default(key: str) -> Any:
    """Env-derived default for ``key`` (no override applied)."""
    knob = KNOBS.get(key)
    if knob is None:
        raise KnobError(f"Unknown runtime config key: {key}")
    return knob["default"]()


def _coerce(key: str, knob: Dict[str, Any], value: Any) -> Any:
    """Validate ``value`` against the knob's type and range."""
    kind = knob["type"]

    if kind is bool:
        if isinstance(value, bool):
            out: Any = value
        elif isinstance(value, (int, float)) and value in (0, 1):
            out = bool(value)
        elif isinstance(value, str) and value.strip().lower() in ("true", "yes", "on", "1"):
            out = True
        elif isinstance(value, str) and value.strip().lower() in ("false", "no", "off", "0"):
            out = False
        else:
            raise KnobError(f"'{key}' expects a boolean")
        return out

    if kind is int:
        if isinstance(value, bool):
            raise KnobError(f"'{key}' expects an integer")
        if isinstance(value, float) and not float(value).is_integer():
            raise KnobError(f"'{key}' expects a whole number")
        try:
            out = int(value)
        except (TypeError, ValueError):
            raise KnobError(f"'{key}' expects an integer")
    elif kind is float:
        if isinstance(value, bool):
            raise KnobError(f"'{key}' expects a number")
        try:
            out = float(value)
        except (TypeError, ValueError):
            raise KnobError(f"'{key}' expects a number")
    else:  # pragma: no cover — table is closed
        raise KnobError(f"'{key}' has an unsupported type")

    low, high = knob.get("min"), knob.get("max")
    if low is not None and out < low:
        raise KnobError(f"'{key}' must be >= {low}")
    if high is not None and out > high:
        raise KnobError(f"'{key}' must be <= {high}")
    return out


def _db():
    """Late import: ``Backend.__init__`` builds the Database instance."""
    from Backend import db
    return db


class RuntimeConfigStore:
    """In-process snapshot of the persisted runtime-config overrides."""

    def __init__(self) -> None:
        self._overrides: Dict[str, Any] = {}
        self._loaded = False
        self._poll_task: Optional[asyncio.Task] = None
        self._write_lock = asyncio.Lock()

    # ── reads ───────────────────────────────────────────────────────────────
    def get(self, key: str) -> Any:
        """Effective value of ``key`` — override if present, else the default.

        Synchronous on purpose: this is called from inside hot loops.
        """
        knob = KNOBS.get(key)
        if knob is None:
            raise KnobError(f"Unknown runtime config key: {key}")
        if key in self._overrides:
            return self._overrides[key]
        return knob["default"]()

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def snapshot(self) -> Dict[str, Any]:
        """Effective value of every knob."""
        return {key: self.get(key) for key in KNOBS}

    def overrides(self) -> Dict[str, Any]:
        """Only the persisted overrides (not the effective values)."""
        return dict(self._overrides)

    def is_overridden(self, key: str) -> bool:
        return key in self._overrides

    def describe(self) -> Dict[str, Any]:
        """Panel-friendly view: effective value, override state and metadata."""
        rows = []
        for key, knob in KNOBS.items():
            rows.append({
                "key": key,
                "label": knob.get("label", key),
                "type": "bool" if knob["type"] is bool else knob["type"].__name__,
                "min": knob.get("min"),
                "max": knob.get("max"),
                "value": self.get(key),
                "default": knob["default"](),
                "overridden": self.is_overridden(key),
                "live": knob.get("live", ""),
                "restart_only": key in RESTART_ONLY_KEYS,
            })
        return {"knobs": rows, "loaded": self._loaded}

    # ── refresh ─────────────────────────────────────────────────────────────
    async def refresh(self) -> bool:
        """Reload overrides from Mongo. Never raises."""
        try:
            collection = _db().settings_collection
            doc = await collection.find_one({"_id": CONFIG_DOC_ID})
        except Exception as e:
            LOGGER.warning(f"Runtime config refresh failed, keeping previous values: {e}")
            return False

        stored = (doc or {}).get("values") or {}
        clean: Dict[str, Any] = {}
        for key, value in stored.items():
            knob = KNOBS.get(key)
            if knob is None:
                # An unknown key is never honoured, even if it reached the DB.
                continue
            try:
                clean[key] = _coerce(key, knob, value)
            except KnobError as e:
                LOGGER.warning(f"Ignoring invalid stored runtime config: {e}")

        self._overrides = clean
        self._loaded = True
        return True

    async def _poll_forever(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover — refresh already guards
                LOGGER.warning(f"Runtime config poll failed: {e}")
            await asyncio.sleep(POLL_SECONDS)

    def start(self) -> Optional[asyncio.Task]:
        """Start the background poller (idempotent)."""
        if self._poll_task and not self._poll_task.done():
            return self._poll_task
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._poll_task = loop.create_task(self._poll_forever())
        return self._poll_task

    async def stop(self) -> None:
        task = self._poll_task
        self._poll_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ── writes ──────────────────────────────────────────────────────────────
    async def set(self, key: str, value: Any, updated_by: str = "panel") -> Any:
        """Persist an override for ``key`` and apply it immediately."""
        knob = KNOBS.get(key)
        if knob is None:
            raise KnobError(f"Unknown runtime config key: {key}")
        coerced = _coerce(key, knob, value)

        async with self._write_lock:
            collection = _db().settings_collection
            # Atomic dotted write: never rewrite the whole values map, so a
            # concurrent writer (or a transient failed read) cannot clobber
            # other overrides.
            await collection.update_one(
                {"_id": CONFIG_DOC_ID},
                {"$set": {
                    f"values.{key}": coerced,
                    "updated_by": str(updated_by)[:120],
                    "updated_at": datetime.utcnow(),
                }},
                upsert=True,
            )
            self._overrides[key] = coerced
            self._loaded = True
        return coerced

    async def set_bool(self, key: str, value: bool, updated_by: str = "panel") -> bool:
        return bool(await self.set(key, bool(value), updated_by=updated_by))

    async def toggle_bool(self, key: str, updated_by: str = "panel") -> bool:
        """Flip a boolean knob and persist the new value."""
        if KNOBS.get(key, {}).get("type") is not bool:
            raise KnobError(f"'{key}' is not a boolean knob")
        return await self.set_bool(key, not self.get_bool(key), updated_by=updated_by)

    async def unset(self, key: str, updated_by: str = "panel") -> bool:
        """Drop an override so the knob falls back to its env default."""
        if key not in KNOBS:
            raise KnobError(f"Unknown runtime config key: {key}")
        async with self._write_lock:
            collection = _db().settings_collection
            await collection.update_one(
                {"_id": CONFIG_DOC_ID},
                {
                    "$set": {
                        "updated_by": str(updated_by)[:120],
                        "updated_at": datetime.utcnow(),
                    },
                    "$unset": {f"values.{key}": ""},
                },
                upsert=True,
            )
            self._overrides.pop(key, None)
            self._loaded = True
        return True


# Process-wide store. ``get()`` is safe before the poller ever runs — it
# returns the env default until the first successful refresh.
runtime_config = RuntimeConfigStore()
