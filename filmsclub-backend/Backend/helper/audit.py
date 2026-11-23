"""Admin audit log.

One immutable document per admin mutation, whatever the source: the web panel
or a Telegram command. The writer is deliberately fire-and-forget — a failed
audit write is logged and swallowed, and never changes the outcome of the
action it records.

Documents live in the ``admin_audit`` collection on the ops cluster, so they
follow the ops relocation (see ``Database._copy_ops_state``).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, Optional, Set

from Backend.config import AdminOpsConfig
from Backend.logger import LOGGER

# ``detail`` is a bounded summary — never a full document dump.
MAX_DETAIL_CHARS = 1000

# Keep strong references to in-flight audit writes so they are not garbage
# collected mid-flight.
_PENDING: Set[asyncio.Task] = set()

# Patterns that must never reach the audit log. Login credentials and session
# strings are the realistic risks; the panel also lets an admin paste free text.
#
# The key name is kept so the trail still says *what* was touched — only the
# value is dropped. Keys are matched as whole identifiers (so ``USER_SESSION_STRING``
# is caught, not just ``session_string``), which means the pattern cannot rely on
# ``\b``: an underscore is a word character, so there is no boundary between
# ``USER_`` and ``SESSION``.
_SECRET_KEYWORDS = (
    "password", "passwd", "secret", "apikey", "api_key", "api-key",
    "authkey", "auth_key", "auth-key", "authorization", "access_key",
    "accesskey", "access-key", "private_key", "privatekey", "client_secret",
    "clientsecret", "session_string", "session-string", "sessionstring",
    "session_id", "session-id", "sessionid", "session", "token", "cookie",
)
# Longest first so the alternation prefers the specific spelling.
_SECRET_CORE = "|".join(
    re.escape(word) for word in sorted(_SECRET_KEYWORDS, key=len, reverse=True)
)

# A credential value, optionally preceded by an auth scheme ("Bearer <jwt>").
# ``&`` is excluded so URL parameters are redacted one at a time rather than
# swallowing the rest of the query string.
_SECRET_VALUE = (
    r"(?:(?:Bearer|Basic|Digest|Token)\s+)?"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"
)

# ``key = value`` / ``key: value`` / ``"key": "value"`` where the key contains
# a secret keyword.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<key>[\"']?[A-Za-z0-9_.\-]*(?:" + _SECRET_CORE + r")[A-Za-z0-9_.\-]*[\"']?)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>" + _SECRET_VALUE + r")"
)

# Prose form — "the password is hunter2". Standalone keywords only, and the
# value must look like a credential, so ordinary sentences survive intact.
_PROSE_CORE = "|".join((
    "password", "passwd", "secret", "apikey", "api[ _-]?key",
    "auth[ _-]?key", "access[ _-]?key", "private[ _-]?key",
    "client[ _-]?secret", "session[ _-]?string", "user[ _-]?session",
    "authorization", "token", "cookie",
))
_PROSE_RE = re.compile(
    r"(?i)\b(?:" + _PROSE_CORE + r")\b\s+(?:is\s+|was\s+|are\s+|=|:)?\s*"
    r"(?P<value>\"[^\"]*\"|'[^']*'|\S+)"
)

# Common words that follow a secret keyword in ordinary prose but are not
# credentials ("the token is invalid").
_PROSE_STOPWORDS = {
    "invalid", "required", "expired", "updated", "missing", "incorrect",
    "unknown", "error", "empty", "present", "rotated", "revoked", "correct",
    "wrong", "mismatch", "unchanged", "changed", "disabled", "enabled",
    "active", "inactive", "verified", "unverified", "expired", "provided",
    "cleared", "deleted", "reset", "renewed", "not", "the", "a", "an", "and",
    "is", "was", "has", "been", "will", "should", "must", "cannot", "no",
}

# A bare session string with no ``key=`` in front of it — the value must be
# long enough to be a real credential. Only the space-separated form is handled
# here; the ``key=value`` form is already covered by the assignment pattern,
# and running both would drop the key name from the trail.
_SECRET_BARE_RE = re.compile(
    r"(?i)\b(?P<key>user[ _-]?session|session[ _-]?string)\b\s+(?P<value>\S{12,})"
)


def _db():
    """Late import: ``Backend.__init__`` builds the Database instance."""
    from Backend import db
    return db


def _looks_like_credential(value: str) -> bool:
    """Heuristic for the prose form — a word alone is not a secret."""
    text = value.strip().strip("\"'").lower()
    if not text or text in _PROSE_STOPWORDS:
        return False
    has_digit = any(ch.isdigit() for ch in text)
    has_upper = any(ch.isupper() for ch in value)
    has_lower = any(ch.islower() for ch in value)
    if has_digit and (has_upper or has_lower):
        return True
    if len(text) >= 16 and has_upper and has_lower:
        return True
    return len(text) >= 10


def _redact(text: str) -> str:
    def _mask_assignment(match):
        return f"{match.group('key')}{match.group('sep')}[redacted]"

    def _mask_prose(match):
        value = match.group("value")
        if not _looks_like_credential(value):
            return match.group(0)
        # Keep the keyword, drop the value.
        return match.group(0)[: match.start("value") - match.start(0)] + "[redacted]"

    text = _SECRET_ASSIGNMENT_RE.sub(_mask_assignment, text)
    text = _PROSE_RE.sub(_mask_prose, text)
    return _SECRET_BARE_RE.sub(
        lambda m: f"{m.group('key')} [redacted]", text
    )


def _stringify(detail: Any) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(detail)


def bounded_detail(detail: Any) -> str:
    """Turn ``detail`` into a redacted, length-bounded summary."""
    text = _redact(_stringify(detail))
    if len(text) > MAX_DETAIL_CHARS:
        text = text[: MAX_DETAIL_CHARS - 1] + "…"
    return text


def build_event(
    action: str,
    actor: Any = "system",
    actor_kind: str = "web",
    target: Any = "",
    detail: Any = None,
    result: Any = "ok",
    ip: Any = "",
) -> Dict[str, Any]:
    """Build the audit document. Raises only on programmer error."""
    from bson import ObjectId

    return {
        "_id": ObjectId(),
        "ts": datetime.utcnow(),
        "actor": str(actor)[:120] if actor is not None else "unknown",
        "actor_kind": str(actor_kind)[:16] if actor_kind else "web",
        "action": str(action)[:80],
        "target": str(target)[:200] if target is not None else "",
        "detail": bounded_detail(detail),
        "result": str(result)[:200],
        "ip": str(ip)[:64] if ip else "",
    }


async def _write(doc: Dict[str, Any]) -> None:
    """Persist one audit document. Never raises."""
    try:
        collection = _db().admin_audit_collection
        if collection is None:
            return
        await collection.insert_one(doc)
    except Exception as e:
        LOGGER.warning(f"Audit write failed (action={doc.get('action')}): {e}")


def audit_event(
    action: str,
    actor: Any = "system",
    actor_kind: str = "web",
    target: Any = "",
    detail: Any = None,
    result: Any = "ok",
    ip: Any = "",
) -> Optional[asyncio.Task]:
    """Record one admin mutation.

    Returns the scheduled task, or ``None`` when the event could not be
    scheduled. Never raises — a failed audit write must not change the outcome
    of the action it records.
    """
    try:
        doc = build_event(
            action=action, actor=actor, actor_kind=actor_kind,
            target=target, detail=detail, result=result, ip=ip,
        )
    except Exception as e:
        LOGGER.warning(f"Audit event could not be prepared (action={action}): {e}")
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        LOGGER.warning(f"Audit event dropped, no running loop (action={action})")
        return None

    try:
        task = loop.create_task(_write(doc))
    except Exception as e:
        LOGGER.warning(f"Audit event could not be scheduled (action={action}): {e}")
        return None

    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


async def audit_event_async(
    action: str,
    actor: Any = "system",
    actor_kind: str = "web",
    target: Any = "",
    detail: Any = None,
    result: Any = "ok",
    ip: Any = "",
) -> bool:
    """Await the audit write instead of scheduling it.

    Used where the caller wants the write to be durable before it continues
    (for example a destructive command). Still never raises.
    """
    try:
        doc = build_event(
            action=action, actor=actor, actor_kind=actor_kind,
            target=target, detail=detail, result=result, ip=ip,
        )
    except Exception as e:
        LOGGER.warning(f"Audit event could not be prepared (action={action}): {e}")
        return False
    await _write(doc)
    return True


def audit_telegram(
    action: str,
    user_id: Any,
    target: Any = "",
    detail: Any = None,
    result: Any = "ok",
) -> Optional[asyncio.Task]:
    """Record an action taken from Telegram."""
    return audit_event(
        action=action,
        actor=str(user_id),
        actor_kind="telegram",
        target=target,
        detail=detail,
        result=result,
    )


def retention_days() -> int:
    return max(1, int(AdminOpsConfig.AUDIT_RETENTION_DAYS))
