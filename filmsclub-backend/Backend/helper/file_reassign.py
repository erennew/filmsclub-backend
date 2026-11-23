"""Move an already-indexed Telegram file onto the correct movie / episode.

The automatic pipeline identifies a release from its filename. When that guess
is wrong, the file lands under the wrong title (or the wrong episode). Re-adding
it with ``/manualadd`` does not help: the duplicate guard correctly reports
"already in the library". This module *moves* the existing quality entry instead
of creating a second one, so the encoded stream id (and therefore every link the
site already handed out) keeps working.

Both the admin console (``POST /admin/api/files/reassign``) and the Telegram
``/reassign`` command call :func:`reassign_file`, so the two surfaces cannot
diverge. Deliberately free of FastAPI imports: errors are :class:`ReassignError`
carrying a user-facing message and a suggested HTTP status.

Safety rules
------------
* Attach first, detach second. A failed attach leaves the file exactly where it
  was; a failed detach leaves a harmless duplicate plus an explicit warning —
  never a lost file.
* Moving a file onto the title/episode it already occupies is a no-op (409).
* A same-quality file already on the target is not silently replaced; the admin
  must opt in with ``replace_existing_quality``.
* Empty source titles are removed only when ``remove_empty_title`` is set.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from Backend.helper.encrypt import decode_string, encode_string
from Backend.helper.manual_add import ManualAddError, build_manual_metadata_info


class ReassignError(Exception):
    """A validation or storage error an admin should see verbatim."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _late_db():
    """Resolve the live DB at call time (the handle is rebound on reconnect)."""
    import Backend

    return Backend.db


def iter_qualities(doc: Optional[dict]):
    """Yield ``(quality, season_number, episode_number)`` for movie or TV docs."""
    if not doc:
        return
    if doc.get("seasons") or doc.get("type") == "tv":
        for season in doc.get("seasons") or []:
            for episode in season.get("episodes") or []:
                for quality in episode.get("telegram") or []:
                    yield quality, season.get("season_number"), episode.get("episode_number")
    else:
        for quality in doc.get("telegram") or []:
            yield quality, None, None


def _row(doc: dict, quality: dict, season, episode, channel=None, message_id=None) -> dict:
    return {
        "file_id": quality.get("id"),
        "file_unique_id": quality.get("file_unique_id"),
        "quality_entry": dict(quality),
        "file_name": quality.get("name"),
        "stored_quality": quality.get("quality"),
        "title": doc.get("title") or "Untitled",
        "tmdb_id": doc.get("tmdb_id"),
        "collection": doc.get("type") or ("tv" if doc.get("seasons") else "movie"),
        "db_index": doc.get("db_index"),
        "season_number": season,
        "episode_number": episode,
        "channel": channel,
        "message_id": message_id,
    }


async def _resolve_link(link: str, bot) -> Tuple[str, int, Optional[str], str]:
    """Return (bare channel id, message id, file_unique_id, encoded file id)."""
    from Backend.helper.channel_index import (
        IndexTargetError,
        clean_channel_id,
        resolve_link_target,
    )

    try:
        from Backend.wzgram_pkg import UserBot
    except Exception:  # pragma: no cover - client modules not loaded in unit tests
        UserBot = None

    try:
        chat, message_id = await resolve_link_target(bot, link, fallbacks=[UserBot])
    except IndexTargetError as e:
        raise ReassignError(str(e)) from e
    except Exception as e:  # pragma: no cover - client/network dependent
        raise ReassignError(f"Could not open that post: {e}") from e

    try:
        post = await bot.get_messages(chat.id, int(message_id))
    except Exception as e:  # pragma: no cover - client/network dependent
        raise ReassignError(f"Could not read that post: {e}") from e
    file = getattr(post, "video", None) or getattr(post, "document", None) if post else None
    if not file:
        raise ReassignError("That post has no video or document in it.")

    channel = clean_channel_id(chat.id)
    unique = getattr(file, "file_unique_id", None)
    encoded = await encode_string({
        "chat_id": int(channel),
        "msg_id": int(message_id),
        "hash": (unique or "")[:6],
    })
    return channel, int(message_id), unique, encoded


