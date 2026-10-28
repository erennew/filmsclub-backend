import asyncio
import os
import re
import time
import zlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from bson import ObjectId
from fastapi import HTTPException
import motor.motor_asyncio
from pydantic import ValidationError
from pymongo import ASCENDING, DESCENDING, ReturnDocument, IndexModel
from pymongo.errors import DuplicateKeyError

from Backend.logger import LOGGER
from Backend.config import Telegram, QueueConfig, StorageConfig
from Backend.helper.encrypt import encode_string, decode_string
from Backend.helper.modal import Episode, MovieSchema, QualityDetail, Season, TVShowSchema


# Codec preference order: H265/HEVC > H264/AVC > VP9 > others.
CODEC_PRIORITY = {
    'hevc': 3, 'h265': 3, 'h.265': 3, 'x265': 3,
    'h264': 2, 'avc': 2, 'h.264': 2, 'x264': 2,
    'vp9': 1,
}

ANIME_GENRES = ["Animation", "Anime", "anime"]


# =============================================================================
# QUEUE DOC COMPACTION — keep queue documents small so lakhs of pending files
# don't blow up the storage quota. Decision-relevant data is preserved.
# =============================================================================
def compact_metadata_for_queue(metadata_info: Optional[dict]) -> dict:
    """Shrink metadata_info for queue storage without losing decision data."""
    if not isinstance(metadata_info, dict):
        return {}
    mi = dict(metadata_info)
    desc = mi.get("description")
    if isinstance(desc, str) and len(desc) > 600:
        mi["description"] = desc[:600]
    cast = mi.get("cast")
    if isinstance(cast, list) and len(cast) > 10:
        mi["cast"] = [
            {"name": c.get("name"), "character": c.get("character"), "profile_path": c.get("profile_path")}
            for c in cast[:10]
        ]
    for key in ("audio_tracks", "subtitle_tracks"):
        tracks = mi.get(key)
        if isinstance(tracks, list) and len(tracks) > 12:
            mi[key] = [
                {"language": t.get("language"), "codec": t.get("codec")}
                for t in tracks[:12] if isinstance(t, dict)
            ]
    mi.pop("enhanced_caption", None)
    return mi


def compact_probe_result(probe_result: Optional[dict]) -> Optional[dict]:
    """Keep only probe fields used for quality comparison / scoring."""
    if not isinstance(probe_result, dict):
        return None
    return {
        "height": probe_result.get("height"),
        "codec": probe_result.get("codec"),
        "hdr": probe_result.get("hdr"),
        "has_subtitles": probe_result.get("has_subtitles"),
        "audio_tracks": [
            {"language": t.get("language")}
            for t in (probe_result.get("audio_tracks") or [])[:12] if isinstance(t, dict)
        ],
        "subtitle_tracks": [
            {"language": t.get("language")}
            for t in (probe_result.get("subtitle_tracks") or [])[:12] if isinstance(t, dict)
        ],
    }


# =============================================================================
# LIGHTWEIGHT RESULT WRAPPERS (mirror pymongo result objects)
# =============================================================================
class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _UpdateResult:
    def __init__(self, matched_count=0, modified_count=0):
        self.matched_count = matched_count
        self.modified_count = modified_count


class _DeleteResult:
    def __init__(self, deleted_count=0):
        self.deleted_count = deleted_count


class _Cluster:
    """One MongoDB cluster (connection + media collections)."""
    __slots__ = ("index", "client", "db", "movie", "tv", "free_bytes", "free_ts")

    def __init__(self, index: int, client, db):
        self.index = index  # 1-based cluster number (matches admin db_index)
        self.client = client
        self.db = db
        self.movie = db["movie"]
        self.tv = db["tv"]
        self.free_bytes = 0
        self.free_ts = 0.0

    def collection(self, media_type: str):
        return self.movie if media_type == "movie" else self.tv


def _extract_tmdb_id(filt: Optional[dict]) -> Optional[int]:
    """Pull a tmdb_id out of a filter (direct or inside $or)."""
    if not isinstance(filt, dict):
        return None
    tid = filt.get("tmdb_id")
    if isinstance(tid, bool):
        return None
    if isinstance(tid, int):
        return tid
    if isinstance(tid, str) and tid.isdigit():
        return int(tid)
    ors = filt.get("$or")
    if isinstance(ors, list):
        for sub in ors:
            if isinstance(sub, dict):
                tid = sub.get("tmdb_id")
                if isinstance(tid, int):
                    return tid
                if isinstance(tid, str) and tid.isdigit():
                    return int(tid)
    return None


class _MediaCursor:
    """Lazy merged cursor over the same collection on every cluster."""

    def __init__(self, clusters: List[_Cluster], name: str, filt: Optional[dict],
                 projection: Optional[dict] = None, sort: Optional[list] = None,
                 skip: int = 0, limit: Optional[int] = None):
        self._clusters = clusters
        self._name = name
        self._filter = filt
        self._projection = projection
        self._sort = sort
        self._skip = skip
        self._limit = limit
        self._iter = None

    def sort(self, *args, **kwargs):
        key_list = kwargs.get("key_or_list") if "key_or_list" in kwargs else (args[0] if args else None)
        if isinstance(key_list, tuple):
            key_list = [key_list]
        self._sort = key_list or self._sort
        return self

    def skip(self, n: int):
        self._skip = n
        return self

    def limit(self, n: Optional[int]):
        self._limit = n
        return self

    async def _merged_docs(self) -> List[dict]:
        docs: List[dict] = []
        for cluster in self._clusters:
            col = cluster.collection(self._name)
            cur = col.find(self._filter, self._projection)
            if self._sort:
                cur = cur.sort(self._sort)
            # per-cluster overshoot so global skip/limit stays correct
            fetch = (self._skip or 0) + (self._limit if self._limit else 0)
            if self._sort and fetch > 0:
                cur = cur.limit(fetch)
            try:
                docs.extend(await cur.to_list(length=fetch if (self._sort and fetch > 0) else None))
            except Exception as e:
                LOGGER.error(f"Fan-out find failed on cluster {cluster.index}/{self._name}: {e}")
        if self._sort:
            import functools

            def cmp(a, b):
                for field, direction in self._sort:
                    va, vb = a.get(field), b.get(field)
                    if va == vb:
                        continue
                    try:
                        if va > vb:
                            r = 1
                        else:
                            r = -1
                    except TypeError:
                        r = 1 if str(va) > str(vb) else -1
                    return r * (1 if direction == ASCENDING else -1)
                return 0

            docs.sort(key=functools.cmp_to_key(cmp))
        if self._skip:
            docs = docs[self._skip:]
        if self._limit:
            docs = docs[:self._limit]
        return docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        docs = await self._merged_docs()
        if length is not None:
            docs = docs[:length]
        return docs

    def __aiter__(self):
        self._iter = self._merged_docs().__aiter__()
        return self

    async def __anext__(self) -> dict:
        return await self._iter.__anext__()


class MediaCollectionProxy:
    """
    Motor-collection-like proxy spanning all clusters.
    Routes point-operations by tmdb_id (via shard map), fans out everything else.
    """

    def __init__(self, database: "Database", media_type: str):
        self._db = database
        self._media_type = media_type

    # -- helpers -------------------------------------------------------------
    def _clusters(self) -> List[_Cluster]:
        return self._db.clusters

    async def _route(self, filt: Optional[dict]) -> Tuple[Optional[_Cluster], Optional[int]]:
        tid = _extract_tmdb_id(filt)
        if tid is not None:
            doc, idx = await self._db._find_media_doc(self._media_type, tid)
            if idx is not None:
                return self._db.clusters[idx], idx
            return None, None  # tmdb_id given but doc doesn't exist anywhere
        return None, None

    # -- reads ---------------------------------------------------------------
    async def find_one(self, filt: Optional[dict] = None, *args, **kwargs) -> Optional[dict]:
        filt = dict(filt or {})
        projection = kwargs.get("projection") or (args[0] if args else None)
        tid = _extract_tmdb_id(filt)
        clusters = self._clusters()
        if not clusters:
            return None
        if tid is not None:
            doc, _ = await self._db._find_media_doc(self._media_type, tid, projection)
            return doc
        tasks = [c.collection(self._media_type).find_one(filt, projection) for c in clusters]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                LOGGER.error(f"Fan-out find_one error: {r}")
            elif r is not None:
                return r
        return None
    def find(self, filt: Optional[dict] = None, *args, **kwargs) -> _MediaCursor:
        projection = kwargs.get("projection") or (args[0] if args else None)
        return _MediaCursor(self._clusters(), self._media_type, filt, projection)

    async def count_documents(self, filt: Optional[dict] = None, **kwargs) -> int:
        filt = dict(filt or {})
        clusters = self._clusters()
        if not clusters:
            return 0
        tasks = [c.collection(self._media_type).count_documents(filt) for c in clusters]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total = 0
        for r in results:
            if isinstance(r, Exception):
                LOGGER.error(f"Fan-out count error: {r}")
            else:
                total += r
        return total

    # -- writes --------------------------------------------------------------
    async def insert_one(self, doc: dict) -> _InsertResult:
        tid = _extract_tmdb_id(doc)
        if tid is not None:
            idx = self._db._deterministic_cluster_index(self._media_type, tid)
        else:
            idx = await self._db._pick_cluster_for_new()
        cluster = self._db.clusters[idx]
        try:
            res = await cluster.collection(self._media_type).insert_one(doc)
        except DuplicateKeyError:
            # doc already exists on this cluster (idempotent re-add) — find it
            existing = await cluster.collection(self._media_type).find_one({"tmdb_id": doc.get("tmdb_id")})
            if tid is not None:
                await self._db._record_shard(self._media_type, tid, idx)
            return _InsertResult(existing["_id"] if existing else None)
        if tid is not None:
            await self._db._record_shard(self._media_type, tid, idx)
        return _InsertResult(res.inserted_id)

    async def replace_one(self, filt: dict, doc: dict, **kwargs) -> _UpdateResult:
        cluster, _ = await self._route(filt)
        if cluster is None:
            return _UpdateResult(0, 0)
        res = await cluster.collection(self._media_type).replace_one(filt, doc, **kwargs)
        return _UpdateResult(res.matched_count, res.modified_count)

    async def update_one(self, filt: dict, update: dict, array_filters: Optional[list] = None, **kwargs) -> _UpdateResult:
        cluster, _ = await self._route(filt)
        if cluster is None:
            return _UpdateResult(0, 0)
        if array_filters is not None:
            res = await cluster.collection(self._media_type).update_one(filt, update, array_filters=array_filters, **kwargs)
        else:
            res = await cluster.collection(self._media_type).update_one(filt, update, **kwargs)
        return _UpdateResult(res.matched_count, res.modified_count)

    async def update_many(self, filt: dict, update: dict) -> _UpdateResult:
        matched = modified = 0
        for cluster in self._clusters():
            res = await cluster.collection(self._media_type).update_many(filt, update)
            matched += res.matched_count
            modified += res.modified_count
        return _UpdateResult(matched, modified)

    async def delete_one(self, filt: dict) -> _DeleteResult:
        cluster, _ = await self._route(filt)
        if cluster is None:
            return _DeleteResult(0)
        res = await cluster.collection(self._media_type).delete_one(filt)
        return _DeleteResult(res.deleted_count)

    async def delete_many(self, filt: Optional[dict] = None) -> _DeleteResult:
        total = 0
        for cluster in self._clusters():
            try:
                res = await cluster.collection(self._media_type).delete_many(filt or {})
                total += res.deleted_count
            except Exception as e:
                LOGGER.error(f"delete_many failed on cluster {cluster.index}: {e}")
        return _DeleteResult(total)

    async def aggregate(self, pipeline: list, **kwargs) -> _MediaCursor:
        """Fan-out aggregation: runs the same pipeline on every cluster and
        concatenates results (global ordering/pagination is the caller's job)."""
        docs: List[dict] = []
        for cluster in self._clusters():
            try:
                res = await cluster.collection(self._media_type).aggregate(pipeline, **kwargs).to_list(None)
                docs.extend(res)
            except Exception as e:
                LOGGER.error(f"Fan-out aggregate failed on cluster {cluster.index}: {e}")
        return _ListCursor(docs)

    async def drop(self):
        for cluster in self._clusters():
            try:
                await cluster.collection(self._media_type).drop()
            except Exception as e:
                LOGGER.error(f"drop failed on cluster {cluster.index}: {e}")

    async def create_index(self, keys, **kwargs):
        for cluster in self._clusters():
            try:
                await cluster.collection(self._media_type).create_index(keys, **kwargs)
            except Exception as e:
                LOGGER.error(f"create_index failed on cluster {cluster.index}: {e}")


