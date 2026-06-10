import asyncio
import shutil
import os
from time import time
from typing import Any, Dict, List, Optional, Union
from Backend.helper.encrypt import decode_string
from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
import urllib.parse

from fastapi.templating import Jinja2Templates



import mimetypes
import secrets
import math

from Backend.logger import LOGGER
from Backend.config import Telegram, Cache as CacheConfig
from pyrogram.enums import ChatMemberStatus
from Backend.pyrofork import StreamBot, work_loads, multi_clients
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

templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ---------------------------------------------------------------------------
# Admin collection helpers
# Media is stored in two collections ("movie" and "tv"); anime lives in "tv".
# The legacy `db_index` path segment is no longer used for lookups.
# ---------------------------------------------------------------------------
def _admin_collection(media_type: str):
    """Return the Mongo collection for an admin media_type ('movie' | 'tv' | 'anime')."""
    return db.movie_collection if media_type == "movie" else db.tv_collection


def _coerce_tmdb_id(tmdb_id):
    """tmdb_id is stored as an int for TMDB records; cast where possible."""
    try:
        return int(tmdb_id)
    except (TypeError, ValueError):
        return tmdb_id


def _admin_query(tmdb_id):
    """Build a lookup filter for a single media document by tmdb_id."""
    return {"tmdb_id": _coerce_tmdb_id(tmdb_id)}


def _all_qualities(media: dict) -> list:
    """Flatten all telegram quality entries for a movie or TV document."""
    if not media:
        return []
    if media.get("type") == "movie" or "telegram" in media:
        out = list(media.get("telegram", []) or [])
        if out:
            return out
    out = []
    for season in media.get("seasons", []) or []:
        for episode in season.get("episodes", []) or []:
            out.extend(episode.get("telegram", []) or [])
    return out


def _stringify_object_id(doc):
    """Convert a Mongo document's _id (and any nested ObjectId) to a string."""
    if isinstance(doc, dict) and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


# Admin Panel Routes
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    """Serve the admin panel HTML"""
    return templates.TemplateResponse("admin.html", {"request": request})