async def find_library_files(
    *,
    link: str = "",
    file_id: str = "",
    file_unique_id: str = "",
    bot=None,
    limit: int = 50,
) -> dict:
    """Find every library entry pointing at one Telegram file.

    Accepts a direct post link, a stored encoded ``file_id`` (from the media
    editor), or a Telegram ``file_unique_id``. Matching by encoded id is exact;
    matching by ``file_unique_id`` is the fallback for files that were
    re-uploaded or whose stored hash no longer matches the live post.
    """
    dbb = _late_db()
    ids: List[str] = []
    unique = str(file_unique_id or "").strip() or None
    channel = message_id = None

    if file_id:
        ids.append(str(file_id).strip())
    if link:
        if bot is None:
            raise ReassignError("A Telegram client is required to resolve a post link.")
        channel, message_id, link_unique, encoded = await _resolve_link(link, bot)
        unique = unique or link_unique
        if encoded not in ids:
            ids.append(encoded)
    if not ids and not unique:
        raise ReassignError("Give a Telegram post link, a stored file id, or a file_unique_id.")

    queries: List[dict] = []
    for fid in ids:
        queries.append({"type": "movie", "telegram.id": fid})
        queries.append({"type": "tv", "seasons.episodes.telegram.id": fid})
    if unique:
        queries.append({"type": "movie", "telegram.file_unique_id": unique})
        queries.append({"type": "tv", "seasons.episodes.telegram.file_unique_id": unique})

    matches: List[dict] = []
    seen = set()
    for query in queries:
        try:
            docs = await dbb.media.find(query).to_list(limit)
        except Exception as e:
            raise ReassignError(f"Library lookup failed: {e}", 502) from e
        for doc in docs or []:
            for quality, season, episode in iter_qualities(doc):
                hit = quality.get("id") in ids or (
                    unique and quality.get("file_unique_id") == unique
                )
                if not hit:
                    continue
                key = (
                    doc.get("db_index"), doc.get("tmdb_id"), quality.get("id"),
                    season, episode,
                )
                if key in seen:
                    continue
                seen.add(key)
                matches.append(_row(doc, quality, season, episode, channel, message_id))

    return {
        "matches": matches,
        "count": len(matches),
        "file_id": ids[0] if ids else None,
        "file_unique_id": unique,
        "channel": channel,
        "message_id": message_id,
    }


def _same_slot(season, episode, info: dict) -> bool:
    """Does a target quality slot equal the requested season/episode?"""
    if info.get("media_type") != "tv":
        return True
    return (
        str(season) == str(info.get("season_number"))
        and str(episode) == str(info.get("episode_number"))
    )


def _file_in_source_slot(doc, fid: str, match: dict) -> bool:
    """Is the file still in the exact source movie/episode slot after the pull?"""
    if not doc:
        return False
    if match.get("collection") == "tv":
        for season in doc.get("seasons") or []:
            if season.get("season_number") != match.get("season_number"):
                continue
            for episode in season.get("episodes") or []:
                if episode.get("episode_number") != match.get("episode_number"):
                    continue
                return any(q.get("id") == fid for q in episode.get("telegram") or [])
        return False
    return any(q.get("id") == fid for q in doc.get("telegram") or [])


