"""Resumable admin job runner.

Long fan-out work — backfills, remediation sweeps, orphan audits — gets its own
collection and an opaque cursor, so a restart resumes where it stopped instead
of starting over.

Rules that keep this safe:

* One job per kind may run at a time.
* A job only ever starts because an admin asked for it. Nothing in this module
  runs because a value was saved.
* A failed job never breaks the admin request that started it.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo import ASCENDING

from Backend.config import AdminOpsConfig
from Backend.helper.audio_languages import (
    BACKFILL_PROJECTION,
    document_is_probed,
    languages_from_document,
)
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


JOB_KINDS = ("audio_lang_backfill", "metadata_remediation", "orphan_scan")
JOB_STATUSES = ("pending", "running", "paused", "completed", "error")


class JobError(Exception):
    """Raised for a bad job request (unknown kind, already running, …)."""


class AdminJobRunner:
    """Starts, stops and tracks the resumable admin jobs."""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}

    # ── introspection ───────────────────────────────────────────────────────
    def is_running(self, kind: str) -> bool:
        task = self._tasks.get(kind)
        return task is not None and not task.done()

    def should_stop(self, kind: str) -> bool:
        return bool(self._stop_flags.get(kind))

    def status(self) -> Dict[str, Any]:
        return {
            kind: {
                "running": self.is_running(kind),
                "stopping": self.should_stop(kind),
            }
            for kind in JOB_KINDS
        }

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def start(self, kind: str, params: Optional[dict] = None,
                    actor: str = "panel") -> Dict[str, Any]:
        if kind not in JOB_KINDS:
            raise JobError(f"Unknown job kind: {kind}")
        if self.is_running(kind):
            raise JobError(f"A '{kind}' job is already running.")

        params = dict(params or {})
        job_id = f"{kind}:{uuid.uuid4().hex[:8]}"

        existing = await _db().find_active_admin_job(kind)
        if existing and existing.get("_id"):
            existing_id = str(existing["_id"])
            stored_params = dict(existing.get("params") or {})
            if params and params != stored_params:
                # A different target supersedes the paused/stale run. Close the
                # old document so it no longer blocks this kind, then create a
                # fresh job below.
                await _db().upsert_admin_job(existing_id, {
                    "status": "error",
                    "error": "Superseded by a new run.",
                    "finished_at": datetime.utcnow(),
                })
            else:
                # No in-memory task owns the document: the job was paused by
                # an admin, or the process restarted while it still said
                # pending/running. Resume it from its saved cursor.
                await _db().upsert_admin_job(existing_id, {
                    "status": "pending",
                    "error": None,
                    "finished_at": None,
                    "resumed_at": datetime.utcnow(),
                })
                self._stop_flags[kind] = False
                task = asyncio.create_task(
                    self._run(existing_id, kind, stored_params or params)
                )
                self._tasks[kind] = task
                LOGGER.info(f"Admin job {existing_id} resumed (kind={kind})")
                return {
                    "id": existing_id, "kind": kind,
                    "status": "pending", "resumed": True,
                }

        await _db().upsert_admin_job(job_id, {
            "kind": kind,
            "status": "pending",
            "params": params,
            "cursor": None,
            "counts": {},
            "error": None,
            "started_at": datetime.utcnow(),
            "finished_at": None,
            "started_by": str(actor)[:120],
        })

        self._stop_flags[kind] = False
        task = asyncio.create_task(self._run(job_id, kind, params))
        self._tasks[kind] = task
        LOGGER.info(f"Admin job {job_id} queued (kind={kind})")
        return {"id": job_id, "kind": kind, "status": "pending"}

    def stop(self, kind: str) -> bool:
        """Ask a running job to stop at its next safe point."""
        if not self.is_running(kind):
            return False
        self._stop_flags[kind] = True
        LOGGER.info(f"Admin job {kind} marked for stop")
        return True

    async def _run(self, job_id: str, kind: str, params: dict) -> None:
        handler = HANDLERS.get(kind)
        counts: Dict[str, Any] = {}
        try:
            await _db().upsert_admin_job(job_id, {"status": "running"})
            if handler is None:
                raise JobError(f"No handler for job kind {kind}")
            counts = await handler(self, job_id, params) or {}
            stopped = self.should_stop(kind)
            await _db().upsert_admin_job(job_id, {
                "status": "paused" if stopped else "completed",
                "counts": counts,
                "finished_at": None if stopped else datetime.utcnow(),
            })
            LOGGER.info(f"Admin job {job_id} {'paused' if stopped else 'completed'}: {counts}")
        except asyncio.CancelledError:
            await _db().upsert_admin_job(job_id, {
                "status": "paused", "error": "stopped by admin",
            })
            raise
        except Exception as e:
            LOGGER.error(f"Admin job {job_id} failed: {e}")
            await _db().upsert_admin_job(job_id, {
                "status": "error", "error": str(e)[:300],
                "finished_at": datetime.utcnow(),
            })
        finally:
            self._tasks.pop(kind, None)
            self._stop_flags.pop(kind, None)


# =============================================================================
# AUDIO-LANGUAGE BACKFILL
# =============================================================================
async def _apply_audio_languages(collection, doc: dict, kind: str, counts: dict) -> None:
    """Denormalize one document's audio languages, writing only on a change."""
    counts["titles_scanned"] = counts.get("titles_scanned", 0) + 1

    languages = languages_from_document(doc)

    if not document_is_probed(doc):
        # No file was ever probed — the cheap backfill cannot recover these.
        counts["titles_unprobeable"] = counts.get("titles_unprobeable", 0) + 1

    dropped = doc.get("dropped_audio_languages") or []
    extra = [lang for lang in dropped if lang not in languages]
    if extra:
        counts["dropped_language_files"] = counts.get("dropped_language_files", 0) + len(extra)

    if doc.get("audio_languages") == languages and doc.get("audio_language_count") == len(languages):
        return  # unchanged — do not rewrite the document

    await collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "audio_languages": languages,
            "audio_language_count": len(languages),
        }},
    )
    counts["titles_updated"] = counts.get("titles_updated", 0) + 1


