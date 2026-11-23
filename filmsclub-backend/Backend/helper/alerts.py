"""Telegram alert push.

Every ``ADMIN_ALERT_INTERVAL`` seconds the watchdog evaluates the same data the
Health route builds and turns it into an *alert-key set* rather than prose. A
key that appears sends one message, a key that disappears sends one recovery
line, and an unchanged key sends nothing — so the admin is told once per
change, not once per tick.

The state lives in ``app_settings`` under ``_id="alerts"``, so a restart does
not re-send alerts that were already delivered. A send failure logs and
retries on the next tick without marking the key as sent, so an outage can
never be silently swallowed.
"""

import asyncio
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

from Backend.config import AdminOpsConfig, Telegram
from Backend.helper.runtime_config import runtime_config
from Backend.logger import LOGGER

ALERTS_DOC_ID = "alerts"

# Queue backlog (pending items) that raises an alert.
QUEUE_BACKLOG_THRESHOLD = 1000


def _db():
    """Late import: ``Backend.__init__`` builds the Database instance.

    Resolved on every call so the module never holds a stale handle — the
    same pattern ``audit`` and ``runtime_config`` use.
    """
    from Backend import db
    return db


async def _load_state() -> Dict[str, Any]:
    try:
        doc = await _db().settings_collection.find_one({"_id": ALERTS_DOC_ID})
    except Exception as e:
        LOGGER.warning(f"Alert state read failed: {e}")
        return {}
    return doc or {}


async def _save_state(state: Dict[str, Any]) -> None:
    try:
        await _db().settings_collection.update_one(
            {"_id": ALERTS_DOC_ID},
            {"$set": dict(state)},
            upsert=True,
        )
    except Exception as e:
        LOGGER.warning(f"Alert state write failed: {e}")


async def _send(text: str) -> Optional[int]:
    """Send one alert line. Returns the Telegram message id, or None."""
    try:
        from Backend.wzgram_pkg import StreamBot
        from wzgram.enums import ParseMode

        if StreamBot is None:
            LOGGER.warning("Alert not sent: the bot client is unavailable")
            return None
        chat_id = int(AdminOpsConfig.ADMIN_ALERT_CHAT_ID or Telegram.OWNER_ID)
        message = await StreamBot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        return getattr(message, "id", None)
    except Exception as e:
        LOGGER.warning(f"Alert send failed: {e}")
        return None


async def collect_alert_keys() -> List[Dict[str, Any]]:
    """Evaluate the health data and return the current alert-key set.

    Each entry is ``{"key", "message", "level"}``. The key is stable so the
    watchdog can tell "new" from "unchanged" from "recovered".
    """
    alerts: List[Dict[str, Any]] = []
    database = _db()

    # ── cluster reachability ──
    for cluster in getattr(database, "clusters", None) or []:
        try:
            await cluster.db.command("ping")
        except Exception as e:
            alerts.append({
                "key": f"cluster_down:{cluster.index}",
                "level": "critical",
                "message": (
                    f"🔴 <b>Cluster {cluster.index} is unreachable</b>\n"
                    f"{str(e)[:180]}"
                ),
            })

    # ── queue backlog + FloodWait ──
    try:
        from Backend.fastapi.main import _queue_snapshot
        queue = await _queue_snapshot()
    except Exception as e:
        LOGGER.warning(f"Alert queue snapshot failed: {e}")
        queue = {}

    pending = int(queue.get("pending") or 0)
    if pending >= QUEUE_BACKLOG_THRESHOLD:
        alerts.append({
            "key": "queue_backlog",
            "level": "warning",
            "message": (
                f"🟠 <b>Queue backlog is {pending:,}</b> pending items "
                f"(threshold {QUEUE_BACKLOG_THRESHOLD:,})."
            ),
        })

    floodwait = int(queue.get("floodwait_seconds") or 0)
    if floodwait:
        alerts.append({
            "key": "floodwait",
            "level": "warning",
            "message": f"🟠 <b>Telegram FloodWait active</b> — {floodwait}s remaining.",
        })

    # ── storage ──
    try:
        stop_percent = int(runtime_config.get("storage_stop_percent"))
        usage = await database.get_storage_usage()
        for entry in usage:
            used = float(entry.get("used_percent") or 0)
            if used >= stop_percent:
                alerts.append({
                    "key": f"storage:{entry.get('cluster')}",
                    "level": "warning",
                    "message": (
                        f"🟠 <b>Cluster {entry.get('cluster')} storage at {used}%</b> "
                        f"({entry.get('used_mb')}/{entry.get('quota_mb')} MB) — "
                        f"scans pause above {stop_percent}%."
                    ),
                })
    except Exception as e:
        LOGGER.warning(f"Alert storage check failed: {e}")

    # ── scans in error ──
    try:
        scans = await database.list_index_scans(50)
        for scan in scans:
            if scan.get("status") != "error":
                continue
            channel_id = scan.get("channel_id")
            alerts.append({
                "key": f"scan_error:{channel_id}",
                "level": "warning",
                "message": (
                    f"🟠 <b>Index scan failed for channel {channel_id}</b>\n"
                    f"{str(scan.get('error') or 'unknown error')[:180]}"
                ),
            })
    except Exception as e:
        LOGGER.warning(f"Alert scan check failed: {e}")

    # ── ffmpeg ──
    if not shutil.which("ffmpeg"):
        alerts.append({
            "key": "ffmpeg_missing",
            "level": "critical",
            "message": (
                "🔴 <b>ffmpeg is not installed</b> — probing, remuxing and "
                "subtitle extraction are unavailable."
            ),
        })

    return alerts


