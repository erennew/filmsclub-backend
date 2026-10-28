import asyncio
import hashlib
import hmac
import shutil
import os
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

from Backend.logger import LOGGER
from Backend.config import Telegram, Cache as CacheConfig, QueueConfig
from Backend.fastapi.auth import (
    COOKIE_NAME,
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
from wzgram.enums import ChatMemberStatus
from Backend.wzgram_pkg import StreamBot, work_loads, multi_clients
from Backend.helper.exceptions import InvalidHash
from Backend.helper.custom_dl import ByteStreamer
from fastapi.middleware.cors import CORSMiddleware
from Backend.helper.pyro import get_readable_time
from Backend.helper.media_tracks import probe_media_tracks
from Backend.helper.cache import get_all_cache_stats, clear_all_caches, video_chunk_cache
from Backend.helper.redis_cache import redis_cache, cache_response, invalidate_cache_pattern
from Backend import StartTime, __version__, db

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


app = FastAPI()
class_cache = {}

# =============================================================================
# REMUX DISK CACHE — ffmpeg output for switched audio/subtitle tracks
# =============================================================================
REMUX_STALL_TIMEOUT = 120        # seconds without ffmpeg output before abort
REMUX_WAIT_TIMEOUT = 1800        # how long a later viewer waits for a build

# cache key -> event set when the build finishes (success or failure)
_remux_builds: Dict[str, asyncio.Event] = {}
_remux_last_sweep = 0.0


def _pick_client():
    """Return (index, client); tolerate an empty workload map."""
    if not multi_clients:
        raise HTTPException(status_code=503, detail="No Telegram clients available")
    if work_loads:
        index = min(work_loads, key=work_loads.get)
        client = multi_clients.get(index)
        if client is not None:
            return index, client
    index = next(iter(multi_clients))
    return index, multi_clients[index]


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
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
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

# ── CORS ─────────────────────────────────────────────────────────────────────
# Allow specific origins for production
_RATE_RULES = (
    ("/dl/", 240, 60),
    ("/api/media-info/", 120, 60),
    ("/api/subtitle/", 120, 60),
)
_rate_windows = {}


@app.middleware("http")
async def stream_rate_limit(request: Request, call_next):
    for prefix, limit, window in _RATE_RULES:
        if request.url.path.startswith(prefix):
            key = (prefix, client_ip(request))
            now = time()
            start, count = _rate_windows.get(key, (now, 0))
            if now - start >= window:
                start, count = now, 0
            count += 1
            _rate_windows[key] = (start, count)
            if len(_rate_windows) > 20000:
                _rate_windows.clear()
            if count > limit:
                return JSONResponse({"detail": "Too many requests"}, status_code=429)
            break
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=Telegram.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
)


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
    return templates.TemplateResponse(request=request, name="admin.html")


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


@app.post("/admin/api/logout")
async def admin_logout(response: Response):
    clear_session_cookie(response)
    return {"authenticated": False}