async def _projection_is_trustworthy(collection, doc: dict, kind: str) -> bool:
    """Cross-check a projected read against the full document.

    The projection keeps only the ``audio_tracks`` arrays. MongoDB is free to
    re-nest an array-of-arrays, so this compares one projected document with
    the same document read whole. If they disagree the projection is not
    reporting everything, and the caller must fall back to full reads rather
    than write a language set that is missing entries.
    """
    doc_id = doc.get("_id")
    if doc_id is None:
        return True
    try:
        full = await collection.find_one({"_id": doc_id})
    except Exception as e:
        LOGGER.warning(f"audio_lang_backfill could not verify the projection: {e}")
        return False
    if not full:
        return True
    return languages_from_document(full) == languages_from_document(doc)


async def _handle_audio_lang_backfill(runner: AdminJobRunner, job_id: str, params: dict) -> dict:
    """Walk every cluster and both collections, denormalizing audio languages.

    Work units are the ordered cross product of the clusters and the two media
    collections. The cursor is ``{cluster_index, collection, last_id}``: inside
    a unit the job pages by ``_id`` ascending, and when a unit is exhausted it
    advances to the next unit and resets ``last_id``. A restart resumes at the
    stored unit and id, so a mid-cluster restart never rescans finished units.
    """
    batch_size = max(1, int(AdminOpsConfig.JOB_BATCH_SIZE))
    job = await _db().get_admin_job(job_id) or {}
    counts = dict(job.get("counts") or {})
    cursor = dict(job.get("cursor") or {})

    clusters = sorted(_db().clusters or [], key=lambda c: c.index)
    units: List[tuple] = [
        (cluster.index, kind) for cluster in clusters for kind in ("movie", "tv")
    ]

    start_unit = 0
    job_cursor = cursor.get("last_id")
    if cursor.get("cluster_index") is not None and cursor.get("collection"):
        try:
            start_unit = units.index(
                (int(cursor["cluster_index"]), str(cursor["collection"]))
            )
        except ValueError:
            start_unit = 0

    for unit_index in range(start_unit, len(units)):
        cluster_index, kind = units[unit_index]
        cluster = next((c for c in clusters if c.index == cluster_index), None)
        if cluster is None:
            continue
        collection = cluster.collection(kind)

        # The projection is verified once per unit against a full read. If it
        # ever under-reports, this unit falls back to full documents for the
        # rest of its run rather than writing a short language set.
        trusted = True
        verified = False

        query: Dict[str, Any] = {}
        if job_cursor:
            try:
                query["_id"] = {"$gt": ObjectId(str(job_cursor))}
            except Exception:
                query = {}

        while True:
            if runner.should_stop("audio_lang_backfill"):
                await _db().upsert_admin_job(job_id, {
                    "cursor": {"cluster_index": cluster_index, "collection": kind,
                               "last_id": job_cursor},
                    "counts": counts,
                })
                return counts

            scan_cursor = collection.find(query, BACKFILL_PROJECTION if trusted else None)
            docs = await scan_cursor.sort("_id", ASCENDING).limit(batch_size).to_list(
                length=batch_size
            )
            if not docs:
                break

            for doc in docs:
                try:
                    if trusted and not verified:
                        # Verify the projection against a full read once per unit.
                        verified = True
                        trusted = await _projection_is_trustworthy(
                            collection, doc, kind
                        )
                        if not trusted:
                            LOGGER.warning(
                                "audio_lang_backfill: the language projection did "
                                "not match a full read — falling back to full "
                                "documents for this unit."
                            )
                            full = await collection.find_one({"_id": doc.get("_id")})
                            if full:
                                doc = full
                    await _apply_audio_languages(collection, doc, kind, counts)
                except Exception as e:
                    counts["errors"] = counts.get("errors", 0) + 1
                    LOGGER.warning(f"audio_lang_backfill failed on {doc.get('_id')}: {e}")
                job_cursor = str(doc.get("_id"))
                query = {"_id": {"$gt": ObjectId(str(job_cursor))}}

            await _db().upsert_admin_job(job_id, {
                "cursor": {"cluster_index": cluster_index, "collection": kind,
                           "last_id": job_cursor},
                "counts": counts,
            })

        # Unit exhausted — move on and start the next unit from the beginning.
        job_cursor = None

    return counts