class _ListCursor:
    """Cursor over an in-memory list (async-iterable + to_list)."""

    def __init__(self, docs: List[dict]):
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        return self._docs if length is None else self._docs[:length]

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self) -> dict:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class UnifiedMediaFacade:
    """
    Legacy admin-panel surface (db.media). Presents a single virtual collection
    over movie+tv across ALL clusters. Translates legacy admin fields:
      type -> media_type / collection choice, db_index -> stripped,
      telegram.file_id -> telegram.id (movie) / seasons.episodes.telegram.id (tv),
      is_anime -> genres animation filter, string tmdb_id -> int.
    """

    def __init__(self, database: "Database"):
        self._db = database

    @staticmethod
    def _rewrite(filt: Optional[dict]) -> Tuple[dict, Optional[str]]:
        f = dict(filt or {})
        mtype = f.pop("type", None)
        f.pop("db_index", None)
        if mtype == "anime":
            f["genres"] = {"$in": ANIME_GENRES}
            mtype = None  # anime lives in both collections
        if "telegram.file_id" in f:
            v = f.pop("telegram.file_id")
            key = "seasons.episodes.telegram.id" if mtype == "tv" else "telegram.id"
            f[key] = v
        if "telegram.id" in f and mtype == "tv":
            f["seasons.episodes.telegram.id"] = f.pop("telegram.id")
        if "telegram.subtitles.id" in f:
            f.pop("telegram.subtitles.id", None)
        tid = f.get("tmdb_id")
        if isinstance(tid, str) and tid.isdigit():
            f["tmdb_id"] = int(tid)
        return f, mtype

    def _cols(self, mtype: Optional[str]) -> List[Tuple[Any, str]]:
        clusters = self._db.clusters
        out = []
        for c in clusters:
            if mtype in (None, "movie"):
                out.append((c.movie, "movie"))
            if mtype in (None, "tv"):
                out.append((c.tv, "tv"))
        return out

    def find(self, filt: Optional[dict] = None, *args, **kwargs) -> "_FacadeCursor":
        f, mtype = self._rewrite(filt)
        return _FacadeCursor(self._cols(mtype), f, mtype)

    async def find_one(self, filt: Optional[dict] = None, *args, **kwargs) -> Optional[dict]:
        f, mtype = self._rewrite(filt)
        for col, kind in self._cols(mtype):
            try:
                doc = await col.find_one(f)
            except Exception as e:
                LOGGER.error(f"facade find_one error: {e}")
                continue
            if doc is not None:
                doc = dict(doc)
                doc["type"] = kind
                doc.setdefault("db_index", 1)
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                return doc
        return None

    async def count_documents(self, filt: Optional[dict] = None, **kwargs) -> int:
        f, mtype = self._rewrite(filt)
        total = 0
        for col, _ in self._cols(mtype):
            try:
                total += await col.count_documents(f)
            except Exception as e:
                LOGGER.error(f"facade count error: {e}")
        return total

    async def update_one(self, filt: dict, update: dict, array_filters: Optional[list] = None, **kwargs) -> _UpdateResult:
        f, mtype = self._rewrite(filt)
        matched = modified = 0
        for col, _ in self._cols(mtype):
            try:
                if array_filters is not None:
                    res = await col.update_one(f, update, array_filters=array_filters)
                else:
                    res = await col.update_one(f, update)
                matched += res.matched_count
                modified += res.modified_count
            except Exception as e:
                LOGGER.error(f"facade update error: {e}")
        return _UpdateResult(matched, modified)

    async def delete_one(self, filt: dict) -> _DeleteResult:
        f, mtype = self._rewrite(filt)
        total = 0
        for col, _ in self._cols(mtype):
            try:
                res = await col.delete_one(f)
                total += res.deleted_count
            except Exception as e:
                LOGGER.error(f"facade delete error: {e}")
        return _DeleteResult(total)

    async def insert_one(self, doc: dict) -> _InsertResult:
        mtype = doc.get("type") or doc.get("media_type") or "movie"
        col = self._db.movie_collection if mtype == "movie" else self._db.tv_collection
        return await col.insert_one(doc)


class _FacadeCursor:
    def __init__(self, cols: List[Tuple[Any, str]], filt: dict, mtype: Optional[str],
                 skip: int = 0, limit: Optional[int] = None):
        self._cols = cols
        self._filter = filt
        self._mtype = mtype
        self._skip = skip
        self._limit = limit

    def skip(self, n: int):
        self._skip = n
        return self

    def limit(self, n: Optional[int]):
        self._limit = n
        return self

    def sort(self, *args, **kwargs):
        return self  # admin lists are unsorted; keep API-compatible

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        out: List[dict] = []
        skip_left = self._skip
        batch = 200
        for col, kind in self._cols:
            if length is not None and len(out) >= length:
                break
            while True:
                docs = await col.find(self._filter).skip(skip_left).limit(batch).to_list(batch)
                if not docs:
                    break
                skip_left = 0  # skip only applies before the first batch globally
                for doc in docs:
                    doc = dict(doc)
                    doc["type"] = kind
                    doc.setdefault("db_index", 1)
                    out.append(doc)
                    if length is not None and len(out) >= length:
                        return out
                if len(docs) < batch:
                    break
        return out if length is None else out[:length]

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for col, kind in self._cols:
            async for doc in col.find(self._filter):
                doc = dict(doc)
                doc["type"] = kind
                doc.setdefault("db_index", 1)
                yield doc


