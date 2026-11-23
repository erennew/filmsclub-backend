import asyncio
import hashlib
import hmac
import os
import re
import shutil
from time import time
from typing import Any, Dict, List, Optional, Union
from Backend.helper.encrypt import decode_string
from fastapi import FastAPI, Query, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse, JSONResponse
import urllib.parse

from fastapi.templating import Jinja2Templates



import mimetypes
import platform
import secrets
import math
from datetime import datetime, timezone

from Backend.logger import LOGGER
from Backend.config import (
    AdminOpsConfig,
    Telegram,
    Cache as CacheConfig,
    QueueConfig,
    Security,
)
from Backend.fastapi.auth import (
    COOKIE_NAME,
    admin_actor,
    clear_session_cookie,
    client_ip,
    create_session,
    login_allowed,
    read_session,
    record_failed_login,
    set_session_cookie,
    verify_admin_auth,
    verify_credentials,
)
from Backend.fastapi.security_middleware import install_security_middleware
from Backend.helper.security import (
    StreamTokenError,
    content_disposition,
    internal_base_url,
    mint_stream_token,
    outbound_headers,
    resolve_stream_id,
    sanitize_filename,
    stream_token_legacy_id,
)
from wzgram.enums import ChatMemberStatus
from Backend.wzgram_pkg import StreamBot, work_loads, multi_clients
from Backend.helper.exceptions import InvalidHash
from Backend.helper.custom_dl import ByteStreamer
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from Backend.helper.pyro import get_readable_time
from Backend.helper.media_tracks import probe_media_tracks
from Backend.helper.cache import get_all_cache_stats, clear_all_caches, video_chunk_cache
from Backend.helper.redis_cache import redis_cache, cache_response, invalidate_cache_pattern
from Backend.helper.audit import audit_event
from Backend.helper.runtime_config import KNOBS, KnobError, runtime_config
from Backend import StartTime, __version__, db


# ── Audit helper ─────────────────────────────────────────────────────────────
def audit(request: Request, action: str, target: str = "", detail=None,
          result: str = "ok", actor: str = "") -> None:
    """Record one admin mutation. Never raises — a failed audit write must not
    change the outcome of the action it records."""
    try:
        audit_event(
            action=action,
            actor=actor or admin_actor(request),
            actor_kind="web",
            target=target,
            detail=detail,
            result=result,
            ip=client_ip(request),
        )
    except Exception as e:  # pragma: no cover — audit_event already guards
        LOGGER.warning(f"Audit call failed for {action}: {e}")

# Get absolute path for templates directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "fastapi", "templates")


# Helper function to add HTTP cache headers for browser caching
def add_cache_headers(response, max_age: int = 60, etag: Optional[str] = None):
    """Add HTTP cache headers for browser caching."""
    headers = {
        "Cache-Control": f"public, max-age={max_age}",
        "Vary": "Accept-Encoding"
    }
    if etag:
        headers["ETag"] = etag
    for key, value in headers.items():
        response.headers[key] = value
    return response