async def reassign_file(
    match: dict,
    *,
    media_ref: str,
    kind: Optional[str] = None,
    quality: Optional[str] = None,
    rip: Optional[str] = None,
    languages=None,
    codec: Optional[str] = None,
    season=None,
    episode=None,
    replace_existing_quality: bool = False,
    remove_empty_title: bool = True,
) -> dict:
    """Move ``match`` (a row from :func:`find_library_files`) to ``media_ref``."""
    dbb = _late_db()
    if not isinstance(match, dict) or not match.get("file_id"):
        raise ReassignError("Look the file up first — no library entry was supplied.")

    try:
        info = await build_manual_metadata_info(
            media_ref=media_ref,
            kind=kind,
            quality=quality,
            rip=rip,
            languages=languages,
            codec=codec,
            season=season,
            episode=episode,
        )
    except ManualAddError as e:
        raise ReassignError(str(e), e.status) from e

    fid = str(match["file_id"])
    source_type = match.get("collection") or "movie"
    target_type = info["media_type"]

    # ── Guard: moving onto the exact slot it already occupies would delete it ──
    if (
        str(match.get("tmdb_id")) == str(info["tmdb_id"])
        and source_type == target_type
        and _same_slot(match.get("season_number"), match.get("episode_number"), info)
    ):
        raise ReassignError(
            "That file is already on the requested title/episode — nothing to move.",
            409,
        )

    stored = dict(match.get("quality_entry") or {})
    wanted_quality = str(quality or stored.get("quality") or info.get("quality") or "1080p").lower()
    stored["quality"] = wanted_quality
    if codec:
        stored["codec"] = str(codec).strip().lower()

    target_filter = {"type": target_type, "tmdb_id": info["tmdb_id"]}
    try:
        target_doc = await dbb.media.find_one(target_filter)
    except Exception as e:
        raise ReassignError(f"Could not read the target title: {e}", 502) from e

    # ── Never silently replace a different file already on the target ────────
    conflict = None
    for tq, ts, te in iter_qualities(target_doc):
        if tq.get("id") == fid or not _same_slot(ts, te, info):
            continue
        if str(tq.get("quality") or "").lower() == wanted_quality:
            conflict = tq
            break
    # Snapshot the id/name now: the conflict row lives inside the stored doc and
    # insert_media mutates that very dict when it replaces the same quality.
    conflict_id = conflict.get("id") if conflict else None
    if conflict and not replace_existing_quality:
        raise ReassignError(
            f"The target already has a {wanted_quality} file "
            f"({conflict.get('name') or conflict_id}). Enable "
            "'Replace same-quality file' or choose a different quality.",
            409,
        )

    # Preserve title-level fields that insert_media would otherwise overwrite
    # with the manual-add defaults when the target already exists.
    preserve = {
        key: target_doc[key]
        for key in ("rip", "languages")
        if target_doc and key in target_doc
    }
    target_scope = dict(target_filter)
    if target_doc and target_doc.get("db_index") is not None:
        target_scope["db_index"] = target_doc["db_index"]

    try:
        decoded = await decode_string(fid)
    except Exception as e:
        raise ReassignError(f"That stored file id is corrupt: {e}") from e

    info["quality"] = wanted_quality
    info["audio_tracks"] = stored.get("audio_tracks")
    info["subtitle_tracks"] = stored.get("subtitle_tracks")
    if codec:
        info["codec"] = str(codec).strip().lower()

    # ── 1. Attach. Re-encoding with the original chat/msg/hash reproduces the
    #       exact same id, so existing stream links stay valid.
    try:
        await dbb.insert_media(
            info,
            hash=str(decoded.get("hash") or ""),
            channel=int(decoded["chat_id"]),
            msg_id=int(decoded["msg_id"]),
            size=stored.get("size") or "",
            name=stored.get("name") or "",
            codec=stored.get("codec") or info.get("codec"),
            file_unique_id=stored.get("file_unique_id"),
            size_bytes=stored.get("size_bytes"),
            duration=stored.get("duration"),
        )
    except Exception as e:
        raise ReassignError(f"Could not attach the file to the new title: {e}", 502) from e

    id_field = (
        "telegram.id" if target_type == "movie"
        else "seasons.episodes.telegram.id"
    )
    try:
        landed = await dbb.media.find_one({**target_filter, id_field: fid})
    except Exception:
        landed = None
    if not landed:
        raise ReassignError(
            "The file did not land on the target title (it may have been dropped "
            "by the per-title quality cap). Nothing was removed from the old title.",
            502,
        )

    if preserve:
        try:
            await dbb.media.update_one(target_scope, {"$set": preserve})
        except Exception as e:  # pragma: no cover - best effort
            from Backend.logger import LOGGER

            LOGGER.warning(f"[reassign] could not restore target metadata: {e}")

    # ── 2. Detach from the old title / episode ──────────────────────────────
    source_filter = {
        "db_index": match.get("db_index"),
        "tmdb_id": match.get("tmdb_id"),
        "type": source_type,
    }
    if source_filter["db_index"] is None:
        source_filter.pop("db_index")

    if source_type == "tv":
        update = {"$pull": {"seasons.$[s].episodes.$[e].telegram": {"id": fid}}}
        array_filters: Optional[list] = [
            {"s.season_number": match.get("season_number")},
            {"e.episode_number": match.get("episode_number")},
        ]
    else:
        update = {"$pull": {"telegram": {"id": fid}}}
        array_filters = None

    try:
        result = await dbb.media.update_one(
            source_filter, update, array_filters=array_filters
        )
    except Exception as e:
        raise ReassignError(
            f"Attached to the new title, but removing the old entry failed: {e}. "
            "Remove it manually from the old title's Files tab.",
            502,
        ) from e
    # Counter-only checks are unreliable: a $pull that matches a document but
    # removes nothing still reports matched_count=1. Read the exact source slot
    # back instead — this also handles a same-title move between TV episodes,
    # where the id is legitimately still present elsewhere in the same doc.
    modified = getattr(result, "modified_count", None)
    detached = bool(modified) if modified is not None else bool(
        getattr(result, "matched_count", 0)
    )
    try:
        after = await dbb.media.find_one(source_filter)
        if after is not None:
            detached = not _file_in_source_slot(after, fid, match)
    except Exception:  # pragma: no cover - keep the counter-based answer
        pass

    # ── 3. Drop the wrong title if it has no files left ──────────────────────
    old_title_removed = False
    if detached and remove_empty_title:
        try:
            old_doc = await dbb.media.find_one(source_filter)
            if old_doc and not list(iter_qualities(old_doc)):
                deleted = await dbb.media.delete_one(source_filter)
                old_title_removed = bool(getattr(deleted, "deleted_count", 0))
        except Exception as e:  # pragma: no cover - cleanup is best effort
            from Backend.logger import LOGGER

            LOGGER.warning(f"[reassign] could not prune empty source title: {e}")

    return {
        "ok": True,
        "moved": detached,
        "file_id": fid,
        "file_name": stored.get("name"),
        "channel": str(decoded.get("chat_id") or ""),
        "message_id": int(decoded.get("msg_id") or 0),
        "quality": wanted_quality,
        "title": info["title"],
        "tmdb_id": info["tmdb_id"],
        "tmdb_url": info["tmdb_url"],
        "media_type": target_type,
        "season_number": info.get("season_number"),
        "episode_number": info.get("episode_number"),
        "from": {
            "title": match.get("title"),
            "tmdb_id": match.get("tmdb_id"),
            "collection": source_type,
            "season_number": match.get("season_number"),
            "episode_number": match.get("episode_number"),
        },
        "replaced_file_id": conflict_id,
        "old_title_removed": old_title_removed,
        "warning": None if detached else (
            "Attached to the new title, but the old entry could not be removed. "
            "Delete it manually from the old title's Files tab."
        ),
    }


# ── Telegram command argument flags ─────────────────────────────────────────
def split_reassign_flags(tokens) -> Tuple[List[str], Dict[str, bool]]:
    """Strip ``/reassign``-only switches before reusing parse_manual_args."""
    flags = {"replace_existing_quality": False, "remove_empty_title": True}
    rest: List[str] = []
    for token in tokens:
        low = str(token).strip().lower()
        if low in ("replace", "replace=1", "--replace"):
            flags["replace_existing_quality"] = True
        elif low in ("keepempty", "keep=1", "--keep-empty"):
            flags["remove_empty_title"] = False
        else:
            rest.append(token)
    return rest, flags