@app.get("/admin/api/session")
async def admin_session(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    return {"authenticated": bool(token and read_session(token))}


# Admin API Endpoints
@app.get("/admin/api/media")
async def admin_list_media(
    request: Request,
    media_type: str = Query("movie"),
    query: str = Query(""),
    page: int = Query(1),
    page_size: int = Query(30)
):
    """List media with search and pagination"""
    verify_admin_auth(request)
    
    try:
        # Query database for media
        skip = (page - 1) * page_size
        
        if query:
            # Search by title
            cursor = db.media.find({
                "title": {"$regex": query, "$options": "i"},
                "type": media_type
            })
        else:
            # Get all media of type
            cursor = db.media.find({"type": media_type})
        
        total_count = await db.media.count_documents(
            {"title": {"$regex": query, "$options": "i"}, "type": media_type} if query else {"type": media_type}
        )
        
        items = await cursor.skip(skip).limit(page_size).to_list(length=page_size)
        
        # Format items
        formatted_items = []
        for item in items:
            formatted_items.append({
                "db_index": item.get("db_index", 1),
                "tmdb_id": item.get("tmdb_id", ""),
                "title": item.get("title", ""),
                "release_year": item.get("release_year", ""),
                "poster": item.get("poster", ""),
                "backdrop": item.get("backdrop", ""),
                "languages": item.get("languages", []),
                "file_count": len(item.get("telegram", []) or []),
                "subtitle_count": sum(len(q.get("subtitle_tracks") or []) for q in (item.get("telegram", []) or []))
            })
        
        return {
            "items": formatted_items,
            "total_count": total_count,
            "page": page,
            "page_size": page_size
        }
    except Exception as e:
        LOGGER.error(f"Error listing media: {e}")
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
            update_data["languages"] = [lang.strip() for lang in payload["languages"].split(",")]
        if "genres" in payload:
            update_data["genres"] = [genre.strip() for genre in payload["genres"].split(",")]
        if "description" in payload:
            update_data["description"] = payload["description"]
        if "poster" in payload:
            update_data["poster"] = payload["poster"]
        if "backdrop" in payload:
            update_data["backdrop"] = payload["backdrop"]
        if "cast" in payload:
            # Parse cast from format: Name | Role | Image URL | TMDB ID
            cast_list = []
            for line in payload["cast"].strip().split("\n"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 1:
                    cast_member = {"name": parts[0]}
                    if len(parts) >= 2:
                        cast_member["character"] = parts[1]
                    if len(parts) >= 3:
                        cast_member["profile_path"] = parts[2]
                    if len(parts) >= 4:
                        cast_member["id"] = int(parts[3])
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
            # Delete Telegram messages (decode chat/msg from encoded id)
            for quality in _quality_files(media):
                telegram_total += 1
                try:
                    from Backend.helper.encrypt import decode_string
                    decoded = await decode_string(quality.get("id"))
                    await StreamBot.delete_messages(int(f"-100{decoded['chat_id']}"), int(decoded["msg_id"]))
                    telegram_deleted += 1
                except Exception as e:
                    LOGGER.warning(f"Failed to delete Telegram message: {e}")
        
        # Delete from database
        await db.media.delete_one({
            "tmdb_id": tmdb_id,
            "type": media_type
        })
        
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
            {"tmdb_id": tmdb_id, "type": media_type},
            {"$set": update_fields},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="File not found")
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
                {"tmdb_id": tmdb_id, "type": media_type},
                {"$pull": {"seasons.$[s].episodes.$[e].telegram": {"id": file_id}}},
                array_filters=[
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                ],
            )
        else:
            result = await db.media.update_one(
                {"tmdb_id": tmdb_id, "type": media_type},
                {"$pull": {"telegram": {"id": file_id}}},
            )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="File not found")

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
            {"tmdb_id": tmdb_id, "type": media_type},
            {"$push": {push_path: subtitle_data}},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Target file not found")
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
            {"tmdb_id": tmdb_id, "type": media_type},
            {"$set": update_data},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Subtitle not found")
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
            {"tmdb_id": tmdb_id, "type": media_type},
            {"$pull": {pull_path: {"id": subtitle_id}}},
            array_filters=array_filters,
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Subtitle not found")

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


# Admin Command Endpoints
@app.post("/admin/api/commands/set")
async def admin_set_default_id(request: Request, payload: dict):
    """Set default ID for metadata fetching"""
    verify_admin_auth(request)
    
    try:
        default_id = payload.get("default_id")
        if default_id:
            Telegram.USE_DEFAULT_ID = default_id
            return {"set": True, "default_id": default_id}
        else:
            Telegram.USE_DEFAULT_ID = None
            return {"set": True, "default_id": None}
    except Exception as e:
        LOGGER.error(f"Error setting default ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/commands/rescan")