_docs_enabled = Security.ENABLE_API_DOCS
app = FastAPI(
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
class_cache = {}

# =============================================================================
# REMUX DISK CACHE — ffmpeg output for switched audio/subtitle tracks
# =============================================================================
REMUX_STALL_TIMEOUT = 120        # seconds without ffmpeg output before abort
REMUX_WAIT_TIMEOUT = 1800        # how long a later viewer waits for a build

# cache key -> event set when the build finishes (success or failure)
_remux_builds: Dict[str, asyncio.Event] = {}
_remux_last_sweep = 0.0


async def _resolve_source(chat_id: int, msg_id: int):
    """Resolve a message with the least-loaded client that can see it.

    A MULTI_TOKEN bot that has not been added to the source channel cannot
    resolve its messages, and without the fall-through every viewer routed to
    that bot would fail.
    """
    candidates = sorted(work_loads, key=work_loads.get) if work_loads else list(multi_clients)
    first_error = None
    for index in candidates:
        client = multi_clients.get(index)
        if client is None:
            continue
        tg_connect = class_cache.get(client)
        if tg_connect is None:
            tg_connect = ByteStreamer(client)
            class_cache[client] = tg_connect
        try:
            file_id = await tg_connect.get_file_properties(chat_id=chat_id, message_id=msg_id)
            return index, tg_connect, file_id
        except Exception as e:
            if first_error is None:
                first_error = e
            LOGGER.debug(f"Client {index} cannot resolve {chat_id}:{msg_id}: {e}")
    if first_error is not None:
        raise first_error
    raise HTTPException(status_code=503, detail="No Telegram clients available")


def _parse_byte_range(range_header: Optional[str], file_size: int) -> Optional[tuple]:
    """Parse a single HTTP byte range.

    Returns (start, end) inclusive, or None when the header is malformed or
    unsatisfiable. Handles ``bytes=start-end``, ``bytes=start-`` and the
    suffix form ``bytes=-length``; multi-range requests use the first range.
    """
    if not range_header:
        return (0, file_size - 1) if file_size > 0 else None
    value = range_header.strip()
    if not value.lower().startswith("bytes="):
        return None
    spec = value[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    start_s, end_s = start_s.strip(), end_s.strip()
    if not start_s:
        if not end_s.isdigit() or file_size <= 0:
            return None
        suffix = int(end_s)
        if suffix <= 0:
            return None
        return (max(file_size - suffix, 0), file_size - 1)
    if not start_s.isdigit() or (end_s and not end_s.isdigit()):
        return None
    start = int(start_s)
    if start >= file_size:
        return None
    end = min(int(end_s), file_size - 1) if end_s else file_size - 1
    if end < start:
        return None
    return (start, end)


def _range_not_satisfiable(file_size: int) -> StreamingResponse:
    return StreamingResponse(
        content=(b"416: Range not satisfiable",),
        status_code=416,
        headers={"Content-Range": f"bytes */{file_size}"},
    )


def _remux_paths(file_id: str, audio_idx: int, subtitle_idx: int) -> tuple:
    key = f"{file_id}|a{audio_idx}|s{subtitle_idx}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    os.makedirs(CacheConfig.REMUX_CACHE_DIR, exist_ok=True)
    return (
        os.path.join(CacheConfig.REMUX_CACHE_DIR, f"{digest}.mp4"),
        os.path.join(CacheConfig.REMUX_CACHE_DIR, f"{digest}.part.mp4"),
    )


def _remux_cached_ok(path: str) -> bool:
    try:
        return os.path.getsize(path) > 1024 * 1024
    except OSError:
        return False


def _sweep_remux_cache(force: bool = False):
    """Drop expired remux files and enforce the configured size budget."""
    global _remux_last_sweep
    now = time()
    if not force and now - _remux_last_sweep < 600:
        return
    _remux_last_sweep = now
    cache_dir = CacheConfig.REMUX_CACHE_DIR
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return
    entries = []
    for name in names:
        path = os.path.join(cache_dir, name)
        try:
            if name.endswith(".part.mp4"):
                # Abandoned partial builds (crashed process) older than 6 hours
                if now - os.path.getmtime(path) > 6 * 3600:
                    os.remove(path)
                continue
            if not name.endswith(".mp4"):
                continue
            stat = os.stat(path)
        except OSError:
            continue
        entries.append((path, stat.st_size, stat.st_mtime))

    ttl = CacheConfig.REMUX_CACHE_TTL_HOURS * 3600
    kept = []
    for path, size, mtime in sorted(entries, key=lambda e: e[2], reverse=True):
        if ttl > 0 and now - mtime > ttl:
            try:
                os.remove(path)
                continue
            except OSError:
                pass
        kept.append((path, size))

    budget = CacheConfig.REMUX_CACHE_SIZE_MB * 1024 * 1024
    total = sum(size for _, size in kept)
    for path, size in reversed(kept):
        if total <= budget:
            break
        try:
            os.remove(path)
            total -= size
            LOGGER.info(f"Evicted remux cache file: {path}")
        except OSError:
            pass


def _remux_cache_stats() -> Dict[str, Any]:
    """Disk usage of the remuxed audio/subtitle cache (admin cache panel)."""
    files = total = partials = 0
    try:
        with os.scandir(CacheConfig.REMUX_CACHE_DIR) as entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                    if entry.name.endswith(".part.mp4"):
                        partials += 1
                        continue
                    if entry.name.endswith(".mp4"):
                        files += 1
                        total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return {
        "files": files,
        "partials": partials,
        "mb": round(total / 1048576, 1),
        "budget_mb": CacheConfig.REMUX_CACHE_SIZE_MB,
        "ttl_hours": CacheConfig.REMUX_CACHE_TTL_HOURS,
    }


def _clear_remux_cache() -> Dict[str, Any]:
    """Delete remuxed files only (never other files in the cache dir)."""
    removed = freed = 0
    try:
        with os.scandir(CacheConfig.REMUX_CACHE_DIR) as entries:
            for entry in entries:
                try:
                    if not entry.is_file() or not entry.name.endswith(".mp4"):
                        continue
                    size = entry.stat().st_size
                    os.remove(entry.path)
                    removed += 1
                    freed += size
                except OSError:
                    continue
    except OSError:
        pass
    return {"removed": removed, "freed_mb": round(freed / 1048576, 1)}


async def serve_local_file(request: Request, file_path: str, file_name: str):
    """Serve a local remuxed file with HTTP byte-range support for seeking."""
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        raise HTTPException(status_code=404, detail="Cached file not found")
    byte_range = _parse_byte_range(request.headers.get("Range"), file_size)
    if byte_range is None:
        return _range_not_satisfiable(file_size)
    from_bytes, until_bytes = byte_range
    req_length = until_bytes - from_bytes + 1

    async def file_chunk_generator():
        with open(file_path, "rb") as f:
            f.seek(from_bytes)
            remaining = req_length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Type": "video/mp4",
        "Content-Disposition": content_disposition("inline", file_name),
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
    }
    status_code = 200
    if request.headers.get("Range"):
        status_code = 206
        headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"
    headers["Content-Length"] = str(req_length)

    return StreamingResponse(
        content=file_chunk_generator(),
        status_code=status_code,
        headers=headers,
    )


templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _quality_files(media: Optional[dict]) -> List[dict]:
    """Return every quality entry for a movie or TV document."""
    if not media:
        return []
    if media.get("seasons"):
        return [
            quality
            for season in media.get("seasons") or []
            for episode in season.get("episodes") or []
            for quality in episode.get("telegram") or []
        ]
    return list(media.get("telegram") or [])


def _require_tv_episode(media_type: str, season_number: Optional[int], episode_number: Optional[int]) -> None:
    if media_type == "tv" and (season_number is None or episode_number is None):
        raise HTTPException(
            status_code=400,
            detail="season_number and episode_number are required for TV files",
        )


# ── Admin helpers ────────────────────────────────────────────────────────────
async def _resolve_admin_media(item: dict) -> dict:
    """Resolve a list row (type may be 'anime') to the real collection doc."""
    # List rows carry the real collection in ``collection``; ``type`` is a
    # display value ("anime") for some rows and must not select the document.
    media_type = item.get("collection") or item.get("type") or item.get("media_type") or "movie"
    tmdb_id = item.get("tmdb_id")
    if tmdb_id in (None, ""):
        raise ValueError("Missing tmdb_id")
    media = await db.media.find_one({
        "db_index": item.get("db_index", 1),
        "tmdb_id": tmdb_id,
        "type": media_type,
    })
    if not media:
        raise ValueError("Media not found")
    return media


async def _delete_telegram_quality_files(media: dict) -> Dict[str, int]:
    """Delete every video message referenced by a media document."""
    deleted = total = 0
    for quality in _quality_files(media):
        if not quality.get("id"):
            continue
        total += 1
        try:
            decoded = await decode_string(quality.get("id"))
            await StreamBot.delete_messages(int(f"-100{decoded['chat_id']}"), int(decoded["msg_id"]))
            deleted += 1
        except Exception as e:
            LOGGER.warning(f"Failed to delete Telegram message: {e}")
    return {"deleted": deleted, "total": total}


async def _tmdb_details(kind: str, tmdb_id: int) -> dict:
    """Shared TMDB detail read (see helper/manual_add.py)."""
    from Backend.helper.manual_add import ManualAddError, tmdb_details
    try:
        return await tmdb_details(kind, tmdb_id)
    except ManualAddError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


async def _refresh_media_from_tmdb(media: dict, overwrite: bool = False) -> dict:
    """Fill (or overwrite) metadata from TMDB; never touches files or seasons."""
    kind = media.get("type") or "movie"
    try:
        tmdb_id = int(media.get("tmdb_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid TMDB id")

    data = await _tmdb_details(kind, tmdb_id)
    poster = f"https://image.tmdb.org/t/p/w500{data['poster_path']}" if data.get("poster_path") else ""
    backdrop = f"https://image.tmdb.org/t/p/original{data['backdrop_path']}" if data.get("backdrop_path") else ""
    release = str(data.get("release_date") or data.get("first_air_date") or "")
    candidates = {
        "title": data.get("title") or data.get("name") or "",
        "description": data.get("overview") or "",
        "rating": data.get("vote_average") or 0,
        "release_year": int(release[:4]) if release[:4].isdigit() else 0,
        "genres": [g.get("name") for g in data.get("genres") or [] if g.get("name")],
        "poster": poster,
        "backdrop": backdrop,
    }
    if kind == "tv":
        candidates.update({
            "status": data.get("status") or "",
            "total_seasons": int(data.get("number_of_seasons") or 0),
            "total_episodes": int(data.get("number_of_episodes") or 0),
        })
    else:
        candidates["runtime"] = int(data.get("runtime") or 0)

    update_data = {}
    for field, value in candidates.items():
        if value in (None, "", [], 0):
            continue
        if overwrite or not media.get(field):
            update_data[field] = value

    if not media.get("languages"):
        languages = [
            lang.get("english_name") or lang.get("name")
            for lang in data.get("spoken_languages") or []
        ]
        languages = [lang for lang in languages if lang]
        if languages:
            update_data["languages"] = list(dict.fromkeys(languages))

    cast = []
    for person in (data.get("credits") or {}).get("cast") or []:
        name = (person.get("name") or "").strip()
        if not name:
            continue
        cast.append({
            "name": name,
            "character": (person.get("character") or "").strip(),
            "profile_path": person.get("profile_path") or "",
            "id": person.get("id"),
        })
        if len(cast) >= 15:
            break
    if cast and (overwrite or not media.get("cast")):
        update_data["cast"] = cast

    if not update_data:
        return {"updated_fields": [], "media": media}
    await db.media.update_one(
        {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": kind},
        {"$set": update_data},
    )
    updated = await db.media.find_one({"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": kind})
    return {"updated_fields": sorted(update_data.keys()), "media": updated}


async def _get_tv_media(media_type: str, db_index: int, tmdb_id: str) -> dict:
    media = await db.media.find_one({"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type})
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    if (media.get("type") or "movie") != "tv":
        raise HTTPException(status_code=400, detail="Episode management is only available for TV shows")
    return media


async def _sync_tv_counts(media: dict) -> None:
    updated = await db.media.find_one({"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"})
    if not updated:
        return
    seasons = updated.get("seasons") or []
    await db.media.update_one(
        {"db_index": updated.get("db_index"), "tmdb_id": updated.get("tmdb_id"), "type": "tv"},
        {"$set": {
            "total_seasons": len(seasons),
            "total_episodes": sum(len(season.get("episodes") or []) for season in seasons),
        }},
    )


async def _retry_failed_entry(failed_file: dict) -> None:
    """Re-queue one failed file from its stored Telegram source message."""
    parse_data = failed_file.get("parse_data") or {}
    metadata_info = failed_file.get("metadata_info") or {}
    channel = parse_data.get("channel") or metadata_info.get("channel")
    msg_id = parse_data.get("msg_id") or metadata_info.get("msg_id")
    if not channel or not msg_id:
        raise HTTPException(
            status_code=400,
            detail="No source message stored for this entry — re-send the file or paste its t.me link in Bulk add.",
        )

    raw = str(channel).strip()
    bare = raw[4:] if raw.startswith("-100") else raw.lstrip("-")
    chat_id = int(raw) if raw.startswith("-") else int(f"-100{raw}")
    message = await StreamBot.get_messages(chat_id, int(msg_id))
    if not message or getattr(message, "empty", False):
        raise HTTPException(status_code=404, detail="The original Telegram message no longer exists")

    from Backend.wzgram_pkg.plugins.start import parse_and_queue_file

    queued = await parse_and_queue_file(message, bare, is_rescan=False, allow_probe=True, notify=False)
    if not queued:
        raise HTTPException(
            status_code=502,
            detail="Re-queue failed; the entry was kept. Check the bot logs and try again.",
        )

# ── CORS ─────────────────────────────────────────────────────────────────────
# Explicit origins only; admin auth uses same-origin cookies, so cross-origin
# requests never need credentials.
_cors_origins = [origin for origin in Telegram.CORS_ORIGINS if origin and origin != "*"]
if len(_cors_origins) != len(Telegram.CORS_ORIGINS):
    LOGGER.warning("Wildcard CORS origin ignored; set explicit origins in CORS_ORIGINS")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Range",
        "If-Range",
        "X-Requested-With",
        "X-Admin-Username",
        "X-Admin-Password",
        "X-Admin-Session",
    ],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
    max_age=600,
)
install_security_middleware(app)


# ── GZIP for JSON API responses ──────────────────────────────────────────────
# The list endpoints ship whole media documents: /api/trending/week is ~775 KB
# and /api/tvshows ~400 KB of JSON per page. The app served all of it
# uncompressed, so on a phone the transfer alone outlasted the 8s request
# timeout even when the query itself was fast — a shelf would fail with "Couldn't
# load this shelf" purely because of payload size.
#
# Starlette's GZipMiddleware is not usable here: it buffers and re-compresses
# streaming bodies, which is exactly what /api/stream and /api/dl must never do.
# This wrapper only touches JSON API responses, never streaming or ranged media.
import gzip as _gzip

_GZIP_MIN_BYTES = 1024          # below this, framing overhead outweighs the win
_GZIP_LEVEL = 6                 # good ratio, cheap enough for a 512 MB dyno
# Media is served from /dl/{id}/{name} (outside /api/) and is excluded by both
# the path check and the JSON content-type check below. These prefixes cover the
# /api/ endpoints that must stay uncompressed: subtitles are fetched as files,
# and media-info can carry large probe payloads a player reads verbatim.
_GZIP_SKIP_PREFIXES = ("/api/stream", "/api/dl", "/api/subtitle")


class JSONGZipMiddleware(BaseHTTPMiddleware):
    """gzip JSON API bodies when the client advertises support."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only plain GET/HEAD JSON API payloads; never media bytes.
        if request.method not in ("GET", "HEAD"):
            return response
        path = request.url.path
        if not path.startswith("/api/") or path.startswith(_GZIP_SKIP_PREFIXES):
            return response
        if response.status_code != 200:
            return response
        if response.headers.get("content-encoding"):
            return response
        # A ranged response is partial media by definition — never touch it.
        if response.headers.get("content-range") or request.headers.get("range"):
            return response
        if "gzip" not in (request.headers.get("accept-encoding") or "").lower():
            return response
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode()

        headers = dict(response.headers)
        # `Response` derives content-type/length from the body it is given, so
        # drop the inherited copies to avoid emitting the header twice.
        headers.pop("content-length", None)
        headers.pop("content-type", None)

        if len(body) < _GZIP_MIN_BYTES:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=content_type,
            )

        compressed = _gzip.compress(body, compresslevel=_GZIP_LEVEL)
        headers["content-encoding"] = "gzip"
        # Merge with the existing Vary (CORSMiddleware already sets "Origin");
        # overwriting it would make caches serve one origin's response to another.
        vary = {v.strip() for v in headers.get("vary", "").split(",") if v.strip()}
        vary.add("Accept-Encoding")
        headers["vary"] = ", ".join(sorted(vary))
        return Response(
            content=compressed,
            status_code=response.status_code,
            headers=headers,
            media_type=content_type,
        )


app.add_middleware(JSONGZipMiddleware)


@app.on_event("startup")
async def _start_runtime_config_poll():
    """Keep the runtime-config snapshot fresh, even in API-only mode.

    The bot's own startup also starts this poll; starting it twice is
    harmless because the store keeps a single task.
    """
    try:
        runtime_config.start()
    except Exception as e:  # pragma: no cover — the store already guards
        LOGGER.warning(f"Runtime config poll could not start: {e}")


# ── Manga stub: stop VioletStream's infinite retry loop ──────────────────────
# VioletStream hits /api/manga on port 8000 (this backend). Since this backend
# has no manga data, we return a well-formed empty response with has_next=False
# so VioletStream stops paginating immediately instead of looping forever.
@app.get("/api/manga")
async def manga_stub():
    return {
        "items": [],
        "total": 0,
        "page": 1,
        "per_page": 24,
        "total_pages": 0,
        "has_next": False,
        "has_prev": False,
    }



# Admin Panel Routes
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    """Serve the admin panel HTML"""
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"website_url": Telegram.WEBSITE_URL},
    )


@app.post("/admin/api/login")
async def admin_login(request: Request, response: Response, payload: dict):
    """Verify admin credentials and issue an HttpOnly session cookie."""
    ip = client_ip(request)
    if not login_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if not verify_credentials(username, password):
        record_failed_login(ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_session(username)
    set_session_cookie(request, response, token)
    return {"authenticated": True, "username": username}


@app.post("/admin/api/login-token")
async def admin_login_token(request: Request, payload: dict):
    """Verify admin credentials and return the session token in the body.

    Cross-origin panels (e.g. the Vercel frontend) cannot use the HttpOnly
    cookie because CORS here is credential-less, so they exchange credentials
    for this token and send it back as X-Admin-Session.
    """
    ip = client_ip(request)
    if not login_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if not verify_credentials(username, password):
        record_failed_login(ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {"authenticated": True, "token": create_session(username), "username": username}


@app.post("/admin/api/logout")
async def admin_logout(response: Response):
    clear_session_cookie(response)
    return {"authenticated": False}


@app.get("/admin/api/session")
async def admin_session(request: Request):
    token = request.cookies.get(COOKIE_NAME) or request.headers.get("x-admin-session", "")
    return {"authenticated": bool(token and read_session(token))}


# Bump this whenever the React console starts depending on a new backend route.
# The console reads it after login so a stale backend produces one clear
# "deploy/restart the backend" banner instead of six cryptic "Not Found" pages.
ADMIN_API_BUILD = "2026-11-07"


@app.get("/admin/api/capabilities")
async def admin_capabilities(request: Request):
    """Build marker and the exact admin routes this process serves."""
    verify_admin_auth(request)
    paths = sorted({
        route.path for route in app.routes
        if route.path.startswith("/admin/api/")
    })
    return {
        "version": __version__,
        "build": ADMIN_API_BUILD,
        "routes": paths,
        "uptime_seconds": round(time() - StartTime, 1),
    }


# Admin API Endpoints
@app.get("/admin/api/media")
async def admin_list_media_endpoint(
    request: Request,
    media_type: str = Query("movie"),
    query: str = Query(""),
    page: int = Query(1),
    page_size: int = Query(30),
    sort: str = Query("updated_on:desc"),
    language: str = Query(""),
    genre: str = Query(""),
    flag: str = Query("")
):
    """Filterable, sortable media list (filters run before pagination)."""
    verify_admin_auth(request)

    try:
        return await db.admin_list_media(
            media_type=media_type,
            query=query,
            page=page,
            page_size=page_size,
            sort=sort,
            language=language,
            genre=genre,
            flag=flag,
        )
    except Exception as e:
        LOGGER.error(f"Error listing media: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/media/bulk")
async def admin_bulk_media(request: Request, payload: dict):
    """Multi-select actions: delete, delete+telegram, update fields, refresh."""
    verify_admin_auth(request)
    action = str(payload.get("action") or "").strip().lower()
    if action not in ("delete", "delete_telegram", "update", "refresh"):
        raise HTTPException(status_code=400, detail="action must be delete, delete_telegram, update or refresh")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="No items selected")
    items = items[:100]

    update_data: Dict[str, Any] = {}
    if action == "update":
        fields = payload.get("fields") or {}
        for key in ("languages", "genres"):
            value = fields.get(key)
            if isinstance(value, str):
                parsed = [part.strip() for part in value.split(",") if part.strip()]
                if parsed:
                    update_data[key] = parsed
            elif isinstance(value, list) and value:
                update_data[key] = [str(part).strip() for part in value if str(part).strip()]
        if "rip" in fields and str(fields.get("rip") or "").strip():
            update_data["rip"] = str(fields["rip"]).strip()
        if not update_data:
            raise HTTPException(status_code=400, detail="Provide languages and/or genres to update")

    summary: Dict[str, Any] = {"action": action, "attempted": 0, "succeeded": 0, "failed": 0, "errors": []}
    for item in items:
        summary["attempted"] += 1
        label = item.get("title") or item.get("tmdb_id") or "item"
        try:
            media = await _resolve_admin_media(item)
            kind = media.get("type") or "movie"
            if action == "delete":
                await db.media.delete_one({"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": kind})
            elif action == "delete_telegram":
                await _delete_telegram_quality_files(media)
                await db.media.delete_one({"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": kind})
            elif action == "update":
                await db.media.update_one(
                    {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": kind},
                    {"$set": update_data},
                )
            else:
                await _refresh_media_from_tmdb(media, overwrite=bool(payload.get("overwrite")))
            summary["succeeded"] += 1
        except Exception as e:
            summary["failed"] += 1
            if len(summary["errors"]) < 5:
                summary["errors"].append(f"{label}: {getattr(e, 'detail', None) or e}")
    audit(request, f"media.bulk_{action}", target=f"media:bulk:{action}",
          detail={"attempted": summary["attempted"], "succeeded": summary["succeeded"],
                  "failed": summary["failed"]})
    return summary


@app.get("/admin/api/hero")
async def admin_get_hero(request: Request):
    """Curated hero slots in panel order, with the library row behind each."""
    verify_admin_auth(request)
    from Backend.helper.database import HERO_MAX_PICKS

    try:
        rows = []
        for pick, doc in await db.resolve_hero_picks():
            row = dict(pick)
            row["resolved"] = bool(doc)
            for field in ("title", "poster", "backdrop", "release_year"):
                row[field] = (doc or {}).get(field)
            rows.append(row)
        return {"picks": rows, "max": HERO_MAX_PICKS}
    except Exception as e:
        LOGGER.error(f"Error reading hero picks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/hero")
async def admin_save_hero(request: Request, payload: dict):
    """Replace the curated hero. An empty list clears it and restores the default."""
    verify_admin_auth(request)
    from Backend.helper.database import HERO_MAX_PICKS, normalize_hero_picks

    try:
        picks = normalize_hero_picks(payload.get("picks"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    unknown = [
        f"{pick['type']}#{pick['tmdb_id']}@{pick['db_index']}"
        for pick, doc in await db.resolve_hero_picks(picks)
        if doc is None
    ]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Not in the library: {', '.join(unknown[:5])}",
        )

    if not await db.set_hero_picks(picks, updated_by=admin_actor(request)):
        raise HTTPException(status_code=500, detail="Could not save hero picks")

    audit(request, "hero.save", target="hero:picks",
          detail={"count": len(picks),
                  "picks": [f"{p['type']}:{p['db_index']}:{p['tmdb_id']}" for p in picks]})
    return {"picks": picks, "max": HERO_MAX_PICKS}


@app.post("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/refresh-metadata")
async def admin_refresh_media_metadata(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    payload: Optional[dict] = None,
):
    """Re-fetch title/description/images/cast from TMDB for one title."""
    verify_admin_auth(request)
    try:
        media = await db.media.find_one({
            "db_index": db_index,
            "tmdb_id": tmdb_id,
            "type": media_type,
        })
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        result = await _refresh_media_from_tmdb(
            media, overwrite=bool((payload or {}).get("overwrite"))
        )
        audit(request, "media.refresh_metadata",
              target=f"media:{media_type}:{tmdb_id}",
              detail={"updated_fields": result.get("updated_fields")})
        return result
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error refreshing metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/media/{media_type}/{db_index}/{tmdb_id}")
async def admin_get_media(request: Request, media_type: str, db_index: int, tmdb_id: str):
    """Get specific media details"""
    verify_admin_auth(request)
    
    try:
        media = await db.media.find_one({
            "db_index": db_index,
            "tmdb_id": tmdb_id,
            "type": media_type
        })
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")

        first_file_id = next((q.get("id") for q in _quality_files(media) if q.get("id")), None)
        media["telegram_link"] = await db.admin_telegram_link(first_file_id)
        return media
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error getting media: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/admin/api/media/{media_type}/{db_index}/{tmdb_id}")
async def admin_update_media(request: Request, media_type: str, db_index: int, tmdb_id: str, payload: dict):
    """Update media details"""
    verify_admin_auth(request)
    
    try:
        update_data = {}
        
        if "title" in payload:
            update_data["title"] = payload["title"]
        if "release_year" in payload:
            update_data["release_year"] = payload["release_year"]
        if "rating" in payload:
            update_data["rating"] = payload["rating"]
        if "rip" in payload:
            update_data["rip"] = payload["rip"]
        if "languages" in payload:
            value = payload["languages"]
            parts = value if isinstance(value, list) else str(value or "").split(",")
            update_data["languages"] = [str(part).strip() for part in parts if str(part).strip()]
        if "genres" in payload:
            value = payload["genres"]
            parts = value if isinstance(value, list) else str(value or "").split(",")
            update_data["genres"] = [str(part).strip() for part in parts if str(part).strip()]
        if "description" in payload:
            update_data["description"] = payload["description"]
        if "poster" in payload:
            update_data["poster"] = payload["poster"]
        if "backdrop" in payload:
            update_data["backdrop"] = payload["backdrop"]
        if "cast" in payload:
            # Parse cast from format: Name | Role | Image URL | TMDB ID
            cast_list = []
            for line in str(payload["cast"] or "").split("\n"):
                parts = [p.strip() for p in line.split("|")]
                # An empty textarea must clear the cast, not add a blank member.
                if not parts[0]:
                    continue
                cast_member = {"name": parts[0]}
                if len(parts) >= 2 and parts[1]:
                    cast_member["character"] = parts[1]
                if len(parts) >= 3 and parts[2]:
                    cast_member["profile_path"] = parts[2]
                if len(parts) >= 4 and parts[3]:
                    # IDs are advisory; a typo here must not break the whole save.
                    try:
                        cast_member["id"] = int(parts[3])
                    except ValueError:
                        pass
                cast_list.append(cast_member)
            update_data["cast"] = cast_list
        if "runtime" in payload and media_type == "movie":
            update_data["runtime"] = payload["runtime"]
        
        if update_data:
            await db.media.update_one(
                {"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type},
                {"$set": update_data}
            )
        
        updated_media = await db.media.find_one({
            "db_index": db_index,
            "tmdb_id": tmdb_id,
            "type": media_type
        })

        audit(request, "media.update", target=f"media:{media_type}:{tmdb_id}",
              detail={"fields": sorted(update_data.keys())})
        return {"media": updated_media, "updated": True}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error updating media: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/media/{media_type}/{db_index}/{tmdb_id}")
async def admin_delete_media(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    delete_telegram: bool = Query(False)
):
    """Delete media"""
    verify_admin_auth(request)
    
    try:
        media = await db.media.find_one({
            "db_index": db_index,
            "tmdb_id": tmdb_id,
            "type": media_type
        })
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        telegram_deleted = 0
        telegram_total = 0

        if delete_telegram:
            result = await _delete_telegram_quality_files(media)
            telegram_deleted, telegram_total = result["deleted"], result["total"]
        
        # Delete from database
        await db.media.delete_one({
            "db_index": db_index,
            "tmdb_id": tmdb_id,
            "type": media_type
        })

        audit(request, "media.delete", target=f"media:{media_type}:{db_index}:{tmdb_id}",
              detail={"title": media.get("title"), "delete_telegram": bool(delete_telegram),
                      "telegram_deleted": telegram_deleted})

        return {
            "deleted": True,
            "telegram_deleted": telegram_deleted,
            "telegram_total": telegram_total
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting media: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/files/{file_id}")
async def admin_update_file(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    file_id: str,
    season_number: int = Query(None),
    episode_number: int = Query(None),
    payload: dict = None
):
    """Update file details"""
    verify_admin_auth(request)

    try:
        media = await db.media.find_one({
            "db_index": db_index,
            "tmdb_id": tmdb_id,
            "type": media_type
        })

        if not media:
            raise HTTPException(status_code=404, detail="Media not found")

        _require_tv_episode(media_type, season_number, episode_number)
        payload = payload or {}

        if media_type == "tv":
            file_path = "seasons.$[s].episodes.$[e].telegram.$[q]"
            array_filters = [
                {"s.season_number": season_number},
                {"e.episode_number": episode_number},
                {"q.id": file_id},
            ]
        else:
            file_path = "telegram.$[q]"
            array_filters = [{"q.id": file_id}]

        update_fields = {
            f"{file_path}.{field}": payload[field]
            for field in ("quality", "size", "name")
            if field in payload
        }
        if not update_fields:
            raise HTTPException(status_code=400, detail="No updatable fields supplied")

        result = await db.media.update_one(
            {"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type},
            {"$set": update_fields},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="File not found")
        audit(request, "media.update_file",
              target=f"media_file:{tmdb_id}:{file_id}",
              detail={"fields": sorted(payload.keys()), "season": season_number,
                      "episode": episode_number})
        return {"updated": result.modified_count > 0}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error updating file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/files/{file_id}")
async def admin_delete_file(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    file_id: str,
    season_number: int = Query(None),
    episode_number: int = Query(None),
    delete_telegram: bool = Query(False)
):
    """Delete file"""
    verify_admin_auth(request)
    
    try:
        media = await db.media.find_one({
            "db_index": db_index,
            "tmdb_id": tmdb_id,
            "type": media_type
        })
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        telegram_deleted = 0
        telegram_total = 0

        if delete_telegram:
            # Delete Telegram messages for this file (decode chat/msg from encoded id)
            for quality in _quality_files(media):
                if quality.get("id") == file_id:
                    telegram_total += 1
                    try:
                        from Backend.helper.encrypt import decode_string
                        decoded = await decode_string(quality.get("id"))
                        await StreamBot.delete_messages(int(f"-100{decoded['chat_id']}"), int(decoded["msg_id"]))
                        telegram_deleted += 1
                    except Exception as e:
                        LOGGER.warning(f"Failed to delete Telegram message: {e}")

        _require_tv_episode(media_type, season_number, episode_number)
        if media_type == "tv":
            result = await db.media.update_one(
                {"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type},
                {"$pull": {"seasons.$[s].episodes.$[e].telegram": {"id": file_id}}},
                array_filters=[
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                ],
            )
        else:
            result = await db.media.update_one(
                {"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type},
                {"$pull": {"telegram": {"id": file_id}}},
            )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="File not found")

        audit(request, "media.delete_file", target=f"media_file:{tmdb_id}:{file_id}",
              detail={"season": season_number, "episode": episode_number,
                      "delete_telegram": bool(delete_telegram),
                      "telegram_deleted": telegram_deleted})

        return {
            "deleted": True,
            "telegram_deleted": telegram_deleted,
            "telegram_total": telegram_total
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/subtitles")
async def admin_add_subtitle(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    payload: dict
):
    """Add subtitle to media"""
    verify_admin_auth(request)
    
    try:
        target = payload.get("target_file_id") or "all"
        season_number = payload.get("season_number")
        episode_number = payload.get("episode_number")
        _require_tv_episode(media_type, season_number, episode_number)

        subtitle_data = {
            "id": payload.get("id"),
            "name": payload.get("name"),
            "language": payload.get("language"),
            "format": payload.get("format", "srt"),
            "label": f"{payload.get('language')} • {payload.get('format', 'srt').upper()}"
        }
        if not subtitle_data["id"]:
            raise HTTPException(status_code=400, detail="Subtitle id is required")

        if media_type == "tv":
            base_path = "seasons.$[s].episodes.$[e].telegram"
            array_filters = [
                {"s.season_number": season_number},
                {"e.episode_number": episode_number},
            ]
            if target == "all":
                push_path = f"{base_path}.$[].subtitle_tracks"
            else:
                push_path = f"{base_path}.$[q].subtitle_tracks"
                array_filters.append({"q.id": target})
        else:
            push_path = "telegram.$[].subtitle_tracks" if target == "all" else "telegram.$[q].subtitle_tracks"
            array_filters = None if target == "all" else [{"q.id": target}]

        result = await db.media.update_one(
            {"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type},
            {"$push": {push_path: subtitle_data}},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Target file not found")
        audit(request, "media.add_subtitle", target=f"subtitle:{tmdb_id}:{subtitle_data['id']}",
              detail={"language": subtitle_data.get("language"),
                      "format": subtitle_data.get("format"), "target": target})
        return {"added": True}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error adding subtitle: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/subtitles/{subtitle_id}")
async def admin_update_subtitle(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    subtitle_id: str,
    file_id: str = Query(None),
    season_number: int = Query(None),
    episode_number: int = Query(None),
    payload: dict = None
):
    """Update subtitle details"""
    verify_admin_auth(request)
    
    try:
        if not file_id:
            raise HTTPException(status_code=400, detail="file_id is required")
        _require_tv_episode(media_type, season_number, episode_number)
        payload = payload or {}

        if media_type == "tv":
            sub_path = (
                "seasons.$[s].episodes.$[e].telegram.$[q].subtitle_tracks.$[sub]"
            )
            array_filters = [
                {"s.season_number": season_number},
                {"e.episode_number": episode_number},
                {"q.id": file_id},
                {"sub.id": subtitle_id},
            ]
        else:
            sub_path = "telegram.$[q].subtitle_tracks.$[sub]"
            array_filters = [{"q.id": file_id}, {"sub.id": subtitle_id}]

        update_data = {
            f"{sub_path}.{field}": payload[field]
            for field in ("language", "format", "name")
            if field in payload
        }
        update_data[f"{sub_path}.label"] = (
            f"{payload.get('language', 'Unknown')} • {str(payload.get('format', 'srt')).upper()}"
        )

        result = await db.media.update_one(
            {"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type},
            {"$set": update_data},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Subtitle not found")
        audit(request, "media.update_subtitle", target=f"subtitle:{tmdb_id}:{subtitle_id}",
              detail={"fields": sorted(payload.keys()), "file_id": file_id})
        return {"updated": result.modified_count > 0}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error updating subtitle: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/subtitles/{subtitle_id}")
async def admin_delete_subtitle(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    subtitle_id: str,
    file_id: str = Query(None),
    season_number: int = Query(None),
    episode_number: int = Query(None),
    delete_telegram: bool = Query(False)
):
    """Delete subtitle"""
    verify_admin_auth(request)
    
    try:
        telegram_deleted = 0
        telegram_total = 0

        if delete_telegram:
            media = await db.media.find_one({
                "db_index": db_index,
                "tmdb_id": tmdb_id,
                "type": media_type
            })
            for quality in _quality_files(media):
                for sub in quality.get("subtitle_tracks") or []:
                    if sub.get("id") == subtitle_id:
                        telegram_total += 1
                        try:
                            from Backend.helper.encrypt import decode_string
                            decoded = await decode_string(sub.get("id"))
                            await StreamBot.delete_messages(int(f"-100{decoded['chat_id']}"), int(decoded["msg_id"]))
                            telegram_deleted += 1
                        except Exception as e:
                            LOGGER.warning(f"Failed to delete Telegram subtitle: {e}")

        _require_tv_episode(media_type, season_number, episode_number)
        if media_type == "tv":
            if file_id:
                pull_path = "seasons.$[s].episodes.$[e].telegram.$[q].subtitle_tracks"
                array_filters = [
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                    {"q.id": file_id},
                ]
            else:
                pull_path = "seasons.$[s].episodes.$[e].telegram.$[].subtitle_tracks"
                array_filters = [
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                ]
        else:
            pull_path = "telegram.$[q].subtitle_tracks" if file_id else "telegram.$[].subtitle_tracks"
            array_filters = [{"q.id": file_id}] if file_id else None

        result = await db.media.update_one(
            {"db_index": db_index, "tmdb_id": tmdb_id, "type": media_type},
            {"$pull": {pull_path: {"id": subtitle_id}}},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Subtitle not found")

        audit(request, "media.delete_subtitle", target=f"subtitle:{tmdb_id}:{subtitle_id}",
              detail={"file_id": file_id, "delete_telegram": bool(delete_telegram),
                      "telegram_deleted": telegram_deleted})

        return {
            "deleted": True,
            "telegram_deleted": telegram_deleted,
            "telegram_total": telegram_total
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting subtitle: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Episode manager (TV only)
@app.post("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/episodes")
async def admin_add_episode(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    payload: dict,
):
    """Add an episode (creates the season when it does not exist yet)."""
    verify_admin_auth(request)
    try:
        media = await _get_tv_media(media_type, db_index, tmdb_id)
        try:
            season_number = int(payload.get("season_number"))
            episode_number = int(payload.get("episode_number"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="season_number and episode_number are required")

        for season in media.get("seasons") or []:
            if int(season.get("season_number") or 0) != season_number:
                continue
            for episode in season.get("episodes") or []:
                if int(episode.get("episode_number") or 0) == episode_number:
                    raise HTTPException(status_code=409, detail="Episode already exists")

        new_episode = {
            "episode_number": episode_number,
            "title": str(payload.get("title") or f"Episode {episode_number}").strip(),
            "episode_backdrop": str(payload.get("episode_backdrop") or ""),
            "telegram": [],
            "external_subtitles": [],
        }
        result = await db.media.update_one(
            {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv", "seasons.season_number": season_number},
            {"$push": {"seasons.$[s].episodes": new_episode}},
            array_filters=[{"s.season_number": season_number}],
        )
        if not result.matched_count:
            await db.media.update_one(
                {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"},
                {"$push": {"seasons": {"season_number": season_number, "episodes": [new_episode]}}},
            )
        await _sync_tv_counts(media)
        audit(request, "media.add_episode",
              target=f"episode:{tmdb_id}:{season_number}:{episode_number}",
              detail={"title": new_episode.get("title")})
        return {"added": True, "season_number": season_number, "episode_number": episode_number}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error adding episode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/episodes/{season_number}/{episode_number}")
async def admin_update_episode(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    season_number: int,
    episode_number: int,
    payload: dict = None,
):
    """Rename an episode or change its backdrop."""
    verify_admin_auth(request)
    try:
        media = await _get_tv_media(media_type, db_index, tmdb_id)
        payload = payload or {}
        update_fields = {}
        if "title" in payload:
            update_fields["seasons.$[s].episodes.$[e].title"] = str(payload.get("title") or "").strip()
        if "episode_backdrop" in payload:
            update_fields["seasons.$[s].episodes.$[e].episode_backdrop"] = str(payload.get("episode_backdrop") or "")
        if not update_fields:
            raise HTTPException(status_code=400, detail="Provide title or episode_backdrop")

        result = await db.media.update_one(
            {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"},
            {"$set": update_fields},
            array_filters=[
                {"s.season_number": season_number},
                {"e.episode_number": episode_number},
            ],
        )
        if not result.matched_count:
            raise HTTPException(status_code=404, detail="Episode not found")
        audit(request, "media.update_episode",
              target=f"episode:{tmdb_id}:{season_number}:{episode_number}",
              detail={"fields": sorted(update_fields.keys())})
        return {"updated": result.modified_count > 0}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error updating episode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/seasons/{season_number}")
async def admin_delete_season(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    season_number: int,
    delete_telegram: bool = Query(False)
):
    """Delete one whole season, including every episode and file it holds."""
    verify_admin_auth(request)
    try:
        media = await _get_tv_media(media_type, db_index, tmdb_id)
        season = next(
            (s for s in media.get("seasons") or []
             if int(s.get("season_number") or 0) == season_number),
            None,
        )
        if not season:
            raise HTTPException(status_code=404, detail="Season not found")

        telegram_deleted = 0
        telegram_total = 0
        if delete_telegram:
            for episode in season.get("episodes") or []:
                result = await _delete_telegram_quality_files(
                    {"telegram": episode.get("telegram") or []}
                )
                telegram_deleted += result["deleted"]
                telegram_total += result["total"]

        removed = await db.media.update_one(
            {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"},
            {"$pull": {"seasons": {"season_number": season_number}}},
        )
        if not removed.matched_count:
            raise HTTPException(status_code=404, detail="Season not found")

        await _sync_tv_counts(media)
        episode_count = len(season.get("episodes") or [])
        audit(request, "season.delete",
              target=f"media:tv:{db_index}:{tmdb_id}:season:{season_number}",
              detail={"title": media.get("title"), "episodes": episode_count,
                      "delete_telegram": bool(delete_telegram),
                      "telegram_deleted": telegram_deleted})
        return {
            "deleted": True,
            "episodes": episode_count,
            "telegram_deleted": telegram_deleted,
            "telegram_total": telegram_total,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting season: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/episodes/{season_number}/{episode_number}")
async def admin_delete_episode(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    season_number: int,
    episode_number: int,
    delete_telegram: bool = Query(False),
):
    """Remove an episode; empty seasons are pruned afterwards."""
    verify_admin_auth(request)
    try:
        media = await _get_tv_media(media_type, db_index, tmdb_id)
        telegram_deleted = telegram_total = 0
        if delete_telegram:
            for season in media.get("seasons") or []:
                if int(season.get("season_number") or 0) != season_number:
                    continue
                for episode in season.get("episodes") or []:
                    if int(episode.get("episode_number") or 0) != episode_number:
                        continue
                    result = await _delete_telegram_quality_files({"telegram": episode.get("telegram") or []})
                    telegram_deleted, telegram_total = result["deleted"], result["total"]

        result = await db.media.update_one(
            {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"},
            {"$pull": {"seasons.$[s].episodes": {"episode_number": episode_number}}},
            array_filters=[{"s.season_number": season_number}],
        )
        if not result.matched_count:
            raise HTTPException(status_code=404, detail="Episode not found")

        refreshed = await db.media.find_one({"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"})
        empty = next(
            (s for s in (refreshed or {}).get("seasons") or []
             if int(s.get("season_number") or 0) == season_number and not s.get("episodes")),
            None,
        )
        if empty is not None:
            await db.media.update_one(
                {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"},
                {"$pull": {"seasons": {"season_number": season_number}}},
            )
        await _sync_tv_counts(media)
        audit(request, "media.delete_episode",
              target=f"episode:{tmdb_id}:{season_number}:{episode_number}",
              detail={"delete_telegram": bool(delete_telegram),
                      "telegram_deleted": telegram_deleted})
        return {
            "deleted": True,
            "telegram_deleted": telegram_deleted,
            "telegram_total": telegram_total,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting episode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/media/{media_type}/{db_index}/{tmdb_id}/episodes/subtitles")
async def admin_attach_episode_subtitles(
    request: Request,
    media_type: str,
    db_index: int,
    tmdb_id: str,
    payload: dict,
):
    """Attach one subtitle file to every quality file of every matching episode."""
    verify_admin_auth(request)
    try:
        media = await _get_tv_media(media_type, db_index, tmdb_id)
        subtitle_id = payload.get("id")
        if not subtitle_id:
            raise HTTPException(status_code=400, detail="Subtitle id is required")

        subtitle_data = {
            "id": subtitle_id,
            "name": payload.get("name") or "",
            "language": payload.get("language") or "Unknown",
            "format": str(payload.get("format") or "srt").lower(),
            "label": f"{payload.get('language') or 'Unknown'} • {str(payload.get('format') or 'srt').upper()}",
        }
        season_number = payload.get("season_number")
        if season_number in (None, "", "all"):
            push_path = "seasons.$[].episodes.$[].telegram.$[].subtitle_tracks"
            array_filters = None
        else:
            try:
                season_number = int(season_number)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid season_number")
            push_path = "seasons.$[s].episodes.$[].telegram.$[].subtitle_tracks"
            array_filters = [{"s.season_number": season_number}]

        result = await db.media.update_one(
            {"db_index": media.get("db_index"), "tmdb_id": media.get("tmdb_id"), "type": "tv"},
            {"$push": {push_path: subtitle_data}},
            array_filters=array_filters,
        )
        if not result.matched_count:
            raise HTTPException(status_code=404, detail="Episodes not found")
        audit(request, "media.attach_episode_subtitles",
              target=f"subtitle:{tmdb_id}:{subtitle_id}",
              detail={"season_number": season_number, "language": subtitle_data.get("language")})
        return {"attached": True, "modified": result.modified_count}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error attaching episode subtitles: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Admin Command Endpoints
@app.post("/admin/api/commands/set")
async def admin_set_default_id(request: Request, payload: dict):
    """Set default ID for metadata fetching"""
    verify_admin_auth(request)
    
    try:
        default_id = payload.get("default_id")
        if default_id:
            Telegram.USE_DEFAULT_ID = default_id
        else:
            Telegram.USE_DEFAULT_ID = None
        audit(request, "commands.set_default_id", target="commands:set",
              detail={"default_id": default_id})
        return {"set": True, "default_id": default_id}
    except Exception as e:
        LOGGER.error(f"Error setting default ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/commands/rescan")
async def admin_rescan_channel(request: Request, payload: dict):
    """Trigger channel rescan"""
    verify_admin_auth(request)
    
    try:
        try:
            channel_index = int(payload.get("channel_index", 0))
            limit = int(payload.get("limit", 100))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="channel_index and limit must be integers",
            )

        channels = Telegram.AUTH_CHANNEL or []
        if not channels:
            raise HTTPException(status_code=400, detail="No AUTH_CHANNEL is configured")
        if channel_index < 0 or channel_index >= len(channels):
            raise HTTPException(
                status_code=400,
                detail=f"channel_index must be between 0 and {len(channels) - 1}",
            )
        if limit < 0 or limit > 100000:
            raise HTTPException(
                status_code=400,
                detail="limit must be between 0 and 100000 (0 = no limit)",
            )

        # Import the standalone rescan runner from start.py
        from Backend.wzgram_pkg.plugins.start import perform_rescan
        from Backend.helper.channel_index import normalize_skip_first

        try:
            skip_first = normalize_skip_first(payload.get("skip_first"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Trigger rescan in background
        asyncio.create_task(perform_rescan(channel_index, limit,
                                           skip_first=skip_first))

        audit(request, "commands.rescan", target=f"index:{channel_index}",
              detail={"channel_index": channel_index, "limit": limit,
                      "skip_first": skip_first})
        return {"started": True, "channel_index": channel_index,
                "limit": limit, "skip_first": skip_first}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error triggering rescan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/channels")
async def admin_channels(request: Request):
    """Configured AUTH_CHANNEL entries for the admin channel pickers.

    Returns only the channel ids already present in the server config (they are
    not secret — the panel needs them to build t.me links). No network calls,
    so the dropdown stays usable even when the bot client is offline.
    """
    verify_admin_auth(request)
    from Backend.helper.channel_index import clean_channel_id

    channels = []
    for index, raw in enumerate(Telegram.AUTH_CHANNEL or []):
        value = str(raw).strip().strip('"').strip("'").strip()
        if not value:
            continue
        bare = clean_channel_id(int(value) if value.lstrip("-").isdigit() else value)
        channels.append({
            "index": index,
            "id": value,
            "clean_id": bare,
            "label": f"Channel {index + 1} · {bare}",
            "link": f"https://t.me/c/{bare}/1",
        })
    return {
        "channels": channels,
        "count": len(channels),
        "user_session": bool(os.getenv("USER_SESSION_STRING")),
    }


@app.get("/admin/api/storage")
async def admin_storage_report(request: Request):
    """Per-cluster storage quota + ops collection counts."""
    verify_admin_auth(request)
    try:
        return await db.get_storage_report()
    except Exception as e:
        LOGGER.error(f"Error building storage report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/merge-duplicates")
async def admin_merge_duplicates(request: Request, apply: bool = Query(False)):
    """Merge titles duplicated across clusters (apply=false is a dry run)."""
    verify_admin_auth(request)
    try:
        result = await db.merge_duplicate_media(apply=apply)
        audit(request, "media.merge_duplicates", target="db:merge_duplicates",
              detail={"applied": bool(apply),
                      **{k: v for k, v in (result or {}).items() if k != "errors"}})
        return result
    except Exception as e:
        LOGGER.error(f"Error merging duplicates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/queue/links")
async def admin_queue_links(request: Request, payload: dict):
    """Preview or queue a list of t.me post links (bulk add)."""
    verify_admin_auth(request)
    try:
        from Backend.wzgram_pkg.plugins.start import queue_message_links

        links = str(payload.get("links") or "")
        if not links.strip():
            raise HTTPException(status_code=400, detail="No links provided")
        preview = bool(payload.get("preview", False))
        result = await queue_message_links(links, preview=preview)
        if not preview:
            audit(request, "queue.links", target="queue:links",
                  detail={"queued": result.get("queued"), "failed": result.get("failed")})
        return result
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error queueing links: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Manual ingest metadata ──────────────────────────────────────────────────
# The real logic lives in helper/manual_add.py so the Telegram /manualadd
# command and this API route resolve references exactly the same way. The
# helper has no FastAPI dependency; convert its errors here.
from Backend.helper.manual_add import (  # noqa: E402
    ManualAddError as _ManualAddError,
    build_manual_metadata_info as _build_manual_metadata_info,
    parse_media_ref as _parse_media_ref,
)


def _media_ref_identifiers(media_ref: str) -> tuple:
    """Backwards-compatible name kept for the admin API and its tests."""
    try:
        return _parse_media_ref(media_ref)
    except _ManualAddError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


async def build_manual_metadata_info(**kwargs) -> dict:
    """Build metadata from an explicit TMDB/IMDb reference (no filename parse)."""
    try:
        return await _build_manual_metadata_info(**kwargs)
    except _ManualAddError as e:
        raise HTTPException(status_code=e.status, detail=str(e))



@app.post("/admin/api/queue/manual")
async def admin_queue_manual(request: Request, payload: dict):
    """Queue one Telegram post with metadata taken straight from TMDB/IMDb.

    The filename never enters the parser, so release names the auto pipeline
    cannot read are still addable. Only AUTH_CHANNEL posts are accepted.
    """
    verify_admin_auth(request)
    from Backend.wzgram_pkg.plugins.start import parse_and_queue_file
    from Backend.helper.channel_index import (
        IndexTargetError,
        clean_channel_id,
        is_auth_channel,
        resolve_link_target,
    )

    link = str(payload.get("link") or "").strip()
    if not link:
        raise HTTPException(status_code=400, detail="Paste the t.me link of the file post")

    try:
        chat, message_id = await resolve_link_target(StreamBot, link)
    except IndexTargetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not is_auth_channel(chat):
        raise HTTPException(status_code=403, detail="Channel is not listed in AUTH_CHANNEL")

    message = await StreamBot.get_messages(chat.id, message_id)
    if not message or getattr(message, "empty", False):
        raise HTTPException(status_code=404, detail="Post not found")
    file = message.video or message.document
    if not file:
        raise HTTPException(status_code=400, detail="That post has no video/document in it")

    info = await build_manual_metadata_info(
        media_ref=str(payload.get("media_ref") or ""),
        kind=payload.get("kind"),
        quality=payload.get("quality"),
        rip=payload.get("rip"),
        languages=payload.get("languages"),
        codec=payload.get("codec"),
        season=payload.get("season"),
        episode=payload.get("episode"),
    )

    channel_id = clean_channel_id(chat.id)
    file_unique_id = getattr(file, "file_unique_id", None)
    hash_val = (file_unique_id or "")[:6]
    already = await db.is_file_exists(int(channel_id), message_id, hash_val) or bool(
        file_unique_id and await db.is_file_unique_id_known(file_unique_id)
    )

    if already:
        queued, detail = False, "This file is already in the library — nothing new was added"
    else:
        queued = await parse_and_queue_file(
            message,
            channel_id,
            is_rescan=False,
            allow_probe=True,
            notify=False,
            metadata_info_override=info,
        )
        detail = None if queued else "The queue rejected it — check the bot log"

    LOGGER.info(
        f"🛠 Manual add {info['title']} ({info['media_type']} {info['quality']}) "
        f"from {channel_id}/{message_id} -> {'duplicate' if already else ('queued' if queued else 'rejected')}"
    )
    audit(request, "queue.manual", target=f"media:{info['media_type']}:{info['tmdb_id']}",
          detail={"channel": channel_id, "message_id": message_id,
                  "queued": bool(queued), "duplicate": already})
    return {
        "ok": bool(queued),
        "queued": bool(queued),
        "duplicate": already,
        "title": info["title"],
        "tmdb_id": info["tmdb_id"],
        "tmdb_url": info["tmdb_url"],
        "media_type": info["media_type"],
        "season_number": info.get("season_number"),
        "episode_number": info.get("episode_number"),
        "quality": info["quality"],
        "rip": info["rip"],
        "languages": info["languages"],
        "codec": info.get("codec"),
        "channel": channel_id,
        "message_id": message_id,
        "file_name": getattr(file, "file_name", None),
        "detail": detail,
    }


# ── File reassignment (fix a title the filename parser guessed wrong) ────────
@app.post("/admin/api/files/lookup")
async def admin_lookup_file(request: Request, payload: dict):
    """Find where a Telegram file is currently indexed before moving it."""
    verify_admin_auth(request)
    from Backend.helper.file_reassign import ReassignError, find_library_files

    link = str(payload.get("link") or "").strip()
    file_id = str(payload.get("file_id") or "").strip()
    unique = str(payload.get("file_unique_id") or "").strip()
    if not (link or file_id or unique):
        raise HTTPException(
            status_code=400,
            detail="Give a Telegram post link, a stored file id, or a file_unique_id.",
        )
    try:
        found = await find_library_files(
            link=link, file_id=file_id, file_unique_id=unique, bot=StreamBot
        )
    except ReassignError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e

    # The full quality entry is re-read server-side during the move; keep the
    # preview payload small.
    rows = [
        {k: v for k, v in row.items() if k != "quality_entry"}
        for row in found["matches"]
    ]
    return {**found, "matches": rows}


@app.post("/admin/api/files/reassign")
async def admin_reassign_file(request: Request, payload: dict):
    """Move an already-indexed file onto the correct movie / episode."""
    verify_admin_auth(request)
    from Backend.helper.file_reassign import ReassignError, find_library_files, reassign_file

    link = str(payload.get("link") or "").strip()
    file_id = str(payload.get("file_id") or "").strip()
    unique = str(payload.get("file_unique_id") or "").strip()
    media_ref = str(payload.get("media_ref") or "").strip()
    if not (link or file_id or unique):
        raise HTTPException(status_code=400, detail="Look the file up first.")
    if not media_ref:
        raise HTTPException(status_code=400, detail="Paste the correct TMDB/IMDb reference.")

    try:
        found = await find_library_files(
            link=link, file_id=file_id, file_unique_id=unique, bot=StreamBot
        )
    except ReassignError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e

    rows = found["matches"]
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="That file is not in the library yet — use Manual add to add it.",
        )

    source = payload.get("source")
    if len(rows) > 1:
        keys = ("db_index", "tmdb_id", "collection", "season_number", "episode_number")
        picked = None
        if isinstance(source, dict):
            picked = next(
                (
                    row for row in rows
                    if all(
                        str(row.get(k)) == str(source[k])
                        for k in keys if source.get(k) is not None
                    )
                ),
                None,
            )
        if picked is None:
            raise HTTPException(
                status_code=409,
                detail="That file is indexed under several titles. Pick one in the lookup result and retry.",
            )
        rows = [picked]

    kwargs = dict(
        media_ref=media_ref,
        kind=payload.get("kind"),
        quality=payload.get("quality"),
        rip=payload.get("rip"),
        languages=payload.get("languages"),
        codec=payload.get("codec"),
        season=payload.get("season"),
        episode=payload.get("episode"),
        replace_existing_quality=bool(payload.get("replace_existing_quality")),
        remove_empty_title=payload.get("remove_empty_title", True) is not False,
    )
    try:
        result = await reassign_file(rows[0], **kwargs)
    except ReassignError as e:
        audit(request, "media.reassign_file", target=f"media_file:{rows[0].get('file_id')}",
              detail={"media_ref": media_ref, "error": str(e)}, result="error")
        raise HTTPException(status_code=e.status, detail=str(e)) from e
    except Exception as e:
        LOGGER.error(f"Reassign failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    # A moved file changes what /api/id/* returns; drop stale cached responses.
    try:
        await clear_all_caches()
    except Exception as e:  # pragma: no cover - cache is best effort
        LOGGER.debug(f"Cache clear after reassign failed: {e}")

    LOGGER.info(
        f"🔀 Reassign {result['from']['title']} -> {result['title']} "
        f"({result['media_type']} {result['quality']}) file={result['file_id']}"
    )
    audit(request, "media.reassign_file",
          target=f"media:{result['media_type']}:{result['tmdb_id']}",
          detail={
              "file_id": result["file_id"], "media_ref": media_ref,
              "from": result["from"], "quality": result["quality"],
              "replaced_file_id": result["replaced_file_id"],
              "old_title_removed": result["old_title_removed"],
              "moved": result["moved"],
          })
    return result


async def _queue_snapshot() -> Dict[str, Any]:
    """Queue state shared by the admin queue desk and the health page."""
    from Backend.wzgram_pkg.plugins import start as queue_start

    engine = "mongodb" if QueueConfig.ENABLE_MONGODB_QUEUE else "memory"
    raw_counters = getattr(queue_start, "queue_stats", {}) or {}
    counters = {
        key: int(raw_counters.get(key, 0) or 0)
        for key in ("processed", "failed", "skipped", "retried", "replaced")
    }
    try:
        floodwait = int(queue_start.floodwait_remaining())
    except Exception:
        floodwait = 0

    if engine == "mongodb":
        counts = await db.get_queue_stats()
        paused = bool(await db.is_queue_paused())
        channels = await db.get_queue_channels(10)
        processing_items = await db.get_queue_processing(10)
        pending = int(counts.get("pending", 0) or 0)
        processing = int(counts.get("processing", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)
        retries = int(counts.get("total_retries", 0) or 0)
    else:
        paused = bool(getattr(queue_start, "is_queue_paused", False))
        channels = []
        processing_items = []
        pending = queue_start.file_queue.qsize()
        processing = 0
        failed = counters["failed"]
        retries = counters["retried"]

    workers = max(1, int(runtime_config.get("queue_workers")))
    eta_seconds = None
    file_delay = int(runtime_config.get("file_queue_delay"))
    batch_size = max(1, int(runtime_config.get("batch_size")))
    batch_gap = max(0.0, float(runtime_config.get("batch_gap_seconds")))
    if pending and not paused:
        per_file = max(0.0, float(file_delay)) + 10.0
        work = pending * per_file + math.ceil(pending / batch_size) * batch_gap
        eta_seconds = int(work / workers) + int(floodwait)

    # Stuck claims: items a worker holds right now. In the in-memory engine
    # there is no claim record, so the list is always empty there.
    claims = await db.get_queue_claims(100) if engine == "mongodb" else []

    # The supervisor reports what is actually live versus what was asked for.
    worker_report = {"configured": workers, "desired": workers, "running": [],
                     "retiring": [], "supervised": False}
    if engine == "mongodb":
        try:
            from Backend.wzgram_pkg.plugins import start as queue_start
            worker_report = queue_start.queue_supervisor.report()
        except Exception as e:
            LOGGER.debug(f"Queue supervisor report unavailable: {e}")

    return {
        "engine": engine,
        "paused": paused,
        "workers": workers,
        "worker_report": worker_report,
        "floodwait_seconds": floodwait,
        "pending": pending,
        "processing": processing,
        "failed": failed,
        "retries": retries,
        "eta_seconds": eta_seconds,
        "processing_items": processing_items,
        "claims": claims,
        "channels": channels,
        "counters": counters,
        "settings": {
            "file_delay": file_delay,
            "batch_size": batch_size,
            "batch_gap": batch_gap,
            "max_retry": QueueConfig.MAX_RETRY_COUNT,
            "queue_ttl_days": QueueConfig.QUEUE_TTL_DAYS,
        },
    }


@app.get("/admin/api/queue")
async def admin_queue_desk(request: Request):
    """Live queue status: counts, pause state, FloodWait and channel backlog."""
    verify_admin_auth(request)
    try:
        return await _queue_snapshot()
    except Exception as e:
        LOGGER.error(f"Error building queue snapshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/queue/pause")
async def admin_queue_pause(request: Request):
    verify_admin_auth(request)
    try:
        from Backend.wzgram_pkg.plugins import start as queue_start

        queue_start.is_queue_paused = True
        persisted = await db.set_queue_paused(True) if QueueConfig.ENABLE_MONGODB_QUEUE else False
        audit(request, "queue.pause", target="queue:pause", detail={"persisted": persisted})
        return {"paused": True, "persisted": persisted}
    except Exception as e:
        LOGGER.error(f"Error pausing queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/queue/resume")
async def admin_queue_resume(request: Request):
    verify_admin_auth(request)
    try:
        from Backend.wzgram_pkg.plugins import start as queue_start

        queue_start.is_queue_paused = False
        persisted = await db.set_queue_paused(False) if QueueConfig.ENABLE_MONGODB_QUEUE else False
        audit(request, "queue.resume", target="queue:resume", detail={"persisted": persisted})
        return {"paused": False, "persisted": persisted}
    except Exception as e:
        LOGGER.error(f"Error resuming queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/queue/clear")
async def admin_queue_clear(request: Request, payload: dict):
    verify_admin_auth(request)
    try:
        if str(payload.get("confirm") or "").strip().upper() != "CLEAR":
            raise HTTPException(status_code=400, detail="Type CLEAR to confirm")
        if not QueueConfig.ENABLE_MONGODB_QUEUE:
            raise HTTPException(status_code=400, detail="Clear is only available for the MongoDB queue")
        cleared = await db.clear_queue()
        audit(request, "queue.clear", target="queue:clear", detail={"cleared": cleared})
        return {"cleared": cleared}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error clearing queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/cache")
async def admin_cache_stats(request: Request):
    """In-memory cache stats plus remux cache disk usage."""
    verify_admin_auth(request)
    try:
        return {
            "caches": await get_all_cache_stats(),
            "remux": _remux_cache_stats(),
            "redis": await redis_cache.health_check() if redis_cache._available else {"status": "disabled"},
        }
    except Exception as e:
        LOGGER.error(f"Error building cache report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# RUNTIME CONFIG
# ============================================================================
@app.get("/admin/api/config")
async def admin_get_config(request: Request):
    """Effective value, override state and metadata for every runtime knob."""
    verify_admin_auth(request)
    try:
        return runtime_config.describe()
    except Exception as e:
        LOGGER.error(f"Error reading runtime config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/admin/api/config")
async def admin_set_config(request: Request, payload: dict):
    """Persist a runtime-config override. ``{"key": ..., "value": ...}``.

    An unknown key is rejected, not written.
    """
    verify_admin_auth(request)
    key = str(payload.get("key") or "").strip()
    if key not in KNOBS:
        raise HTTPException(status_code=400, detail=f"Unknown config key: {key}")

    if payload.get("reset"):
        try:
            await runtime_config.unset(key, updated_by=admin_actor(request))
        except KnobError as e:
            raise HTTPException(status_code=400, detail=str(e))
        audit(request, "config.reset", target=f"config:{key}", detail={"key": key})
        return {"key": key, "value": runtime_config.get(key), "overridden": False}

    if "value" not in payload:
        raise HTTPException(status_code=400, detail="Provide a value (or reset: true)")
    try:
        value = await runtime_config.set(key, payload.get("value"),
                                         updated_by=admin_actor(request))
    except KnobError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        LOGGER.error(f"Error saving runtime config {key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    audit(request, "config.set", target=f"config:{key}",
          detail={"key": key, "value": value})
    return {"key": key, "value": value, "overridden": True}


# ============================================================================
# AUDIT LOG
# ============================================================================
@app.get("/admin/api/audit")
async def admin_get_audit(
    request: Request,
    actor: str = Query(""),
    action: str = Query(""),
    target: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Newest-first page of admin audit entries."""
    verify_admin_auth(request)
    try:
        def _parse(value: str):
            if not value:
                return None
            text = str(value).strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed

        return await db.get_admin_audit(
            actor=actor, action=action, target=target,
            date_from=_parse(date_from), date_to=_parse(date_to),
            page=page, page_size=page_size,
        )
    except Exception as e:
        LOGGER.error(f"Error reading audit log: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# STUCK CLAIMS
# ============================================================================
@app.post("/admin/api/queue/claims/release")
async def admin_release_claims(request: Request, payload: dict):
    """Return stuck ``processing`` items to ``pending``.

    Either ``{"ids": [...]}`` or ``{"older_than_seconds": n}``. This is the
    manual "do not wait two hours" path; ``QUEUE_CLAIM_TIMEOUT`` stays as the
    automatic one.
    """
    verify_admin_auth(request)
    ids = payload.get("ids") or []
    older_than = payload.get("older_than_seconds")
    if not ids and older_than is None:
        raise HTTPException(
            status_code=400,
            detail="Provide either ids or older_than_seconds",
        )
    try:
        result = await db.release_queue_claims(
            ids=ids,
            older_than_seconds=int(older_than) if older_than is not None else None,
        )
    except Exception as e:
        LOGGER.error(f"Error releasing queue claims: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    audit(request, "queue.claims_release", target="queue:claims",
          detail={"ids": len(ids), "older_than_seconds": older_than,
                  "released": result.get("released")})
    return result


# ============================================================================
# INDEX SCANS
# ============================================================================
@app.get("/admin/api/index-scans")
async def admin_index_scans(request: Request):
    """Scan states plus the live registry (cap, running, waiting)."""
    verify_admin_auth(request)
    try:
        from Backend.helper.channel_index import scan_status
        scans = await db.list_index_scans(50)
        waiting = await db.get_waiting_index_scans()
        return {
            "cap": scan_status()["cap"],
            "registry": scan_status(),
            "scans": scans,
            "waiting": [
                {"channel_id": s.get("channel_id"), "title": s.get("title"),
                 "updated_at": s.get("updated_at")}
                for s in waiting
            ],
        }
    except Exception as e:
        LOGGER.error(f"Error listing index scans: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/index-scans/start")
async def admin_index_scans_start(request: Request, payload: dict):
    """Start channel scans from pasted t.me links.

    Uses the same ``request_index_scans`` path the bot uses, so the panel and
    the bot cannot diverge.
    """
    verify_admin_auth(request)
    links = payload.get("links") or ""
    channel_ids = payload.get("channels") or []
    if isinstance(channel_ids, str):
        channel_ids = re.split(r"[\s,]+", channel_ids)
    mode = str(payload.get("mode") or "index").strip().lower()
    if mode not in ("index", "audit"):
        raise HTTPException(status_code=400, detail="mode must be 'index' or 'audit'")
    if mode == "audit" and not AdminOpsConfig.ORPHAN_SCAN:
        raise HTTPException(
            status_code=400,
            detail="Orphan scan is disabled (ORPHAN_SCAN must be enabled)",
        )
    if not str(links).strip() and not channel_ids:
        raise HTTPException(
            status_code=400, detail="Select a channel or paste one or more t.me links"
        )

    from Backend.helper.channel_index import (
        MODE_AUDIT, is_auth_channel, normalize_skip_first,
        request_index_scans, resolve_link_target,
    )
    try:
        skip_first = normalize_skip_first(payload.get("skip_first"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from Backend.wzgram_pkg import get_index_client
    from Backend.wzgram_pkg.plugins.start import _index_queue_file

    try:
        client = get_index_client()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    chats = []
    errors = []
    seen = set()

    # Channels picked from the dropdown are resolved directly by id — no post
    # link is needed, so an empty/private channel history still works.
    for raw_id in channel_ids:
        value = str(raw_id).strip()
        if not value:
            continue
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            errors.append(f"{value} — not a channel id")
            continue
        try:
            chat = await client.get_chat(numeric)
        except Exception as e:
            errors.append(f"{value} — could not open channel: {e}")
            continue
        if not is_auth_channel(chat):
            errors.append(f"{value} — channel is not listed in AUTH_CHANNEL")
            continue
        if chat.id in seen:
            continue
        seen.add(chat.id)
        chats.append(chat)

    for token in re.split(r"[\s,]+", str(links)):
        token = token.strip()
        if not token:
            continue
        try:
            chat, _message_id = await resolve_link_target(client, token)
        except Exception as e:
            errors.append(f"{token} — {e}")
            continue
        if not is_auth_channel(chat):
            errors.append(f"{token} — channel is not listed in AUTH_CHANNEL")
            continue
        if chat.id in seen:
            continue
        seen.add(chat.id)
        chats.append(chat)

    if not chats:
        raise HTTPException(
            status_code=400,
            detail="No usable channel selected"
            + (f" ({'; '.join(errors[:5])})" if errors else ""),
        )

    try:
        result = await request_index_scans(
            client, chats, 0, _index_queue_file,
            notify_client=None, mode=MODE_AUDIT if mode == "audit" else "index",
            skip_first=skip_first,
        )
    except Exception as e:
        LOGGER.error(f"Could not start index scans: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    audit(request, "index.start", target="index:multi",
          detail={"mode": mode, "started": result.get("started"),
                  "waiting": result.get("waiting"),
                  "skip_first": skip_first})
    return {**result, "errors": errors}


@app.post("/admin/api/index-scans/stop")
async def admin_index_scans_stop(request: Request, payload: dict):
    """Stop one scan (or every scan)."""
    verify_admin_auth(request)
    from Backend.helper.channel_index import stop_index_scan

    raw = payload.get("channel_id")
    target = None
    if raw not in (None, ""):
        try:
            target = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="channel_id must be an integer")

    stopped = await stop_index_scan(target)
    audit(request, "index.stop", target=f"index:{target or 'all'}",
          detail={"stopped": stopped})
    return {"stopped": stopped}


# ============================================================================
# ADMIN JOBS
# ============================================================================
@app.get("/admin/api/jobs")
async def admin_jobs(request: Request):
    """Job documents plus which kinds are running right now."""
    verify_admin_auth(request)
    try:
        from Backend.helper.admin_jobs import runner
        from Backend.helper.database import ADMIN_LIST_FLAGS
        return {
            "jobs": await db.list_admin_jobs(50),
            "running": runner.status(),
            "flags": sorted(ADMIN_LIST_FLAGS.keys()),
            "switches": {
                "audio_lang_backfill": AdminOpsConfig.AUDIO_LANG_BACKFILL,
                "metadata_remediation": AdminOpsConfig.METADATA_REMEDIATION,
                "orphan_scan": AdminOpsConfig.ORPHAN_SCAN,
            },
        }
    except Exception as e:
        LOGGER.error(f"Error listing admin jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/jobs/start")
async def admin_job_start(request: Request, payload: dict):
    """Start one of the resumable admin jobs."""
    verify_admin_auth(request)
    from Backend.helper.admin_jobs import JobError, runner

    kind = str(payload.get("kind") or "").strip()
    params = payload.get("params") or {}

    # Each job kind has its own switch, so a job can never run by accident.
    if kind == "audio_lang_backfill" and not AdminOpsConfig.AUDIO_LANG_BACKFILL:
        raise HTTPException(
            status_code=400,
            detail="Audio-language backfill is disabled (AUDIO_LANG_BACKFILL must be enabled)",
        )
    if kind == "metadata_remediation" and not AdminOpsConfig.METADATA_REMEDIATION:
        raise HTTPException(
            status_code=400,
            detail="Metadata remediation is disabled (METADATA_REMEDIATION must be enabled)",
        )
    if kind == "orphan_scan" and not AdminOpsConfig.ORPHAN_SCAN:
        raise HTTPException(
            status_code=400,
            detail="Orphan scan is disabled (ORPHAN_SCAN must be enabled)",
        )

    try:
        result = await runner.start(kind, params, actor=admin_actor(request))
    except JobError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        LOGGER.error(f"Error starting job {kind}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    audit(request, "job.start", target=f"job:{kind}",
          detail={"kind": kind, "params": params})
    return result


@app.post("/admin/api/jobs/stop")
async def admin_job_stop(request: Request, payload: dict):
    """Ask a running job to stop at its next safe point."""
    verify_admin_auth(request)
    from Backend.helper.admin_jobs import runner

    kind = str(payload.get("kind") or "").strip()
    stopped = runner.stop(kind)
    audit(request, "job.stop", target=f"job:{kind}", detail={"kind": kind})
    return {"kind": kind, "stopping": stopped}


# ============================================================================
# AUDIO-LANGUAGE CURATION
# ============================================================================
@app.get("/admin/api/audio-curation")
async def admin_audio_curation(
    request: Request,
    min_count: int = Query(0, ge=0),
    sort: str = Query("count:desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    """Titles ranked by their distinct audio-language count, across clusters."""
    verify_admin_auth(request)
    try:
        from Backend.helper.audio_languages import language_names_for

        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)
        direction = -1 if str(sort).endswith("asc") else 1
        by_count = not str(sort).startswith("title")

        rows: List[dict] = []
        for cluster in db.clusters or []:
            for kind in ("movie", "tv"):
                collection = cluster.collection(kind)
                query: Dict[str, Any] = {}
                if min_count:
                    query["audio_language_count"] = {"$gte": int(min_count)}
                try:
                    cursor = collection.find(
                        query,
                        {"tmdb_id": 1, "title": 1, "release_year": 1, "poster": 1,
                         "audio_languages": 1, "audio_language_count": 1,
                         "dropped_audio_languages": 1, "telegram": 1, "seasons": 1},
                    )
                    async for doc in cursor:
                        languages = doc.get("audio_languages")
                        if not isinstance(languages, list):
                            languages = language_names_for(kind, doc)
                        rows.append({
                            "tmdb_id": doc.get("tmdb_id"),
                            "title": doc.get("title"),
                            "release_year": doc.get("release_year"),
                            "poster": doc.get("poster"),
                            "type": kind,
                            "db_index": cluster.index,
                            "audio_languages": languages,
                            "audio_language_count": doc.get("audio_language_count")
                            if doc.get("audio_language_count") is not None else len(languages),
                            "dropped_audio_languages": doc.get("dropped_audio_languages") or [],
                        })
                except Exception as e:
                    LOGGER.error(f"Audio curation read failed on cluster {cluster.index}/{kind}: {e}")
                    continue

        if by_count:
            rows.sort(key=lambda r: (r["audio_language_count"], str(r.get("title") or "")),
                      reverse=direction == 1)
        else:
            rows.sort(key=lambda r: str(r.get("title") or "").casefold(),
                      reverse=direction == 1)

        total = len(rows)
        start = (page - 1) * page_size
        return {
            "items": rows[start:start + page_size],
            "total_count": total,
            "page": page,
            "page_size": page_size,
            "sort": sort,
            "min_count": min_count,
        }
    except Exception as e:
        LOGGER.error(f"Error building audio curation view: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# ORPHAN FILE REPORT
# ============================================================================
@app.get("/admin/api/orphans")
async def admin_orphans(
    request: Request,
    channel: str = Query(""),
    include_resolved: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Video posts with no database match, newest scan first."""
    verify_admin_auth(request)
    try:
        return await db.list_orphan_files(
            channel=channel, include_resolved=include_resolved,
            page=page, page_size=page_size,
        )
    except Exception as e:
        LOGGER.error(f"Error listing orphan files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/orphans/queue")
async def admin_queue_orphans(request: Request, payload: dict):
    """Feed selected orphan posts back into the normal ingestion pipeline.

    The recovery path is the link path the panel already has.
    """
    verify_admin_auth(request)
    ids = payload.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="Provide the orphan ids to queue")

    listing = await db.get_orphan_files([str(i) for i in ids])
    if not listing:
        raise HTTPException(status_code=404, detail="No matching orphan rows found")
    rows = [{
        "channel": doc.get("channel"),
        "message_id": doc.get("message_id"),
    } for doc in listing]

    from Backend.wzgram_pkg.plugins.start import queue_message_links

    links = []
    for row in rows:
        channel = row.get("channel")
        message_id = row.get("message_id")
        if channel and message_id:
            links.append(f"https://t.me/c/{str(channel).lstrip('-')}/{message_id}")

    if not links:
        raise HTTPException(status_code=400, detail="Selected rows have no usable link")

    try:
        result = await queue_message_links("\n".join(links), preview=False)
    except Exception as e:
        LOGGER.error(f"Error queueing orphan files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    audit(request, "orphan.queue", target="orphan:queue",
          detail={"ids": len(rows), "queued": result.get("queued")})
    return result


@app.post("/admin/api/cache/clear")
async def admin_cache_clear(request: Request, payload: dict):
    verify_admin_auth(request)
    target = str(payload.get("target") or "all").lower()
    if target not in ("video", "remux", "all"):
        raise HTTPException(status_code=400, detail="target must be video, remux or all")
    try:
        result: Dict[str, Any] = {}
        if target in ("video", "all"):
            await video_chunk_cache.clear()
            result["video"] = "cleared"
        if target == "all":
            await clear_all_caches()
            result["all"] = "cleared"
        if target in ("remux", "all"):
            result["remux"] = _clear_remux_cache()
        audit(request, "cache.clear", target=f"cache:{target}", detail={"target": target})
        return result
    except Exception as e:
        LOGGER.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/health")
async def admin_health(request: Request):
    """One-screen health: clusters, storage alerts, queue, ffmpeg, disk."""
    verify_admin_auth(request)
    try:
        alerts: List[str] = []
        clusters = []
        if not (getattr(db, "clusters", None) or []):
            alerts.append("No MongoDB cluster reachable")
        for cluster in db.clusters or []:
            entry = {"cluster": cluster.index, "status": "up"}
            try:
                await cluster.db.command("ping")
            except Exception as e:
                entry["status"] = "down"
                entry["error"] = str(e)[:140]
                alerts.append(f"Cluster {cluster.index} is unreachable")
            clusters.append(entry)

        queue = await _queue_snapshot()
        if queue.get("floodwait_seconds"):
            alerts.append(f"Telegram FloodWait active ({queue['floodwait_seconds']}s)")
        if queue.get("pending", 0) >= 1000:
            alerts.append(f"Queue backlog: {queue['pending']} pending")

        try:
            storage = await db.get_storage_report()
            alerts.extend(storage.get("alerts") or [])
        except Exception as e:
            storage = {"clusters": [], "alerts": []}
            alerts.append(f"Storage report failed: {str(e)[:120]}")

        try:
            disk = shutil.disk_usage(os.getcwd())
            disk_info = {
                "free_gb": round(disk.free / 1073741824, 1),
                "total_gb": round(disk.total / 1073741824, 1),
            }
        except OSError:
            disk_info = {}

        return {
            "status": "ok" if not alerts else "degraded",
            "alerts": alerts,
            "clusters": clusters,
            "queue": queue,
            "storage": storage,
            "system": {
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "ffprobe": bool(shutil.which("ffprobe")),
                "python": platform.python_version(),
                "version": __version__,
                "uptime_seconds": int(time() - StartTime),
                "disk": disk_info,
            },
        }
    except Exception as e:
        LOGGER.error(f"Error building health report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/analytics")
async def admin_get_analytics(request: Request):
    """Get system analytics"""
    verify_admin_auth(request)
    
    try:
        # Get media counts
        movie_count = await db.media.count_documents({"type": "movie"})
        tv_count = await db.media.count_documents({"type": "tv"})
        anime_count = await db.media.count_documents({"is_anime": True})
        
        # Get total files
        total_files = 0
        total_subtitles = 0
        
        async for media in db.media.find({}):
            total_files += len(media.get("telegram", []))
            for quality in media.get("telegram", []):
                total_subtitles += len(quality.get("subtitles", []))
        
        # Get cache stats
        cache_stats = await get_all_cache_stats()
        
        return {
            "media": {
                "movies": movie_count,
                "tv_series": tv_count,
                "anime": anime_count,
                "total": movie_count + tv_count
            },
            "files": {
                "total": total_files,
                "subtitles": total_subtitles
            },
            "cache": cache_stats,
            "system": {
                "version": __version__,
                "uptime": str(get_readable_time(time() - StartTime))
            }
        }
    except Exception as e:
        LOGGER.error(f"Error getting analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/library-health")
async def admin_library_health(request: Request, sample_limit: int = Query(6, ge=1, le=20)):
    """Missing metadata/files/subtitles counts plus samples, across clusters."""
    verify_admin_auth(request)
    try:
        return await db.get_library_health(sample_limit=sample_limit)
    except Exception as e:
        LOGGER.error(f"Error building library health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/views-analytics")
async def admin_views_analytics(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
):
    """Daily view counts and top viewed titles."""
    verify_admin_auth(request)
    try:
        return await db.get_views_analytics(days=days, limit=limit)
    except Exception as e:
        LOGGER.error(f"Error building views analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/commands/clear-cache")
async def admin_clear_cache(request: Request):
    """Clear all caches"""
    verify_admin_auth(request)
    
    try:
        await clear_all_caches()
        audit(request, "cache.clear", target="cache:all", detail={"target": "all"})
        return {"cleared": True}
    except Exception as e:
        LOGGER.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Failed Files Endpoints
@app.get("/admin/api/failed-files")
async def admin_get_failed_files(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    reason_filter: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None)
):
    """Get failed files with pagination and optional filtering"""
    verify_admin_auth(request)
    
    try:
        from datetime import datetime
        
        # Parse date filters if provided
        date_from_dt = None
        date_to_dt = None
        if date_from:
            try:
                date_from_dt = datetime.fromisoformat(date_from)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_dt = datetime.fromisoformat(date_to)
            except ValueError:
                pass
        
        result = await db.get_failed_files(
            page=page,
            page_size=page_size,
            reason_filter=reason_filter,
            date_from=date_from_dt,
            date_to=date_to_dt
        )
        
        return result
    except Exception as e:
        LOGGER.error(f"Error getting failed files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/failed-files/{id}")
async def admin_delete_failed_file(request: Request, id: str):
    """Delete a specific failed file entry"""
    verify_admin_auth(request)
    
    try:
        result = await db.delete_failed_file(id)
        if result == 0:
            raise HTTPException(status_code=404, detail="Failed file entry not found")

        audit(request, "failed_files.delete", target=f"failed_files:{id}", detail={"id": id})
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting failed file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/failed-files")
async def admin_clear_failed_files(request: Request, reason_filter: str = Query(None)):
    """Clear failed file logs, optionally only entries matching a reason filter."""
    verify_admin_auth(request)

    try:
        deleted = await db.clear_failed_files(reason_filter)
        audit(request, "failed_files.clear", target="failed_files:clear",
              detail={"reason_filter": reason_filter, "deleted": deleted})
        return {"cleared": True, "deleted": deleted}
    except Exception as e:
        LOGGER.error(f"Error clearing failed files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/failed-files/retry-all")
async def admin_retry_all_failed_files(
    request: Request,
    reason_filter: str = Query(None),
    limit: int = Query(25, ge=1, le=100),
):
    """Retry the oldest failed entries that match the current reason filter."""
    verify_admin_auth(request)
    try:
        result = await db.get_failed_files(page=1, page_size=limit, reason_filter=reason_filter)
        items = result.get("items") or []
        summary: Dict[str, Any] = {
            "attempted": len(items),
            "queued": 0,
            "failed": 0,
            "remaining": max(0, int(result.get("total_count") or 0) - len(items)),
            "errors": [],
        }
        for entry in items:
            label = entry.get("title") or entry.get("filename") or entry.get("_id")
            try:
                await _retry_failed_entry(entry)
                await db.delete_failed_file(entry["_id"])
                summary["queued"] += 1
            except HTTPException as e:
                summary["failed"] += 1
                if len(summary["errors"]) < 5:
                    summary["errors"].append(f"{label}: {e.detail}")
            except Exception as e:
                summary["failed"] += 1
                if len(summary["errors"]) < 5:
                    summary["errors"].append(f"{label}: {e}")
            await asyncio.sleep(0.5)
        audit(request, "failed_files.retry_all", target="failed_files:retry_all",
              detail={"attempted": summary["attempted"], "queued": summary["queued"],
                      "failed": summary["failed"], "reason_filter": reason_filter})
        return summary
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error retrying failed files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/failed-files/{id}/retry")
async def admin_retry_failed_file(request: Request, id: str):
    """Re-queue a failed file from its stored Telegram source message."""
    verify_admin_auth(request)
    try:
        failed_file = await db.get_failed_file(id)
        if not failed_file:
            raise HTTPException(status_code=404, detail="Failed file entry not found")

        await _retry_failed_entry(failed_file)
        await db.delete_failed_file(id)
        LOGGER.info(f"Retried failed file from Telegram: {failed_file.get('title')}")
        audit(request, "failed_files.retry", target=f"failed_files:{id}",
              detail={"title": failed_file.get("title")})
        return {"retry": True, "queued": True, "message": "Re-queued from Telegram"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error retrying failed file: {e}")
        raise HTTPException(status_code=500, detail=str(e))






@app.get("/", response_model=Dict[str, Any])
async def get_bot_workloads():
    """Public liveness endpoint; intentionally reveals no internals."""
    return {"status": "ok"}


@app.get("/health")
async def health():
    """Lightweight liveness probe for Docker/Heroku."""
    return {"status": "ok"}


@app.get("/is_member")
async def is_member(request: Request, user_id: int, channel: int):
    expected_key = Telegram.INTERNAL_API_KEY
    if not expected_key:
        raise HTTPException(status_code=404, detail="Not found")
    provided_key = request.headers.get("x-api-key", "")
    if not hmac.compare_digest(provided_key, expected_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    try:
        member = await StreamBot.get_chat_member(channel, user_id)
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return {"is_member": True}
        else:
            return {"is_member": False}
    except Exception as e:
        LOGGER.warning(f"is_member lookup failed: {e}")
        return {"is_member": False}


@app.get("/watch/{tmdb_id}", response_class=HTMLResponse)
async def watch(
    request: Request, 
    tmdb_id: int, 
    season_number: Optional[int] = Query(None), 
    episode_number: Optional[int] = Query(None)
):
    """
    Serve the appropriate HTML template for watching a movie or a specific TV episode.

    :param request: The incoming HTTP request.
    :param tmdb_id: The TMDB ID of the movie or TV show.
    :param season_number: The season number (optional, only for TV shows).
    :param episode_number: The episode number (optional, only for TV shows).
    :return: The rendered HTML template.
    """

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "id": tmdb_id,
            "season": season_number,
            "episode": episode_number
        }
    )



@app.get("/api/tvshows", response_model=dict)
@cache_response(ttl=CacheConfig.LIST_CACHE_TTL, key_prefix="tvshows")
async def get_sorted_tv_shows(
    sort_by: List[str] = Query(default=["rating:desc"], description="List of fields to sort by. Format: field:direction"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=10, ge=1, description="Number of TV shows per page"),
    genre: Optional[str] = Query(None, description="Filter by genre name (e.g., 'Action', 'Comedy')"),
    language: Optional[str] = Query(None, description="Filter by language code (e.g., 'en', 'hi', 'ko')"),
    min_rating: Optional[float] = Query(None, ge=0, le=10, description="Minimum rating filter")
):
    try:
        sort_params = [tuple(param.split(":")) for param in sort_by]
        sorted_tv_shows = await db.sort_tv_shows(sort_params, page, page_size, genre=genre, language=language, min_rating=min_rating)
        return sorted_tv_shows
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies", response_model=dict)
@cache_response(ttl=CacheConfig.LIST_CACHE_TTL, key_prefix="movies")
async def get_sorted_movies(
    sort_by: List[str] = Query(default=["rating:desc"], description="List of fields to sort by. Format: field:direction"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=10, ge=1, description="Number of movies per page"),
    genre: Optional[str] = Query(None, description="Filter by genre name (e.g., 'Action', 'Comedy')"),
    language: Optional[str] = Query(None, description="Filter by language code (e.g., 'en', 'hi', 'ko')"),
    min_rating: Optional[float] = Query(None, ge=0, le=10, description="Minimum rating filter")
):
    try:
        sort_params = [tuple(param.split(":")) for param in sort_by]
        sorted_movies = await db.sort_movies(sort_params, page, page_size, genre=genre, language=language, min_rating=min_rating)
        return sorted_movies
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/hero", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="hero")
async def get_hero_carousel():
    """Hero carousel: the curated picks when the admin pinned any.

    With nothing pinned this returns the same newest-titles list the homepage
    used before curation existed, so an untouched install looks untouched.
    """
    try:
        return await db.build_hero_carousel()
    except Exception as e:
        LOGGER.error(f"Error building hero carousel: {e}")
        raise HTTPException(status_code=400, detail=str(e))


#Homepage:------
# hero = http://localhost:8000/api/tvshows?sort_by=rating:desc&sort_by=release_year:desc&page=1&page_size=10
# latest movies = http://localhost:8000/api/movies?sort_by=updated_on:desc&page=1&page_size=20
# latest tvshows = http://localhost:8000/api/tvshows?sort_by=updated_on:desc&page=1&page_size=20

#Movies:----------
# latest movies = http://localhost:8000/api/movies?sort_by=updated_on:desc&page=1&page_size=40
# action movies = http://localhost:8000/api/movies?genre=Action&sort_by=rating:desc&page=1&page_size=20
# comedy movies = http://localhost:8000/api/movies?genre=Comedy&sort_by=rating:desc&page=1&page_size=20

#Tvshow:----------
# latest tvshows = http://localhost:8000/api/tvshows?sort_by=updated_on:desc&page=1&page_size=40



@app.get("/api/id/{tmdb_id}", response_model=dict)
async def get_media_details(
    tmdb_id: int, 
    season_number: Optional[int] = Query(None), 
    episode_number: Optional[int] = Query(None)
) -> Union[dict, None]:
    """
    FastAPI endpoint to get details of a document, specific season, or episode
    by TMDB ID, season number, and episode number.
    """
    details = await db.get_media_details(
        tmdb_id=tmdb_id, 
        season_number=season_number, 
        episode_number=episode_number
    )

    if not details:
        raise HTTPException(status_code=404, detail="Requested details not found")
    
    return details



@app.get("/api/similar/")
async def get_similar_media(
    tmdb_id: int,
    media_type: str = Query(..., pattern="^(movie|tvshow)$"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=10, ge=1, description="Number of similar media per page")
):
    """
    FastAPI endpoint to get similar movies or TV shows based on the parent tmdb_id, sorted by the number of genre matches and rating.
    
    :param tmdb_id: The TMDB ID of the parent movie or TV show.
    :param media_type: The media type ('movie' or 'tvshow').
    :param page: The page number to return.
    :param page_size: The number of similar media per page.
    :return: A dictionary containing the total count and a list of similar movies or TV shows.
    """
    similar_media = await db.find_similar_media(tmdb_id=tmdb_id, media_type=media_type, page=page, page_size=page_size)
    return similar_media


# moviepage = http://127.0.0.1:8000/api/similar/?tmdb_id=695962&media_type=movie&limit=10
# similar movie tab = http://127.0.0.1:8000/api/similar/?tmdb_id=695962&media_type=movie&limit=40

# tvshowpage = http://127.0.0.1:8000/api/similar/?tmdb_id=695962&media_type=tvshow&limit=10
# similar tvshow tab = http://127.0.0.1:8000/api/similar/?tmdb_id=695962&media_type=tvshow&limit=40


@app.get("/api/cast/{cast_id}", response_model=dict)
@cache_response(ttl=CacheConfig.MEDIA_INFO_CACHE_TTL, key_prefix="cast_media")
async def get_media_by_cast(
    cast_id: str,
    name: str = Query(default="", description="Cast member display name"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=24, ge=1, le=100, description="Number of titles per page"),
):
    """
    Get all movies/TV shows featuring a cast member (by TMDB person id),
    across every shard. Falls back to name matching when the id is absent.
    """
    try:
        person_id = None
        if cast_id.isdigit():
            try:
                person_id = int(cast_id)
            except ValueError:
                person_id = None
        if person_id is None and not name:
            return {"total_count": 0, "results": []}

        projection = {
            "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1,
            "release_year": 1, "poster": 1, "backdrop": 1, "description": 1,
            "media_type": 1, "languages": 1,
        }
        combined: List[dict] = []
        for cluster in db.clusters:
            for kind, col in (("movie", cluster.movie), ("tv", cluster.tv)):
                try:
                    if person_id is not None:
                        match = {"cast.id": person_id}
                    else:
                        match = {"cast.name": {"$regex": f"^{name}$", "$options": "i"}}
                    cur = col.find(match, projection).limit(200)
                    for d in await cur.to_list(200):
                        d["type"] = kind
                        combined.append(db._convert_object_id(dict(d)))
                except Exception as e:
                    LOGGER.error(f"Cast media fan-out failed on cluster {cluster.index}/{kind}: {e}")

        # de-dup by (type, tmdb_id) — a person entry may exist in both id and name forms
        seen = set()
        unique = []
        for d in combined:
            key = (d.get("type"), d.get("tmdb_id"))
            if key not in seen:
                seen.add(key)
                unique.append(d)
        unique.sort(key=lambda x: (x.get("rating") or 0), reverse=True)

        skip = (page - 1) * page_size
        return {"total_count": len(unique), "results": unique[skip:skip + page_size]}
    except Exception as e:
        LOGGER.error(f"Error fetching cast media for {cast_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch cast media")



@app.get("/api/search/", response_model=dict)
@cache_response(ttl=CacheConfig.SEARCH_CACHE_TTL, key_prefix="search")
async def search_documents_endpoint(
    query: str = Query(..., description="Search query string"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=10, ge=1, description="Number of documents per page")
):
    """
    FastAPI endpoint to search documents by title across TV and Movie collections,
    with pagination and total count.

    :param query: The search query string.
    :param page: The page number to return.
    :param page_size: The number of documents per page.
    :return: A dictionary containing the total count and a list of search results.
    """
    try:
        search_results = await db.search_documents(query=query, page=page, page_size=page_size)
        return search_results
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/track-view")
async def track_view_endpoint(
    tmdb_id: int = Query(..., description="TMDB ID of the media"),
    media_type: str = Query(..., pattern="^(movie|tv)$", description="Media type: movie or tv")
):
    """
    Track a view for a movie or TV show. Increments the daily view counter.
    """
    try:
        await db.track_view(tmdb_id=tmdb_id, media_type=media_type)
        return {"success": True, "message": "View tracked"}
    except Exception as e:
        LOGGER.error(f"Error tracking view: {e}")
        raise HTTPException(status_code=500, detail="Failed to track view")


@app.get("/api/trending/today", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="trending")
async def get_trending_today_endpoint(
    limit: int = Query(default=10, ge=1, le=50, description="Number of trending items to return")
):
    """
    Get the top trending movies and TV shows for today based on view counts.
    Cached for 1 minute to reduce database load.
    """
    try:
        trending = await db.get_trending_today(limit=limit)
        return trending
    except Exception as e:
        LOGGER.error(f"Error fetching trending: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch trending data")


@app.get("/api/trending/week", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="trending_week")
async def get_trending_week_endpoint(
    limit: int = Query(default=10, ge=1, le=50, description="Number of trending items to return")
):
    """
    Get the top movies and TV shows over the last 7 days based on view counts.
    Cached for 1 minute to reduce database load.
    """
    try:
        trending = await db.get_trending_week(limit=limit)
        return trending
    except Exception as e:
        LOGGER.error(f"Error fetching weekly trending: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch weekly trending data")


@app.get("/api/most-viewed", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="most_viewed")
async def get_most_viewed_endpoint(
    limit: int = Query(default=10, ge=1, le=50, description="Number of most viewed items to return")
):
    """
    Get the top movies and TV shows by all-time view count.
    Cached for 5 minutes to reduce database load.
    """
    try:
        most_viewed = await db.get_most_viewed(limit=limit)
        return most_viewed
    except Exception as e:
        LOGGER.error(f"Error fetching most viewed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch most viewed data")


@app.get("/api/anime", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="anime")
async def get_anime_endpoint(
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=20, ge=1, description="Number of anime per page")
):
    """
    Get anime movies and TV shows (Animation/Anime genre).
    """
    try:
        anime = await db.get_anime(limit=page_size, page=page)
        return anime
    except Exception as e:
        LOGGER.error(f"Error fetching anime: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch anime data")


@app.get("/api/k-drama", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="kdrama")
async def get_kdrama_endpoint(
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=20, ge=1, description="Number of K-Drama per page")
):
    """
    Get K-Drama TV shows (Korean language + Drama genre).
    """
    try:
        kdrama = await db.get_kdrama(limit=page_size, page=page)
        return kdrama
    except Exception as e:
        LOGGER.error(f"Error fetching K-Drama: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch K-Drama data")


@app.get("/api/genre/{genre}", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="genre")
async def get_media_by_genre_endpoint(
    genre: str,
    media_type: str = Query(default="all", pattern="^(all|movie|tv)$", description="Media type filter"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=20, ge=1, description="Number of items per page")
):
    """
    Get movies and/or TV shows by specific genre.
    """
    try:
        media = await db.get_media_by_genre(genre=genre, media_type=media_type, limit=page_size, page=page)
        return media
    except Exception as e:
        LOGGER.error(f"Error fetching media by genre '{genre}': {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch {genre} media data")


def _slim_new_episode_show(show: dict) -> dict:
    latest = None
    for season in show.get("seasons") or []:
        season_number = season.get("season_number") or 0
        for episode in season.get("episodes") or []:
            episode_number = episode.get("episode_number") or 0
            if latest is None or (season_number, episode_number) > latest[0]:
                sources = episode.get("sources") or []
                latest = (
                    (season_number, episode_number),
                    {
                        "season_number": season_number,
                        "episode_number": episode_number,
                        "title": episode.get("title"),
                        "episode_backdrop": episode.get("episode_backdrop"),
                        "quality": sources[0].get("quality") if sources else None,
                    },
                )

    return {
        "tmdb_id": show.get("tmdb_id"),
        "title": show.get("title"),
        "poster": show.get("poster"),
        "backdrop": show.get("backdrop"),
        "rating": show.get("rating"),
        "genres": show.get("genres", []),
        "release_year": show.get("release_year"),
        "total_seasons": show.get("total_seasons"),
        "total_episodes": show.get("total_episodes"),
        "updated_on": show.get("updated_on"),
        "latest_episode": latest[1] if latest else None,
    }


@app.get("/api/new-episodes", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="new_episodes")
async def get_new_episodes_endpoint(
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=20, ge=1, description="Number of episodes per page")
):
    """
    Recently updated TV shows with their latest episode (card-friendly payload).
    """
    try:
        new_episodes = await db.get_new_episodes(limit=page_size, page=page)
        return {
            "total_count": new_episodes.get("total_count", 0),
            "tv_shows": [_slim_new_episode_show(show) for show in new_episodes.get("tv_shows", [])],
        }
    except Exception as e:
        LOGGER.error(f"Error fetching new episodes: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch new episodes data")


@app.get("/api/media-info/{file_id}")
async def get_media_info(
    file_id: str,
    name: str = Query(..., description="Filename of the video")
):
    """
    Get audio and subtitle track information for a video file.
    Accepts only a signed stream token; legacy file IDs are never echoed back.
    """
    try:
        legacy_file_id = resolve_stream_id(file_id, name)
        await decode_string(legacy_file_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        # First check if we have cached data
        media_info = await db.get_media_with_tracks(legacy_file_id)
        
        if media_info and (media_info.get("audio_tracks") is not None or media_info.get("subtitle_tracks") is not None):
            return {
                "file_id": legacy_file_id,
                "filename": media_info.get("file_name", name),
                "media_type": media_info.get("media_type"),
                "tmdb_id": media_info.get("tmdb_id"),
                "audio_tracks": media_info.get("audio_tracks", []),
                "subtitle_tracks": media_info.get("subtitle_tracks", []),
                "cached": True
            }
        
        # No cached data, probe the file through a short-lived self token
        internal_token = mint_stream_token(legacy_file_id, name)
        file_url = f"{internal_base_url()}/dl/{internal_token}/{urllib.parse.quote(name)}"
        
        tracks = await probe_media_tracks(file_url, headers=outbound_headers())
        
        # If we found the media in DB and have tracks, cache them
        if media_info:
            if tracks["audio_tracks"]:
                if media_info["media_type"] == "movie":
                    await db.update_movie_audio_tracks(
                        media_info["tmdb_id"],
                        media_info["quality"],
                        tracks["audio_tracks"]
                    )
                elif media_info["media_type"] == "tv":
                    await db.update_tv_episode_audio_tracks(
                        media_info["tmdb_id"],
                        media_info["season_number"],
                        media_info["episode_number"],
                        media_info["quality"],
                        tracks["audio_tracks"]
                    )
            
            if tracks["subtitle_tracks"]:
                if media_info["media_type"] == "movie":
                    await db.update_movie_subtitle_tracks(
                        media_info["tmdb_id"],
                        media_info["quality"],
                        tracks["subtitle_tracks"]
                    )
                elif media_info["media_type"] == "tv":
                    await db.update_tv_episode_subtitle_tracks(
                        media_info["tmdb_id"],
                        media_info["season_number"],
                        media_info["episode_number"],
                        media_info["quality"],
                        tracks["subtitle_tracks"]
                    )
        
        # Get external subtitles from media_info
        external_subtitles = []
        if media_info:
            external_subtitles = media_info.get("external_subtitles", [])
        
        return {
            "file_id": legacy_file_id,
            "filename": name,
            "audio_tracks": tracks["audio_tracks"],
            "subtitle_tracks": tracks["subtitle_tracks"],
            "external_subtitles": external_subtitles,
            "cached": False
        }
        
    except Exception as e:
        LOGGER.error(f"Error getting media info for {legacy_file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get media info")


@app.get("/api/subtitle/{file_id}")
async def get_subtitle_file(
    file_id: str,
    name: str = Query(..., description="Filename of the subtitle file")
):
    """
    Serve external subtitle file (SRT, ASS, SSA) with proper content-type.
    Returns the subtitle file content converted to WebVTT if needed.
    """
    try:
        legacy_file_id = resolve_stream_id(file_id, name)
        decoded = await decode_string(legacy_file_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        chat_id = f"-100{decoded['chat_id']}"
        
        # Get file from Telegram
        index, tg_connect, file_props = await _resolve_source(int(chat_id), int(decoded['msg_id']))
        
        if file_props.unique_id[:6] != decoded['hash']:
            raise InvalidHash
        
        # Stream subtitle file
        chunk_size = 64 * 1024  # Smaller chunks for text files
        body = tg_connect.yield_file(
            file_props, index, 0, 0, file_props.file_size % chunk_size or chunk_size,
            math.ceil(file_props.file_size / chunk_size), chunk_size
        )
        
        # Determine content type based on extension
        safe_name = sanitize_filename(name, "subtitle")
        ext = safe_name.lower().split('.')[-1] if '.' in safe_name else 'srt'
        mime_types = {
            'srt': 'text/srt',
            'ass': 'text/x-ass',
            'ssa': 'text/x-ssa',
            'vtt': 'text/vtt',
            'webvtt': 'text/vtt'
        }
        content_type = mime_types.get(ext, 'text/plain')
        
        return StreamingResponse(
            content=body,
            media_type=content_type,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": content_disposition("inline", safe_name),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            }
        )
        
    except Exception as e:
        LOGGER.error(f"Error serving subtitle file: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve subtitle")


# search popup = http://127.0.0.1:8000/api/search/?query=the%20boys&page=1&page_size=10
# search tab = http://127.0.0.1:8000/api/search/?query=the%20boys&page=1&page_size=40


@app.get("/admin/video-cache-stats")
async def get_video_cache_stats(request: Request):
    """
    Admin endpoint to monitor video chunk cache statistics.
    Shows hot start cache, LRU cache, and active streams.
    """
    verify_admin_auth(request)
    try:
        stats = await video_chunk_cache.get_stats()
        return {
            "success": True,
            "cache_stats": stats
        }
    except Exception as e:
        LOGGER.error(f"Error getting video cache stats: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/admin/clear-video-cache")
async def clear_video_cache(request: Request):
    """
    Admin endpoint to clear all video chunk caches.
    Use when experiencing cache issues or for testing.
    """
    verify_admin_auth(request)
    try:
        await video_chunk_cache.clear()
        audit(request, "cache.clear_video", target="cache:video", detail={"target": "video"})
        return {
            "success": True,
            "message": "Video chunk cache cleared successfully"
        }
    except Exception as e:
        LOGGER.error(f"Error clearing video cache: {e}")
        audit(request, "cache.clear_video", target="cache:video",
              detail=str(e)[:200], result=f"error:{e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/admin/queue-health")
async def queue_health(request: Request):
    """
    Monitor queue health and performance.
    Returns queue size, workers, stats, estimated time, TMDB cache, and status.
    """
    verify_admin_auth(request)
    try:
        # Import queue stats from start.py (circular import handled at runtime)
        from Backend.wzgram_pkg.plugins.start import file_queue, queue_stats, QueueConfig
        from Backend.helper.pyro import get_tmdb_cache_stats

        pending = file_queue.qsize()

        # Effective values — a live override must be reflected here, not the
        # raw env default the process started with.
        file_delay = int(runtime_config.get("file_queue_delay"))
        batch_size = max(1, int(runtime_config.get("batch_size")))
        batch_gap = int(runtime_config.get("batch_gap_seconds"))
        workers = int(runtime_config.get("queue_workers"))

        # Calculate estimated time
        if pending <= 0:
            est_time = "0m 0s"
        else:
            est_time_per_file = file_delay + 10  # file delay + 10s processing
            est_batch_gap = (pending // batch_size) * batch_gap
            est_total_seconds = (pending * est_time_per_file) + est_batch_gap
            est_minutes = est_total_seconds // 60
            est_seconds = est_total_seconds % 60
            est_time = f"{est_minutes}m {est_seconds}s"

        # Get TMDB cache stats
        tmdb_stats = get_tmdb_cache_stats()

        # Determine status
        status = "healthy" if pending < 1000 else "backlogged"

        return {
            "queue_size": pending,
            "workers": workers,
            "stats": queue_stats,
            "estimated_time_minutes": est_time,
            "tmdb_cache_size": tmdb_stats["cache_size"],
            "tmdb_cache_hit_rate": tmdb_stats["hit_rate"],
            "status": status,
            "settings": {
                "file_delay": file_delay,
                "batch_size": batch_size,
                "batch_gap": batch_gap,
                "max_retry_count": QueueConfig.MAX_RETRY_COUNT,
                "rate_limit": f"{QueueConfig.MAX_MESSAGES_PER_MINUTE}/min"
            }
        }
    except ImportError as e:
        # If start.py hasn't been loaded yet (circular import)
        LOGGER.warning(f"Queue health check failed (start.py not loaded): {e}")
        return {
            "error": "Queue system not yet initialized",
            "status": "initializing"
        }
    except Exception as e:
        LOGGER.error(f"Error getting queue health: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get queue health: {str(e)}")


@app.get('/dl/{id}/{name}')
async def stream_handler(
    request: Request,
    id: str,
    name: str,
    audio: Optional[int] = Query(None, description="Audio track index (0-based)"),
    subtitle: Optional[int] = Query(None, description="Subtitle track index (-1 = off, 0-based)"),
):
    try:
        legacy_id = resolve_stream_id(id, name)
        decoded_data = await decode_string(legacy_id)
        if (
            decoded_data.get('chat_id') is None
            or not decoded_data.get('msg_id')
            or not decoded_data.get('hash')
        ):
            raise StreamTokenError("incomplete reference")
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")

    # If audio or subtitle track switching is requested, use ffmpeg remux
    if audio is not None or subtitle is not None:
        audio_idx = audio if audio is not None else 0
        subtitle_idx = subtitle if subtitle is not None else -1
        remuxed = await media_streamer_with_tracks(
            request, legacy_id, name, audio_idx, subtitle_idx,
            decoded_data['chat_id'], decoded_data['msg_id'], decoded_data['hash']
        )
        if remuxed is not None:
            return remuxed
        # Fallback to direct stream if ffmpeg fails

    chat_id = f"-100{decoded_data['chat_id']}"
    return await media_streamer(request, int(chat_id), int(decoded_data['msg_id']), decoded_data['hash'])



    


async def media_streamer(request: Request, chat_id: int, id: int, secure_hash: str):
    range_header = request.headers.get("Range")
    index, tg_connect, file_id = await _resolve_source(chat_id, id)
    if Telegram.MULTI_CLIENT:
        LOGGER.debug(f"Client {index} is now serving {request.client.host}")
    if file_id.unique_id[:6] != secure_hash:
        LOGGER.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash
    file_size = file_id.file_size
    if file_size <= 0:
        return _range_not_satisfiable(0)
    byte_range = _parse_byte_range(range_header, file_size)
    if byte_range is None:
        return _range_not_satisfiable(file_size)
    from_bytes, until_bytes = byte_range

    chunk_size = CacheConfig.VIDEO_CACHE_CHUNK_SIZE
    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = (until_bytes // chunk_size) - (offset // chunk_size) + 1
    
    # Generate stream ID for cache tracking
    stream_id = f"{chat_id}:{id}:{range_header or 'full'}"
    
    # Use cached streaming if enabled
    if CacheConfig.ENABLE_VIDEO_CACHE:
        body = tg_connect.yield_file_with_cache(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size,
            stream_id=stream_id, chat_id=chat_id, message_id=id
        )
    else:
        body = tg_connect.yield_file(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
        )
    mime_type = file_id.mime_type
    file_name = file_id.file_name
    disposition = "inline"

    if mime_type:
        if not file_name:
            try:
                file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
            except (IndexError, AttributeError):
                file_name = f"{secrets.token_hex(2)}.unknown"
    else:
        if file_name:
            mime_type = mimetypes.guess_type(file_name)[0]
        else:
            mime_type = "application/octet-stream"
            file_name = f"{secrets.token_hex(2)}.unknown"

    # async def file_chunk_generator():
    #     async for chunk in tg_connect.yield_file(
    #         file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    #     ):
    #         yield chunk
    headers = {
        "Content-Type": mime_type,
        "Content-Disposition": content_disposition(disposition, file_name),
        "Accept-Ranges": "bytes",
        "Content-Length": str(req_length),
    }
    status_code = 200
    if range_header:
        status_code = 206
        headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"

    LOGGER.info(f"{mime_type}, {file_name}, {disposition}")
    return StreamingResponse(
        status_code=status_code,
        content=body,
        headers=headers,
    )


async def _feed_remux(process, part_path: str, final_path: str, cache_key: str,
                      queue: asyncio.Queue, client_gone: asyncio.Event,
                      done_event: asyncio.Event):
    """Drain ffmpeg into the disk cache and, while attached, into the client queue.

    If the viewer disconnects the cache still finishes, so the next request
    for this track combination is served from disk with range support.
    """
    stderr_buf = bytearray()

    async def drain_stderr():
        try:
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    break
                if len(stderr_buf) < 8192:
                    stderr_buf.extend(chunk)
        except Exception:
            pass

    stderr_task = asyncio.create_task(drain_stderr())
    fh = open(part_path, "wb")
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(1024 * 1024), timeout=REMUX_STALL_TIMEOUT)
            except asyncio.TimeoutError:
                LOGGER.error(f"FFmpeg remux stalled for {REMUX_STALL_TIMEOUT}s; killing process")
                process.kill()
                break
            if not chunk:
                break
            try:
                fh.write(chunk)
            except OSError as e:
                LOGGER.warning(f"Failed writing remux cache chunk: {e}")
            if not client_gone.is_set():
                try:
                    await asyncio.wait_for(queue.put(chunk), timeout=15)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    client_gone.set()
    except Exception as e:
        LOGGER.error(f"Remux stream error: {e}")
    finally:
        try:
            fh.close()
        except OSError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=60)
        except asyncio.TimeoutError:
            process.kill()
            try:
                await process.wait()
            except Exception:
                pass
        try:
            await asyncio.wait_for(stderr_task, timeout=5)
        except Exception:
            pass

        ok = process.returncode == 0 and _remux_cached_ok(part_path)
        if ok:
            try:
                os.replace(part_path, final_path)
                LOGGER.info(f"Remux cached: {final_path} ({os.path.getsize(final_path)} bytes)")
            except OSError as e:
                LOGGER.warning(f"Failed to publish remux cache {final_path}: {e}")
                ok = False
        if not ok:
            err = bytes(stderr_buf).decode("utf-8", errors="ignore")[-500:]
            LOGGER.error(f"FFmpeg remux failed (rc={process.returncode}): {err}")
            try:
                os.remove(part_path)
            except OSError:
                pass
        _remux_builds.pop(cache_key, None)
        done_event.set()
        # Wake the attached viewer; with no viewer nothing consumes the queue,
        # and a slow viewer may need several attempts while it drains.
        while not client_gone.is_set():
            try:
                await asyncio.wait_for(queue.put(None), timeout=30)
                break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break


async def _queue_stream(queue: asyncio.Queue, client_gone: asyncio.Event):
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        client_gone.set()


async def media_streamer_with_tracks(
    request: Request,
    file_id: str,
    file_name: str,
    audio_idx: int,
    subtitle_idx: int,
    chat_id_raw: str,
    msg_id: int,
    secure_hash: str,
):
    """
    Remux video with a specific audio track and optional subtitle using ffmpeg.

    The first viewer is streamed a fragmented MP4 immediately while the same
    output is written to a disk cache. Once complete, every later request
    (and seeks) is served from disk with HTTP range support. The cache is
    bounded by REMUX_CACHE_SIZE_MB / REMUX_CACHE_TTL_HOURS.
    """
    # Check if ffmpeg is installed
    if not shutil.which("ffmpeg"):
        LOGGER.warning("ffmpeg not found on system, falling back to direct stream")
        return None

    final_path, part_path = _remux_paths(file_id, audio_idx, subtitle_idx)
    cache_key = os.path.basename(final_path)
    _sweep_remux_cache()

    # Fast path: completed remux on disk (seekable)
    if _remux_cached_ok(final_path):
        LOGGER.info(f"Remux cache hit: {cache_key}")
        return await serve_local_file(request, final_path, file_name)

    # Someone is already building this combination — wait for it, then serve
    pending = _remux_builds.get(cache_key)
    if pending is not None:
        LOGGER.info(f"Waiting for in-progress remux: {cache_key}")
        try:
            await asyncio.wait_for(asyncio.shield(pending.wait()), timeout=REMUX_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            LOGGER.warning(f"Timed out waiting for remux {cache_key}")
            return None
        if _remux_cached_ok(final_path):
            return await serve_local_file(request, final_path, file_name)
        return None

    # Validate the source message before spawning ffmpeg
    try:
        _, _, props = await _resolve_source(int(f"-100{chat_id_raw}"), int(msg_id))
    except Exception as e:
        LOGGER.warning(f"Remux source lookup failed: {e}")
        return None
    if props.unique_id[:6] != secure_hash:
        raise InvalidHash

    done_event = asyncio.Event()
    _remux_builds[cache_key] = done_event
    registered = True

    def _abort() -> None:
        nonlocal registered
        if registered:
            registered = False
            _remux_builds.pop(cache_key, None)
            done_event.set()

    # Build a fresh short-lived self token instead of reusing the caller's.
    internal_token = mint_stream_token(file_id, file_name)
    source_url = f"{internal_base_url()}/dl/{internal_token}/{urllib.parse.quote(file_name)}"
    edge_headers = outbound_headers()
    header_arg = "".join(f"{key}: {value}\r\n" for key, value in edge_headers.items())
    LOGGER.info(f"FFmpeg remux: audio={audio_idx}, subtitle={subtitle_idx}")

    # Pre-flight test: check if ffmpeg can process the source
    try:
        test_cmd = ["ffmpeg", "-v", "error"]
        if header_arg:
            test_cmd.extend(["-headers", header_arg])
        test_cmd.extend([
            "-i", source_url,
            "-t", "1",
            "-f", "null",
            "-",
        ])
        test_process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *test_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=10,
        )
        _, stderr = await asyncio.wait_for(test_process.communicate(), timeout=10)
        if test_process.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore")[:200] if stderr else "unknown"
            LOGGER.error(f"FFmpeg pre-flight failed: {err}")
            _abort()
            return None
    except asyncio.TimeoutError:
        LOGGER.warning("FFmpeg pre-flight timeout, falling back to direct stream")
        _abort()
        return None
    except asyncio.CancelledError:
        _abort()
        raise
    except Exception as e:
        LOGGER.error(f"FFmpeg pre-flight error: {e}")
        _abort()
        return None

    # Build ffmpeg command (fragmented MP4 so playback starts immediately)
    cmd = ["ffmpeg", "-v", "error"]
    if header_arg:
        cmd.extend(["-headers", header_arg])
    cmd.extend([
        "-i", source_url,
        "-map", "0:v:0",
        "-map", f"0:a:{audio_idx}",
    ])

    # Map subtitle if requested
    if subtitle_idx >= 0:
        cmd.extend(["-map", f"0:s:{subtitle_idx}"])
        cmd.extend(["-c:s", "mov_text"])

    cmd.extend([
        "-c:v", "copy",
        "-c:a", "copy",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-fflags", "+genpts",
        "-f", "mp4",
        "-",
    ])

    LOGGER.info(f"FFmpeg command: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except asyncio.CancelledError:
        _abort()
        raise
    except Exception as e:
        LOGGER.error(f"Failed to start ffmpeg: {e}")
        _abort()
        return None

    registered = False
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    client_gone = asyncio.Event()
    asyncio.create_task(
        _feed_remux(process, part_path, final_path, cache_key, queue, client_gone, done_event)
    )

    return StreamingResponse(
        content=_queue_stream(queue, client_gone),
        media_type="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Content-Disposition": content_disposition("inline", file_name),
            "Cache-Control": "no-cache",
        },
    )