# =============================================================================
# BULK METADATA REMEDIATION
# =============================================================================
async def _refresh_with_backoff(media: dict, attempts: int = 4) -> dict:
    """Call the existing per-title refresh, backing off on a TMDB 429."""
    from Backend.fastapi.main import _refresh_media_from_tmdb

    delay = 5.0
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return await _refresh_media_from_tmdb(media, overwrite=False)
        except Exception as e:
            last_error = e
            text = str(getattr(e, "detail", "") or "") or str(e)
            rate_limited = "429" in text
            if attempt >= attempts or (not rate_limited and attempt >= 2):
                raise
            LOGGER.warning(
                f"TMDB refresh for {media.get('tmdb_id')} failed ({text}); "
                f"retrying in {delay:.0f}s"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
    raise last_error  # pragma: no cover — loop always raises or returns


async def _handle_metadata_remediation(runner: AdminJobRunner, job_id: str, params: dict) -> dict:
    """Refresh metadata for every title matching one ADMIN_LIST_FLAGS value.

    The seven existing flags are the selection language — no new "needs
    metadata" field is invented. Completed titles are not re-run, and a
    per-title error increments ``counts.errors`` and lets the job continue.
    """
    from Backend.fastapi.main import _resolve_admin_media
    from Backend.helper.database import ADMIN_LIST_FLAGS

    flag = str(params.get("flag") or "").strip().lower()
    if flag not in ADMIN_LIST_FLAGS:
        raise JobError(
            f"Unknown metadata flag '{flag}'. Valid flags: {', '.join(sorted(ADMIN_LIST_FLAGS))}"
        )
    media_type = str(params.get("media_type") or "all").strip().lower()
    if media_type not in ("movie", "tv", "all", "anime"):
        raise JobError(f"Unknown media type '{media_type}'")

    job = await _db().get_admin_job(job_id) or {}
    counts = dict(job.get("counts") or {})
    cursor = dict(job.get("cursor") or {})

    page_size = 100
    concurrency = max(1, int(AdminOpsConfig.REMEDIATION_CONCURRENCY))
    semaphore = asyncio.Semaphore(concurrency)

    def _target_key(item: dict) -> str:
        return "|".join([
            str(item.get("collection") or item.get("type") or "movie"),
            str(item.get("db_index") or 1),
            str(item.get("tmdb_id")),
        ])

    def _target_item(key: str) -> Optional[dict]:
        parts = str(key).split("|", 2)
        if len(parts) != 3:
            return None
        collection, db_index, tmdb_id = parts
        try:
            db_index_value: Any = int(db_index)
        except (TypeError, ValueError):
            db_index_value = db_index
        try:
            tmdb_id_value: Any = int(tmdb_id)
        except (TypeError, ValueError):
            tmdb_id_value = tmdb_id
        return {
            "type": collection,
            "collection": collection,
            "db_index": db_index_value,
            "tmdb_id": tmdb_id_value,
        }

    async def _refresh_one(item: dict) -> None:
        async with semaphore:
            try:
                media = await _resolve_admin_media(item)
            except Exception as e:
                counts["errors"] = counts.get("errors", 0) + 1
                LOGGER.warning(f"metadata_remediation could not resolve a title: {e}")
                return
            try:
                result = await _refresh_with_backoff(media)
                if result.get("updated_fields"):
                    counts["updated"] = counts.get("updated", 0) + 1
                else:
                    counts["unchanged"] = counts.get("unchanged", 0) + 1
            except Exception as e:
                counts["errors"] = counts.get("errors", 0) + 1
                LOGGER.warning(
                    f"metadata_remediation failed for {item.get('tmdb_id')}: {e}"
                )

    targets: List[str] = [str(key) for key in (cursor.get("targets") or [])]
    processed = int(cursor.get("index") or 0)

    if not targets:
        # Snapshot the matching titles before refreshing any of them. A refresh
        # rewrites ``updated_on`` and can clear the flag, so offset paging over
        # the live filtered list would skip titles as earlier pages shrink.
        legacy_processed = {str(key) for key in (cursor.get("processed") or [])}
        seen: set = set()
        snapshot_page = 1
        while True:
            listing = await _db().admin_list_media(
                media_type=media_type, query="", page=snapshot_page,
                page_size=page_size, sort="updated_on:desc", flag=flag,
            )
            items = listing.get("items") or []
            if not items:
                break
            for item in items:
                key = _target_key(item)
                legacy_key = (
                    f"{item.get('type') or item.get('collection') or ''}:"
                    f"{item.get('tmdb_id')}"
                )
                if key in seen or legacy_key in legacy_processed:
                    continue
                seen.add(key)
                targets.append(key)
            total = int(listing.get("total_count") or 0)
            if snapshot_page * page_size >= total:
                break
            snapshot_page += 1

        processed = 0
        await _db().upsert_admin_job(job_id, {
            "cursor": {"targets": targets, "index": 0},
            "counts": counts,
        })

    batch_size = 50
    while processed < len(targets):
        if runner.should_stop("metadata_remediation"):
            break

        batch = []
        for key in targets[processed:processed + batch_size]:
            item = _target_item(key)
            if item is not None:
                batch.append(item)

        if batch:
            await asyncio.gather(*[_refresh_one(item) for item in batch])
            counts["processed"] = counts.get("processed", 0) + len(batch)

        processed += len(targets[processed:processed + batch_size])
        await _db().upsert_admin_job(job_id, {
            "cursor": {"targets": targets, "index": processed},
            "counts": counts,
        })

    return counts


# =============================================================================
# ORPHAN SCAN
# =============================================================================
async def _handle_orphan_scan(runner: AdminJobRunner, job_id: str, params: dict) -> dict:
    """Start audit-mode channel scans.

    Audit mode walks the channel history exactly like an index scan but never
    enqueues — it only records posts that have no database match.
    """
    from Backend.helper.channel_index import (
        MODE_AUDIT, is_auth_channel, request_index_scans,
    )
    from Backend.wzgram_pkg import get_index_client

    channels = params.get("channels") or []
    if isinstance(channels, str):
        channels = [channels]
    if not channels:
        raise JobError("No channels given — pass one or more channel ids.")

    client = get_index_client()
    chats = []
    skipped = 0
    for raw in channels:
        try:
            chat_id = int(str(raw).strip())
        except (TypeError, ValueError):
            skipped += 1
            continue
        try:
            chat = await client.get_chat(chat_id)
        except Exception as e:
            LOGGER.warning(f"orphan_scan could not open {chat_id}: {e}")
            skipped += 1
            continue
        if not is_auth_channel(chat):
            skipped += 1
            continue
        chats.append(chat)

    if not chats:
        raise JobError("No usable channel was given for the orphan scan.")

    from Backend.wzgram_pkg.plugins.start import _index_queue_file

    result = await request_index_scans(
        client, chats, 0, _index_queue_file, notify_client=None, mode=MODE_AUDIT
    )
    return {
        "started": len(result.get("started") or []),
        "waiting": len(result.get("waiting") or []),
        "skipped": skipped,
        "channels": [c.id for c in chats],
    }


HANDLERS = {
    "audio_lang_backfill": _handle_audio_lang_backfill,
    "metadata_remediation": _handle_metadata_remediation,
    "orphan_scan": _handle_orphan_scan,
}


runner = AdminJobRunner()