async def admin_rescan_channel(request: Request, payload: dict):
    """Trigger channel rescan"""
    verify_admin_auth(request)
    
    try:
        channel_index = payload.get("channel_index", 0)
        limit = payload.get("limit", 100)
        
        # Import the standalone rescan runner from start.py
        from Backend.wzgram_pkg.plugins.start import perform_rescan
        
        # Trigger rescan in background
        asyncio.create_task(perform_rescan(channel_index, limit))
        
        return {"started": True, "channel_index": channel_index, "limit": limit}
    except Exception as e:
        LOGGER.error(f"Error triggering rescan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        return await db.merge_duplicate_media(apply=apply)
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
        return await queue_message_links(links, preview=bool(payload.get("preview", False)))
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error queueing links: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        pending = int(counts.get("pending", 0) or 0)
        processing = int(counts.get("processing", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)
        retries = int(counts.get("total_retries", 0) or 0)
    else:
        paused = bool(getattr(queue_start, "is_queue_paused", False))
        channels = []
        pending = queue_start.file_queue.qsize()
        processing = 0
        failed = counters["failed"]
        retries = counters["retried"]

    return {
        "engine": engine,
        "paused": paused,
        "workers": max(1, QueueConfig.QUEUE_WORKERS),
        "floodwait_seconds": floodwait,
        "pending": pending,
        "processing": processing,
        "failed": failed,
        "retries": retries,
        "channels": channels,
        "counters": counters,
        "settings": {
            "file_delay": QueueConfig.FILE_QUEUE_DELAY,
            "batch_size": QueueConfig.BATCH_SIZE,
            "batch_gap": QueueConfig.BATCH_GAP_SECONDS,
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


@app.post("/admin/api/commands/clear-cache")
async def admin_clear_cache(request: Request):
    """Clear all caches"""
    verify_admin_auth(request)
    
    try:
        await clear_all_caches()
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
        
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting failed file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/failed-files")
async def admin_clear_failed_files(request: Request):
    """Clear all failed file logs"""
    verify_admin_auth(request)
    
    try:
        result = await db.clear_failed_files()
        return {"cleared": result}
    except Exception as e:
        LOGGER.error(f"Error clearing failed files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/failed-files/{id}/retry")
async def admin_retry_failed_file(request: Request, id: str):
    """Re-queue a failed file from its stored Telegram source message."""
    verify_admin_auth(request)
    try:
        failed_file = await db.get_failed_file(id)
        if not failed_file:
            raise HTTPException(status_code=404, detail="Failed file entry not found")

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

        queued = await parse_and_queue_file(
            message, bare, is_rescan=False, allow_probe=True, notify=False
        )
        if not queued:
            raise HTTPException(
                status_code=502,
                detail="Re-queue failed; the entry was kept. Check the bot logs and try again.",
            )
        await db.delete_failed_file(id)
        LOGGER.info(f"Retried failed file from Telegram: {failed_file.get('title')} ({bare}/{msg_id})")
        return {"retry": True, "queued": True, "message": "Re-queued from Telegram"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error retrying failed file: {e}")
        raise HTTPException(status_code=500, detail=str(e))






@app.get("/", response_model=Dict[str, Any])
async def get_bot_workloads():
    """
    Home route to list each bot's workload and total number of bots.
    """
    # Check database connection
    db_status = "connected" if db.db is not None else "disconnected"
    redis_status = await redis_cache.health_check() if redis_cache._available else {"status": "disabled"}

    bot_username = getattr(StreamBot, "username", None)
    if not bot_username:
        try:
            me = await StreamBot.get_me()
            bot_username = me.username
        except Exception:
            bot_username = None

    response = {
            "server_status": "running",
            "uptime": get_readable_time(time() - StartTime),
            "telegram_bot": "@" + bot_username if bot_username else "bot-not-started",
            "connected_bots": len(multi_clients),
            "database": db_status,
            "redis": redis_status.get("status", "unknown"),
            "loads": dict(
                ("bot" + str(c + 1), l)
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            ),
            "version": __version__,
        }
    return response



@app.get("/health")
async def health():
    """Lightweight liveness probe for Docker/Heroku."""
    return {"status": "ok", "version": __version__}


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


@app.get("/api/new-episodes", response_model=dict)
@cache_response(ttl=CacheConfig.TRENDING_CACHE_TTL, key_prefix="new_episodes")
async def get_new_episodes_endpoint(
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=20, ge=1, description="Number of episodes per page")
):
    """
    Get recently added TV episodes based on updated_on timestamp.
    """
    try:
        new_episodes = await db.get_new_episodes(limit=page_size, page=page)
        return new_episodes
    except Exception as e:
        LOGGER.error(f"Error fetching new episodes: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch new episodes data")


@app.get("/api/media-info/{file_id}")
@cache_response(ttl=CacheConfig.MEDIA_INFO_CACHE_TTL, key_prefix="media_info")
async def get_media_info(
    file_id: str,
    name: str = Query(..., description="Filename of the video")
):
    """
    Get audio and subtitle track information for a video file.
    Returns cached data if available, probes with ffprobe if needed.
    Cached for 24 hours since audio/subtitle tracks don't change.
    """
    try:
        # First check if we have cached data
        media_info = await db.get_media_with_tracks(file_id)
        
        if media_info and (media_info.get("audio_tracks") is not None or media_info.get("subtitle_tracks") is not None):
            return {
                "file_id": file_id,
                "filename": media_info.get("file_name", name),
                "media_type": media_info.get("media_type"),
                "tmdb_id": media_info.get("tmdb_id"),
                "audio_tracks": media_info.get("audio_tracks", []),
                "subtitle_tracks": media_info.get("subtitle_tracks", []),
                "cached": True
            }
        
        # No cached data, probe the file
        base_url = Telegram.BASE_URL
        file_url = f"{base_url}/dl/{file_id}/{urllib.parse.quote(name)}"
        
        tracks = await probe_media_tracks(file_url)
        
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
            "file_id": file_id,
            "filename": name,
            "audio_tracks": tracks["audio_tracks"],
            "subtitle_tracks": tracks["subtitle_tracks"],
            "external_subtitles": external_subtitles,
            "cached": False
        }
        
    except Exception as e:
        LOGGER.error(f"Error getting media info for {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get media info: {str(e)}")


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
        decoded = await decode_string(file_id)
        chat_id = f"-100{decoded['chat_id']}"
        
        # Get file from Telegram
        index, client = _pick_client()
        
        tg_connect = ByteStreamer(client)
        file_props = await tg_connect.get_file_properties(int(chat_id), int(decoded['msg_id']))
        
        if file_props.unique_id[:6] != decoded['hash']:
            raise InvalidHash
        
        # Stream subtitle file
        chunk_size = 64 * 1024  # Smaller chunks for text files
        body = tg_connect.yield_file(
            file_props, index, 0, 0, file_props.file_size % chunk_size or chunk_size,
            math.ceil(file_props.file_size / chunk_size), chunk_size
        )
        
        # Determine content type based on extension
        ext = name.lower().split('.')[-1] if '.' in name else 'srt'
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
                "Content-Disposition": f'inline; filename="{name}"',
                "Access-Control-Allow-Origin": "*"
            }
        )
        
    except Exception as e:
        LOGGER.error(f"Error serving subtitle file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to serve subtitle: {str(e)}")


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
        return {
            "success": True,
            "message": "Video chunk cache cleared successfully"
        }
    except Exception as e:
        LOGGER.error(f"Error clearing video cache: {e}")
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
        
        # Calculate estimated time
        if pending <= 0:
            est_time = "0m 0s"
        else:
            est_time_per_file = QueueConfig.FILE_QUEUE_DELAY + 10  # 8s delay + 10s processing
            est_batch_gap = (pending // QueueConfig.BATCH_SIZE) * QueueConfig.BATCH_GAP_SECONDS
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
            "workers": QueueConfig.QUEUE_WORKERS,
            "stats": queue_stats,
            "estimated_time_minutes": est_time,
            "tmdb_cache_size": tmdb_stats["cache_size"],
            "tmdb_cache_hit_rate": tmdb_stats["hit_rate"],
            "status": status,
            "settings": {
                "file_delay": QueueConfig.FILE_QUEUE_DELAY,
                "batch_size": QueueConfig.BATCH_SIZE,
                "batch_gap": QueueConfig.BATCH_GAP_SECONDS,
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
    decoded_data = await decode_string(id)
    if not decoded_data['msg_id'] or not decoded_data['hash']:
        raise HTTPException(status_code=400, detail="Missing id or hash")

    # If audio or subtitle track switching is requested, use ffmpeg remux
    if audio is not None or subtitle is not None:
        audio_idx = audio if audio is not None else 0
        subtitle_idx = subtitle if subtitle is not None else -1
        remuxed = await media_streamer_with_tracks(
            request, id, name, audio_idx, subtitle_idx,
            decoded_data['chat_id'], decoded_data['msg_id'], decoded_data['hash']
        )
        if remuxed is not None:
            return remuxed
        # Fallback to direct stream if ffmpeg fails

    chat_id = f"-100{decoded_data['chat_id']}"
    return await media_streamer(request, int(chat_id), int(decoded_data['msg_id']), decoded_data['hash'])



    


async def media_streamer(request: Request, chat_id: int, id: int, secure_hash: str):
    range_header = request.headers.get("Range")
    index, faster_client = _pick_client()
    if Telegram.MULTI_CLIENT:
        LOGGER.debug(f"Client {index} is now serving {request.client.host}")
    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
        LOGGER.debug(f"Using cached ByteStreamer object for client {index}")
    else:
        LOGGER.debug(f"Creating new ByteStreamer object for client {index}")
        tg_connect = ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect
    LOGGER.debug("before calling get_file_properties")
    file_id = await tg_connect.get_file_properties(chat_id=chat_id, message_id=id)
    LOGGER.debug("after calling get_file_properties")
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
        "Content-Disposition": f'{disposition}; filename="{file_name}"',
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
    index, client = _pick_client()
    tg_connect = class_cache.get(client)
    if tg_connect is None:
        tg_connect = ByteStreamer(client)
        class_cache[client] = tg_connect
    try:
        props = await tg_connect.get_file_properties(
            chat_id=int(f"-100{chat_id_raw}"), message_id=int(msg_id)
        )
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

    # Build source URL (self-referential without audio/subtitle params to avoid recursion)
    source_url = f"{Telegram.BASE_URL}/dl/{file_id}/{urllib.parse.quote(file_name)}"
    LOGGER.info(f"FFmpeg remux: audio={audio_idx}, subtitle={subtitle_idx}, source={source_url}")

    # Pre-flight test: check if ffmpeg can process the source
    try:
        test_cmd = [
            "ffmpeg",
            "-v", "error",
            "-i", source_url,
            "-t", "1",
            "-f", "null",
            "-",
        ]
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
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-i", source_url,
        "-map", "0:v:0",
        "-map", f"0:a:{audio_idx}",
    ]

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
            "Content-Disposition": f'inline; filename="{file_name}"',
            "Cache-Control": "no-cache",
        },
    )
