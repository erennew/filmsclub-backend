"""Opaque, expiring stream tokens and public payload scrubbing.

Public API responses never expose the legacy file identifiers that encode
Telegram chat/message coordinates.  Instead every stream reference is replaced
with a short-lived AES-GCM token that is bound to the file name, so a token
harvested from one page cannot be replayed against another file and stops
working after ``STREAM_TOKEN_TTL`` seconds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import unicodedata
from typing import Any, Dict, Optional
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from Backend.config import Security, Telegram
from Backend.logger import LOGGER

_TOKEN_PREFIX = "fc1."
_TOKEN_AAD = b"filmsclub.stream.v1"
_KEY_CACHE: Optional[bytes] = None

_DROP_KEYS = {
    "_id",
    "chat_id",
    "message_id",
    "msg_id",
    "file_unique_id",
    "unique_id",
    "secure_hash",
    "access_hash",
    "channel",
    "channel_id",
    "telegram_file_id",
    "stream_url",
    "stream_id",
}
_SOURCE_LIST_KEYS = {
    "sources",
    "telegram",
    "subtitles",
    "external_subtitles",
    "subtitle_tracks",
    "audio_tracks",
}
_RENAME_KEYS = {"telegram": "sources"}
_TRACK_DISPLAY_KEYS = ("title", "label", "display_name")

_FILENAME_BAD = re.compile(r'[\x00-\x1f\x7f"\\/:*?<>|;]')
_TELEGRAM_LINK = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/[A-Za-z0-9_+\-/]+",
    re.IGNORECASE,
)
_TELEGRAM_HANDLE = re.compile(
    r"(?<![A-Za-z0-9_])@[A-Za-z][A-Za-z0-9_]{3,31}(?![A-Za-z0-9_])"
)
_TELEGRAM_WORD = re.compile(r"\btelegram\b", re.IGNORECASE)


class StreamTokenError(ValueError):
    """Token is malformed, expired or bound to a different file name."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _load_key() -> bytes:
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE

    source = "STREAM_TOKEN_SECRET"
    secret = Security.STREAM_TOKEN_SECRET
    if not secret:
        source = "ADMIN_SESSION_SECRET"
        secret = Telegram.ADMIN_SESSION_SECRET
    if not secret:
        source = "BOT_TOKEN"
        secret = Telegram.BOT_TOKEN
    if not secret:
        source = "ephemeral"
        secret = secrets.token_urlsafe(48)

    _KEY_CACHE = hashlib.sha256(
        b"filmsclub.stream-token.v1|" + secret.encode("utf-8")
    ).digest()
    if source != "STREAM_TOKEN_SECRET":
        LOGGER.warning(
            "STREAM_TOKEN_SECRET is not set; stream tokens derive from %s. "
            "Set a dedicated STREAM_TOKEN_SECRET in production.",
            source,
        )
    return _KEY_CACHE


def _name_digest(name: str) -> str:
    cleaned = unicodedata.normalize(
        "NFC", os.path.basename((name or "").replace("\\", "/"))
    )
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def mint_stream_token(legacy_id: str, name: str = "", ttl: Optional[int] = None) -> str:
    """Wrap a legacy file identifier in an encrypted, expiring token."""
    if not isinstance(legacy_id, str) or not legacy_id:
        raise StreamTokenError("invalid file id")

    seconds = int(ttl if ttl is not None else Security.STREAM_TOKEN_TTL)
    seconds = max(60, min(seconds, 7 * 86400))
    payload = {
        "v": 1,
        "i": legacy_id,
        "n": _name_digest(name),
        "e": int(time.time()) + seconds,
    }
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_load_key()).encrypt(
        nonce,
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        _TOKEN_AAD,
    )
    return _TOKEN_PREFIX + _b64e(nonce + ciphertext)