async def evaluate_and_notify() -> Dict[str, Any]:
    """Compare the current alert set with the last one and notify on changes."""
    current = await collect_alert_keys()
    current_map = {alert["key"]: alert for alert in current}

    state = await _load_state()
    last_keys = set(state.get("last_keys") or [])
    per_key = dict(state.get("per_key") or {})
    now = datetime.utcnow()

    next_keys: set = set()
    sent: List[str] = []
    failed: List[str] = []

    for key in sorted(current_map):
        if key in last_keys:
            next_keys.add(key)
            continue
        alert = current_map[key]
        message_id = await _send(alert["message"])
        if message_id is None:
            # Not marked as sent — the next tick retries it.
            failed.append(key)
            continue
        next_keys.add(key)
        sent.append(key)
        entry = dict(per_key.get(key) or {})
        entry.setdefault("first_seen", now.isoformat())
        entry["last_message_id"] = message_id
        entry["level"] = alert.get("level", "warning")
        per_key[key] = entry

    recovered: List[str] = []
    for key in sorted(last_keys - set(current_map)):
        message_id = await _send(f"✅ <b>Recovered</b> — {key} is no longer active.")
        if message_id is None:
            failed.append(key)
            # Keep the key active so the recovery is retried on the next tick
            # instead of being dropped from the state entirely.
            next_keys.add(key)
            continue
        recovered.append(key)
        per_key.pop(key, None)

    await _save_state({
        "last_keys": sorted(next_keys),
        "last_sent_at": now.isoformat(),
        "per_key": per_key,
    })

    if sent or recovered:
        LOGGER.info(
            f"Alerts: {len(sent)} raised, {len(recovered)} recovered, {len(failed)} failed"
        )
    return {"sent": sent, "recovered": recovered, "failed": failed,
            "active": sorted(next_keys)}


async def alert_watchdog() -> None:
    """Evaluate the alert set on a fixed interval, forever."""
    if not AdminOpsConfig.ADMIN_ALERT_ENABLED:
        LOGGER.info("🔕 Telegram alerts disabled (ADMIN_ALERT_ENABLED=false)")
        return

    interval = max(30, int(AdminOpsConfig.ADMIN_ALERT_INTERVAL))
    LOGGER.info(
        f"🔔 Telegram alert watchdog started (every {interval}s, chat "
        f"{AdminOpsConfig.ADMIN_ALERT_CHAT_ID})"
    )
    while True:
        try:
            await evaluate_and_notify()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            LOGGER.warning(f"Alert watchdog tick failed: {e}")
        await asyncio.sleep(interval)