class Database:
    def __init__(self, connection_uri: str = None, db_name: str = "projectS"):
        self.connection_uri = connection_uri or Telegram.DATABASE
        self.db_name = db_name
        self._conn = None          # primary client (truthiness used for health checks)
        self.db = None             # primary Motor database (ops collections live here)
        self.clusters: List[_Cluster] = []

        # Ops collections (primary cluster only)
        self.tv_collection = None
        self.movie_collection = None
        self.views_collection = None
        self.deploy_config = None
        self.replaced_versions = None
        self.failed_files_collection = None
        self.queue_collection = None
        self.processed_collection = None
        self.checkpoint_collection = None
        self.index_scans_collection = None
        self.shard_map_collection = None
        self.settings_collection = None

        # Shard routing state
        self._shard_cache: Dict[str, int] = {}
        self._rr_counter = 0

        # Queue scheduling state (shared by all queue workers)
        self._queue_rr = 0
        self._queue_channels_cache: List[int] = []
        self._queue_channels_ts = 0.0
        self._queue_paused: Optional[bool] = None
        self._queue_paused_ts = 0.0

    # =========================================================================
    # CONNECTION
    # =========================================================================
    async def connect(self):
        """Establish connections to every configured cluster."""
        uris = Telegram.DATABASE_URIS
        if not uris:
            LOGGER.error("No DATABASE/DATABASE_2..5 URIs configured.")
            return

        self.clusters = []
        for i, uri in enumerate(uris, start=1):
            try:
                client = motor.motor_asyncio.AsyncIOMotorClient(
                    uri, serverSelectionTimeoutMS=20000, connectTimeoutMS=20000
                )
                mdb = client[self.db_name]
                await mdb.command("ping")
                self.clusters.append(_Cluster(i, client, mdb))
                LOGGER.info(f"Cluster {i} connected")
            except Exception as e:
                LOGGER.error(f"Cluster {i} failed to connect (skipped): {e}")

        if not self.clusters:
            self._conn = None
            self.db = None
            LOGGER.error("No MongoDB cluster could be reached.")
            return

        primary = self.clusters[0]
        self._conn = primary.client
        self.db = primary.db

        # Sharded media collections
        self.movie_collection = MediaCollectionProxy(self, "movie")
        self.tv_collection = MediaCollectionProxy(self, "tv")

        # Ops collections on primary
        self.deploy_config = primary.db["deploy_config"]
        self.views_collection = primary.db["views"]
        self.replaced_versions = primary.db["replaced_versions"]
        self.failed_files_collection = primary.db["failed_files"]
        self.shard_map_collection = primary.db["shard_map"]
        self.index_scans_collection = primary.db["index_scans"]
        self.settings_collection = primary.db["app_settings"]
        if QueueConfig.ENABLE_MONGODB_QUEUE:
            self.queue_collection = primary.db["file_queue"]
            self.processed_collection = primary.db["processed_files"]
            self.checkpoint_collection = primary.db["queue_checkpoints"]

        try:
            await self._init_indexes()
            LOGGER.info(f"Database ready — {len(self.clusters)} cluster(s) active")
        except Exception as e:
            LOGGER.error(f"Error creating indexes: {e}")

    async def disconnect(self):
        for cluster in self.clusters:
            try:
                cluster.client.close()
            except Exception:
                pass
        LOGGER.info("Database connections closed")
        self._conn = None
        self.db = None
        self.clusters = []
        self.movie_collection = None
        self.tv_collection = None

    async def _ensure_unique_tmdb_index(self, collection, label: str):
        """Enforce one document per tmdb_id per cluster.

        Older deployments only have a non-unique tmdb_id index, so it is
        dropped and recreated as unique. If duplicates already exist the
        unique creation fails (logged) and the plain index is restored.
        """
        try:
            info = await collection.index_information()
            has_unique = False
            for name, spec in info.items():
                if spec.get("key") == [("tmdb_id", 1)]:
                    if spec.get("unique"):
                        has_unique = True
                    else:
                        await collection.drop_index(name)
            if not has_unique:
                await collection.create_index(
                    [("tmdb_id", ASCENDING)], unique=True, name="uniq_tmdb_id"
                )
        except Exception as e:
            LOGGER.warning(
                f"Could not enforce unique tmdb_id on {label} (duplicates present?): {e}"
            )
            try:
                await collection.create_index([("tmdb_id", ASCENDING)])
            except Exception:
                pass

    async def _init_indexes(self):
        """Create indexes on every cluster + ops TTL/indexes on primary."""
        primary = self.clusters[0]

        # ── Per-cluster media indexes (routing + streaming lookups) ──
        for cluster in self.clusters:
            await cluster.movie.create_indexes([
                IndexModel([("telegram.id", ASCENDING)]),
                IndexModel([("telegram.file_unique_id", ASCENDING)]),
                IndexModel([("title", ASCENDING), ("release_year", ASCENDING)]),
            ])
            await cluster.tv.create_indexes([
                IndexModel([("seasons.episodes.telegram.id", ASCENDING)]),
                IndexModel([("seasons.episodes.telegram.file_unique_id", ASCENDING)]),
                IndexModel([("title", ASCENDING), ("release_year", ASCENDING)]),
            ])
            await self._ensure_unique_tmdb_index(cluster.movie, "movie")
            await self._ensure_unique_tmdb_index(cluster.tv, "tv")

        # ── Shard map ──
        await self.shard_map_collection.create_index(
            [("media_type", ASCENDING), ("tmdb_id", ASCENDING)], unique=True
        )

        # ── Channel index scans (one durable state per channel) ──
        await self.index_scans_collection.create_indexes([
            IndexModel([("channel_id", ASCENDING)], unique=True),
            IndexModel([("status", ASCENDING), ("updated_at", ASCENDING)]),
        ])

        # ── Views (trending) ──
        await self.views_collection.create_index([("date", DESCENDING), ("count", DESCENDING)])
        await self.views_collection.create_index([("tmdb_id", ASCENDING), ("media_type", ASCENDING), ("date", ASCENDING)])
        try:
            await self.views_collection.create_index(
                [("created_at", ASCENDING)], expireAfterSeconds=StorageConfig.VIEWS_TTL_DAYS * 86400
            )
        except Exception as e:
            LOGGER.warning(f"views TTL index: {e}")

        # ── replaced_versions — keep only 7 days ──
        await self.replaced_versions.create_index([("tmdb_id", ASCENDING)])
        try:
            await self.replaced_versions.create_index(
                [("replaced_at", ASCENDING)], expireAfterSeconds=7 * 86400, name="ttl_replaced_versions"
            )
        except Exception as e:
            LOGGER.warning(f"replaced_versions TTL: {e}")

        # ── failed_files — keep only 7 days ──
        await self.failed_files_collection.create_indexes([
            IndexModel([("timestamp", DESCENDING)]),
            IndexModel([("reason", ASCENDING)]),
            IndexModel([("tmdb_id", ASCENDING)]),
            IndexModel([("timestamp", ASCENDING)], expireAfterSeconds=7 * 86400, name="ttl_failed_files"),
        ])

        # ── Queue collections ──
        if QueueConfig.ENABLE_MONGODB_QUEUE:
            await self.queue_collection.create_indexes([
                IndexModel([("status", ASCENDING), ("priority", ASCENDING), ("created_at", ASCENDING)]),
                IndexModel([("channel_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)]),
                IndexModel([("status", ASCENDING), ("retry_count", ASCENDING), ("created_at", ASCENDING)]),
                IndexModel([("channel_id", ASCENDING), ("message_id", ASCENDING)], unique=True),
                # Terminal-only expiry: pending/processing items must never
                # vanish silently, so only finished items get expire_at set.
                IndexModel([("expire_at", ASCENDING)], expireAfterSeconds=0, name="ttl_queue_expire_at"),
            ])
            await self.processed_collection.create_indexes([
                IndexModel([("channel_id", ASCENDING), ("message_id", ASCENDING)], unique=True),
                IndexModel([("processed_at", ASCENDING)], expireAfterSeconds=max(QueueConfig.PROCESSED_TTL_DAYS, 1) * 86400),
            ])
            await self.checkpoint_collection.create_indexes([
                IndexModel([("channel_id", ASCENDING)], unique=True),
                IndexModel([("updated_at", ASCENDING)]),
            ])
            # file_unique_id is Telegram's stable, global identity for a file.
            try:
                await self.queue_collection.create_index(
                    [("file_unique_id", ASCENDING)], unique=True, sparse=True,
                    name="uniq_queue_file_uid",
                )
            except Exception as e:
                LOGGER.warning(f"Queue file_unique_id index: {e}")

            # Drop the legacy created_at TTL: it expired pending items too.
            try:
                info = await self.queue_collection.index_information()
                for name, spec in info.items():
                    if spec.get("key") == [("created_at", 1)] and "expireAfterSeconds" in spec:
                        await self.queue_collection.drop_index(name)
                        LOGGER.info(f"Dropped legacy queue TTL index {name}")
            except Exception as e:
                LOGGER.warning(f"Legacy queue TTL cleanup: {e}")

            # Proactive startup purge — only terminal items. Pending work is
            # never deleted just because the bot was offline for a while.
            try:
                cutoff = datetime.utcnow() - timedelta(days=max(QueueConfig.QUEUE_TTL_DAYS, 1))
                res = await self.queue_collection.delete_many({
                    "status": {"$in": ["completed", "failed"]},
                    "retry_count": {"$gte": QueueConfig.MAX_RETRY_COUNT},
                    "updated_at": {"$lt": cutoff},
                })
                if res.deleted_count:
                    LOGGER.info(f"🧹 Startup purge: removed {res.deleted_count} exhausted queue items (> {QueueConfig.QUEUE_TTL_DAYS}d)")
            except Exception as ce:
                LOGGER.warning(f"Startup queue cleanup failed (non-fatal): {ce}")

        LOGGER.info("All database indexes created")

    # =========================================================================
    # SHARD ROUTING
    # =========================================================================
    async def _record_shard(self, media_type: str, tmdb_id: int, cluster_index: int):
        key = f"{media_type}:{tmdb_id}"
        self._shard_cache[key] = cluster_index
        try:
            await self.shard_map_collection.update_one(
                {"media_type": media_type, "tmdb_id": tmdb_id},
                {"$set": {"cluster": cluster_index, "updated_at": datetime.utcnow()}},
                upsert=True,
            )
        except Exception as e:
            LOGGER.warning(f"shard_map record failed: {e}")

    async def _pick_cluster_for_new(self) -> int:
        """Pick the cluster with the most free space (cached), round-robin fallback."""
        now = time.time()
        best, best_free = None, -1
        for cluster in self.clusters:
            if now - cluster.free_ts > StorageConfig.SHARD_STATS_REFRESH_SECONDS:
                try:
                    stats = await cluster.db.command("dbStats")
                    total = stats.get("fsTotalSize") or stats.get("maxSize") or 0
                    used = stats.get("fsUsedSize") or stats.get("storageSize") or 0
                    cluster.free_bytes = max(total - used, 0)
                    cluster.free_ts = now
                except Exception as e:
                    cluster.free_bytes = 0
                    cluster.free_ts = now
                    LOGGER.warning(f"dbStats failed on cluster {cluster.index}: {e}")
            if cluster.free_bytes > best_free:
                best, best_free = cluster, cluster.free_bytes
        if best is not None and best_free > 0:
            return best.index - 1
        # Round-robin fallback (stats unavailable on every cluster)
        self._rr_counter = (self._rr_counter + 1) % len(self.clusters)
        return self._rr_counter

    def _deterministic_cluster_index(self, media_type: str, tmdb_id: int) -> int:
        """Stable placement: a title always lands on the same cluster.

        crc32 (not Python's hash()) so placement survives process restarts and
        is identical across dynos. This makes cross-cluster duplicates from
        concurrent inserts impossible for brand-new titles.
        """
        if not self.clusters:
            return 0
        key = f"{media_type}:{tmdb_id}".encode("utf-8")
        return zlib.crc32(key) % len(self.clusters)

    async def _find_media_doc(self, media_type: str, tmdb_id: int, projection: Optional[dict] = None):
        """Return (doc, cluster_list_index). Resolves shard map, falls back to scan."""
        key = f"{media_type}:{tmdb_id}"
        cached = self._shard_cache.get(key)
        order = []
        if cached is not None and 0 <= cached < len(self.clusters):
            order.append(cached)
        order.extend(i for i in range(len(self.clusters)) if i not in order)

        for i in order:
            try:
                doc = await self.clusters[i].collection(media_type).find_one({"tmdb_id": tmdb_id}, projection)
            except Exception as e:
                LOGGER.error(f"Shard lookup failed on cluster {i + 1}: {e}")
                continue
            if doc is not None:
                if cached != i:
                    self._shard_cache[key] = i
                    try:
                        await self.shard_map_collection.update_one(
                            {"media_type": media_type, "tmdb_id": tmdb_id},
                            {"$set": {"cluster": i + 1, "updated_at": datetime.utcnow()}},
                            upsert=True,
                        )
                    except Exception:
                        pass
                return doc, i
        return None, None

    # Public single-doc getters (used by bot + helpers)
    async def get_movie_doc(self, tmdb_id: int) -> Optional[dict]:
        doc, _ = await self._find_media_doc("movie", tmdb_id)
        return doc

    async def get_tv_doc(self, tmdb_id: int) -> Optional[dict]:
        doc, _ = await self._find_media_doc("tv", tmdb_id)
        return doc

    async def get_media_doc(self, media_type: str, tmdb_id: int) -> Optional[dict]:
        if media_type == "movie":
            return await self.get_movie_doc(tmdb_id)
        return await self.get_tv_doc(tmdb_id)

    async def reset_all_databases(self) -> Tuple[int, List[str]]:
        """Wipe all user collections on EVERY cluster (admin /resetdb)."""
        deleted = 0
        names = []
        for cluster in self.clusters:
            try:
                cols = await cluster.db.list_collection_names()
                for col_name in cols:
                    if col_name.startswith("system."):
                        continue
                    res = await cluster.db[col_name].delete_many({})
                    deleted += res.deleted_count
                    names.append(f"cluster{cluster.index}:{col_name}")
            except Exception as e:
                LOGGER.error(f"reset failed on cluster {cluster.index}: {e}")
        self._shard_cache.clear()
        LOGGER.warning(f"🗑️ Full reset: {deleted} docs from {len(names)} collections")
        return deleted, names

    # =========================================================================
    # MEDIA CRUD (shard-aware)
    # =========================================================================
    @staticmethod
    def _convert_object_id(document: dict) -> dict:
        if "_id" in document:
            document["_id"] = str(document["_id"])
        return document

    @staticmethod
    def sort_by_codec_preference(quality_details: List[Dict]) -> List[Dict]:
        def get_codec_priority(item: Dict) -> int:
            codec = item.get('codec', 'other').lower()
            return CODEC_PRIORITY.get(codec, 0)
        return sorted(quality_details, key=get_codec_priority, reverse=True)

    @staticmethod
    def _quality_rank(quality: Optional[str]) -> int:
        value = str(quality or "").upper()
        if "4K" in value or "2160" in value:
            return 2160
        if "1440" in value or "2K" in value:
            return 1440
        match = re.search(r"(\d{3,4})", value)
        return int(match.group(1)) if match else 0

    @classmethod
    def _cap_qualities(cls, qualities: List[Dict], new_quality: Dict) -> List[Dict]:
        """Bound the number of qualities per title, keeping the newest upload.

        Without a cap, rescans of large channels can append dozens of near-
        identical files per episode and quietly eat the Atlas quota.
        """
        cap = QueueConfig.MAX_QUALITIES_PER_TITLE
        if cap <= 0 or len(qualities) <= cap:
            return qualities
        new_id = new_quality.get("id")
        others = sorted(
            (q for q in qualities if q.get("id") != new_id),
            key=lambda q: cls._quality_rank(q.get("quality")),
            reverse=True,
        )
        return others[: max(cap - 1, 0)] + [new_quality]

    async def update_tv_show(self, tv_show_data: TVShowSchema) -> Optional[ObjectId]:
        try:
            tv_show_dict = tv_show_data.model_dump()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None

        tmdb_id = tv_show_dict["tmdb_id"]
        existing_media, idx = await self._find_media_doc("tv", tmdb_id)

        if existing_media is None:
            res = await self.tv_collection.insert_one(tv_show_dict)
            return res.inserted_id

        updated = False
        for season in tv_show_dict["seasons"]:
            existing_season = next(
                (s for s in existing_media["seasons"]
                 if s["season_number"] == season["season_number"]), None)

            if existing_season:
                for episode in season["episodes"]:
                    existing_episode = next(
                        (e for e in existing_season["episodes"]
                         if e["episode_number"] == episode["episode_number"]), None)

                    if existing_episode:
                        for quality in episode["telegram"]:
                            existing_file = next(
                                (q for q in existing_episode["telegram"]
                                 if q["id"] == quality["id"]), None)
                            if existing_file:
                                LOGGER.debug(f"File {quality['id']} already exists in TV {tmdb_id} S{season['season_number']}E{episode['episode_number']}")
                                continue
                            existing_quality = next(
                                (q for q in existing_episode["telegram"]
                                 if q["quality"] == quality["quality"]), None)
                            if existing_quality:
                                existing_quality.update(quality)
                            else:
                                existing_episode["telegram"].append(quality)
                            existing_episode["telegram"] = self._cap_qualities(
                                existing_episode["telegram"], existing_quality or quality
                            )
                            updated = True
                    else:
                        existing_season["episodes"].append(episode)
                        updated = True
            else:
                existing_media["seasons"].append(season)
                updated = True

        if updated:
            existing_media["updated_on"] = datetime.utcnow()
            existing_media["languages"] = tv_show_dict["languages"]
            existing_media["rip"] = tv_show_dict["rip"]
            await self.clusters[idx].tv.replace_one({"tmdb_id": tmdb_id}, existing_media)
            return existing_media["_id"]

        LOGGER.info(f"No updates made for TV show: {tmdb_id}")
        return existing_media["_id"]

    async def update_movie(self, movie_data: MovieSchema) -> Optional[ObjectId]:
        if self.movie_collection is None:
            LOGGER.error("Database collection is not initialized. Did you call db.connect()?")
            return None
        try:
            movie_dict = movie_data.model_dump()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None

        tmdb_id = movie_dict["tmdb_id"]
        existing_media, idx = await self._find_media_doc("movie", tmdb_id)

        if existing_media is None:
            res = await self.movie_collection.insert_one(movie_dict)
            return res.inserted_id

        updated = False
        for quality in movie_dict["telegram"]:
            existing_file = next(
                (q for q in existing_media["telegram"]
                 if q["id"] == quality["id"]), None)
            if existing_file:
                LOGGER.debug(f"File {quality['id']} already exists in movie {tmdb_id}")
                continue
            existing_quality = next(
                (q for q in existing_media["telegram"]
                 if q["quality"] == quality["quality"]), None)
            if existing_quality:
                existing_quality.update(quality)
            else:
                existing_media["telegram"].append(quality)
            existing_media["telegram"] = self._cap_qualities(
                existing_media["telegram"], existing_quality or quality
            )
            updated = True

        if updated:
            existing_media["updated_on"] = datetime.utcnow()
            existing_media["languages"] = movie_dict["languages"]
            existing_media["rip"] = movie_dict["rip"]
            if movie_dict.get("cast"):
                existing_media["cast"] = movie_dict["cast"]
            await self.clusters[idx].movie.replace_one({"tmdb_id": tmdb_id}, existing_media)
            return existing_media["_id"]

        LOGGER.info(f"No updates made for movie: {tmdb_id}")
        return existing_media["_id"]

    async def insert_media(
        self,
        metadata_info: dict,
        hash: str,
        channel: int,
        msg_id: int,
        size: str,
        name: str,
        codec: Optional[str] = None,
        file_unique_id: Optional[str] = None,
        size_bytes: Optional[int] = None,
        duration: Optional[int] = None,
    ) -> Optional[ObjectId]:
        data = {"chat_id": channel, "msg_id": msg_id, "hash": hash}
        encoded_string = await encode_string(data)

        if metadata_info['media_type'] == "movie":
            media = MovieSchema(
                tmdb_id=metadata_info['tmdb_id'],
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                runtime=metadata_info['runtime'],
                media_type=metadata_info['media_type'],
                languages=metadata_info['languages'],
                rip=metadata_info['rip'],
                telegram=[
                    QualityDetail(
                        quality=metadata_info['quality'],
                        id=encoded_string,
                        name=name,
                        size=size,
                        codec=codec,
                        file_unique_id=file_unique_id,
                        size_bytes=size_bytes,
                        duration=duration,
                        audio_tracks=metadata_info.get('audio_tracks'),
                        subtitle_tracks=metadata_info.get('subtitle_tracks')
                    )],
                cast=metadata_info.get('cast')
            )
            return await self.update_movie(media)
        else:
            tv_show = TVShowSchema(
                tmdb_id=metadata_info['tmdb_id'],
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                media_type=metadata_info['media_type'],
                status=metadata_info['status'],
                total_seasons=metadata_info['total_seasons'],
                total_episodes=metadata_info['total_episodes'],
                languages=metadata_info['languages'],
                rip=metadata_info['rip'],
                seasons=[
                    Season(
                        season_number=metadata_info['season_number'],
                        episodes=[
                            Episode(
                                episode_number=metadata_info['episode_number'],
                                title=metadata_info['episode_title'],
                                episode_backdrop=metadata_info['episode_backdrop'],
                                telegram=[
                                    QualityDetail(
                                        quality=metadata_info['quality'],
                                        id=encoded_string,
                                        name=name,
                                        size=size,
                                        codec=codec,
                                        file_unique_id=file_unique_id,
                                        size_bytes=size_bytes,
                                        duration=duration,
                                        audio_tracks=metadata_info.get('audio_tracks'),
                                        subtitle_tracks=metadata_info.get('subtitle_tracks')
                                    )
                                ]
                            )
                        ]
                    )
                ]
            )
            return await self.update_tv_show(tv_show)

    async def is_file_exists(self, channel: int, msg_id: int, hash: str) -> bool:
        """Check if a file (by channel + msg_id + hash) exists on ANY cluster."""
        try:
            data = {"chat_id": channel, "msg_id": msg_id, "hash": hash}
            encoded_id = await encode_string(data)
            movie, tv = await asyncio.gather(
                self.movie_collection.find_one({"telegram.id": encoded_id}, {"_id": 1}),
                self.tv_collection.find_one({"seasons.episodes.telegram.id": encoded_id}, {"_id": 1}),
                return_exceptions=True,
            )
            return bool(movie and not isinstance(movie, Exception)) or bool(
                tv and not isinstance(tv, Exception)
            )
        except Exception as e:
            LOGGER.error(f"Error checking file existence: {e}")
            return False

    async def is_file_exists_strict(self, channel: int, msg_id: int, hash: str) -> bool:
        """Like is_file_exists, but raises when the check itself fails.

        Long index scans use this so a DB outage retries the batch instead of
        silently skipping every file until the outage ends.
        """
        data = {"chat_id": channel, "msg_id": msg_id, "hash": hash}
        encoded_id = await encode_string(data)
        movie, tv = await asyncio.gather(
            self.movie_collection.find_one({"telegram.id": encoded_id}, {"_id": 1}),
            self.tv_collection.find_one({"seasons.episodes.telegram.id": encoded_id}, {"_id": 1}),
            return_exceptions=True,
        )
        if movie and not isinstance(movie, Exception):
            return True
        if tv and not isinstance(tv, Exception):
            return True
        for result in (movie, tv):
            if isinstance(result, Exception):
                raise result
        return False

    async def get_quality_details(
        self,
        tmdb_id: int,
        quality: str,
        season: Optional[int] = None,
        episode: Optional[int] = None
    ) -> List[Dict[str, int]]:
        if season is None:
            doc = await self.movie_collection.find_one(
                {"tmdb_id": tmdb_id}, {"telegram": 1}
            )
            if not doc:
                return []
            return [
                {"id": item["id"], "name": item["name"]}
                for item in doc.get("telegram", [])
                if item["quality"] == quality
            ]
        else:
            doc = await self.tv_collection.find_one(
                {"tmdb_id": tmdb_id}, {"seasons": 1}
            )
            if not doc:
                return []
            results = []
            for s in doc.get("seasons", []):
                if s["season_number"] == season:
                    episodes = s.get("episodes", [])
                    if episode is not None:
                        episodes = [ep for ep in episodes if ep["episode_number"] == episode]
                    for ep in episodes:
                        results.extend([
                            {"id": t["id"], "name": t["name"]}
                            for t in ep.get("telegram", [])
                            if t["quality"] == quality
                        ])
            return results

    async def delete_document(self, media_type: str, tmdb_id: int) -> bool:
        if media_type == "movie":
            result = await self.movie_collection.delete_one({"tmdb_id": tmdb_id})
        else:
            result = await self.tv_collection.delete_one({"tmdb_id": tmdb_id})
        if result.deleted_count > 0:
            LOGGER.info(f"{media_type} with tmdb_id {tmdb_id} deleted successfully.")
            return True
        LOGGER.info(f"No document found with tmdb_id {tmdb_id}.")
        return False

    async def pull_quality(
        self,
        media_type: str,
        tmdb_id: int,
        quality: str,
        season: Optional[int] = None,
        episode: Optional[int] = None
    ) -> bool:
        """Remove all entries of a quality (used before a REPLACE insert)."""
        try:
            if media_type == "movie":
                res = await self.movie_collection.update_one(
                    {"tmdb_id": tmdb_id},
                    {"$pull": {"telegram": {"quality": quality}}}
                )
            else:
                res = await self.tv_collection.update_one(
                    {"tmdb_id": tmdb_id},
                    {"$pull": {"seasons.$[s].episodes.$[e].telegram": {"quality": quality}}},
                    array_filters=[
                        {"s.season_number": season},
                        {"e.episode_number": episode},
                    ] if season is not None and episode is not None else None,
                )
            return res.modified_count > 0
        except Exception as e:
            LOGGER.error(f"pull_quality failed for {media_type} {tmdb_id}: {e}")
            return False

    # =========================================================================
    # LISTINGS / SEARCH — fan-out + merge across clusters
    # =========================================================================
    @staticmethod
    def _match_conditions(genre=None, language=None, min_rating=None) -> dict:
        cond: dict = {}
        if genre:
            # Match any element of the genres array — "genres.0" only looked
            # at the first genre, hiding media whose genre was not first.
            cond["genres"] = {"$in": [genre, genre.lower(), genre.title(), genre.upper()]}
        if language:
            cond["languages"] = {"$in": [language, language.lower(), language.upper()]}
        if min_rating is not None:
            cond["rating"] = {"$gte": min_rating}
        return cond

    async def _paged_fanout(
        self,
        media_type: str,
        match: dict,
        sort_list: List[Tuple[str, int]],
        page: int,
        page_size: int,
        projection: Optional[dict] = None,
    ) -> Tuple[int, List[dict]]:
        """Fetch (skip+limit) sorted docs per cluster, merge globally."""
        fetch = (page - 1) * page_size + page_size
        total = 0
        merged: List[dict] = []
        for cluster in self.clusters:
            col = cluster.collection(media_type)
            try:
                total += await col.count_documents(match)
                cur = col.find(match, projection).sort(sort_list).limit(fetch)
                merged.extend(await cur.to_list(fetch))
            except Exception as e:
                LOGGER.error(f"Fan-out query failed on cluster {cluster.index}/{media_type}: {e}")
        import functools

        def cmp(a, b):
            for field, direction in sort_list:
                va, vb = a.get(field), b.get(field)
                if va == vb:
                    continue
                if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                    r = 1 if va > vb else -1
                else:
                    va_s, vb_s = str(va or ""), str(vb or "")
                    r = 1 if va_s > vb_s else -1
                return r * (1 if direction == ASCENDING else -1)
            return 0

        merged.sort(key=functools.cmp_to_key(cmp))
        skip = (page - 1) * page_size
        return total, merged[skip:skip + page_size]

    async def sort_tv_shows(
        self,
        sort_params: List[Tuple[str, str]],
        page: int,
        page_size: int,
        genre: Optional[str] = None,
        language: Optional[str] = None,
        min_rating: Optional[float] = None
    ) -> dict:
        match = self._match_conditions(genre, language, min_rating)
        sort_list = [(field, ASCENDING if d == "asc" else DESCENDING) for field, d in sort_params]
        total_count, docs = await self._paged_fanout("tv", match, sort_list, page, page_size)
        return {"total_count": total_count, "tv_shows": [TVShowSchema(**doc) for doc in docs]}

    async def sort_movies(
        self,
        sort_params: List[Tuple[str, str]],
        page: int,
        page_size: int,
        genre: Optional[str] = None,
        language: Optional[str] = None,
        min_rating: Optional[float] = None
    ) -> dict:
        match = self._match_conditions(genre, language, min_rating)
        sort_list = [(field, ASCENDING if d == "asc" else DESCENDING) for field, d in sort_params]
        total_count, docs = await self._paged_fanout("movie", match, sort_list, page, page_size)
        return {"total_count": total_count, "movies": [MovieSchema(**doc) for doc in docs]}

    async def find_similar_media(
        self,
        tmdb_id: int,
        media_type: str,
        page: int = 1,
        page_size: int = 10
    ) -> dict:
        collection = "movie" if media_type == "movie" else "tv"
        parent_media, _ = await self._find_media_doc(collection, tmdb_id)
        if not parent_media:
            raise HTTPException(status_code=404, detail="Media not found")

        parent_genres = parent_media.get("genres", [])
        if not parent_genres:
            return {"total_count": 0, "similar_media": []}

        match = {"tmdb_id": {"$ne": tmdb_id}, "genres": {"$in": parent_genres}}
        total = 0
        candidates: List[dict] = []
        for cluster in self.clusters:
            col = cluster.collection(collection)
            try:
                total += await col.count_documents(match)
                cur = col.find(match, {"genres": 1, "rating": 1, "tmdb_id": 1, "title": 1,
                                       "release_year": 1, "poster": 1, "backdrop": 1,
                                       "description": 1, "media_type": 1}).limit(500)
                candidates.extend(await cur.to_list(500))
            except Exception as e:
                LOGGER.error(f"Similar media fan-out failed on cluster {cluster.index}: {e}")

        for c in candidates:
            c["genreMatchCount"] = len(set(c.get("genres", [])) & set(parent_genres))
        candidates.sort(key=lambda x: (x.get("genreMatchCount", 0), x.get("rating") or 0), reverse=True)

        skip = (page - 1) * page_size
        similar_media = [self._convert_object_id(doc) for doc in candidates[skip:skip + page_size]]
        return {"total_count": total, "similar_media": similar_media}

    async def search_documents(self, query: str, page: int, page_size: int) -> dict:
        skip = (page - 1) * page_size
        clean = (query or "").strip()
        if not clean:
            return {"total_count": 0, "results": []}

        # Escape user input — raw regex here allowed errors (e.g. a lone
        # "(") and catastrophic backtracking. Cap words/length as well.
        words = [re.escape(w) for w in clean.split()[:8]]
        pattern = ".*".join(words)[:200]
        regex_query = {"$regex": pattern, "$options": "i"}
        fetch = max(skip + page_size, page_size)

        tv_projection = {
            "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1,
            "release_year": 1, "poster": 1, "backdrop": 1, "description": 1,
            "total_seasons": 1, "total_episodes": 1, "media_type": 1
        }
        movie_projection = {
            "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1,
            "release_year": 1, "poster": 1, "backdrop": 1, "description": 1,
            "media_type": 1
        }

        total = 0
        combined: List[dict] = []
        for cluster in self.clusters:
            try:
                tv_match = {"$or": [
                    {"title": regex_query},
                    {"seasons.episodes.telegram.name": regex_query},
                ]}
                mov_match = {"$or": [
                    {"title": regex_query},
                    {"telegram.name": regex_query},
                ]}
                total += await cluster.tv.count_documents(tv_match)
                total += await cluster.movie.count_documents(mov_match)
                # Bounded fetch: only the rows that can appear on this page
                # are read, merged and sorted deterministically for paging.
                tv_res = await cluster.tv.find(tv_match, tv_projection).sort("title", ASCENDING).limit(fetch).to_list(fetch)
                mov_res = await cluster.movie.find(mov_match, movie_projection).sort("title", ASCENDING).limit(fetch).to_list(fetch)
                combined.extend(tv_res + mov_res)
            except Exception as e:
                LOGGER.error(f"Search fan-out failed on cluster {cluster.index}: {e}")

        combined.sort(key=lambda d: (str(d.get("title") or "").lower(), str(d.get("media_type") or "")))
        return {
            "total_count": total,
            "results": [self._convert_object_id(doc) for doc in combined[skip:skip + page_size]]
        }

    async def get_media_details(
        self,
        tmdb_id: int,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None
    ) -> Optional[dict]:
        if episode_number is not None and season_number is not None:
            tv_show, _ = await self._find_media_doc("tv", tmdb_id)
            if not tv_show:
                return None
            for season in tv_show.get("seasons", []):
                if season.get("season_number") == season_number:
                    for episode in season.get("episodes", []):
                        if episode.get("episode_number") == episode_number:
                            details = self._convert_object_id(episode)
                            details.update({
                                "tmdb_id": tmdb_id,
                                "type": "tv",
                                "season_number": season_number,
                                "episode_number": episode_number,
                                "backdrop": episode.get("episode_backdrop")
                            })
                            return details
            return None

        elif season_number is not None:
            tv_show, _ = await self._find_media_doc("tv", tmdb_id)
            if not tv_show:
                return None
            for season in tv_show.get("seasons", []):
                if season.get("season_number") == season_number:
                    details = self._convert_object_id(season)
                    details.update({
                        "tmdb_id": tmdb_id,
                        "type": "tv",
                        "season_number": season_number
                    })
                    return details
            return None

        else:
            tv_doc, _ = await self._find_media_doc("tv", tmdb_id)
            if tv_doc:
                tv_doc = self._convert_object_id(tv_doc)
                tv_doc["type"] = "tv"
                if not tv_doc.get("cast"):
                    try:
                        from Backend.helper.metadata import get_tmdb_people_and_schedule
                        compact = await get_tmdb_people_and_schedule(tmdb_id, "tv")
                        if compact.get("cast"):
                            tv_doc["cast"] = compact["cast"]
                            asyncio.create_task(self.tv_collection.update_one(
                                {"tmdb_id": tmdb_id}, {"$set": {"cast": compact["cast"]}}))
                    except Exception as e:
                        LOGGER.warning(f"Failed to fetch cast fallback for tv {tmdb_id}: {e}")
                return tv_doc

            movie_doc, _ = await self._find_media_doc("movie", tmdb_id)
            if movie_doc:
                movie_doc = self._convert_object_id(movie_doc)
                movie_doc["type"] = "movie"
                if not movie_doc.get("cast"):
                    try:
                        from Backend.helper.metadata import get_tmdb_people_and_schedule
                        compact = await get_tmdb_people_and_schedule(tmdb_id, "movie")
                        if compact.get("cast"):
                            movie_doc["cast"] = compact["cast"]
                            asyncio.create_task(self.movie_collection.update_one(
                                {"tmdb_id": tmdb_id}, {"$set": {"cast": compact["cast"]}}))
                    except Exception as e:
                        LOGGER.warning(f"Failed to fetch cast fallback for movie {tmdb_id}: {e}")
                return movie_doc

            return None

    # =========================================================================
    # VIEWS / TRENDING (views live on primary)
    # =========================================================================
    async def track_view(self, tmdb_id: int, media_type: str) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        await self.views_collection.update_one(
            {"tmdb_id": tmdb_id, "media_type": media_type, "date": today},
            {"$inc": {"count": 1},
             "$setOnInsert": {
                 "tmdb_id": tmdb_id, "media_type": media_type,
                 "date": today, "created_at": datetime.utcnow()
             }},
            upsert=True
        )

    async def get_trending_today(self, limit: int = 10) -> dict:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        pipeline = [
            {"$match": {"date": today}},
            {"$sort": {"count": DESCENDING}},
            {"$limit": limit * 2},
            {"$group": {
                "_id": {"tmdb_id": "$tmdb_id", "media_type": "$media_type"},
                "count": {"$max": "$count"}
            }},
            {"$sort": {"count": DESCENDING}},
            {"$limit": limit}
        ]
        trending_views = await self.views_collection.aggregate(pipeline).to_list(None)

        movies, tv_shows = [], []
        for item in trending_views:
            tmdb_id = item["_id"]["tmdb_id"]
            media_type = item["_id"]["media_type"]
            count = item["count"]
            if media_type == "movie":
                doc = await self.get_movie_doc(tmdb_id)
            else:
                doc = await self.get_tv_doc(tmdb_id)
            if doc:
                doc = self._convert_object_id(doc)
                doc["view_count"] = count
                (movies if media_type == "movie" else tv_shows).append(doc)

        return {"movies": movies, "tv_shows": tv_shows}

    async def get_most_viewed(self, limit: int = 10) -> dict:
        pipeline = [
            {"$group": {
                "_id": {"tmdb_id": "$tmdb_id", "media_type": "$media_type"},
                "total_count": {"$sum": "$count"}
            }},
            {"$sort": {"total_count": DESCENDING}},
            {"$limit": limit}
        ]
        trending_views = await self.views_collection.aggregate(pipeline).to_list(None)

        movies, tv_shows = [], []
        for item in trending_views:
            tmdb_id = item["_id"]["tmdb_id"]
            media_type = item["_id"]["media_type"]
            count = item["total_count"]
            if media_type == "movie":
                doc = await self.get_movie_doc(tmdb_id)
            else:
                doc = await self.get_tv_doc(tmdb_id)
            if doc:
                doc = self._convert_object_id(doc)
                doc["view_count"] = count
                (movies if media_type == "movie" else tv_shows).append(doc)

        return {"movies": movies, "tv_shows": tv_shows}

    # =========================================================================
    # SECTION LISTINGS (anime / kdrama / genre / new episodes)
    # =========================================================================
    async def get_anime(self, limit: int = 20, page: int = 1) -> dict:
        match = {"genres": {"$in": ANIME_GENRES}}
        sort_list = [("rating", DESCENDING), ("release_year", DESCENDING)]
        movie_total, movie_docs = await self._paged_fanout("movie", match, sort_list, page, limit)
        tv_total, tv_docs = await self._paged_fanout("tv", match, sort_list, page, limit)
        movies = [self._convert_object_id(d) for d in movie_docs]
        tv_shows = [self._convert_object_id(d) for d in tv_docs]
        combined = movies + tv_shows
        combined.sort(key=lambda x: (x.get("rating") or 0), reverse=True)
        return {"total_count": movie_total + tv_total, "results": combined[:limit]}

    async def get_kdrama(self, limit: int = 20, page: int = 1) -> dict:
        match = {
            "languages": {"$in": ["Korean", "ko", "korean"]},
            "genres": {"$in": ["Drama", "Korean Drama"]}
        }
        sort_list = [("rating", DESCENDING), ("release_year", DESCENDING)]
        total_count, docs = await self._paged_fanout("tv", match, sort_list, page, limit)
        return {"total_count": total_count, "tv_shows": [self._convert_object_id(d) for d in docs]}

    async def get_media_by_genre(
        self,
        genre: str,
        media_type: str = "all",
        limit: int = 20,
        page: int = 1
    ) -> dict:
        match = {"genres": {"$regex": f"^{genre}$", "$options": "i"}}
        sort_list = [("rating", DESCENDING), ("release_year", DESCENDING)]
        results: dict = {}
        total_count = 0

        if media_type in ["all", "movie"]:
            m_total, m_docs = await self._paged_fanout("movie", match, sort_list, page, limit)
            results["movies"] = [self._convert_object_id(d) for d in m_docs]
            total_count += m_total
        else:
            results["movies"] = []

        if media_type in ["all", "tv"]:
            t_total, t_docs = await self._paged_fanout("tv", match, sort_list, page, limit)
            results["tv_shows"] = [self._convert_object_id(d) for d in t_docs]
            total_count += t_total
        else:
            results["tv_shows"] = []

        if media_type == "all":
            combined = results["movies"] + results["tv_shows"]
            combined.sort(key=lambda x: (x.get("rating") or 0), reverse=True)
            results["results"] = combined[:limit]
        else:
            results["results"] = results["movies"] if media_type == "movie" else results["tv_shows"]

        results["total_count"] = total_count
        return results

    async def get_new_episodes(self, limit: int = 20, page: int = 1) -> dict:
        """Recently updated TV shows. NOTE: matches media_type (docs don't have 'type')."""
        match = {"media_type": "tv"}
        sort_list = [("updated_on", DESCENDING)]
        total_count, docs = await self._paged_fanout("tv", match, sort_list, page, limit)
        return {"total_count": total_count, "tv_shows": [self._convert_object_id(d) for d in docs]}

    # =========================================================================
    # AUDIO / SUBTITLE TRACK CACHE (shard-aware)
    # =========================================================================
    async def update_movie_audio_tracks(self, tmdb_id: int, quality: str, audio_tracks: List[Dict]) -> bool:
        try:
            doc, idx = await self._find_media_doc("movie", tmdb_id)
            if doc is None:
                return False
            res = await self.clusters[idx].movie.update_one(
                {"tmdb_id": tmdb_id, "telegram.quality": quality},
                {"$set": {"telegram.$.audio_tracks": audio_tracks}}
            )
            if res.modified_count > 0:
                LOGGER.info(f"Cached audio tracks for movie {tmdb_id}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating movie audio tracks: {e}")
            return False

    async def update_tv_episode_audio_tracks(
        self, tmdb_id: int, season_number: int, episode_number: int, quality: str, audio_tracks: List[Dict]
    ) -> bool:
        try:
            doc, idx = await self._find_media_doc("tv", tmdb_id)
            if doc is None:
                return False
            res = await self.clusters[idx].tv.update_one(
                {
                    "tmdb_id": tmdb_id,
                    "seasons.season_number": season_number,
                    "seasons.episodes.episode_number": episode_number,
                    "seasons.episodes.telegram.quality": quality
                },
                {"$set": {"seasons.$[s].episodes.$[e].telegram.$[t].audio_tracks": audio_tracks}},
                array_filters=[
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                    {"t.quality": quality}
                ]
            )
            if res.modified_count > 0:
                LOGGER.info(f"Cached audio tracks for TV {tmdb_id} S{season_number}E{episode_number}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating TV episode audio tracks: {e}")
            return False

    async def update_movie_subtitle_tracks(self, tmdb_id: int, quality: str, subtitle_tracks: List[Dict]) -> bool:
        try:
            doc, idx = await self._find_media_doc("movie", tmdb_id)
            if doc is None:
                return False
            res = await self.clusters[idx].movie.update_one(
                {"tmdb_id": tmdb_id, "telegram.quality": quality},
                {"$set": {"telegram.$.subtitle_tracks": subtitle_tracks}}
            )
            if res.modified_count > 0:
                LOGGER.info(f"Cached subtitle tracks for movie {tmdb_id}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating movie subtitle tracks: {e}")
            return False

    async def update_tv_episode_subtitle_tracks(
        self, tmdb_id: int, season_number: int, episode_number: int, quality: str, subtitle_tracks: List[Dict]
    ) -> bool:
        try:
            doc, idx = await self._find_media_doc("tv", tmdb_id)
            if doc is None:
                return False
            res = await self.clusters[idx].tv.update_one(
                {
                    "tmdb_id": tmdb_id,
                    "seasons.season_number": season_number,
                    "seasons.episodes.episode_number": episode_number,
                    "seasons.episodes.telegram.quality": quality
                },
                {"$set": {"seasons.$[s].episodes.$[e].telegram.$[t].subtitle_tracks": subtitle_tracks}},
                array_filters=[
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                    {"t.quality": quality}
                ]
            )
            if res.modified_count > 0:
                LOGGER.info(f"Cached subtitle tracks for TV {tmdb_id} S{season_number}E{episode_number}, quality {quality}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error updating TV episode subtitle tracks: {e}")
            return False

    # =========================================================================
    # FILE-ID LOOKUP (streaming hot path — fan-out, indexed)
    # =========================================================================
    async def get_media_with_tracks(self, file_id: str) -> Optional[Dict]:
        """Find media containing file_id across ALL clusters in parallel."""
        try:
            tasks = []
            for cluster in self.clusters:
                tasks.append(cluster.movie.find_one(
                    {"telegram.id": file_id},
                    {"telegram.$": 1, "tmdb_id": 1, "title": 1, "media_type": 1}
                ))
                tasks.append(cluster.tv.find_one(
                    {"seasons.episodes.telegram.id": file_id},
                    {"seasons.episodes": 1, "tmdb_id": 1, "title": 1, "media_type": 1}
                ))
            results = await asyncio.gather(*tasks, return_exceptions=True)

            movie_hit = None
            tv_hit = None
            for r in results:
                if isinstance(r, Exception):
                    LOGGER.error(f"get_media_with_tracks fan-out error: {r}")
                elif r is not None:
                    if "seasons" in r:
                        tv_hit = r
                    else:
                        movie_hit = r

            if movie_hit and movie_hit.get("telegram"):
                quality_detail = movie_hit["telegram"][0]
                return {
                    "media_type": "movie",
                    "tmdb_id": movie_hit.get("tmdb_id"),
                    "title": movie_hit.get("title"),
                    "quality": quality_detail.get("quality"),
                    "file_name": quality_detail.get("name"),
                    "audio_tracks": quality_detail.get("audio_tracks"),
                    "subtitle_tracks": quality_detail.get("subtitle_tracks")
                }

            if tv_hit:
                for season in tv_hit.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for quality in episode.get("telegram", []):
                            if quality.get("id") == file_id:
                                return {
                                    "media_type": "tv",
                                    "tmdb_id": tv_hit.get("tmdb_id"),
                                    "title": tv_hit.get("title"),
                                    "season_number": season.get("season_number"),
                                    "episode_number": episode.get("episode_number"),
                                    "quality": quality.get("quality"),
                                    "file_name": quality.get("name"),
                                    "audio_tracks": quality.get("audio_tracks"),
                                    "subtitle_tracks": quality.get("subtitle_tracks")
                                }
            return None
        except Exception as e:
            LOGGER.error(f"Error getting media with tracks: {e}")
            return None

    # Backward compatibility alias
    async def get_media_with_audio_tracks(self, file_id: str) -> Optional[Dict]:
        return await self.get_media_with_tracks(file_id)

    # =========================================================================
    # MONGODB QUEUE MANAGEMENT (primary cluster only)
    # =========================================================================
    async def is_file_unique_id_known(self, file_unique_id: Optional[str]) -> bool:
        """True if any media quality document already references this file.

        Telegram's file_unique_id is stable across re-uploads/forwards, unlike
        chat+message ids, so this catches the same file arriving via a second
        channel or a rescan from a different offset.
        """
        if not file_unique_id:
            return False
        try:
            movie_res, tv_res = await asyncio.gather(
                self.movie_collection.find_one(
                    {"telegram.file_unique_id": file_unique_id}, {"_id": 1}
                ),
                self.tv_collection.find_one(
                    {"seasons.episodes.telegram.file_unique_id": file_unique_id}, {"_id": 1}
                ),
                return_exceptions=True,
            )
            return bool(movie_res) or bool(tv_res)
        except Exception as e:
            LOGGER.error(f"file_unique_id lookup failed: {e}")
            return False

    async def add_to_queue(self, channel_id: int, message_id: int, metadata_info: dict,
                           hash_val: str, channel: str, msg_id: int, size: str,
                           title: str, probe_result: dict = None,
                           file_unique_id: str = None, size_bytes: int = None,
                           duration: int = None, priority: int = None,
                           needs_review: bool = False) -> bool:
        """Add file to processing queue. Docs are COMPACTED to save space."""
        try:
            if await self.is_file_unique_id_known(file_unique_id):
                LOGGER.info(f"file_unique_id {file_unique_id} already in media — skipping queue")
                return False

            processed = await self.processed_collection.find_one({
                "channel_id": channel_id,
                "message_id": message_id
            })
            if processed:
                LOGGER.debug(f"Message {message_id} already processed, skipping queue")
                return False

            now = datetime.utcnow()
            doc = {
                "channel_id": channel_id,
                "message_id": message_id,
                "metadata_info": compact_metadata_for_queue(metadata_info),
                "hash": hash_val,
                "channel": channel,
                "msg_id": msg_id,
                "size": size,
                "size_bytes": int(size_bytes) if size_bytes else None,
                "duration": int(duration) if duration else None,
                "title": title,
                "probe_result": compact_probe_result(probe_result),
                "status": "pending",
                "priority": QueueConfig.PRIORITY_LIVE if priority is None else int(priority),
                "needs_review": bool(needs_review),
                "retry_count": 0,
                "error": None,
                "created_at": now,
                "updated_at": now
            }
            # Omit null so the unique sparse index never collides on missing ids.
            if file_unique_id:
                doc["file_unique_id"] = file_unique_id
            await self.queue_collection.insert_one(doc)
            self._queue_channels_ts = 0.0
            return True
        except DuplicateKeyError:
            LOGGER.debug(f"Message {message_id} / file {file_unique_id} already in queue")
            return False
        except Exception as e:
            # Propagate real write failures (DB down, quota) instead of
            # reporting them as a duplicate; callers already catch exceptions.
            LOGGER.error(f"Failed to add to queue: {e}")
            raise

    async def _claim_pending(self, channel_id: Optional[int], claimed_by: str) -> Optional[Dict]:
        query = {
            "status": "pending",
            "retry_count": {"$lt": QueueConfig.MAX_RETRY_COUNT},
        }
        if channel_id:
            query["channel_id"] = channel_id
        try:
            return await self.queue_collection.find_one_and_update(
                query,
                {"$set": {
                    "status": "processing",
                    "claimed_at": datetime.utcnow(),
                    "claimed_by": claimed_by,
                    "updated_at": datetime.utcnow()
                }},
                sort=[("priority", ASCENDING), ("created_at", ASCENDING)],
                return_document=ReturnDocument.AFTER
            )
        except Exception as e:
            LOGGER.error(f"Failed to claim queue item: {e}")
            return None

    async def _pending_channels(self) -> List[int]:
        now = time.time()
        if now - self._queue_channels_ts < QueueConfig.QUEUE_CHANNEL_REFRESH_SECONDS:
            return list(self._queue_channels_cache)
        try:
            channels = await self.queue_collection.distinct("channel_id", {"status": "pending"})
            self._queue_channels_cache = [c for c in channels if c is not None]
            self._queue_channels_ts = now
        except Exception as e:
            LOGGER.error(f"Failed to list pending channels: {e}")
        return list(self._queue_channels_cache)

    async def claim_next_item(self, channel_id: Optional[int] = None,
                              claimed_by: Optional[str] = None) -> Optional[Dict]:
        """Claim one pending item, round-robin across channels when unpinned.

        Live uploads (priority 0) sort ahead of rescan backfill (priority 1);
        within a priority the oldest item wins. Round-robin across channels
        stops one huge channel backlog from starving smaller channels.
        """
        claimed_by = claimed_by or f"pid:{os.getpid()}"
        if channel_id is None:
            channels = await self._pending_channels()
            if channels:
                n = len(channels)
                start = self._queue_rr % n
                for offset in range(n):
                    ch = channels[(start + offset) % n]
                    item = await self._claim_pending(ch, claimed_by)
                    if item:
                        self._queue_rr = (start + offset + 1) % n
                        return item
                # Rotation may be stale (all its channels drained); retry once
                # with a fresh list before falling through to a global claim.
                self._queue_channels_ts = 0.0
                channels = await self._pending_channels()
                for ch in channels:
                    item = await self._claim_pending(ch, claimed_by)
                    if item:
                        return item
        return await self._claim_pending(channel_id, claimed_by)

    async def get_next_batch(self, channel_id: int = None, batch_size: int = QueueConfig.BATCH_SIZE) -> List[Dict]:
        """Claim up to ``batch_size`` pending items atomically.

        Each item is flipped pending -> processing in the same operation that
        reads it, so a second processor (restart, second dyno, another worker)
        can never pick up the same file. Stale claims are recycled by
        ``reset_failed_messages``.
        """
        claimed: List[Dict] = []
        for _ in range(max(1, batch_size)):
            item = await self.claim_next_item(channel_id=channel_id)
            if not item:
                break
            claimed.append(item)
        return claimed

    async def mark_completed(self, queue_id: str, channel_id: int, message_id: int):
        try:
            await self.queue_collection.delete_one({"_id": ObjectId(queue_id)})
            await self.processed_collection.update_one(
                {"channel_id": channel_id, "message_id": message_id},
                {"$set": {"processed_at": datetime.utcnow()}},
                upsert=True
            )
            return True
        except Exception as e:
            LOGGER.error(f"Failed to mark completed: {e}")
            return False

    async def mark_failed(self, queue_id: str, error: str):
        try:
            now = datetime.utcnow()
            item = await self.queue_collection.find_one_and_update(
                {"_id": ObjectId(queue_id)},
                {
                    "$inc": {"retry_count": 1},
                    "$set": {
                        "status": "failed",
                        "error": str(error)[:300],
                        "updated_at": now
                    },
                    "$unset": {"claimed_at": "", "claimed_by": ""}
                },
                return_document=ReturnDocument.AFTER
            )
            if item and item.get("retry_count", 0) >= QueueConfig.MAX_RETRY_COUNT:
                # Terminal: let the expire_at TTL reclaim it after the
                # retention window. Pending/processing items never expire.
                await self.queue_collection.update_one(
                    {"_id": ObjectId(queue_id)},
                    {"$set": {"expire_at": now + timedelta(days=max(QueueConfig.QUEUE_TTL_DAYS, 1))}}
                )
            return item is not None
        except Exception as e:
            LOGGER.error(f"Failed to mark failed: {e}")
            return False

    async def reset_failed_messages(self, channel_id: int = None):
        base = {"channel_id": channel_id} if channel_id else {}
        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=QueueConfig.QUEUE_CLAIM_TIMEOUT)
        try:
            recovered = await self.queue_collection.update_many(
                {**base, "status": "processing", "claimed_at": {"$lt": stale_before}},
                {
                    "$set": {
                        "status": "pending",
                        "error": "Stale claim recovered",
                        "updated_at": now
                    },
                    "$unset": {"claimed_at": "", "claimed_by": "", "expire_at": ""}
                }
            )
            result = await self.queue_collection.update_many(
                {**base, "status": "failed", "retry_count": {"$lt": QueueConfig.MAX_RETRY_COUNT}},
                {
                    "$set": {"status": "pending", "error": None, "updated_at": now},
                    "$unset": {"claimed_at": "", "claimed_by": "", "expire_at": ""}
                }
            )
            if recovered.modified_count:
                LOGGER.warning(f"Recovered {recovered.modified_count} stale queue claims")
            if result.modified_count:
                LOGGER.info(f"Reset {result.modified_count} failed messages")
            return recovered.modified_count + result.modified_count
        except Exception as e:
            LOGGER.error(f"Failed to reset failed messages: {e}")
            return 0

    # ── Queue pause state (persisted so restarts keep honouring /pause) ──
    async def set_queue_paused(self, paused: bool) -> bool:
        self._queue_paused = bool(paused)
        self._queue_paused_ts = time.time()
        try:
            await self.settings_collection.update_one(
                {"_id": "queue_state"},
                {"$set": {"paused": bool(paused), "updated_at": datetime.utcnow()}},
                upsert=True
            )
            return True
        except Exception as e:
            LOGGER.error(f"Failed to persist queue pause state: {e}")
            return False

    async def is_queue_paused(self, max_age: float = None) -> bool:
        now = time.time()
        max_age = QueueConfig.QUEUE_STATE_POLL_SECONDS if max_age is None else max_age
        if self._queue_paused is not None and now - self._queue_paused_ts < max_age:
            return self._queue_paused
        try:
            doc = await self.settings_collection.find_one({"_id": "queue_state"})
            self._queue_paused = bool(doc and doc.get("paused"))
            self._queue_paused_ts = now
        except Exception as e:
            LOGGER.error(f"Failed to read queue pause state: {e}")
        return bool(self._queue_paused)

    async def update_checkpoint(self, channel_id: int, message_id: int):
        try:
            await self.checkpoint_collection.update_one(
                {"channel_id": channel_id},
                {"$set": {"last_processed_msg_id": message_id, "updated_at": datetime.utcnow()}},
                upsert=True
            )
            return True
        except Exception as e:
            LOGGER.error(f"Failed to update checkpoint: {e}")
            return False

    async def get_checkpoint(self, channel_id: int) -> Optional[int]:
        try:
            doc = await self.checkpoint_collection.find_one({"channel_id": channel_id})
            return doc["last_processed_msg_id"] if doc else None
        except Exception as e:
            LOGGER.error(f"Failed to get checkpoint: {e}")
            return None

    # =========================================================================
    # FULL-CHANNEL INDEX SCAN STATE
    # =========================================================================
    async def upsert_index_scan(self, channel_id: int, fields: dict) -> bool:
        """Create or fully reset the scan state for a channel."""
        if not self.index_scans_collection:
            return False
        try:
            doc = dict(fields)
            doc["channel_id"] = channel_id
            doc["updated_at"] = datetime.utcnow()
            await self.index_scans_collection.update_one(
                {"channel_id": channel_id},
                {"$set": doc},
                upsert=True
            )
            return True
        except Exception as e:
            LOGGER.error(f"Failed to upsert index scan: {e}")
            return False

    async def update_index_scan(self, channel_id: int, fields: dict) -> bool:
        if not self.index_scans_collection:
            return False
        try:
            doc = dict(fields)
            doc["updated_at"] = datetime.utcnow()
            result = await self.index_scans_collection.update_one(
                {"channel_id": channel_id}, {"$set": doc}
            )
            return result.modified_count > 0
        except Exception as e:
            LOGGER.error(f"Failed to update index scan: {e}")
            return False

    async def get_index_scan(self, channel_id: int) -> Optional[Dict]:
        if not self.index_scans_collection:
            return None
        try:
            return await self.index_scans_collection.find_one({"channel_id": channel_id})
        except Exception as e:
            LOGGER.error(f"Failed to get index scan: {e}")
            return None

    async def get_running_index_scans(self) -> List[Dict]:
        if not self.index_scans_collection:
            return []
        try:
            cursor = self.index_scans_collection.find({"status": "running"}).sort("updated_at", ASCENDING)
            return await cursor.to_list(length=20)
        except Exception as e:
            LOGGER.error(f"Failed to get running index scans: {e}")
            return []

    async def get_queue_stats(self, channel_id: int = None) -> Dict:
        try:
            match = {"channel_id": channel_id} if channel_id else {}
            pipeline = [
                {"$match": match},
                {"$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "total_retries": {"$sum": "$retry_count"}
                }}
            ]
            cursor = self.queue_collection.aggregate(pipeline)
            stats = {doc["_id"]: {"count": doc["count"], "retries": doc["total_retries"]}
                     for doc in await cursor.to_list(length=10)}
            pending_count = await self.queue_collection.count_documents({**match, "status": "pending"})
            return {
                "pending": pending_count,
                "failed": stats.get("failed", {}).get("count", 0),
                "processing": stats.get("processing", {}).get("count", 0),
                "total_retries": stats.get("failed", {}).get("retries", 0)
            }
        except Exception as e:
            LOGGER.error(f"Failed to get queue stats: {e}")
            return {}

    async def get_queue_channels(self, limit: int = 10) -> List[Dict]:
        """Pending depth per channel, busiest first (admin queue desk)."""
        if self.queue_collection is None:
            return []
        try:
            limit = max(1, int(limit))
            pipeline = [
                {"$match": {"status": "pending"}},
                {"$group": {"_id": "$channel_id", "pending": {"$sum": 1}}},
                {"$sort": {"pending": -1}},
                {"$limit": limit},
            ]
            cursor = self.queue_collection.aggregate(pipeline)
            return [
                {"channel_id": doc["_id"], "pending": doc["pending"]}
                for doc in await cursor.to_list(length=limit)
            ]
        except Exception as e:
            LOGGER.error(f"Failed to get queue channels: {e}")
            return []

    async def get_storage_usage(self) -> List[Dict]:
        """Lightweight per-cluster usage (dbStats only) for periodic guards."""
        usage: List[Dict] = []
        quota_mb = max(StorageConfig.CLUSTER_QUOTA_MB, 1)
        for cluster in self.clusters:
            try:
                stats = await cluster.db.command("dbStats")
                data_size = stats.get("dataSize", 0) or 0
                index_size = stats.get("indexSize", 0) or 0
                storage_size = stats.get("storageSize", 0) or 0
                used_mb = max(data_size + index_size, storage_size) / 1048576
                usage.append({
                    "cluster": cluster.index,
                    "used_mb": round(used_mb, 1),
                    "quota_mb": quota_mb,
                    "used_percent": round(used_mb * 100 / quota_mb, 1),
                })
            except Exception as e:
                LOGGER.warning(f"dbStats failed on cluster {cluster.index}: {e}")
        return usage

    async def get_storage_report(self) -> Dict:
        """Per-cluster quota usage + ops collection counts (admin /storage)."""
        quota_mb = max(StorageConfig.CLUSTER_QUOTA_MB, 1)
        report: Dict = {"clusters": [], "alerts": [], "ops": {}}
        for cluster in self.clusters:
            entry = {"cluster": cluster.index, "status": "ok"}
            try:
                stats = await cluster.db.command("dbStats")
                data_size = stats.get("dataSize", 0) or 0
                index_size = stats.get("indexSize", 0) or 0
                storage_size = stats.get("storageSize", 0) or 0
                used_mb = max(data_size + index_size, storage_size) / 1048576
                entry.update({
                    "data_mb": round(data_size / 1048576, 1),
                    "index_mb": round(index_size / 1048576, 1),
                    "storage_mb": round(storage_size / 1048576, 1),
                    "objects": stats.get("objects", 0) or 0,
                    "movies": await cluster.movie.count_documents({}),
                    "tv": await cluster.tv.count_documents({}),
                    "used_mb": round(used_mb, 1),
                    "quota_mb": quota_mb,
                    "used_percent": round(used_mb * 100 / quota_mb, 1),
                })
                if entry["used_percent"] >= StorageConfig.STORAGE_ALERT_PERCENT:
                    entry["status"] = "alert"
                    report["alerts"].append(
                        f"Cluster {cluster.index} at {entry['used_percent']}% ({entry['used_mb']} MB)"
                    )
            except Exception as e:
                entry["status"] = f"error: {e}"
            report["clusters"].append(entry)

        ops_cols = {
            "queue": self.queue_collection,
            "processed": self.processed_collection,
            "failed_files": self.failed_files_collection,
            "views": self.views_collection,
            "replaced_versions": self.replaced_versions,
        }
        for name, col in ops_cols.items():
            if col is None:
                continue
            try:
                report["ops"][name] = await col.count_documents({})
            except Exception:
                report["ops"][name] = "?"
        return report

    # =========================================================================
    # DUPLICATE MEDIA MERGE (legacy non-deterministic shard placement)
    # =========================================================================
    @staticmethod
    def _merge_quality_lists(target: List[Dict], extra: List[Dict]):
        for quality in extra or []:
            if any(t.get("id") == quality.get("id") for t in target):
                continue
            existing = next((t for t in target if t.get("quality") == quality.get("quality")), None)
            if existing:
                for key in ("audio_tracks", "subtitle_tracks", "file_unique_id", "size_bytes", "duration", "codec"):
                    if not existing.get(key) and quality.get(key):
                        existing[key] = quality[key]
            else:
                target.append(quality)

    def _merge_movie_doc(self, target: Dict, extra: Dict):
        target.setdefault("telegram", [])
        self._merge_quality_lists(target["telegram"], extra.get("telegram"))
        if not target.get("cast") and extra.get("cast"):
            target["cast"] = extra["cast"]
        if not target.get("external_subtitles") and extra.get("external_subtitles"):
            target["external_subtitles"] = extra["external_subtitles"]

    def _merge_tv_doc(self, target: Dict, extra: Dict):
        target.setdefault("seasons", [])
        for extra_season in extra.get("seasons") or []:
            season = next(
                (s for s in target["seasons"] if s.get("season_number") == extra_season.get("season_number")),
                None,
            )
            if season is None:
                target["seasons"].append(extra_season)
                continue
            season.setdefault("episodes", [])
            for extra_episode in extra_season.get("episodes") or []:
                episode = next(
                    (e for e in season["episodes"]
                     if e.get("episode_number") == extra_episode.get("episode_number")),
                    None,
                )
                if episode is None:
                    season["episodes"].append(extra_episode)
                    continue
                episode.setdefault("telegram", [])
                self._merge_quality_lists(episode["telegram"], extra_episode.get("telegram"))
        if not target.get("cast") and extra.get("cast"):
            target["cast"] = extra["cast"]

    async def merge_duplicate_media(self, apply: bool = True) -> Dict:
        """Merge the same title spread over multiple clusters into one doc.

        Deterministic placement prevents *new* duplicates; this repairs rows
        written before that change. ``apply=False`` performs a dry run.
        """
        summary = {"duplicates": 0, "merged": 0, "deleted": 0, "errors": 0}
        for media_type in ("movie", "tv"):
            locations: Dict[int, List[int]] = {}
            for cluster in self.clusters:
                try:
                    cursor = cluster.collection(media_type).find({}, {"tmdb_id": 1})
                    async for doc in cursor:
                        tid = doc.get("tmdb_id")
                        if tid is not None:
                            locations.setdefault(tid, []).append(cluster.index - 1)
                except Exception as e:
                    summary["errors"] += 1
                    LOGGER.error(f"Duplicate scan failed on cluster {cluster.index}/{media_type}: {e}")

            for tid, idxs in locations.items():
                if len(idxs) < 2:
                    continue
                summary["duplicates"] += 1
                target = self._deterministic_cluster_index(media_type, tid)
                if target not in idxs:
                    target = idxs[0]
                try:
                    docs = []
                    for i in idxs:
                        doc = await self.clusters[i].collection(media_type).find_one({"tmdb_id": tid})
                        if doc:
                            docs.append((i, doc))
                    if len(docs) < 2:
                        continue
                    target_doc = next((d for i, d in docs if i == target), docs[0][1])
                    for i, doc in docs:
                        if i == target:
                            continue
                        if media_type == "movie":
                            self._merge_movie_doc(target_doc, doc)
                        else:
                            self._merge_tv_doc(target_doc, doc)
                    if apply:
                        await self.clusters[target].collection(media_type).replace_one(
                            {"tmdb_id": tid}, target_doc
                        )
                        for i, _ in docs:
                            if i == target:
                                continue
                            res = await self.clusters[i].collection(media_type).delete_one({"tmdb_id": tid})
                            summary["deleted"] += res.deleted_count
                        await self._record_shard(media_type, tid, target)
                    self._shard_cache[f"{media_type}:{tid}"] = target
                    summary["merged"] += 1
                except Exception as e:
                    summary["errors"] += 1
                    LOGGER.error(f"Merge failed for {media_type}:{tid}: {e}")
        return summary

    async def cleanup_old_queue_records(self, days: int = QueueConfig.QUEUE_TTL_DAYS):
        cutoff = datetime.utcnow() - timedelta(days=days)
        try:
            result = await self.queue_collection.delete_many({
                "created_at": {"$lt": cutoff},
                "status": {"$in": ["completed", "failed"]}
            })
            LOGGER.info(f"Cleaned up {result.deleted_count} old queue records")
            return result.deleted_count
        except Exception as e:
            LOGGER.error(f"Failed to cleanup old queue records: {e}")
            return 0

    async def clear_queue(self):
        """Clear all queue items including processed files and checkpoints."""
        try:
            queue_result = await self.queue_collection.delete_many({})
            LOGGER.info(f"Cleared {queue_result.deleted_count} queue items")
            processed_result = await self.processed_collection.delete_many({})
            LOGGER.info(f"Cleared {processed_result.deleted_count} processed files")
            checkpoint_result = await self.checkpoint_collection.delete_many({})
            LOGGER.info(f"Cleared {checkpoint_result.deleted_count} checkpoints")
            total_cleared = queue_result.deleted_count + processed_result.deleted_count + checkpoint_result.deleted_count
            LOGGER.info(f"Total MongoDB queue cleared: {total_cleared} items")
            return total_cleared
        except Exception as e:
            LOGGER.error(f"Failed to clear queue: {e}")
            return 0

    # =========================================================================
    # FAILED FILES / BACKUPS (primary cluster)
    # =========================================================================
    async def backup_replaced_version(self, metadata_info: dict, old_version: dict, reason: str) -> bool:
        try:
            backup_data = {
                "tmdb_id": metadata_info.get("tmdb_id"),
                "media_type": metadata_info.get("media_type"),
                "title": metadata_info.get("title"),
                "season_number": metadata_info.get("season_number"),
                "episode_number": metadata_info.get("episode_number"),
                "quality": old_version.get("quality"),
                "old_version": old_version,
                "replacement_reason": reason,
                "replaced_at": datetime.utcnow(),
                "backup_status": "success"
            }
            result = await self.replaced_versions.insert_one(backup_data)
            if result.inserted_id:
                LOGGER.info(f"💾 Backed up replaced version for TMDB ID {metadata_info.get('tmdb_id')}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error backing up replaced version: {e}")
            return False

    async def log_failed_file(self, title: str, filename: str, reason: str, metadata_info: dict = None,
                              parse_data: dict = None) -> bool:
        try:
            failed_entry = {
                "title": title,
                "filename": filename,
                "reason": reason,
                "timestamp": datetime.utcnow(),
                "metadata_info": compact_metadata_for_queue(metadata_info or {})
            }
            if parse_data:
                # Keep the fields parsed before the failure so a parser fix can
                # re-process the file without re-downloading it from Telegram.
                failed_entry["parse_data"] = parse_data
            result = await self.failed_files_collection.insert_one(failed_entry)
            if result.inserted_id:
                LOGGER.info(f"📝 Logged failed file: {title} - Reason: {reason}")
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Error logging failed file: {e}")
            return False

    @staticmethod
    def _safe_object_id(id_str) -> Optional[ObjectId]:
        try:
            return ObjectId(id_str)
        except Exception:
            return None

    async def get_failed_file(self, id_str: str) -> Optional[dict]:
        oid = self._safe_object_id(id_str)
        if oid is None:
            return None
        doc = await self.failed_files_collection.find_one({"_id": oid})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    async def delete_failed_file(self, id_str: str) -> int:
        oid = self._safe_object_id(id_str)
        if oid is None:
            return 0
        res = await self.failed_files_collection.delete_one({"_id": oid})
        return res.deleted_count

    async def get_failed_files(self, page: int = 1, page_size: int = 50, reason_filter: str = None,
                               date_from: datetime = None, date_to: datetime = None) -> dict:
        try:
            query = {}
            if reason_filter:
                query["reason"] = {"$regex": reason_filter, "$options": "i"}
            if date_from:
                query["timestamp"] = query.get("timestamp", {})
                query["timestamp"]["$gte"] = date_from
            if date_to:
                query["timestamp"] = query.get("timestamp", {})
                query["timestamp"]["$lte"] = date_to

            skip = (page - 1) * page_size
            total_count = await self.failed_files_collection.count_documents(query)

            cursor = self.failed_files_collection.find(query).sort("timestamp", DESCENDING).skip(skip).limit(page_size)
            failed_files = []
            async for doc in cursor:
                doc["_id"] = str(doc["_id"])
                failed_files.append(doc)

            return {
                "items": failed_files,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": (total_count + page_size - 1) // page_size
            }
        except Exception as e:
            LOGGER.error(f"Error getting failed files: {e}")
            return {"items": [], "total_count": 0, "page": page, "page_size": page_size, "total_pages": 0}

    async def clear_failed_files(self) -> bool:
        try:
            result = await self.failed_files_collection.delete_many({})
            LOGGER.info(f"🗑️ Cleared {result.deleted_count} failed file logs")
            return True
        except Exception as e:
            LOGGER.error(f"Error clearing failed files: {e}")
            return False

    # =========================================================================
    # STATS (cross-cluster, for bot /stats command)
    # =========================================================================
    async def get_stats_summary(self) -> dict:
        """Efficient cross-cluster media statistics."""
        summary = {
            "movies": 0, "tv_shows": 0, "total_movie_files": 0, "total_tv_files": 0,
            "views": 0, "replaced": 0, "failed": 0,
            "clusters": len(self.clusters),
        }
        file_size_pipelines = {
            "movie": [
                {"$project": {"n": {"$size": {"$ifNull": ["$telegram", []]}}}},
                {"$group": {"_id": None, "n": {"$sum": "$n"}}}
            ],
            "tv": [
                {"$project": {"n": {
                    "$sum": {
                        "$map": {
                            "input": {"$ifNull": ["$seasons", []]},
                            "as": "s",
                            "in": {"$sum": {
                                "$map": {
                                    "input": {"$ifNull": ["$$s.episodes", []]},
                                    "as": "e",
                                    "in": {"$size": {"$ifNull": ["$$e.telegram", []]}}
                                }
                            }}
                        }
                    }
                }}},
                {"$group": {"_id": None, "n": {"$sum": "$n"}}}
            ],
        }
        for cluster in self.clusters:
            try:
                summary["movies"] += await cluster.movie.count_documents({})
                summary["tv_shows"] += await cluster.tv.count_documents({})
                mres = await cluster.movie.aggregate(file_size_pipelines["movie"]).to_list(1)
                if mres:
                    summary["total_movie_files"] += mres[0].get("n", 0)
                tres = await cluster.tv.aggregate(file_size_pipelines["tv"]).to_list(1)
                if tres:
                    summary["total_tv_files"] += tres[0].get("n", 0)
            except Exception as e:
                LOGGER.error(f"Stats failed on cluster {cluster.index}: {e}")
        try:
            summary["views"] = await self.views_collection.count_documents({})
            summary["replaced"] = await self.replaced_versions.count_documents({})
            summary["failed"] = await self.failed_files_collection.count_documents({})
        except Exception as e:
            LOGGER.error(f"Ops stats failed: {e}")
        return summary

    # =========================================================================
    # LEGACY ADMIN SURFACE
    # =========================================================================
    @property
    def media(self) -> UnifiedMediaFacade:
        """Unified movie+tv facade for the admin panel (spans all clusters)."""
        return UnifiedMediaFacade(self)