# Admin API Authentication
def verify_admin_auth(request: Request):
    """Verify admin authentication from headers"""
    username = request.headers.get("x-admin-username")
    password = request.headers.get("x-admin-password")
    
    if not username or not password:
        raise HTTPException(status_code=401, detail="Missing authentication headers")
    
    if username != Telegram.ADMIN_WEB_USERNAME or password != Telegram.ADMIN_WEB_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")
    
    return True


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
        skip = (page - 1) * page_size
        collection = _admin_collection(media_type)

        mongo_query: dict = {}
        if query:
            mongo_query["title"] = {"$regex": query, "$options": "i"}
        if media_type == "anime":
            mongo_query["genres"] = {"$in": ["Animation", "Anime", "anime", "animation"]}

        total_count = await collection.count_documents(mongo_query)
        cursor = collection.find(mongo_query).sort("release_year", -1).skip(skip).limit(page_size)
        items = await cursor.to_list(length=page_size)

        formatted_items = []
        for item in items:
            qualities = _all_qualities(item)
            formatted_items.append({
                "db_index": item.get("db_index", 1),
                "tmdb_id": item.get("tmdb_id", ""),
                "title": item.get("title", ""),
                "release_year": item.get("release_year") or item.get("year", ""),
                "poster": item.get("poster", ""),
                "backdrop": item.get("backdrop", ""),
                "languages": item.get("languages", []),
                "media_type": item.get("type", media_type),
                "file_count": len(qualities),
                "subtitle_count": sum(len(q.get("subtitles", [])) for q in qualities),
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
        media = await _admin_collection(media_type).find_one(_admin_query(tmdb_id))
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        return _stringify_object_id(media)
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
            update_data["year"] = payload["release_year"]
        if "rating" in payload:
            update_data["rate"] = payload["rating"]
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
        
        collection = _admin_collection(media_type)
        if update_data:
            await collection.update_one(_admin_query(tmdb_id), {"$set": update_data})
        
        updated_media = await collection.find_one(_admin_query(tmdb_id))
        
        return {"media": _stringify_object_id(updated_media), "updated": True}
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
        collection = _admin_collection(media_type)
        media = await collection.find_one(_admin_query(tmdb_id))
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        telegram_deleted = 0
        telegram_total = 0
        
        if delete_telegram:
            # Delete Telegram messages
            for quality in _all_qualities(media):
                telegram_total += 1
                try:
                    if "file_id" in quality:
                        await StreamBot.delete_messages(quality.get("chat_id"), quality.get("message_id"))
                        telegram_deleted += 1
                except Exception as e:
                    LOGGER.warning(f"Failed to delete Telegram message: {e}")
        
        # Delete from database
        await collection.delete_one(_admin_query(tmdb_id))
        
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
        collection = _admin_collection(media_type)
        media = await collection.find_one(_admin_query(tmdb_id))
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        # Build update query
        update_query = {}
        if media_type == "tv":
            # For TV series, navigate through seasons and episodes
            season_match = {"season_number": season_number}
            episode_match = {"episode_number": episode_number}
            file_match = {"telegram.$[elem].file_id": file_id}
            
            update_query = {
                "telegram.$[elem].quality": payload.get("quality"),
                "telegram.$[elem].size": payload.get("size"),
                "telegram.$[elem].name": payload.get("name")
            }
        else:
            # For movies, direct file update
            update_query = {
                "telegram.$[elem].quality": payload.get("quality"),
                "telegram.$[elem].size": payload.get("size"),
                "telegram.$[elem].name": payload.get("name")
            }
        
        # Update the file
        result = await collection.update_one(
            {**_admin_query(tmdb_id), "telegram.file_id": file_id},
            {"$set": update_query}
        )
        
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
        collection = _admin_collection(media_type)
        media = await collection.find_one(_admin_query(tmdb_id))
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        telegram_deleted = 0
        telegram_total = 0
        
        if delete_telegram:
            # Delete Telegram message for this file
            for quality in _all_qualities(media):
                if quality.get("file_id") == file_id:
                    telegram_total += 1
                    try:
                        await StreamBot.delete_messages(quality.get("chat_id"), quality.get("message_id"))
                        telegram_deleted += 1
                    except Exception as e:
                        LOGGER.warning(f"Failed to delete Telegram message: {e}")
        
        # Remove file from database
        await collection.update_one(
            _admin_query(tmdb_id),
            {"$pull": {"telegram": {"file_id": file_id}}}
        )
        
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
        subtitle_data = {
            "id": payload.get("id"),
            "name": payload.get("name"),
            "language": payload.get("language"),
            "format": payload.get("format", "srt"),
            "label": f"{payload.get('language')} • {payload.get('format', 'srt').upper()}"
        }
        
        # Add subtitle to the target file
        collection = _admin_collection(media_type)
        if payload.get("target_file_id") == "all":
            # Add to all files
            await collection.update_one(
                _admin_query(tmdb_id),
                {"$push": {"telegram.$[].subtitles": subtitle_data}}
            )
        else:
            # Add to specific file
            await collection.update_one(
                {**_admin_query(tmdb_id), "telegram.file_id": payload.get("target_file_id")},
                {"$push": {"telegram.$.subtitles": subtitle_data}}
            )
        
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
        update_data = {
            "telegram.$[elem].subtitles.$[sub].language": payload.get("language"),
            "telegram.$[elem].subtitles.$[sub].format": payload.get("format"),
            "telegram.$[elem].subtitles.$[sub].name": payload.get("name"),
            "telegram.$[elem].subtitles.$[sub].label": f"{payload.get('language')} • {payload.get('format', 'srt').upper()}"
        }
        
        await _admin_collection(media_type).update_one(
            {**_admin_query(tmdb_id), "telegram.subtitles.id": subtitle_id},
            {"$set": update_data}
        )
        
        return {"updated": True}
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
        
        collection = _admin_collection(media_type)
        if delete_telegram:
            # Delete Telegram message for subtitle
            media = await collection.find_one(_admin_query(tmdb_id))
            
            for quality in _all_qualities(media or {}):
                for sub in quality.get("subtitles", []):
                    if sub.get("id") == subtitle_id:
                        telegram_total += 1
                        try:
                            await StreamBot.delete_messages(quality.get("chat_id"), sub.get("message_id"))
                            telegram_deleted += 1
                        except Exception as e:
                            LOGGER.warning(f"Failed to delete Telegram subtitle: {e}")
        
        # Remove subtitle from database
        await collection.update_one(
            _admin_query(tmdb_id),
            {"$pull": {"telegram.$[].subtitles": {"id": subtitle_id}}}
        )
        
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
        
        # Import rescan function from start.py
        from Backend.pyrofork.plugins.start import rescan_channel
        
        # Trigger rescan in background
        asyncio.create_task(rescan_channel(None, channel_index, limit))
        
        return {"started": True, "channel_index": channel_index, "limit": limit}
    except Exception as e:
        LOGGER.error(f"Error triggering rescan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/api/analytics")
async def admin_get_analytics(request: Request):
    """Get system analytics"""
    verify_admin_auth(request)
    
    try:
        anime_genres = ["Animation", "Anime", "anime", "animation"]
        # Get media counts from the real movie / tv collections
        movie_count = await db.movie_collection.count_documents({})
        tv_count = await db.tv_collection.count_documents({})
        anime_count = (
            await db.movie_collection.count_documents({"genres": {"$in": anime_genres}})
            + await db.tv_collection.count_documents({"genres": {"$in": anime_genres}})
        )

        # Get total files + subtitles across both collections
        total_files = 0
        total_subtitles = 0
        for collection in (db.movie_collection, db.tv_collection):
            async for media in collection.find({}):
                for quality in _all_qualities(media):
                    total_files += 1
                    total_subtitles += len(quality.get("subtitles", []))

        # Failed file log count
        failed_count = await db.count_failed_files()

        # Get cache stats
        cache_stats = get_all_cache_stats()
        
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
            "failed_files": failed_count,
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


# ---------------------------------------------------------------------------
# Failed Files (failure log) endpoints
# ---------------------------------------------------------------------------
@app.get("/admin/api/failed-files")
async def admin_list_failed_files(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    reason: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
):
    """List failed/skipped ingestions (newest first), filterable by reason and date range."""
    verify_admin_auth(request)
    try:
        return await db.get_failed_files(
            page=page,
            page_size=page_size,
            reason_filter=reason or None,
            date_from=date_from or None,
            date_to=date_to or None,
        )
    except Exception as e:
        LOGGER.error(f"Error listing failed files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/failed-files/{file_id}")
async def admin_delete_failed_file(request: Request, file_id: str):
    """Delete a single failed-file log entry."""
    verify_admin_auth(request)
    try:
        deleted = await db.delete_failed_file(file_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Failed file entry not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error deleting failed file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/api/failed-files")
async def admin_clear_failed_files(request: Request):
    """Clear all failed-file log entries."""
    verify_admin_auth(request)
    try:
        removed = await db.clear_failed_files()
        return {"cleared": True, "removed": removed}
    except Exception as e:
        LOGGER.error(f"Error clearing failed files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/api/failed-files/{file_id}/retry")
async def admin_retry_failed_file(request: Request, file_id: str):
    """Re-queue a failed file for processing using its stored channel + msg_id."""
    verify_admin_auth(request)
    try:
        entry = await db.get_failed_file(file_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Failed file entry not found")

        channel = entry.get("channel")
        msg_id = entry.get("msg_id")
        if not channel or not msg_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot retry: no source channel/message recorded for this entry",
            )

        # Import the live parser lazily to avoid circular imports at module load.
        from Backend.pyrofork.plugins.start import parse_and_queue_file

        channel_str = str(channel).replace("-100", "")
        message = await StreamBot.get_messages(int(f"-100{channel_str}"), int(msg_id))
        if not message or message.empty:
            raise HTTPException(status_code=410, detail="Source Telegram message no longer exists")

        queued = await parse_and_queue_file(message, channel_str, is_rescan=True)
        if queued:
            await db.delete_failed_file(file_id)
        return {"requeued": bool(queued)}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Error retrying failed file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.get("/", response_model=Dict[str, Any])
async def get_bot_workloads():
    """
    Home route to list each bot's workload and total number of bots.
    """
    # Check database connection
    db_status = "connected" if db.db is not None else "disconnected"
    redis_status = await redis_cache.health_check() if redis_cache._available else {"status": "disabled"}
    
    response = {
            "server_status": "running",
            "uptime": get_readable_time(time() - StartTime),
            "telegram_bot": "@" + StreamBot.username,
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



@app.get("/is_member")
async def is_member(user_id: int, channel: int):
    try:
        member = await StreamBot.get_chat_member(channel, user_id)
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return {"is_member": True}
        else:
            return {"is_member": False}
    except Exception as e:
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
        "index.html", 
        {
            "request": request, 
            "id": tmdb_id, 
            "season": season_number, 
            "episode": episode_number
        }
    )



@app.get("/api/tvshows", response_model=dict)
async def get_sorted_tv_shows(
    sort_by: List[str] = Query(default=["rating:desc"], description="List of fields to sort by. Format: field:direction"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=10, ge=1, description="Number of TV shows per page")
):
    try:
        sort_params = [tuple(param.split(":")) for param in sort_by]
        sorted_tv_shows = await db.sort_tv_shows(sort_params, page, page_size)
        return sorted_tv_shows
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies", response_model=dict)
async def get_sorted_movies(
    sort_by: List[str] = Query(default=["rating:desc"], description="List of fields to sort by. Format: field:direction"),
    page: int = Query(default=1, ge=1, description="Page number to return"),
    page_size: int = Query(default=10, ge=1, description="Number of movies per page")
):
    try:
        sort_params = [tuple(param.split(":")) for param in sort_by]
        sorted_movies = await db.sort_movies(sort_params, page, page_size)
        return sorted_movies
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

#Homepage:------
# hero = http://localhost:8000/api/tvshows?sort_by=rating:desc&sort_by=release_year:desc&page=1&page_size=10
# latest movies = http://localhost:8000/api/movies?sort_by=updated_on:desc&page=1&page_size=20
# latest tvshows = http://localhost:8000/api/tvshows?sort_by=updated_on:desc&page=1&page_size=20

#Movies:----------
# latest movies = http://localhost:8000/api/movies?sort_by=updated_on:desc&page=1&page_size=40

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
        index = min(work_loads, key=work_loads.get)
        client = multi_clients[index]
        
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
async def get_video_cache_stats():
    """
    Admin endpoint to monitor video chunk cache statistics.
    Shows hot start cache, LRU cache, and active streams.
    """
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
async def clear_video_cache():
    """
    Admin endpoint to clear all video chunk caches.
    Use when experiencing cache issues or for testing.
    """
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
async def queue_health():
    """
    Monitor queue health and performance.
    Returns queue size, workers, stats, estimated time, TMDB cache, and status.
    """
    try:
        # Import queue stats from start.py (circular import handled at runtime)
        from Backend.pyrofork.plugins.start import file_queue, queue_stats, QueueConfig
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
    range_header = request.headers.get("Range", 0)
    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]
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
    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes != "" else file_size - 1
    else:
        from_bytes = 0
        until_bytes = file_size - 1
    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return StreamingResponse(
            content=(f"416: Range not satisfiable",),
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    
    # Generate stream ID for cache tracking
    stream_id = f"{id}:{range_header or 'full'}"
    
    # Use cached streaming if enabled
    if CacheConfig.ENABLE_VIDEO_CACHE:
        body = tg_connect.yield_file_with_cache(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size,
            stream_id=stream_id
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
    LOGGER.info(f"{mime_type}, {file_name}, {disposition}")
    return StreamingResponse(
        
        status_code=206 if range_header else 200,
        content=body,
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        },
    )


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
    Falls back to direct stream if ffmpeg is unavailable or fails.
    """
    # Check if ffmpeg is installed
    if not shutil.which("ffmpeg"):
        LOGGER.warning("ffmpeg not found on system, falling back to direct stream")
        return None

    # Build source URL (self-referential without audio/subtitle params to avoid recursion)
    base_url = Telegram.BASE_URL
    source_url = f"{base_url}/dl/{file_id}/{urllib.parse.quote(file_name)}"

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
            timeout=15,
        )
        _, stderr = await asyncio.wait_for(test_process.communicate(), timeout=15)
        if test_process.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore")[:200] if stderr else "unknown"
            LOGGER.error(f"FFmpeg pre-flight failed: {err}")
            return None
    except asyncio.TimeoutError:
        LOGGER.warning("FFmpeg pre-flight timeout, falling back to direct stream")
        return None
    except Exception as e:
        LOGGER.error(f"FFmpeg pre-flight error: {e}")
        return None

    # Build ffmpeg command
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
        "-movflags", "frag_keyframe+empty_moov+faststart",
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
    except Exception as e:
        LOGGER.error(f"Failed to start ffmpeg: {e}")
        return None

    async def stdout_generator():
        chunk_size = 1024 * 1024  # 1MB chunks
        try:
            while True:
                chunk = await process.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        except (ConnectionResetError, asyncio.CancelledError):
            LOGGER.info("Client disconnected during ffmpeg stream")
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except Exception:
                    pass

    # Log stderr asynchronously
    async def log_stderr():
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="ignore").strip()
                if line_str:
                    LOGGER.debug(f"FFmpeg stderr: {line_str}")
        except Exception:
            pass

    asyncio.create_task(log_stderr())

    return StreamingResponse(
        content=stdout_generator(),
        media_type="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Content-Disposition": f'inline; filename="{file_name}"',
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