def _read_token(token: str) -> Dict[str, Any]:
    if not isinstance(token, str) or not token.startswith(_TOKEN_PREFIX):
        raise StreamTokenError("not a stream token")
    try:
        raw = _b64d(token[len(_TOKEN_PREFIX):])
        if len(raw) < 13:
            raise StreamTokenError("truncated token")
        plaintext = AESGCM(_load_key()).decrypt(
            raw[:12], raw[12:], _TOKEN_AAD
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except StreamTokenError:
        raise
    except Exception as exc:  # cryptography raises InvalidTag, JSON raises ValueError
        raise StreamTokenError("invalid token") from exc

    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise StreamTokenError("unsupported token")
    if not isinstance(payload.get("i"), str) or not payload["i"]:
        raise StreamTokenError("missing file id")
    if not isinstance(payload.get("n"), str):
        raise StreamTokenError("missing name binding")
    if not isinstance(payload.get("e"), int):
        raise StreamTokenError("missing expiry")
    return payload


def verify_stream_token(token: str, name: str = "") -> str:
    """Validate a token for ``name`` and return the legacy identifier."""
    payload = _read_token(token)
    if payload["e"] < int(time.time()):
        raise StreamTokenError("expired token")
    expected = payload["n"]
    # A digest of the empty name means the filename was unknown at mint time.
    if expected != _name_digest("") and name:
        if not hmac.compare_digest(expected, _name_digest(name)):
            raise StreamTokenError("token bound to a different file")
    elif expected != _name_digest("") and not name:
        raise StreamTokenError("missing file name")
    return payload["i"]


def stream_token_legacy_id(token: str) -> Optional[str]:
    """Best-effort decode used for cache keys; expiry is not enforced."""
    try:
        return _read_token(token)["i"]
    except StreamTokenError:
        return None


def resolve_stream_id(token: str, name: str = "") -> str:
    """Accept a signed token, and legacy IDs only when explicitly allowed."""
    if isinstance(token, str) and token.startswith(_TOKEN_PREFIX):
        return verify_stream_token(token, name)
    if Security.ALLOW_LEGACY_STREAM_IDS and isinstance(token, str):
        if 8 <= len(token) <= 512 and re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return token
    raise StreamTokenError("stream reference rejected")


def internal_base_url() -> str:
    """Loopback base URL for ffmpeg/ffprobe self-calls.

    Must never use Telegram.BASE_URL: tokens are minted with this process's
    secret, so a public deployment URL would reject them (stale code, a
    different secret, or an unrelated host) and remux would silently fail.
    """
    return f"http://127.0.0.1:{Telegram.PORT}"


def outbound_headers() -> Dict[str, str]:
    """Headers that let internal ffmpeg/ffprobe calls pass the edge lock."""
    if Security.EDGE_SHARED_SECRET:
        return {"X-Edge-Key": Security.EDGE_SHARED_SECRET}
    return {}


def ffmpeg_headers_value(headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    headers = headers if headers is not None else outbound_headers()
    if not headers:
        return None
    return "".join(f"{key}: {value}\r\n" for key, value in headers.items())


def sanitize_filename(name: str, fallback: str = "file") -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    base = _FILENAME_BAD.sub("", base).strip(" .")
    return (base or fallback)[:180]


def content_disposition(disposition: str, filename: str) -> str:
    safe = sanitize_filename(filename)
    ascii_name = safe.encode("ascii", "ignore").decode("ascii").replace('"', "'")
    if not ascii_name.strip(" ."):
        ascii_name = "file"
    return (
        f'{disposition}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(safe, safe='')}"
    )


def _scrub_string(value: str) -> str:
    base = (Telegram.BASE_URL or "").rstrip("/")
    if base and base != "0.0.0.0" and base in value:
        value = value.replace(base, "")
    cleaned = _TELEGRAM_LINK.sub("", value)
    cleaned = _TELEGRAM_HANDLE.sub("", cleaned)
    if cleaned != value:
        value = re.sub(r"[ \t]{2,}", " ", cleaned).strip(" -|•·,;")
    return value


def _clean_track_label(value: str) -> str:
    """Remove Telegram handles/links and channel branding from track titles."""
    cleaned = _TELEGRAM_LINK.sub("", value)
    cleaned = _TELEGRAM_HANDLE.sub("", cleaned)
    cleaned = _TELEGRAM_WORD.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" -|•·,;[]()")


def _token_for_entry(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return _walk(entry, 0)
    cleaned = _walk(entry, 0)
    entry_id = entry.get("id")
    if isinstance(cleaned, dict) and isinstance(entry_id, str) and entry_id:
        entry_name = entry.get("name")
        if not isinstance(entry_name, str):
            entry_name = ""
        try:
            cleaned["id"] = mint_stream_token(entry_id, entry_name)
        except StreamTokenError:
            cleaned.pop("id", None)
    if isinstance(cleaned, dict) and isinstance(entry, dict):
        for key in _TRACK_DISPLAY_KEYS:
            original = entry.get(key)
            if isinstance(original, str):
                cleaned[key] = _clean_track_label(original)
    return cleaned


def _walk(value: Any, depth: int) -> Any:
    if depth > 64:
        return None
    if isinstance(value, dict):
        filename = value.get("filename") or value.get("file_name") or value.get("name") or ""
        if not isinstance(filename, str):
            filename = ""
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if key in _DROP_KEYS:
                continue
            target_key = _RENAME_KEYS.get(key, key)
            if target_key == "file_id" and isinstance(item, str):
                try:
                    result[target_key] = mint_stream_token(item, filename)
                except StreamTokenError:
                    continue
                continue
            if target_key in _SOURCE_LIST_KEYS and isinstance(item, list):
                result[target_key] = [_token_for_entry(entry) for entry in item]
                continue
            result[target_key] = _walk(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_walk(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def sanitize_public_payload(value: Any) -> Any:
    """Recursively remove Telegram identifiers and tokenize stream references."""
    return _walk(value, 0)
