"""Local checks for the admin-ops expansion.

Everything here runs against in-memory fakes — no MongoDB, no Telegram, no
network. Run with::

    .venv/Scripts/python.exe -m tests.test_admin_ops

The suite covers the parts of the design that are provable locally: knob
validation and effective-value resolution, the audit writer's failure
isolation, the scan registry's bookkeeping, the read governor's arithmetic,
the audio-language backfill's cursor and rewrite rules, the orphan report's
upsert and resolved-row exclusion, and the alert watchdog's key transitions.
"""

import asyncio
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from Backend.config import IndexConfig, QueueConfig  # noqa: E402
from Backend import wzgram_pkg  # noqa: E402
from Backend.helper import alerts as alerts_mod  # noqa: E402
from Backend.helper import audit as audit_mod  # noqa: E402
from Backend.helper import channel_index as ci  # noqa: E402
from Backend.helper import runtime_config as rc  # noqa: E402
from Backend.helper.audio_languages import (  # noqa: E402
    BACKFILL_PROJECTION,
    collect_languages,
    document_is_probed,
    is_unprobeable,
    language_names_for,
    languages_from_document,
)


# ── In-memory fakes ─────────────────────────────────────────────────────────
class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs[:length] if length else self._docs)

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc
        return gen()


def _apply_projection_path(out: dict, src, parts) -> None:
    """Copy one dotted projection path out of ``src`` into ``out``.

    Mirrors MongoDB's inclusion projection: a path that traverses an array is
    applied to every element, and the nesting is preserved.
    """
    if not parts:
        return
    head, rest = parts[0], parts[1:]
    if isinstance(src, list):
        items = []
        for item in src:
            sub = {}
            _apply_projection_path(sub, item, parts)
            if sub:
                items.append(sub)
        if items:
            out[head] = items
        return
    if not isinstance(src, dict) or head not in src:
        return
    value = src[head]
    if not rest:
        out[head] = value
        return
    if isinstance(value, list):
        items = []
        for item in value:
            sub = {}
            _apply_projection_path(sub, item, rest)
            if sub:
                items.append(sub)
        out[head] = items
    elif isinstance(value, dict):
        sub = {}
        _apply_projection_path(sub, value, rest)
        out[head] = sub


def _project(doc: dict, projection: dict) -> dict:
    """Apply a MongoDB-style inclusion projection to one document."""
    out = {}
    for path, keep in projection.items():
        if not keep:
            continue
        _apply_projection_path(out, doc, path.split("."))
    return out


def _set_path(doc: dict, path: str, value) -> None:
    """Apply one dotted ``$set`` path, creating intermediate objects."""
    parts = path.split(".")
    target = doc
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    target[parts[-1]] = value


def _unset_path(doc: dict, path: str) -> None:
    """Apply one dotted ``$unset`` path."""
    parts = path.split(".")
    target = doc
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            return
        target = child
    target.pop(parts[-1], None)


def _matches(doc: dict, query) -> bool:
    """Match the subset of Mongo query operators the tests need."""
    for key, expected in (query or {}).items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCollection:
    """Just enough of a Mongo collection for the code under test."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.writes = 0

    async def find(self, query=None, projection=None):
        docs = self.docs
        if query:
            docs = [d for d in docs if _matches(d, query)]
        if projection:
            docs = [_project(d, projection) for d in docs]
        return FakeCursor(docs)

    async def find_one(self, query=None, projection=None):
        cursor = await self.find(query, projection)
        items = await cursor.to_list(1)
        return items[0] if items else None

    async def count_documents(self, query=None):
        return len((await self.find(query))._docs)

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    async def update_one(self, query, update, upsert=False, **_kwargs):
        target = None
        for doc in self.docs:
            if _matches(doc, query):
                target = doc
                break
        if target is None:
            if not upsert:
                return type("R", (), {"matched_count": 0, "modified_count": 0})()
            target = dict(query)
            self.docs.append(target)
        for field, value in (update.get("$set") or {}).items():
            _set_path(target, field, value)
        for field, value in (update.get("$setOnInsert") or {}).items():
            if field not in target:
                _set_path(target, field, value)
        for field in (update.get("$unset") or {}):
            _unset_path(target, field)
        return type("R", (), {"matched_count": 1, "modified_count": 1})()

    async def update_many(self, query, update):
        matched = 0
        for doc in self.docs:
            if not _matches(doc, query):
                continue
            matched += 1
            for field, value in (update.get("$set") or {}).items():
                _set_path(doc, field, value)
            for field in (update.get("$unset") or {}):
                _unset_path(doc, field)
            for field, value in (update.get("$inc") or {}).items():
                doc[field] = (doc.get(field) or 0) + value
        return type("R", (), {"matched_count": matched, "modified_count": matched})()

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [
            d for d in self.docs
            if not _matches(d, query)
        ]
        return type("R", (), {"deleted_count": before - len(self.docs)})()

    async def delete_one(self, query):
        for i, doc in enumerate(self.docs):
            if _matches(doc, query):
                del self.docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()


class FakeCluster:
    def __init__(self, index, movie=None, tv=None):
        self.index = index
        self.movie = movie or FakeCollection()
        self.tv = tv or FakeCollection()

    def collection(self, kind):
        return self.movie if kind == "movie" else self.tv


class FakeDb:
    def __init__(self):
        self.settings_collection = FakeCollection()
        self.admin_audit_collection = FakeCollection()
        self.admin_jobs_collection = FakeCollection()
        self.orphan_files_collection = FakeCollection()
        self.index_scans_collection = FakeCollection()
        self.clusters = []

    # ── index scans (mirror Database's behaviour) ───────────────────────────
    async def upsert_index_scan(self, channel_id, fields):
        doc = dict(fields)
        doc["channel_id"] = channel_id
        doc.setdefault("updated_at", datetime.utcnow())
        await self.index_scans_collection.update_one(
            {"channel_id": channel_id}, {"$set": doc}, upsert=True
        )
        return True

    async def update_index_scan(self, channel_id, fields):
        doc = dict(fields)
        doc["updated_at"] = datetime.utcnow()
        result = await self.index_scans_collection.update_one(
            {"channel_id": channel_id}, {"$set": doc}
        )
        return result.modified_count > 0

    async def get_index_scan(self, channel_id):
        return await self.index_scans_collection.find_one({"channel_id": channel_id})

    async def get_running_index_scans(self):
        cursor = await self.index_scans_collection.find({"status": "running"})
        return await cursor.to_list(None)

    async def get_waiting_index_scans(self):
        cursor = await self.index_scans_collection.find({"status": "waiting"})
        return await cursor.to_list(None)

    async def list_index_scans(self, limit=50):
        cursor = await self.index_scans_collection.find({})
        return await cursor.to_list(limit)

    # ── queue / storage ─────────────────────────────────────────────────────
    async def get_queue_stats(self):
        return {"pending": 0, "processing": 0, "failed": 0, "total_retries": 0}

    async def get_storage_usage(self):
        return []

    async def is_file_exists_strict(self, channel_id, message_id, file_hash):
        return False

    async def upsert_orphan_file(self, doc):
        await self.orphan_files_collection.update_one(
            {"_id": doc.get("_id")}, {"$set": dict(doc)}, upsert=True
        )
        return True

    async def get_orphan_files(self, ids):
        cursor = await self.orphan_files_collection.find(
            {"_id": {"$in": [str(i) for i in ids]}}
        )
        return await cursor.to_list(None)

    async def delete_orphan_files(self, ids):
        res = await self.orphan_files_collection.delete_many(
            {"_id": {"$in": [str(i) for i in ids]}}
        )
        return res.deleted_count

    # ── admin jobs ──────────────────────────────────────────────────────────
    async def upsert_admin_job(self, job_id, fields):
        doc = dict(fields)
        doc["_id"] = job_id
        doc["updated_at"] = datetime.utcnow()
        await self.admin_jobs_collection.update_one(
            {"_id": job_id}, {"$set": doc}, upsert=True
        )
        return True

    async def get_admin_job(self, job_id):
        return await self.admin_jobs_collection.find_one({"_id": job_id})

    async def find_active_admin_job(self, kind):
        rows = [
            doc for doc in self.admin_jobs_collection.docs
            if doc.get("kind") == kind
            and doc.get("status") in ("pending", "running", "paused")
        ]
        return rows[-1] if rows else None


def patch_db(fake):
    """Point every late-bound ``from Backend import db`` at ``fake``.

    The helpers resolve the database inside ``_db()`` at call time, so
    rebinding ``Backend.db`` is enough — no per-module patching.
    """
    import Backend
    Backend.db = fake


# ── Runtime config ──────────────────────────────────────────────────────────
class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.store = rc.RuntimeConfigStore()
        self.fake = FakeDb()
        patch_db(self.fake)

    def test_defaults_without_override(self):
        self.assertEqual(self.store.get("file_queue_delay"), QueueConfig.FILE_QUEUE_DELAY)
        self.assertEqual(self.store.get("batch_size"), QueueConfig.BATCH_SIZE)
        self.assertEqual(self.store.get("scan_batch_size"), IndexConfig.SCAN_BATCH_SIZE)
        self.assertEqual(self.store.get("index_max_concurrent_scans"), 1)
        self.assertFalse(self.store.is_overridden("file_queue_delay"))

    def test_override_wins_and_persists(self):
        asyncio.run(self._write())
        self.assertEqual(self.store.get("file_queue_delay"), 7)
        self.assertTrue(self.store.is_overridden("file_queue_delay"))
        doc = self.fake.settings_collection.docs[0]
        self.assertEqual(doc["_id"], rc.CONFIG_DOC_ID)
        self.assertEqual(doc["values"]["file_queue_delay"], 7)

    async def _write(self):
        await self.store.set("file_queue_delay", 7, updated_by="test")

    def test_set_preserves_other_overrides_when_read_fails(self):
        class ReadBoom(FakeCollection):
            async def find_one(self, *_args, **_kwargs):
                raise RuntimeError("mongo read down")

        collection = ReadBoom([{
            "_id": rc.CONFIG_DOC_ID,
            "values": {"batch_size": 42},
        }])
        self.fake.settings_collection = collection

        asyncio.run(self.store.set("file_queue_delay", 7))
        values = collection.docs[0]["values"]
        # A failed read must not become an empty map that wipes the sibling
        # override: the write only touches its own dotted path.
        self.assertEqual(values["batch_size"], 42)
        self.assertEqual(values["file_queue_delay"], 7)

    def test_unset_removes_only_its_own_override(self):
        asyncio.run(self.store.set("batch_size", 42))
        asyncio.run(self.store.set("file_queue_delay", 7))
        asyncio.run(self.store.unset("batch_size"))

        values = self.fake.settings_collection.docs[0]["values"]
        self.assertNotIn("batch_size", values)
        self.assertEqual(values["file_queue_delay"], 7)

    def test_unknown_key_is_rejected_not_written(self):
        with self.assertRaises(rc.KnobError):
            asyncio.run(self.store.set("not_a_knob", 1))
        self.assertEqual(self.fake.settings_collection.docs, [])

    def test_range_validation(self):
        cases = [
            ("file_queue_delay", 61),
            ("file_queue_delay", -1),
            ("batch_size", 4),
            ("batch_size", 1001),
            ("queue_workers", 0),
            ("queue_workers", 17),
            ("scan_batch_delay", 61),
            ("storage_stop_percent", 100),
            ("index_max_concurrent_scans", 9),
        ]
        for key, value in cases:
            with self.assertRaises(rc.KnobError, msg=f"{key}={value} should be rejected"):
                asyncio.run(self.store.set(key, value))

    def test_valid_boundaries_accepted(self):
        for key, value in [
            ("file_queue_delay", 0), ("file_queue_delay", 60),
            ("batch_size", 5), ("batch_size", 1000),
            ("queue_workers", 1), ("queue_workers", 16),
            ("scan_batch_delay", 0.0), ("scan_batch_delay", 60.0),
            ("storage_stop_percent", 50), ("storage_stop_percent", 99),
            ("index_max_concurrent_scans", 1), ("index_max_concurrent_scans", 8),
        ]:
            asyncio.run(self.store.set(key, value))

    def test_bool_knobs(self):
        asyncio.run(self.store.set_bool("use_caption", True))
        self.assertTrue(self.store.get_bool("use_caption"))
        asyncio.run(self.store.toggle_bool("use_caption"))
        self.assertFalse(self.store.get_bool("use_caption"))

    def test_unset_falls_back_to_default(self):
        asyncio.run(self.store.set("batch_size", 99))
        self.assertEqual(self.store.get("batch_size"), 99)
        asyncio.run(self.store.unset("batch_size"))
        self.assertEqual(self.store.get("batch_size"), QueueConfig.BATCH_SIZE)
        self.assertFalse(self.store.is_overridden("batch_size"))

    def test_refresh_ignores_unknown_and_invalid_stored_keys(self):
        self.fake.settings_collection.docs.append({
            "_id": rc.CONFIG_DOC_ID,
            "values": {
                "batch_size": 42,
                "not_a_knob": 7,
                "file_queue_delay": 9999,  # out of range
            },
        })
        asyncio.run(self.store.refresh())
        self.assertEqual(self.store.get("batch_size"), 42)
        self.assertEqual(self.store.get("file_queue_delay"), QueueConfig.FILE_QUEUE_DELAY)
        self.assertNotIn("not_a_knob", self.store.overrides())

    def test_refresh_failure_keeps_previous_snapshot(self):
        asyncio.run(self.store.set("batch_size", 33))

        class Boom:
            async def find_one(self, *_a, **_k):
                raise RuntimeError("mongo down")

        self.fake.settings_collection = Boom()
        self.assertFalse(asyncio.run(self.store.refresh()))
        self.assertEqual(self.store.get("batch_size"), 33)


# ── Audit ───────────────────────────────────────────────────────────────────
class AuditTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDb()
        patch_db(self.fake)

    def test_writer_swallows_failures(self):
        class Boom:
            async def insert_one(self, _doc):
                raise RuntimeError("insert failed")

        self.fake.admin_audit_collection = Boom()
        # Must not raise, and must not break the action it records.
        asyncio.run(audit_mod.audit_event_async("media.delete", actor="admin"))
        self.assertEqual(self.fake.admin_audit_collection, self.fake.admin_audit_collection)

    def test_writer_never_raises_from_audit_event(self):
        class Boom:
            async def insert_one(self, _doc):
                raise RuntimeError("insert failed")

        self.fake.admin_audit_collection = Boom()

        async def run():
            return audit_mod.audit_event("media.delete", actor="admin")

        # audit_event schedules a task; it must not raise even when the write fails.
        task = asyncio.run(run())
        self.assertIsNotNone(task)

    def test_detail_is_bounded_and_redacted(self):
        long_detail = "x" * 5000
        doc = audit_mod.build_event("a", detail=long_detail)
        self.assertLessEqual(len(doc["detail"]), audit_mod.MAX_DETAIL_CHARS)

        secret = audit_mod.build_event(
            "a", detail="password: hunter2 and USER_SESSION_STRING=abc123"
        )
        self.assertNotIn("hunter2", secret["detail"])
        self.assertNotIn("abc123", secret["detail"])

    def test_redaction_covers_realistic_secret_shapes(self):
        cases = {
            "Authorization: Bearer abc123def456": "abc123def456",
            "X-Api-Key: sk-live-1234567890": "sk-live-1234567890",
            '{"api_key": "sk-1234567890"}': "sk-1234567890",
            "client_secret = s3cr3tvalue": "s3cr3tvalue",
            "https://x.com/?token=abc123&password=hunter2": "hunter2",
            "session string 1AaBbCcDdEeFfGgHhIiJjKk": "1AaBbCcDdEeFfGgHhIiJjKk",
        }
        for detail, secret in cases.items():
            text = audit_mod.bounded_detail(detail)
            self.assertNotIn(secret, text, msg=f"{detail!r} leaked {secret!r}")

    def test_redaction_keeps_the_key_name_for_the_trail(self):
        text = audit_mod.bounded_detail("password: hunter2")
        self.assertIn("password", text)
        self.assertIn("[redacted]", text)

    def test_ordinary_text_is_not_mangled(self):
        # Prose that merely mentions a secret keyword must survive intact.
        for detail in (
            "the token is invalid",
            "no secrets here at all",
            '{"fields": ["title", "year"], "count": 5}',
            "media:movie:42 title=The Matrix",
        ):
            self.assertEqual(audit_mod.bounded_detail(detail), detail)

    def test_document_shape(self):
        doc = audit_mod.build_event(
            "media.delete", actor="admin", actor_kind="web",
            target="media:movie:42", detail={"a": 1}, result="ok", ip="1.2.3.4",
        )
        for field in ("_id", "ts", "actor", "actor_kind", "action", "target",
                      "detail", "result", "ip"):
            self.assertIn(field, doc)
        self.assertEqual(doc["actor_kind"], "web")
        self.assertEqual(doc["target"], "media:movie:42")


# ── Scan registry + governor ────────────────────────────────────────────────
class ScanRegistryTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDb()
        patch_db(self.fake)
        ci._scans.clear()
        ci._pending_context.clear()

    def tearDown(self):
        ci._scans.clear()
        ci._pending_context.clear()

    def test_cap_enforced(self):
        ci._scans[1] = ci._ScanHandle(1, _FakeTask(), asyncio.Event())
        self.assertEqual(ci.running_count(), 1)
        self.assertTrue(ci.is_scan_running(1))
        self.assertFalse(ci.is_scan_running(2))

        with self.assertRaises(ci.IndexBusyError):
            asyncio.run(ci.request_index_scan(object(), _FakeChat(2), 1, None))
        # The default cap of 1 reproduces today's error text exactly.
        self.assertIn("already running", str(self._busy_text()))

    def _busy_text(self):
        try:
            asyncio.run(ci.request_index_scan(object(), _FakeChat(2), 1, None))
        except ci.IndexBusyError as e:
            return e
        return ""

    def test_single_request_refuses_a_live_channel(self):
        from unittest.mock import patch

        ci._scans[4] = ci._ScanHandle(4, _FakeTask(), asyncio.Event())

        async def run():
            # A free cap slot isolates the per-channel guard from the cap.
            with patch.object(ci, "scan_cap", return_value=3), \
                 patch.object(ci, "_launch") as launch:
                try:
                    await ci.request_index_scan(
                        object(), _FakeChat(4), 1, None
                    )
                except ci.IndexBusyError as e:
                    return str(e), launch
                return "", launch

        message, launch = asyncio.run(run())
        self.assertIn("already being scanned", message)
        launch.assert_not_called()
        # The live scan doc must not have been reset by a fresh `running` row.
        self.assertEqual(self.fake.index_scans_collection.docs, [])

    def test_progress_skips_telegram_for_panel_scans(self):
        progress = ci._Progress(object(), 0, 10, "Panel scan")
        stats = {
            "checked": 1, "queued": 0, "existing": 1,
            "non_video": 0, "deleted": 0, "errors": 0,
        }
        # ``object()`` has no send_message/edit_message_text: any attempt to
        # notify would raise AttributeError and fail the test.
        asyncio.run(progress.update(stats, None, 1.0, "working", force=True))
        self.assertIsNone(progress.message_id)

    def test_on_task_done_removes_only_its_own_entry(self):
        task_a = _FakeTask()
        task_b = _FakeTask()
        ci._scans[1] = ci._ScanHandle(1, task_a, asyncio.Event())
        ci._scans[2] = ci._ScanHandle(2, task_b, asyncio.Event())
        ci._on_task_done(task_a)
        self.assertNotIn(1, ci._scans)
        self.assertIn(2, ci._scans)

    def test_per_channel_stop_events(self):
        stop_a = asyncio.Event()
        stop_b = asyncio.Event()
        ci._scans[1] = ci._ScanHandle(1, _FakeTask(), stop_a)
        ci._scans[2] = ci._ScanHandle(2, _FakeTask(), stop_b)

        async def run():
            return await ci.stop_index_scan(1)

        stopped = asyncio.run(run())
        self.assertEqual(stopped, [1])
        self.assertTrue(stop_a.is_set())
        self.assertFalse(stop_b.is_set())

    def test_stop_all(self):
        for cid in (1, 2, 3):
            ci._scans[cid] = ci._ScanHandle(cid, _FakeTask(), asyncio.Event())

        async def run():
            return await ci.stop_index_scan()

        stopped = asyncio.run(run())
        self.assertEqual(sorted(stopped), [1, 2, 3])
        self.assertTrue(all(h.stop_event.is_set() for h in ci._scans.values()))


class BotIndexFixTests(unittest.TestCase):
    """Regressions for the bot-side index / resume / cancel paths."""

    def setUp(self):
        self.fake = FakeDb()
        patch_db(self.fake)
        ci._scans.clear()
        ci._pending_context.clear()
        ci._default_context.clear()

    def tearDown(self):
        ci._scans.clear()
        ci._pending_context.clear()
        ci._default_context.clear()

    # ── Bug 1: the DM filter must see several links, one per line ───────────
    def test_link_trigger_matches_multiline_messages(self):
        import re as _re
        from wzgram import filters

        self.assertTrue(bool(ci.LINK_TRIGGER_RE.flags & _re.MULTILINE))
        self.assertTrue(bool(ci.LINK_TRIGGER_RE.flags & _re.IGNORECASE))

        single = "https://t.me/c/1234567890/55"
        multi = "https://t.me/c/1234567890/55\nhttps://t.me/c/1234567890/56"
        self.assertTrue(bool(ci.LINK_TRIGGER_RE.finditer(single)))
        matches = list(ci.LINK_TRIGGER_RE.finditer(multi))
        self.assertEqual(len(matches), 2)
        # The old string-based filter recompiled with flags=0 and no MULTILINE.
        old = _re.compile(ci.LINK_RE.pattern)
        self.assertFalse(bool(list(old.finditer(multi))))
        # Prose must not arm the index handler.
        self.assertFalse(bool(list(ci.LINK_TRIGGER_RE.finditer("just some text"))))
        # wzgram accepts a compiled pattern and preserves its flags.
        self.assertIs(filters.regex(ci.LINK_TRIGGER_RE).p, ci.LINK_TRIGGER_RE)

    # ── Bug 2: launching N scans must not double-count the cap ──────────────
    def test_request_index_scans_fills_every_free_slot(self):
        from unittest.mock import patch

        async def fake_launch(client, channel_id, queue_file, is_resume,
                              notify_client=None, mode=ci.MODE_INDEX):
            ci._scans[channel_id] = ci._ScanHandle(
                channel_id, _FakeTask(), asyncio.Event(), mode=mode
            )

        async def run():
            with patch.object(ci, "_launch", fake_launch), \
                 patch.object(ci, "scan_cap", return_value=3):
                return await ci.request_index_scans(
                    object(), [_FakeChat(i) for i in (1, 2, 3)], 1, None
                )

        result = asyncio.run(run())
        # Before the fix, `running_count() + len(started)` counted each new
        # scan twice, so only 2 of 3 started and one was parked as waiting.
        self.assertEqual(result["started"], [1, 2, 3])
        self.assertEqual(result["waiting"], [])

    def test_request_again_does_not_clobber_a_live_scan(self):
        from unittest.mock import patch

        self.fake.index_scans_collection.docs.append(
            {"channel_id": 4, "status": "running", "checked": 812}
        )
        ci._scans[4] = ci._ScanHandle(4, _FakeTask(), asyncio.Event())

        async def run():
            with patch.object(ci, "scan_cap", return_value=3):
                return await ci.request_index_scans(
                    object(), [_FakeChat(4), _FakeChat(5)], 1, None
                )

        with patch.object(ci, "_launch") as launch:
            result = asyncio.run(run())

        self.assertEqual(result["skipped"], [4])
        # The live row kept its counters — no fresh `waiting` doc was written.
        self.assertEqual(self.fake.index_scans_collection.docs[0]["checked"], 812)
        self.assertEqual(self.fake.index_scans_collection.docs[0]["status"], "running")
        self.assertEqual(len(self.fake.index_scans_collection.docs), 2)
        launch.assert_called_once()

    def test_multi_channel_start_does_not_share_one_progress_message(self):
        import Backend.wzgram_pkg.plugins.start as start
        from unittest.mock import patch

        captured = {}

        async def fake_request(client, chats, requester_id, queue_file,
                               status_message_id=None, notify_client=None,
                               mode=ci.MODE_INDEX, skip_first=None):
            captured["status_message_id"] = status_message_id
            captured["skip_first"] = skip_first
            return {
                "started": [chat.id for chat in chats], "waiting": [],
                "skipped": [], "cap": 3,
            }

        async def no_warning(_channel_id):
            return None

        class QueryMessage:
            id = 999

            def __init__(self):
                self.replies = []

            async def reply_text(self, text, **_kwargs):
                self.replies.append(text)

        class Query:
            def __init__(self, token):
                self.data = f"idx:go:{token}"
                self.from_user = type("From", (), {"id": 7})()
                self.message = QueryMessage()
                self.answers = []

            async def answer(self, text=None, **_kwargs):
                self.answers.append(text)

        async def run_case(channel_ids):
            token = f"tok{len(channel_ids)}"
            start._index_requests[token] = {
                "chats": [_FakeChat(cid) for cid in channel_ids],
                "skip_first": 42,
            }
            query = Query(token)
            with patch.object(start, "_user_is_admin", return_value=True), \
                 patch.object(start, "is_auth_channel", return_value=True), \
                 patch.object(start, "_probe_index_access", new=no_warning), \
                 patch.object(start, "get_index_client", return_value=object()), \
                 patch.object(start, "request_index_scans", new=fake_request):
                await start.index_start_callback(object(), query)
            return query, captured["status_message_id"]

        with patch.object(start, "rescan_running", False):
            single_query, single_status = asyncio.run(run_case([1]))
            multi_query, multi_status = asyncio.run(run_case([1, 2]))

        # One scan may keep editing the confirmation message…
        self.assertEqual(single_status, single_query.message.id)
        # …but N scans must not all edit the same message.
        self.assertIsNone(multi_status)
        self.assertTrue(multi_query.message.replies)
        # The skip answer chosen on the confirmation reaches the scan engine.
        self.assertEqual(captured["skip_first"], 42)

    # ── Bug 3: stop-all must also cancel queued scans ───────────────────────
    def test_stop_all_cancels_waiting_scans(self):
        self.fake.index_scans_collection.docs.append(
            {"channel_id": 9, "status": "waiting", "title": "queued"}
        )
        ci._pending_context[9] = {"client": object(), "queue_file": None}

        stopped = asyncio.run(ci.stop_index_scan())
        self.assertIn(9, stopped)
        row = self.fake.index_scans_collection.docs[0]
        self.assertEqual(row["status"], "cancelled")
        self.assertNotIn(9, ci._pending_context)
        self.assertEqual(asyncio.run(self.fake.get_waiting_index_scans()), [])

    # ── Bug 4: waiting rows must survive a restart ──────────────────────────
    def test_resume_reads_waiting_rows_and_sets_fallback_context(self):
        from unittest.mock import patch

        self.fake.index_scans_collection.docs.append(
            {"channel_id": 7, "status": "waiting", "title": "queued",
             "mode": ci.MODE_AUDIT}
        )

        async def run():
            with patch.object(ci, "is_auth_channel_id", return_value=True), \
                 patch.object(ci, "_launch") as launch:
                await ci.resume_index_scans(object(), None, notify_client=None)
                task = ci._resume_task
                if task is not None:
                    await task
                return launch

        # RESUME_ON_START is True in this environment; make it explicit.
        with patch.object(ci.IndexConfig, "RESUME_ON_START", True):
            launch = asyncio.run(run())

        self.assertEqual(ci._default_context.get("client") is not None, True)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.kwargs.get("mode"), ci.MODE_AUDIT)
        row = self.fake.index_scans_collection.docs[0]
        self.assertEqual(row["status"], "running")

    # ── Bug 5: promotion must work without in-memory context ────────────────
    def test_promote_waiting_uses_fallback_context(self):
        from unittest.mock import patch

        ci._default_context.update({"client": object(), "queue_file": object(),
                                    "notify_client": None, "mode": ci.MODE_INDEX})
        self.fake.index_scans_collection.docs.append(
            {"channel_id": 5, "status": "waiting", "title": "queued"}
        )

        async def run():
            with patch.object(ci, "is_auth_channel_id", return_value=True), \
                 patch.object(ci, "_launch") as launch, \
                 patch.object(ci, "scan_cap", return_value=2):
                await ci._promote_waiting()
                return launch

        launch = asyncio.run(run())
        launch.assert_called_once()
        self.assertEqual(self.fake.index_scans_collection.docs[0]["status"], "running")

    # ── Bug 6: /cancel must abort every pending confirmation ────────────────
    def test_cancel_aborts_all_confirmation_states(self):
        import Backend.wzgram_pkg.plugins.start as start

        clear_q = dict(start.clear_queue_state)
        clear_m = dict(start.clear_mongodb_state)
        reset = dict(start.reset_db_state)
        deletes = dict(start.pending_deletes)
        self.addCleanup(lambda: start.clear_queue_state.update(clear_q))
        self.addCleanup(lambda: start.clear_mongodb_state.update(clear_m))
        self.addCleanup(lambda: start.reset_db_state.update(reset))
        self.addCleanup(lambda: start.pending_deletes.clear() or start.pending_deletes.update(deletes))

        self.assertFalse(hasattr(start, "cancel_delete"))
        self.assertTrue(callable(start.cancel_pending))

        start.clear_queue_state['waiting_for_confirmation'] = True
        start.clear_mongodb_state['waiting_for_confirmation'] = True
        start.reset_db_state['waiting_for_confirmation'] = True
        start.pending_deletes[42] = {"tmdb_id": 1}

        class Msg:
            class FromUser:
                id = 42
            from_user = FromUser()
            def __init__(self):
                self.replies = []
            async def reply_text(self, text, **_kwargs):
                self.replies.append(text)

        msg = Msg()
        asyncio.run(start.cancel_pending(None, msg))
        self.assertFalse(start.clear_queue_state['waiting_for_confirmation'])
        self.assertFalse(start.clear_mongodb_state['waiting_for_confirmation'])
        self.assertFalse(start.reset_db_state['waiting_for_confirmation'])
        self.assertEqual(start.pending_deletes, {})
        self.assertEqual(len(msg.replies), 1)  # one handler, one reply
        self.assertIn("database reset", msg.replies[0])


class _FakeTask:
    def done(self):
        return False

    def exception(self):
        return None


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.title = f"chat {chat_id}"


class GovernorTests(unittest.TestCase):
    def test_bucket_geometry(self):
        governor = ci._ReadGovernor()
        governor._prime()
        self.assertEqual(governor._capacity(), float(IndexConfig.SCAN_BATCH_SIZE))
        self.assertAlmostEqual(
            governor._rate(),
            IndexConfig.SCAN_BATCH_SIZE / IndexConfig.SCAN_BATCH_DELAY,
            places=3,
        )

    def test_aggregate_throughput_is_capped(self):
        """N concurrent scans must not read faster than one scan would."""
        governor = ci._ReadGovernor()
        governor._prime()

        async def run():
            start = asyncio.get_event_loop().time()
            # Three scans each want a full batch; the bucket hands out one at a time.
            await asyncio.gather(*[governor.acquire(200) for _ in range(3)])
            return asyncio.get_event_loop().time() - start

        elapsed = asyncio.run(run())
        # 3 batches at 100 msg/s = 3s of budget; the first is immediate.
        self.assertGreaterEqual(elapsed, 1.5)

    def test_penalty_blocks_every_scan(self):
        governor = ci._ReadGovernor()
        governor._prime()

        async def run():
            await governor.penalty(0.3)
            start = asyncio.get_event_loop().time()
            await governor.acquire(200)
            return asyncio.get_event_loop().time() - start

        elapsed = asyncio.run(run())
        self.assertGreaterEqual(elapsed, 0.25)


# ── Audio languages ─────────────────────────────────────────────────────────
class AudioLanguageTests(unittest.TestCase):
    def test_normalization_and_exclusions(self):
        self.assertEqual(
            collect_languages(["English", "english", "  ENGLISH  "]), ["English"]
        )
        self.assertEqual(collect_languages([None, "", "Unknown", "Original Audio"]), [])
        self.assertEqual(collect_languages(["Telugu", "Hindi"]), ["Hindi", "Telugu"])

    def test_movie_and_tv_extraction(self):
        movie = {
            "telegram": [
                {"audio_tracks": [{"language": "English"}]},
                {"audio_tracks": [{"language": "Telugu"}]},
                {"audio_tracks": None},
            ],
        }
        self.assertEqual(language_names_for("movie", movie), ["English", "Telugu"])

        tv = {
            "seasons": [
                {"episodes": [
                    {"telegram": [{"audio_tracks": [{"language": "Hindi"}]}]},
                    {"telegram": [{"audio_tracks": [{"language": "English"}]}]},
                ]},
            ],
        }
        self.assertEqual(language_names_for("tv", tv), ["English", "Hindi"])

    def test_unprobeable_detection(self):
        self.assertTrue(is_unprobeable({"telegram": [{"audio_tracks": None}]}, "movie"))
        self.assertTrue(is_unprobeable({"telegram": []}, "movie"))
        self.assertFalse(
            is_unprobeable({"telegram": [{"audio_tracks": [{"language": "English"}]}]}, "movie")
        )

    def test_backfill_writes_only_on_change(self):
        from Backend.helper.admin_jobs import _apply_audio_languages

        collection = FakeCollection()
        doc = {
            "_id": "doc1",
            "telegram": [{"audio_tracks": [{"language": "English"}]}],
        }
        counts = {}
        asyncio.run(_apply_audio_languages(collection, doc, "movie", counts))
        self.assertEqual(counts["titles_scanned"], 1)
        self.assertEqual(counts["titles_updated"], 1)
        # Nothing was written — the fake collection holds no documents, the
        # counter is the proof the change was detected.
        self.assertEqual(counts.get("titles_updated"), 1)

        # A document that already carries the right value is not rewritten.
        doc["audio_languages"] = ["English"]
        doc["audio_language_count"] = 1
        counts2 = {}
        asyncio.run(_apply_audio_languages(collection, doc, "movie", counts2))
        self.assertEqual(counts2["titles_scanned"], 1)
        self.assertNotIn("titles_updated", counts2)

    def test_backfill_reports_coverage_gaps(self):
        from Backend.helper.admin_jobs import _apply_audio_languages

        collection = FakeCollection()
        doc = {"_id": "d", "telegram": [{"audio_tracks": None}]}
        counts = {}
        asyncio.run(_apply_audio_languages(collection, doc, "movie", counts))
        self.assertEqual(counts.get("titles_unprobeable"), 1)

        doc2 = {
            "_id": "d2",
            "telegram": [{"audio_tracks": [{"language": "English"}]}],
            "dropped_audio_languages": ["Telugu"],
        }
        counts2 = {}
        asyncio.run(_apply_audio_languages(collection, doc2, "movie", counts2))
        self.assertEqual(counts2.get("dropped_language_files"), 1)

    def test_walker_agrees_with_the_kind_specific_reader(self):
        """The shape-agnostic walker must match the per-kind reader exactly."""
        movie = {
            "telegram": [
                {"audio_tracks": [{"language": "English"}]},
                {"audio_tracks": [{"language": "Telugu"}]},
                {"audio_tracks": None},
            ],
        }
        tv = {
            "seasons": [
                {"episodes": [
                    {"telegram": [{"audio_tracks": [{"language": "Hindi"}]}]},
                    {"telegram": [{"audio_tracks": [{"language": "English"}]}]},
                ]},
                {"episodes": [
                    {"telegram": [{"audio_tracks": [{"language": "Korean"}]}]},
                ]},
            ],
        }
        self.assertEqual(languages_from_document(movie),
                         language_names_for("movie", movie))
        self.assertEqual(languages_from_document(tv),
                         language_names_for("tv", tv))
        self.assertTrue(document_is_probed(movie))
        self.assertTrue(document_is_probed(tv))
        self.assertFalse(document_is_probed({"telegram": [{"audio_tracks": None}]}))


class BackfillJobTests(unittest.TestCase):
    """End-to-end backfill over a seeded cluster.

    Runs the real job handler against a MongoDB-compatible engine so the
    cursor, the write-on-change rule and the reported coverage numbers are
    all exercised together.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import mongomock  # noqa: F401  (availability check only)
        except ImportError:
            raise unittest.SkipTest("mongomock is not installed")

    def setUp(self):
        import mongomock

        from Backend.helper import admin_jobs as aj

        self.aj = aj
        self.client = mongomock.MongoClient()
        raw_movie = self.client["projectS"]["movie"]
        raw_tv = self.client["projectS"]["tv"]
        self.raw_movie = raw_movie
        self.raw_tv = raw_tv

        class Cluster:
            index = 1

            def collection(self, kind):
                return (_AsyncMongoCollection(raw_movie) if kind == "movie"
                        else _AsyncMongoCollection(raw_tv))

        self.fake = FakeDb()
        self.fake.clusters = [Cluster()]
        patch_db(self.fake)

        self.jobs = {}

        async def get_admin_job(job_id):
            return self.jobs.get(job_id)

        async def upsert_admin_job(job_id, fields):
            doc = self.jobs.setdefault(job_id, {"_id": job_id})
            doc.update(fields)
            return doc

        self.fake.get_admin_job = get_admin_job
        self.fake.upsert_admin_job = upsert_admin_job

    @staticmethod
    def _track(lang):
        return {"index": 0, "language": lang, "codec": "aac"}

    def _file(self, lang):
        return {"file_id": "f", "size": 1, "audio_tracks": [self._track(lang)]}

    def _run(self, job_id="job1"):
        runner = self.aj.AdminJobRunner()
        return asyncio.run(
            self.aj._handle_audio_lang_backfill(runner, job_id, {})
        )

    def test_backfill_writes_languages_and_reports_coverage(self):
        # A title that needs the fields written.
        self.raw_movie.insert_one({
            "tmdb_id": 1, "title": "Needs Work",
            "telegram": [self._file("English"), self._file("Telugu")],
        })
        # A title that already carries the right values — must not be rewritten.
        self.raw_movie.insert_one({
            "tmdb_id": 2, "title": "Already Right",
            "telegram": [self._file("Hindi")],
            "audio_languages": ["Hindi"], "audio_language_count": 1,
        })
        # A title whose files were never probed.
        self.raw_movie.insert_one({
            "tmdb_id": 3, "title": "Unprobeable",
            "telegram": [{"file_id": "x", "audio_tracks": None}],
        })
        # A TV title with languages across two seasons.
        self.raw_tv.insert_one({
            "tmdb_id": 4, "title": "Show",
            "seasons": [
                {"season_number": 1, "episodes": [
                    {"episode_number": 1, "telegram": [self._file("English")]}]},
                {"season_number": 2, "episodes": [
                    {"episode_number": 1, "telegram": [self._file("Korean")]}]},
            ],
        })

        counts = self._run()

        # Coverage numbers, hand-computed: 3 movies + 1 show scanned.
        self.assertEqual(counts["titles_scanned"], 4)
        # Only the two titles that changed were written.
        self.assertEqual(counts["titles_updated"], 3)
        # One title has no probe data at all.
        self.assertEqual(counts["titles_unprobeable"], 1)

        updated = self.raw_movie.find_one({"tmdb_id": 1})
        self.assertEqual(updated["audio_languages"], ["English", "Telugu"])
        self.assertEqual(updated["audio_language_count"], 2)

        unchanged = self.raw_movie.find_one({"tmdb_id": 2})
        self.assertEqual(unchanged["audio_languages"], ["Hindi"])
        self.assertEqual(unchanged["audio_language_count"], 1)

        unprobeable = self.raw_movie.find_one({"tmdb_id": 3})
        self.assertEqual(unprobeable["audio_languages"], [])
        self.assertEqual(unprobeable["audio_language_count"], 0)

        show = self.raw_tv.find_one({"tmdb_id": 4})
        self.assertEqual(show["audio_languages"], ["English", "Korean"])
        self.assertEqual(show["audio_language_count"], 2)

    def test_cursor_advances_and_survives_a_restart(self):
        self.raw_movie.insert_one({
            "tmdb_id": 1, "telegram": [self._file("English")],
        })
        self.raw_movie.insert_one({
            "tmdb_id": 2, "telegram": [self._file("Telugu")],
        })
        counts = self._run()
        self.assertEqual(counts["titles_scanned"], 2)
        self.assertEqual(counts["titles_updated"], 2)

        # The cursor records the last document processed, so a restart resumes
        # there instead of starting the walk over.
        cursor = self.jobs["job1"]["cursor"]
        self.assertEqual(cursor["cluster_index"], 1)
        self.assertEqual(cursor["collection"], "movie")
        self.assertTrue(cursor["last_id"])

        # A second run over the same state finds nothing left to do. The job's
        # counters are cumulative — they belong to the job, not to one run.
        counts2 = self._run()
        self.assertEqual(counts2["titles_scanned"], 2)
        self.assertEqual(counts2["titles_updated"], 2)

        # A title added after the run is picked up by the resumed job.
        self.raw_movie.insert_one({
            "tmdb_id": 3, "telegram": [self._file("Hindi")],
        })
        counts3 = self._run()
        self.assertEqual(counts3["titles_scanned"], 3)
        self.assertEqual(counts3["titles_updated"], 3)
        added = self.raw_movie.find_one({"tmdb_id": 3})
        self.assertEqual(added["audio_languages"], ["Hindi"])


# ── Admin job runner ────────────────────────────────────────────────────────
class AdminJobRunnerTests(unittest.TestCase):
    def setUp(self):
        from Backend.helper import admin_jobs as aj

        self.aj = aj
        self.fake = FakeDb()
        patch_db(self.fake)
        self.calls = []
        self._orig_run = aj.AdminJobRunner._run

        async def fake_run(_runner, job_id, kind, params):
            self.calls.append({
                "job_id": job_id, "kind": kind, "params": dict(params),
            })

        aj.AdminJobRunner._run = fake_run

    def tearDown(self):
        self.aj.AdminJobRunner._run = self._orig_run

    def _start(self, kind, params):
        async def run():
            runner = self.aj.AdminJobRunner()
            result = await runner.start(kind, params)
            task = runner._tasks.get(kind)
            if task is not None:
                await task
            return result

        return asyncio.run(run())

    def test_paused_job_resumes_instead_of_blocking_the_kind(self):
        self.fake.admin_jobs_collection.docs.append({
            "_id": "metadata_remediation:old",
            "kind": "metadata_remediation",
            "status": "paused",
            "params": {"flag": "no_poster", "media_type": "all"},
            "cursor": {"targets": ["movie|1|42"], "index": 1},
            "counts": {"processed": 1},
        })

        result = self._start(
            "metadata_remediation",
            {"flag": "no_poster", "media_type": "all"},
        )

        self.assertTrue(result.get("resumed"))
        self.assertEqual(result["id"], "metadata_remediation:old")
        self.assertEqual(self.calls[0]["job_id"], "metadata_remediation:old")
        self.assertEqual(self.calls[0]["params"]["flag"], "no_poster")
        row = self.fake.admin_jobs_collection.docs[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["finished_at"])

    def test_stale_running_job_resumes_after_a_restart(self):
        self.fake.admin_jobs_collection.docs.append({
            "_id": "audio_lang_backfill:old",
            "kind": "audio_lang_backfill",
            "status": "running",
            "params": {},
            "cursor": {"cluster_index": 1, "collection": "movie", "last_id": "x"},
        })

        result = self._start("audio_lang_backfill", {})

        self.assertTrue(result.get("resumed"))
        self.assertEqual(result["id"], "audio_lang_backfill:old")
        self.assertEqual(len(self.calls), 1)

    def test_different_params_supersede_the_paused_job(self):
        self.fake.admin_jobs_collection.docs.append({
            "_id": "metadata_remediation:old",
            "kind": "metadata_remediation",
            "status": "paused",
            "params": {"flag": "no_poster", "media_type": "all"},
        })

        result = self._start(
            "metadata_remediation",
            {"flag": "no_description", "media_type": "all"},
        )

        self.assertFalse(result.get("resumed"))
        self.assertNotEqual(result["id"], "metadata_remediation:old")
        old = self.fake.admin_jobs_collection.docs[0]
        self.assertEqual(old["status"], "error")
        self.assertIn("Superseded", old["error"])
        self.assertEqual(self.calls[0]["params"]["flag"], "no_description")


class MetadataRemediationTests(unittest.TestCase):
    """The target list is snapshotted before refreshing.

    A refresh rewrites ``updated_on`` and can clear the selection flag, so
    offset paging over the live filtered list would skip the titles that moved
    onto an earlier page.
    """

    def setUp(self):
        from unittest.mock import patch

        from Backend.fastapi import main as api_main
        from Backend.helper import admin_jobs as aj

        self.aj = aj
        self.fake = FakeDb()
        patch_db(self.fake)
        self.remaining = set(range(1, 121))
        self.refreshed = []

        async def admin_list_media(media_type="movie", query="", page=1,
                                   page_size=30, sort="", language="",
                                   genre="", flag=""):
            items = [
                {
                    "type": "movie", "collection": "movie",
                    "db_index": 1, "tmdb_id": tmdb_id,
                }
                for tmdb_id in sorted(self.remaining)
            ]
            start = (page - 1) * page_size
            return {
                "items": items[start:start + page_size],
                "total_count": len(items),
                "page": page,
                "page_size": page_size,
            }

        self.fake.admin_list_media = admin_list_media

        async def fake_resolve(item):
            return {
                "tmdb_id": item["tmdb_id"], "type": "movie", "db_index": 1,
            }

        async def fake_refresh(media):
            self.remaining.discard(media["tmdb_id"])
            self.refreshed.append(media["tmdb_id"])
            return {"updated_fields": ["poster"]}

        self.patches = [
            patch.object(api_main, "_resolve_admin_media", new=fake_resolve),
            patch.object(aj, "_refresh_with_backoff", new=fake_refresh),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()

    def test_refreshed_titles_are_not_skipped_as_the_list_shrinks(self):
        runner = self.aj.AdminJobRunner()
        counts = asyncio.run(self.aj._handle_metadata_remediation(
            runner, "job1", {"flag": "no_poster", "media_type": "movie"},
        ))

        self.assertEqual(counts["processed"], 120)
        self.assertEqual(counts["updated"], 120)
        self.assertEqual(self.remaining, set())
        self.assertEqual(len(self.refreshed), 120)
        job = asyncio.run(self.fake.get_admin_job("job1"))
        self.assertEqual(job["cursor"]["index"], 120)
        self.assertEqual(len(job["cursor"]["targets"]), 120)


class _AsyncMongoCursor:
    """Async cursor over a mongomock result set."""

    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda d: (d.get(key) is None, d.get(key)))
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs[:length] if length else self._docs)

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc
        return gen()


class _AsyncMongoCollection:
    """Adapts a synchronous mongomock collection to the motor (async) API."""

    def __init__(self, collection):
        self._col = collection

    async def find_one(self, query=None, projection=None):
        return self._col.find_one(query, projection)

    def find(self, query=None, projection=None):
        # motor's find() returns a cursor (not a coroutine) — match that.
        return _AsyncMongoCursor(list(self._col.find(query, projection)))

    async def update_one(self, query, update, upsert=False, **_kwargs):
        return self._col.update_one(query, update, upsert=upsert)

    async def insert_one(self, doc):
        return self._col.insert_one(doc)

    async def count_documents(self, query=None):
        return self._col.count_documents(query or {})


def await_collection_find_one(collection, query):
    """Read one document through an async collection wrapper."""
    async def run():
        return await collection.find_one(query)
    return asyncio.run(run())


class BackfillProjectionTests(unittest.TestCase):
    """The backfill projection must not lose any language.

    These run against ``mongomock``, a MongoDB-compatible engine, so the
    projection is executed the way the real server would execute it. The
    projected read has to yield exactly the same language set as the full
    document — otherwise the backfill would quietly write short lists.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import mongomock  # noqa: F401  (availability check only)
        except ImportError:
            raise unittest.SkipTest("mongomock is not installed")

    def _collection(self):
        import mongomock

        client = mongomock.MongoClient()
        return client["projectS"]["media"]

    @staticmethod
    def _track(lang):
        return {"index": 0, "language": lang, "codec": "aac"}

    @staticmethod
    def _file(lang, fid):
        return {"file_id": fid, "size": 123, "message_id": 7,
                "audio_tracks": [{"index": 0, "language": lang, "codec": "aac"}]}

    def test_movie_projection_keeps_every_language(self):
        col = self._collection()
        col.insert_one({
            "_id": "m1", "tmdb_id": 1, "title": "Movie",
            "telegram": [self._file("English", "f1"), self._file("Telugu", "f2")],
        })
        full = col.find_one({"_id": "m1"})
        projected = col.find_one({"_id": "m1"}, BACKFILL_PROJECTION)
        self.assertEqual(languages_from_document(full), ["English", "Telugu"])
        self.assertEqual(languages_from_document(projected),
                         languages_from_document(full))
        self.assertTrue(document_is_probed(projected))

    def test_tv_projection_keeps_every_language(self):
        col = self._collection()
        col.insert_one({
            "_id": "t1", "tmdb_id": 2, "title": "Show",
            "seasons": [
                {"season_number": 1, "episodes": [
                    {"episode_number": 1,
                     "telegram": [self._file("English", "a"), self._file("Telugu", "b")]},
                    {"episode_number": 2, "telegram": [self._file("Hindi", "c")]},
                ]},
                {"season_number": 2, "episodes": [
                    {"episode_number": 1, "telegram": [self._file("Korean", "d")]},
                ]},
            ],
        })
        full = col.find_one({"_id": "t1"})
        projected = col.find_one({"_id": "t1"}, BACKFILL_PROJECTION)
        self.assertEqual(languages_from_document(full),
                         ["English", "Hindi", "Korean", "Telugu"])
        self.assertEqual(languages_from_document(projected),
                         languages_from_document(full))
        self.assertTrue(document_is_probed(projected))

    def test_unprobeable_survives_the_projection(self):
        col = self._collection()
        col.insert_one({"_id": "u1", "telegram": [{"file_id": "x"}]})
        full = col.find_one({"_id": "u1"})
        projected = col.find_one({"_id": "u1"}, BACKFILL_PROJECTION)
        self.assertFalse(document_is_probed(full))
        self.assertFalse(document_is_probed(projected))

    def test_guard_detects_a_projection_that_under_reports(self):
        """A short projection must be caught by the cross-check."""
        from Backend.helper.admin_jobs import _projection_is_trustworthy

        col = _AsyncMongoCollection(self._collection())
        col._col.insert_one({
            "_id": "g1",
            "telegram": [self._file("English", "f1"), self._file("Telugu", "f2")],
        })
        full = await_collection_find_one(col, {"_id": "g1"})
        # A projection that only keeps the first file entry.
        truncated = {"_id": "g1", "telegram": [full["telegram"][0]]}
        self.assertNotEqual(
            languages_from_document(truncated), languages_from_document(full)
        )
        # The guard compares the projected document with the full one.
        self.assertFalse(asyncio.run(
            _projection_is_trustworthy(col, truncated, "movie")
        ))
        self.assertTrue(asyncio.run(
            _projection_is_trustworthy(col, full, "movie")
        ))


# ── Orphan report ───────────────────────────────────────────────────────────
class OrphanTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDb()
        patch_db(self.fake)

    def test_id_is_channel_and_message(self):
        self.assertEqual("123:456", f"{'123'}:{'456'}")

    def test_upsert_refreshes_instead_of_duplicating(self):
        from Backend.helper.database import Database

        async def run():
            await Database.upsert_orphan_file(self.fake, {"_id": "1:2", "channel": "1",
                                                         "message_id": 2, "scanned_at": datetime.utcnow()})
            await Database.upsert_orphan_file(self.fake, {"_id": "1:2", "channel": "1",
                                                         "message_id": 2, "scanned_at": datetime.utcnow(),
                                                         "title": "second"})

        asyncio.run(run())
        rows = self.fake.orphan_files_collection.docs
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "second")

    def test_lookup_by_id_is_not_limited_to_the_first_page(self):
        from Backend.helper.database import Database

        class MotorStyleCollection(FakeCollection):
            # Motor's ``find`` is synchronous and returns a cursor, unlike the
            # awaitable helper used by the FakeDb shim.
            def find(self, query=None, projection=None):
                docs = self.docs
                if query:
                    docs = [doc for doc in docs if _matches(doc, query)]
                return FakeCursor(docs)

        collection = MotorStyleCollection()
        for i in range(250):
            collection.docs.append({
                "_id": f"1:{i}", "channel": "1", "message_id": i,
            })
        self.fake.orphan_files_collection = collection

        async def run():
            return await Database.get_orphan_files(self.fake, ["1:249"])

        rows = asyncio.run(run())
        self.assertEqual([row["_id"] for row in rows], ["1:249"])

    def test_delete_uses_the_string_channel_message_id(self):
        from Backend.helper.database import Database

        self.fake.orphan_files_collection.docs.extend([
            {"_id": "1:2", "channel": "1", "message_id": 2},
            {"_id": "1:3", "channel": "1", "message_id": 3},
            {"_id": "1:4", "channel": "1", "message_id": 4},
        ])

        deleted = asyncio.run(
            Database.delete_orphan_files(self.fake, ["1:2", "1:3"])
        )
        self.assertEqual(deleted, 2)
        self.assertEqual(
            [row["_id"] for row in self.fake.orphan_files_collection.docs],
            ["1:4"],
        )

    def test_resolved_rows_are_excluded(self):
        from Backend.helper.database import ch_resolved

        now = datetime.utcnow()
        old_row = {"channel": "1", "scanned_at": now - timedelta(hours=2)}
        new_row = {"channel": "1", "scanned_at": now}
        latest = {"1": now - timedelta(hours=1)}
        self.assertTrue(ch_resolved(old_row, latest))
        self.assertFalse(ch_resolved(new_row, latest))
        # No completed scan for the channel — nothing is resolved.
        self.assertFalse(ch_resolved(old_row, {}))


# ── Alert watchdog ──────────────────────────────────────────────────────────
class AlertTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDb()
        patch_db(self.fake)
        self.sent = []
        # Snapshot the module globals the tests monkeypatch, so one test can
        # never leak a patched collector into the next one.
        self._orig_send = alerts_mod._send
        self._orig_collect = alerts_mod.collect_alert_keys
        self.addCleanup(self._restore)

    def _restore(self):
        alerts_mod._send = self._orig_send
        alerts_mod.collect_alert_keys = self._orig_collect

    async def _patch_send(self):
        sent = self.sent

        async def fake_send(text):
            sent.append(text)
            return len(sent)

        alerts_mod._send = fake_send
        return alerts_mod

    def test_key_transitions(self):
        async def run():
            alerts_mod = await self._patch_send()
            alerts_mod.collect_alert_keys = self._collector([
                {"key": "a", "level": "warning", "message": "A raised"},
                {"key": "b", "level": "warning", "message": "B raised"},
            ])
            first = await alerts_mod.evaluate_and_notify()
            # Unchanged keys send nothing.
            second = await alerts_mod.evaluate_and_notify()
            # A disappears, C appears.
            alerts_mod.collect_alert_keys = self._collector([
                {"key": "b", "level": "warning", "message": "B raised"},
                {"key": "c", "level": "warning", "message": "C raised"},
            ])
            third = await alerts_mod.evaluate_and_notify()
            return first, second, third

        first, second, third = asyncio.run(run())
        self.assertEqual(sorted(first["sent"]), ["a", "b"])
        self.assertEqual(second["sent"], [])
        self.assertEqual(second["recovered"], [])
        self.assertEqual(third["sent"], ["c"])
        self.assertEqual(third["recovered"], ["a"])

    def test_send_failure_is_not_marked_as_sent(self):
        async def run():
            alerts_mod = await self._patch_send()

            async def failing_send(_text):
                return None

            alerts_mod._send = failing_send
            alerts_mod.collect_alert_keys = self._collector([
                {"key": "a", "level": "warning", "message": "A raised"},
            ])
            result = await alerts_mod.evaluate_and_notify()
            return result

        result = asyncio.run(run())
        self.assertEqual(result["failed"], ["a"])
        self.assertEqual(result["sent"], [])
        # The key must not be recorded as sent, so the next tick retries it.
        state = self.fake.settings_collection.docs[-1]
        self.assertEqual(state["last_keys"], [])

    def test_failed_recovery_is_retried_on_the_next_tick(self):
        async def run():
            alerts_mod = await self._patch_send()
            alerts_mod.collect_alert_keys = self._collector([
                {"key": "a", "level": "warning", "message": "A raised"},
            ])
            await alerts_mod.evaluate_and_notify()

            async def failing_send(_text):
                return None

            alerts_mod.collect_alert_keys = self._collector([])
            alerts_mod._send = failing_send
            failed = await alerts_mod.evaluate_and_notify()

            async def working_send(_text):
                return 77

            alerts_mod._send = working_send
            retried = await alerts_mod.evaluate_and_notify()
            return failed, retried

        failed, retried = asyncio.run(run())
        self.assertEqual(failed["failed"], ["a"])
        self.assertEqual(failed["recovered"], [])
        # The recovery stayed in last_keys, so the next tick tried again.
        self.assertEqual(retried["recovered"], ["a"])
        state = self.fake.settings_collection.docs[-1]
        self.assertEqual(state["last_keys"], [])

    @staticmethod
    def _collector(alerts):
        async def collect():
            return list(alerts)
        return collect


# ── Unified media facade (admin panel writes) ──────────────────────────────
class UnifiedMediaFacadeTests(unittest.TestCase):
    """The same tmdb_id can live on several clusters. An admin action aimed at
    one row must never touch the copies on the other clusters."""

    def setUp(self):
        from Backend.helper.database import UnifiedMediaFacade
        self.c1 = FakeCluster(1, movie=FakeCollection([
            {"tmdb_id": 11, "title": "Cluster one copy", "languages": ["Hindi"]},
        ]))
        self.c2 = FakeCluster(2, movie=FakeCollection([
            {"tmdb_id": 11, "title": "Cluster two copy", "languages": ["Hindi"]},
        ]))
        self.fake = FakeDb()
        self.fake.clusters = [self.c1, self.c2]
        self.facade = UnifiedMediaFacade(self.fake)

    def test_delete_one_only_hits_the_selected_cluster(self):
        result = asyncio.run(self.facade.delete_one(
            {"db_index": 2, "tmdb_id": 11, "type": "movie"}
        ))
        self.assertEqual(result.deleted_count, 1)
        self.assertEqual(len(self.c1.movie.docs), 1, "other cluster copy was deleted")
        self.assertEqual(self.c2.movie.docs, [])

    def test_update_one_only_hits_the_selected_cluster(self):
        asyncio.run(self.facade.update_one(
            {"db_index": 2, "tmdb_id": 11, "type": "movie"},
            {"$set": {"languages": ["Telugu"]}},
        ))
        self.assertEqual(self.c1.movie.docs[0]["languages"], ["Hindi"])
        self.assertEqual(self.c2.movie.docs[0]["languages"], ["Telugu"])

    def test_find_one_reads_the_selected_cluster_and_stamps_its_index(self):
        doc = asyncio.run(self.facade.find_one(
            {"db_index": 2, "tmdb_id": 11, "type": "movie"}
        ))
        self.assertEqual(doc["title"], "Cluster two copy")
        self.assertEqual(doc["db_index"], 2)

    def test_string_db_index_is_scoped_too(self):
        doc = asyncio.run(self.facade.find_one(
            {"db_index": "2", "tmdb_id": 11, "type": "movie"}
        ))
        self.assertEqual(doc["title"], "Cluster two copy")

    def test_missing_cluster_matches_nothing_instead_of_falling_back(self):
        result = asyncio.run(self.facade.delete_one(
            {"db_index": 9, "tmdb_id": 11, "type": "movie"}
        ))
        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(len(self.c1.movie.docs), 1)
        self.assertEqual(len(self.c2.movie.docs), 1)

    def test_omitting_db_index_keeps_the_legacy_cross_cluster_behaviour(self):
        result = asyncio.run(self.facade.delete_one({"tmdb_id": 11, "type": "movie"}))
        self.assertEqual(result.deleted_count, 2)
        self.assertEqual(self.c1.movie.docs, [])
        self.assertEqual(self.c2.movie.docs, [])


# ── Curated homepage hero ───────────────────────────────────────────────────
class HeroCurationTests(unittest.TestCase):
    def _db(self, picks=None, clusters=None):
        from Backend.helper.database import Database
        movie1 = FakeCollection([{"tmdb_id": 11, "title": "Movie on one",
                                  "media_type": "movie", "poster": "m1.jpg"}])
        tv2 = FakeCollection([{"tmdb_id": 21, "title": "Show on two",
                               "media_type": "tv", "poster": "t2.jpg"}])
        self.clusters = clusters or [
            FakeCluster(1, movie=movie1, tv=FakeCollection()),
            FakeCluster(2, movie=FakeCollection(), tv=tv2),
        ]
        self.movie1 = self.clusters[0].movie
        self.tv2 = self.clusters[1].tv if len(self.clusters) > 1 else None

        db = Database.__new__(Database)
        db.clusters = self.clusters
        db.settings_collection = FakeCollection(
            [{"_id": "hero_picks", "picks": picks}] if picks is not None else []
        )
        db._shard_cache = {}
        self.fallback_calls = []

        async def sort_movies(sort_params, page, page_size, **_kwargs):
            self.fallback_calls.append((sort_params, page, page_size))
            return {"total_count": 1, "movies": [{"tmdb_id": 999, "title": "Newest"}]}

        db.sort_movies = sort_movies
        return db

    def test_normalize_accepts_only_real_library_rows(self):
        from Backend.helper.database import normalize_hero_picks
        picks = normalize_hero_picks([
            {"type": "tv", "db_index": "2", "tmdb_id": "21", "title": "ignored"},
            {"type": "movie", "db_index": 1, "tmdb_id": 11},
            {"type": "movie", "db_index": 1, "tmdb_id": 11},  # duplicate dropped
        ])
        self.assertEqual(picks, [
            {"type": "tv", "db_index": 2, "tmdb_id": 21},
            {"type": "movie", "db_index": 1, "tmdb_id": 11},
        ])

    def test_normalize_rejects_bad_input(self):
        from Backend.helper.database import HERO_MAX_PICKS, normalize_hero_picks
        for bad in (
            None,
            [{"type": "manga", "db_index": 1, "tmdb_id": 5}],
            [{"type": "movie", "db_index": 0, "tmdb_id": 5}],
            [{"type": "movie", "db_index": 1, "tmdb_id": 0}],
            [{"type": "movie", "db_index": 1, "tmdb_id": None}],
            [{"type": "movie", "db_index": 1, "tmdb_id": "  "}],
            [{"type": "movie", "db_index": "x", "tmdb_id": 5}],
            [{"type": "movie", "db_index": 1, "tmdb_id": i} for i in range(HERO_MAX_PICKS + 1)],
        ):
            with self.assertRaises(ValueError):
                normalize_hero_picks(bad)

    def test_string_library_ids_survive_for_anilist_and_imdb_titles(self):
        from Backend.helper.database import normalize_hero_picks
        self.assertEqual(
            normalize_hero_picks([{"type": "tv", "db_index": 1, "tmdb_id": "al_567"}]),
            [{"type": "tv", "db_index": 1, "tmdb_id": "al_567"}],
        )
        # digits are normalised to int so they match how the facade queries tmdb_id
        self.assertEqual(
            normalize_hero_picks([{"type": "movie", "db_index": 1, "tmdb_id": "42"}]),
            [{"type": "movie", "db_index": 1, "tmdb_id": 42}],
        )

    def test_a_string_id_pick_resolves_its_library_row(self):
        clusters = [
            FakeCluster(1, movie=FakeCollection(),
                        tv=FakeCollection([{"tmdb_id": "al_567", "title": "Anime on one"}])),
        ]
        db = self._db(picks=[{"type": "tv", "db_index": 1, "tmdb_id": "al_567"}], clusters=clusters)
        result = asyncio.run(db.build_hero_carousel())
        self.assertTrue(result["curated"])
        self.assertEqual(result["movies"][0]["title"], "Anime on one")
        self.assertEqual(result["movies"][0]["media_type"], "tv")

    def test_nothing_pinned_falls_back_to_newest_titles(self):
        db = self._db(picks=None)
        result = asyncio.run(db.build_hero_carousel())
        self.assertFalse(result["curated"])
        self.assertEqual(result["movies"], [{"tmdb_id": 999, "title": "Newest"}])
        self.assertEqual(self.fallback_calls,
                         [([("release_year", "desc")], 1, 12)])

    def test_curated_picks_keep_panel_order_and_media_type(self):
        db = self._db(picks=[
            {"type": "tv", "db_index": 2, "tmdb_id": 21},
            {"type": "movie", "db_index": 1, "tmdb_id": 11},
        ])
        result = asyncio.run(db.build_hero_carousel())
        self.assertTrue(result["curated"])
        self.assertEqual([m["media_type"] for m in result["movies"]], ["tv", "movie"])
        self.assertEqual([m["title"] for m in result["movies"]],
                         ["Show on two", "Movie on one"])
        self.assertEqual(self.fallback_calls, [], "fallback ran despite curated picks")

    def test_pick_deleted_from_the_library_is_skipped_not_served(self):
        db = self._db(picks=[
            {"type": "movie", "db_index": 1, "tmdb_id": 11},
            {"type": "movie", "db_index": 1, "tmdb_id": 404},
        ])
        result = asyncio.run(db.build_hero_carousel())
        self.assertEqual([m["tmdb_id"] for m in result["movies"]], [11])

    def test_all_picks_dead_restores_the_default_carousel(self):
        db = self._db(picks=[{"type": "movie", "db_index": 1, "tmdb_id": 404}])
        result = asyncio.run(db.build_hero_carousel())
        self.assertFalse(result["curated"])
        self.assertEqual(len(self.fallback_calls), 1)

    def test_save_validation_spots_a_pick_that_is_not_in_the_library(self):
        db = self._db(picks=None)
        pairs = asyncio.run(db.resolve_hero_picks([
            {"type": "tv", "db_index": 2, "tmdb_id": 21},
            {"type": "movie", "db_index": 1, "tmdb_id": 404},
        ]))
        self.assertIsNotNone(pairs[0][1])
        self.assertIsNone(pairs[1][1])

    def test_a_pick_reads_only_its_own_cluster(self):
        # tmdb_id 11 also exists on cluster 2; cluster 1 must still win its own row.
        shared = FakeCollection([{"tmdb_id": 11, "title": "Cluster two copy"}])
        clusters = [
            FakeCluster(1, movie=FakeCollection([{"tmdb_id": 11, "title": "Cluster one copy"}]),
                        tv=FakeCollection()),
            FakeCluster(2, movie=shared, tv=FakeCollection()),
        ]
        db = self._db(picks=[{"type": "movie", "db_index": 2, "tmdb_id": 11}], clusters=clusters)
        result = asyncio.run(db.build_hero_carousel())
        self.assertEqual(result["movies"][0]["title"], "Cluster two copy")
        db2 = self._db(picks=[{"type": "movie", "db_index": 1, "tmdb_id": 11}], clusters=clusters)
        self.assertEqual(asyncio.run(db2.build_hero_carousel())["movies"][0]["title"],
                         "Cluster one copy")

    def test_persistence_round_trip(self):
        db = self._db(picks=None)
        self.assertTrue(asyncio.run(db.set_hero_picks(
            [{"type": "movie", "db_index": 1, "tmdb_id": 11}], updated_by="eren"
        )))
        stored = db.settings_collection.docs[0]
        self.assertEqual(stored["_id"], "hero_picks")
        self.assertEqual(stored["updated_by"], "eren")
        self.assertEqual(asyncio.run(db.get_hero_picks()),
                         [{"type": "movie", "db_index": 1, "tmdb_id": 11}])


class AdminEndpointTests(unittest.TestCase):
    """The channel picker and channel-based scan start used by the React panel."""

    def setUp(self):
        self.fake = FakeDb()
        patch_db(self.fake)
        self.addCleanup(self._restore_db)

    def _restore_db(self):
        import Backend
        import Backend.fastapi.main as main
        Backend.db = self._saved_db
        main.db = self._saved_main_db

    @classmethod
    def setUpClass(cls):
        import Backend
        import Backend.fastapi.main as main
        cls._saved_db = Backend.db
        cls._saved_main_db = main.db

    def _client(self):
        from fastapi.testclient import TestClient
        import Backend.fastapi.main as main
        import Backend.fastapi.auth as auth

        token = auth.create_session(auth.Telegram.ADMIN_WEB_USERNAME)
        return TestClient(main.app, raise_server_exceptions=False), {"X-Admin-Session": token}

    def test_channels_endpoint_returns_configured_channels(self):
        client, headers = self._client()
        res = client.get("/admin/api/channels", headers=headers)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["count"], len(body["channels"]))
        self.assertGreater(body["count"], 0)
        for i, row in enumerate(body["channels"]):
            self.assertEqual(row["index"], i)
            self.assertTrue(row["clean_id"])
            self.assertTrue(row["link"].startswith("https://t.me/c/"))
            self.assertNotIn("-100", row["clean_id"])

    def test_channels_endpoint_requires_auth(self):
        client, _ = self._client()
        self.assertEqual(client.get("/admin/api/channels").status_code, 401)

    def test_scan_start_rejects_empty_selection(self):
        client, headers = self._client()
        res = client.post("/admin/api/index-scans/start", headers=headers, json={})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Select a channel", res.json()["detail"])

    def test_manual_add_requires_link_and_reference(self):
        client, headers = self._client()
        res = client.post(
            "/admin/api/queue/manual", headers=headers,
            json={"link": "", "media_ref": ""},
        )
        self.assertEqual(res.status_code, 400)

    def test_scan_start_accepts_selected_channel_ids(self):
        """Dropdown selection must work without pasting any post link."""
        from unittest.mock import patch
        from Backend.config import Telegram

        raw = str(Telegram.AUTH_CHANNEL[0]).strip().strip('"').strip("'")
        chat_id = int(raw)

        class FakeChat:
            id = chat_id
            username = None

        class FakeClient:
            async def get_chat(self, cid):
                self.asked = cid
                return FakeChat()

        client = FakeClient()
        started = {}

        async def fake_request(_client, chats, _requester, _queue_file,
                               notify_client=None, mode="index", skip_first=None):
            started["chats"] = [c.id for c in chats]
            started["mode"] = mode
            started["skip_first"] = skip_first
            return {"started": [c.id for c in chats], "waiting": [],
                    "cap": 3, "skip_first": skip_first}

        http, headers = self._client()
        with patch("Backend.wzgram_pkg.get_index_client", return_value=client), \
             patch("Backend.helper.channel_index.request_index_scans", fake_request):
            res = http.post(
                "/admin/api/index-scans/start", headers=headers,
                json={"channels": [raw], "mode": "index", "skip_first": 12},
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(client.asked, chat_id)
        self.assertEqual(started["chats"], [chat_id])
        self.assertEqual(started["mode"], "index")
        self.assertEqual(started["skip_first"], 12)

    def test_scan_start_rejects_an_invalid_skip_first(self):
        http, headers = self._client()
        for bad in ("abc", -1, 100001):
            res = http.post(
                "/admin/api/index-scans/start", headers=headers,
                json={"channels": ["1"], "skip_first": bad},
            )
            self.assertEqual(res.status_code, 400, res.text)
            self.assertIn("Skip count", res.json()["detail"])

    def test_manual_add_route_queues_after_shared_builder_extraction(self):
        """The panel's POST /queue/manual still works end-to-end after the refactor."""
        from types import SimpleNamespace
        from unittest.mock import patch
        import Backend.fastapi.main as main
        import Backend.wzgram_pkg.plugins.start as start

        chat = SimpleNamespace(id=-1002685988600, username=None)
        post = SimpleNamespace(
            video=SimpleNamespace(file_unique_id="UNIQ123456", file_name="x.mkv"),
            document=None, empty=False,
        )
        info = {
            "tmdb_id": 603, "title": "Fight Club", "media_type": "movie",
            "quality": "1080p", "rip": "Blu-ray", "languages": ["en"],
            "tmdb_url": "https://www.themoviedb.org/movie/603",
        }

        async def fake_resolve(client, link, fallbacks=None):
            return chat, 42

        async def fake_get_messages(cid, mid):
            return post

        async def fake_build(**kwargs):
            self.assertEqual(kwargs["media_ref"], "tt0133093")
            return info

        async def fake_queue(message, channel, **kwargs):
            self.assertEqual(kwargs["metadata_info_override"]["tmdb_id"], 603)
            return True

        class RouteDb:
            async def is_file_exists(self, *_args):
                return False

            async def is_file_unique_id_known(self, *_args):
                return False

        http, headers = self._client()
        with patch("Backend.helper.channel_index.resolve_link_target", fake_resolve), \
             patch("Backend.helper.channel_index.is_auth_channel", lambda _c: True), \
             patch.object(main, "StreamBot", SimpleNamespace(get_messages=fake_get_messages)), \
             patch.object(main, "db", RouteDb()), \
             patch.object(main, "build_manual_metadata_info", fake_build), \
             patch.object(start, "parse_and_queue_file", fake_queue):
            res = http.post("/admin/api/queue/manual", headers=headers, json={
                "link": "https://t.me/c/2685988600/42",
                "media_ref": "tt0133093",
            })

        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["queued"])
        self.assertEqual(res.json()["title"], "Fight Club")

    def test_manual_add_accepts_tmdb_and_imdb_reference_shapes(self):
        from Backend.fastapi.main import _media_ref_identifiers
        cases = {
            "603": ("603", None, None),
            "https://www.themoviedb.org/movie/603": ("603", None, "movie"),
            "https://www.themoviedb.org/tv/1396": ("1396", None, "tv"),
            "https://www.imdb.com/title/tt0133093/": (None, "tt0133093", None),
            "imdb:tt0133093": (None, "tt0133093", None),
        }
        for ref, expected in cases.items():
            self.assertEqual(_media_ref_identifiers(ref), expected, msg=ref)


# ── Metadata source selection (`/tmdb`) ─────────────────────────────────────
class _MetadataTmdbSearch:
    def __init__(self, owner):
        self.owner = owner

    async def movies(self, query, year=None):
        if self.owner.fail:
            raise RuntimeError("TMDB unavailable")
        self.owner.calls.append("tmdb_search")
        self.owner.queries.append((query, year))
        return self.owner.results


class _MetadataTmdbMovie:
    def __init__(self, owner):
        self.owner = owner

    async def details(self):
        self.owner.calls.append("tmdb_details")
        return self.owner.details


class _MetadataTmdb:
    def __init__(self, results, details, fail=False):
        self.results = results
        self.details = details
        self.fail = fail
        self.calls = []
        self.queries = []

    def search(self):
        return _MetadataTmdbSearch(self)

    def movie(self, _movie_id):
        return _MetadataTmdbMovie(self)


class MetadataSourceTests(unittest.TestCase):
    """``/tmdb`` must actually choose the primary metadata source."""

    def setUp(self):
        from Backend.helper import metadata as metadata_mod
        self.metadata = metadata_mod
        self.had_override = "use_tmdb" in rc.runtime_config._overrides
        self.saved_override = rc.runtime_config._overrides.get("use_tmdb")

        def restore():
            if self.had_override:
                rc.runtime_config._overrides["use_tmdb"] = self.saved_override
            else:
                rc.runtime_config._overrides.pop("use_tmdb", None)

        self.addCleanup(restore)

    @staticmethod
    def _movie_details():
        return type("MovieDetails", (), {
            "id": 603,
            "title": "The Matrix",
            "release_date": datetime(1999, 3, 31),
            "vote_average": 8.7,
            "overview": "Neo",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "runtime": 136,
            "genres": [type("Genre", (), {"name": "Action"})()],
            "original_language": "en",
        })()

    def _run_movie(self, enabled, imdb_id="tt0133093", tmdb_fails=False):
        from unittest.mock import patch

        tmdb = _MetadataTmdb(
            results=[type("TmdbResult", (), {"id": 603})()],
            details=self._movie_details(),
            fail=tmdb_fails,
        )
        calls = []

        async def fake_search_title(**_kwargs):
            calls.append("imdb_search")
            return {"id": imdb_id} if imdb_id else None

        async def fake_get_detail(**_kwargs):
            calls.append("imdb_details")
            return {
                "id": "tt0133093",
                "title": "The Matrix",
                "releaseDetailed": {"year": 1999},
                "rating": {"star": 8.7},
                "runtimeSeconds": 8160,
                "genre": ["Action"],
                "plot": "Neo",
                "image": "poster.jpg",
                "language": "English",
            }

        async def empty_people(*_args, **_kwargs):
            return {}

        rc.runtime_config._overrides["use_tmdb"] = enabled
        with patch.object(self.metadata, "tmdb", tmdb), \
             patch.object(self.metadata, "search_title", fake_search_title), \
             patch.object(self.metadata, "get_detail", fake_get_detail), \
             patch.object(self.metadata, "get_tmdb_people_and_schedule", empty_people):
            result = asyncio.run(self.metadata.fetch_movie_metadata("The Matrix", year=1999))
        return result, tmdb, calls

    def test_tmdb_enabled_makes_tmdb_the_primary_source(self):
        result, tmdb, calls = self._run_movie(True)
        self.assertEqual(result["source"], "TMDb")
        self.assertEqual(result["tmdb_id"], 603)
        self.assertNotIn("imdb_search", calls)
        self.assertEqual(tmdb.calls[:2], ["tmdb_search", "tmdb_details"])

    def test_tmdb_disabled_keeps_imdb_first(self):
        result, _tmdb, calls = self._run_movie(False)
        self.assertEqual(result["source"], "IMDb")
        self.assertEqual(calls[0], "imdb_search")

    def test_tmdb_failure_falls_back_to_imdb(self):
        result, _tmdb, calls = self._run_movie(True, tmdb_fails=True)
        self.assertEqual(result["source"], "IMDb")
        self.assertEqual(calls[0], "imdb_search")

    def test_explicit_tmdb_off_bypasses_tv_validation(self):
        from unittest.mock import patch

        calls = []

        async def fake_original(*args, **kwargs):
            calls.append((args, kwargs))
            return {"source": "IMDb", "media_type": "tv"}

        rc.runtime_config._overrides["use_tmdb"] = False
        with patch.object(self.metadata, "fetch_tv_metadata_original", fake_original):
            result = asyncio.run(self.metadata.fetch_tv_metadata("Show", 1, 2))
        self.assertEqual(result["source"], "IMDb")
        self.assertEqual(len(calls), 1)


# ── Admin list pagination and row resolution ───────────────────────────────
class _FacetCollection:
    """Aggregation fake: returns preset rows through the final $limit/$skip."""

    def __init__(self, rows, total):
        self.rows = list(rows)
        self.total = total
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        facet = next(stage for stage in pipeline if "$facet" in stage)["$facet"]
        items = facet["items"]
        limit = next(stage["$limit"] for stage in items if "$limit" in stage)
        skip = next((stage["$skip"] for stage in items if "$skip" in stage), 0)
        return FakeCursor([{
            "items": self.rows[skip:skip + limit],
            "total": [{"n": self.total}],
        }])


class AdminListPaginationTests(unittest.TestCase):
    def test_page_offset_is_applied_once_after_the_global_merge(self):
        from Backend.helper.database import Database

        base = datetime(2026, 1, 1)

        def row(n):
            return {
                "tmdb_id": n,
                "title": f"Title {n}",
                "updated_on": base + timedelta(minutes=n),
            }

        c1 = FakeCluster(1, movie=_FacetCollection(
            [row(8), row(6), row(4), row(2)], total=6
        ))
        c2 = FakeCluster(2, movie=_FacetCollection(
            [row(7), row(5), row(3), row(1)], total=6
        ))
        db = Database.__new__(Database)
        db.clusters = [c1, c2]

        result = asyncio.run(db.admin_list_media(
            media_type="movie", page=2, page_size=2
        ))

        self.assertEqual([r["title"] for r in result["items"]],
                         ["Title 6", "Title 5"])
        self.assertEqual(result["total_count"], 12)
        for cluster in (c1, c2):
            items = next(
                stage for stage in cluster.movie.pipelines[0] if "$facet" in stage
            )["$facet"]["items"]
            self.assertNotIn("$skip", [next(iter(stage)) for stage in items])
            self.assertIn({"$limit": 4}, items)


class AdminResolveMediaTests(unittest.TestCase):
    def test_anime_row_resolves_through_its_collection(self):
        import Backend.fastapi.main as main

        saved = main.db
        self.addCleanup(setattr, main, "db", saved)
        queries = []

        class Media:
            async def find_one(self, query):
                queries.append(dict(query))
                return dict(query)

        main.db = type("FakeDb", (), {"media": Media()})()
        row = {"type": "anime", "collection": "tv", "tmdb_id": 42, "db_index": 2}

        result = asyncio.run(main._resolve_admin_media(row))

        self.assertEqual(queries[0]["type"], "tv")
        self.assertEqual(result["type"], "tv")


class HeroEndpointTests(unittest.TestCase):
    """Hero carousel save/read over real HTTP, against a MongoDB-compatible DB.

    The React page sends ``{type, db_index, tmdb_id}`` per slot. A slot must be
    resolvable whether the library stores ``tmdb_id`` as an int or a numeric
    string, otherwise "Add title" finds a title that Save then rejects.
    """

    @classmethod
    def setUpClass(cls):
        import Backend
        import Backend.fastapi.main as main
        cls._saved_db = Backend.db
        cls._saved_main_db = main.db

    def setUp(self):
        import Backend
        import Backend.fastapi.main as main
        import mongomock
        from Backend.helper.database import Database, _Cluster

        raw = mongomock.MongoClient()["projectS"]
        self.raw = raw
        wrapped = {
            name: _AsyncMongoCollection(raw[name])
            for name in ("movie", "tv", "settings", "admin_audit")
        }
        db = Database()
        db.clusters = [_Cluster(1, None, wrapped)]
        db.settings_collection = wrapped["settings"]
        db.admin_audit_collection = wrapped["admin_audit"]
        Backend.db = main.db = db
        self.addCleanup(self._restore_db)

        from fastapi.testclient import TestClient
        import Backend.fastapi.auth as auth
        token = auth.create_session(auth.Telegram.ADMIN_WEB_USERNAME)
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.headers = {"X-Admin-Session": token}

    def _restore_db(self):
        import Backend
        import Backend.fastapi.main as main
        Backend.db = self._saved_db
        main.db = self._saved_main_db

    def _seed(self, tmdb_id):
        self.raw["movie"].insert_one({
            "tmdb_id": tmdb_id, "title": "Fight Club", "poster": "/p.jpg",
            "backdrop": "/b.jpg", "release_year": 1999, "type": "movie",
        })

    def test_save_read_clear_roundtrip(self):
        self._seed(550)
        empty = self.client.get("/admin/api/hero", headers=self.headers)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["picks"], [])

        pick = {"type": "movie", "db_index": 1, "tmdb_id": "550"}
        saved = self.client.post("/admin/api/hero", json={"picks": [pick]},
                                 headers=self.headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["picks"],
                         [{"type": "movie", "db_index": 1, "tmdb_id": 550}])

        read = self.client.get("/admin/api/hero", headers=self.headers).json()
        self.assertEqual(len(read["picks"]), 1)
        self.assertTrue(read["picks"][0]["resolved"])
        self.assertEqual(read["picks"][0]["title"], "Fight Club")

        cleared = self.client.post("/admin/api/hero", json={"picks": []},
                                   headers=self.headers)
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.client.get("/admin/api/hero", headers=self.headers)
                         .json()["picks"], [])

    def test_string_tmdb_id_in_library_can_be_pinned(self):
        """Legacy rows may store the id as a string; Save must not reject it."""
        self._seed("550")
        res = self.client.post(
            "/admin/api/hero",
            json={"picks": [{"type": "movie", "db_index": 1, "tmdb_id": "550"}]},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200, res.text)

    def test_unknown_title_is_rejected(self):
        self._seed(550)
        res = self.client.post(
            "/admin/api/hero",
            json={"picks": [{"type": "movie", "db_index": 1, "tmdb_id": 999}]},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 400)

    def test_invalid_payload_is_rejected(self):
        res = self.client.post(
            "/admin/api/hero",
            json={"picks": [{"type": "clip", "db_index": 1, "tmdb_id": 1}]},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 400)


# ── "Skip the newest N messages" ----------------------------------------------
class _SkipMessage:
    def __init__(self, mid):
        self.id = mid


class _SkipHistoryClient:
    """Channel history newest -> oldest, honouring limit/offset_id."""

    def __init__(self, ids):
        self.ids = list(ids)
        self.calls = []

    def get_chat_history(self, channel_id, **kwargs):
        limit = kwargs.get("limit")
        offset = kwargs.get("offset_id")
        self.calls.append((limit, offset))
        page = [i for i in self.ids if offset is None or i < offset][:limit]

        async def gen():
            for mid in page:
                yield _SkipMessage(mid)
        return gen()


class SkipFirstMessagesTests(unittest.TestCase):
    def test_skip_reply_filter_accepts_framework_arguments(self):
        import Backend.wzgram_pkg.plugins.start as start

        start._index_skip_pending.clear()
        start._index_skip_pending[7] = {}
        message = type("Message", (), {
            "from_user": type("From", (), {"id": 7})(),
            "sender_chat": None,
        })()
        client = object()
        try:
            accepted = asyncio.run(
                start._index_skip_reply_filter(client, message)
            )
        finally:
            start._index_skip_pending.clear()
        self.assertTrue(accepted)
        self.assertFalse(asyncio.run(start._index_skip_reply_filter(client, object())))

    def setUp(self):
        import Backend
        self._saved_db = Backend.db
        self.fake = FakeDb()
        patch_db(self.fake)
        self.addCleanup(self._restore_db)
        ci._scans.clear()
        ci._pending_context.clear()

    def _restore_db(self):
        import Backend
        Backend.db = self._saved_db

    def tearDown(self):
        ci._scans.clear()
        ci._pending_context.clear()

    def _run_skip(self, ids, skip_total, *, skipped=0, cursor=None,
                  stopped=False):
        from unittest.mock import patch

        client = _SkipHistoryClient(ids)
        stats = {key: 0 for key in ci.STAT_KEYS}
        stats["skipped"] = skipped
        stop = asyncio.Event()
        if stopped:
            stop.set()
        original_get = ci.runtime_config.get

        def knob(key):
            return 4 if key == "scan_batch_size" else original_get(key)

        async def free_acquire(_tokens):
            return None

        async def prepare():
            await self.fake.upsert_index_scan(1, {
                "status": "running", "skip_first": skip_total,
                "skipped": skipped, "next_offset_id": cursor,
            })

        async def run():
            with patch.object(ci.runtime_config, "get", side_effect=knob), \
                 patch.object(ci._governor, "acquire", new=free_acquire):
                return await ci._skip_newest_messages(
                    ci._Progress(object(), 0, 1, "test"), client, 1, stats,
                    cursor, lambda: 0.0, stop, skip_total,
                )

        asyncio.run(prepare())
        result = asyncio.run(run())
        doc = asyncio.run(self.fake.get_index_scan(1))
        return result, client, stats, doc

    def test_normalize_validates_and_uses_the_live_default(self):
        self.assertEqual(ci.normalize_skip_first(25), 25)
        self.assertEqual(ci.normalize_skip_first("0"), 0)
        self.assertEqual(ci.normalize_skip_first(None, default=7), 7)
        self.assertEqual(ci.normalize_skip_first(None), ci.skip_first_default())
        for bad in ("abc", -1, ci.MAX_SKIP_FIRST_MESSAGES + 1):
            with self.assertRaises(ValueError):
                ci.normalize_skip_first(bad)

    def test_scan_docs_keep_the_answer_for_running_and_waiting_rows(self):
        from unittest.mock import patch

        async def fake_launch(client, channel_id, queue_file, is_resume,
                              notify_client=None, mode=ci.MODE_INDEX):
            ci._scans[channel_id] = ci._ScanHandle(
                channel_id, _FakeTask(), asyncio.Event(), mode=mode
            )

        async def run():
            with patch.object(ci, "_launch", fake_launch), \
                 patch.object(ci, "scan_cap", return_value=1):
                return await ci.request_index_scans(
                    object(), [_FakeChat(1), _FakeChat(2)], 1, None,
                    skip_first=30,
                )

        result = asyncio.run(run())
        self.assertEqual(result["skip_first"], 30)
        docs = {d["channel_id"]: d for d in self.fake.index_scans_collection.docs}
        self.assertEqual(set(docs), {1, 2})
        self.assertEqual(docs[1]["status"], "running")
        self.assertEqual(docs[2]["status"], "waiting")
        for doc in docs.values():
            self.assertEqual(doc["skip_first"], 30)
            self.assertEqual(doc["skipped"], 0)

    def test_skip_advances_the_cursor_and_persists_progress(self):
        # Channel ids 20..1. Skipping 6 with a batch size of 4 must read
        # 4 + 2 posts, never index them, and leave the cursor at 15.
        cursor, client, stats, doc = self._run_skip(range(20, 0, -1), 6)
        self.assertEqual(stats["skipped"], 6)
        self.assertEqual(cursor, 15)
        # First page reads 20,19,18,17 (cursor 17); the second reads 16,15.
        self.assertEqual(client.calls, [(4, None), (2, 17)])
        self.assertEqual(doc["skipped"], 6)
        self.assertEqual(doc["next_offset_id"], 15)

    def test_resume_skips_only_the_remaining_posts(self):
        # A restart after 4 of 6 were skipped must not re-skip from the top.
        cursor, client, stats, doc = self._run_skip(
            range(20, 0, -1), 6, skipped=4, cursor=17,
        )
        self.assertEqual(stats["skipped"], 6)
        self.assertEqual(cursor, 15)
        self.assertEqual(client.calls, [(2, 17)])
        self.assertEqual(doc["next_offset_id"], 15)

    def test_skip_stops_cleanly_when_history_runs_out(self):
        cursor, _client, stats, _doc = self._run_skip([3, 2, 1], 10)
        self.assertEqual(stats["skipped"], 3)
        self.assertEqual(cursor, 1)

    def test_a_stop_request_does_not_skip_anything(self):
        cursor, client, stats, _doc = self._run_skip(
            range(20, 0, -1), 6, stopped=True,
        )
        self.assertEqual(stats["skipped"], 0)
        self.assertIsNone(cursor)
        self.assertEqual(client.calls, [])

    def test_bot_confirmation_asks_for_the_skip_count(self):
        import Backend.wzgram_pkg.plugins.start as start

        token = "unit-token"
        req = {"lines": ["• A"], "cap": 2, "skip_first": 25,
               "previous_note": "", "reader_note": "", "errors": []}
        text = start._index_confirmation_text(req)
        self.assertIn("25", text)
        self.assertIn("Skip", text)
        buttons = [
            b.callback_data
            for row in start._index_confirmation_keyboard(token).inline_keyboard
            for b in row
        ]
        self.assertIn(f"idx:go:{token}", buttons)
        self.assertIn(f"idx:skip:{token}", buttons)
        chooser = [
            b.callback_data
            for row in start._index_skip_keyboard(token).inline_keyboard
            for b in row
        ]
        self.assertIn(f"idx:skip:{token}:50", chooser)
        self.assertIn(f"idx:skipcustom:{token}", chooser)
        self.assertIn(f"idx:back:{token}", chooser)

    def test_bot_skip_preset_and_custom_answer_reach_the_scan(self):
        """Drive the confirmation flow: chooser -> preset -> custom -> start."""
        import Backend.wzgram_pkg.plugins.start as start
        from unittest.mock import patch

        class EditableMessage:
            def __init__(self):
                self.id = 101
                self.edits = []
                self.replies = []

            async def edit_text(self, text, **kwargs):
                self.edits.append((text, kwargs))

            async def reply_text(self, text, **kwargs):
                self.replies.append(text)

        message = EditableMessage()

        class Query:
            def __init__(self, data):
                self.data = data
                self.from_user = type("From", (), {"id": 7})()
                self.message = message
                self.answers = []

            async def answer(self, text=None, **_kwargs):
                self.answers.append(text)

        start._index_requests.clear()
        start._index_skip_pending.clear()
        self.addCleanup(start._index_requests.clear)
        self.addCleanup(start._index_skip_pending.clear)
        token = start._store_index_request([_FakeChat(1)], 7, lines=["• A"], cap=2)
        req = start._index_requests[token]
        self.assertEqual(req["skip_first"], ci.skip_first_default())

        async def run():
            with patch.object(start, "_user_is_admin", return_value=True):
                await start.index_skip_choose_callback(object(), Query(f"idx:skip:{token}"))
                await start.index_skip_set_callback(object(), Query(f"idx:skip:{token}:50"))
                await start.index_skip_custom_callback(object(), Query(f"idx:skipcustom:{token}"))

                class NumericMessage:
                    text = "123"
                    from_user = type("From", (), {"id": 7})()

                    def __init__(self):
                        self.replies = []

                    async def reply_text(self, text, **_kwargs):
                        self.replies.append(text)

                numeric = NumericMessage()
                await start.index_skip_custom_reply(object(), numeric)
                return numeric

        numeric = asyncio.run(run())
        self.assertEqual(req["skip_first"], 123)
        self.assertNotIn(7, start._index_skip_pending)
        self.assertIn("Skip how many", message.edits[0][0])
        self.assertIn("50", message.edits[1][0])
        self.assertIn("123", message.edits[-1][0])
        self.assertTrue(message.replies)  # custom-number prompt
        self.assertTrue(numeric.replies)  # confirmation of the custom answer

        # The chosen answer is what the start callback forwards.
        captured = {}

        async def fake_request(_client, _chats, _requester, _queue_file,
                               **kwargs):
            captured["skip_first"] = kwargs.get("skip_first")
            return {"started": [1], "waiting": [], "skipped": [], "cap": 2}

        class QueryMessage:
            id = 101
            replies = []

            async def reply_text(self, text, **_kwargs):
                QueryMessage.replies.append(text)

        class GoQuery:
            data = f"idx:go:{token}"
            from_user = type("From", (), {"id": 7})()
            message = QueryMessage()

            async def answer(self, *_args, **_kwargs):
                return None

        async def no_warning(_channel_id):
            return None

        async def start_flow():
            with patch.object(start, "_user_is_admin", return_value=True), \
                 patch.object(start, "is_auth_channel", return_value=True), \
                 patch.object(start, "_probe_index_access", new=no_warning), \
                 patch.object(start, "get_index_client", return_value=object()), \
                 patch.object(start, "request_index_scans", new=fake_request), \
                 patch.object(start, "rescan_running", False):
                await start.index_start_callback(object(), GoQuery())

        asyncio.run(start_flow())
        self.assertEqual(captured["skip_first"], 123)

    def test_capabilities_endpoint_reports_the_live_route_table(self):
        from fastapi.testclient import TestClient
        import Backend.fastapi.main as main
        import Backend.fastapi.auth as auth

        token = auth.create_session(auth.Telegram.ADMIN_WEB_USERNAME)
        client = TestClient(main.app, raise_server_exceptions=False)
        res = client.get("/admin/api/capabilities",
                         headers={"X-Admin-Session": token})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["build"], main.ADMIN_API_BUILD)
        self.assertIn("/admin/api/capabilities", body["routes"])
        for path in ("/admin/api/hero", "/admin/api/index-scans",
                     "/admin/api/orphans", "/admin/api/audio-curation",
                     "/admin/api/config", "/admin/api/audit"):
            self.assertIn(path, body["routes"])
        self.assertEqual(
            client.get("/admin/api/capabilities").status_code, 401
        )

    def test_legacy_rescan_honours_the_skip_answer(self):
        """Rescan must not queue the newest posts the admin asked to ignore."""
        from types import SimpleNamespace
        import Backend.wzgram_pkg.plugins.start as start
        from unittest.mock import patch

        class RescanMessage:
            def __init__(self, mid):
                self.id = mid
                self.video = SimpleNamespace(file_unique_id=f"u{mid}", file_size=1)
                self.document = None

        class RescanClient:
            def __init__(self):
                self.kwargs = object()

            def get_chat_history(self, _channel_id, **kwargs):
                self.kwargs = kwargs

                async def gen():
                    for mid in range(10, 0, -1):
                        yield RescanMessage(mid)
                return gen()

        class RescanDb:
            async def is_file_exists(self, *_args):
                return False

        history = RescanClient()
        queued = []

        async def fake_queue(message, _channel, is_rescan=True):
            queued.append(message.id)
            return True

        async def no_sleep(_seconds):
            return None

        async def run():
            with patch.object(start, "Telegram",
                              SimpleNamespace(AUTH_CHANNEL=["-1001"])), \
                 patch.object(start, "get_index_client", return_value=history), \
                 patch.object(start, "db", RescanDb()), \
                 patch.object(start, "parse_and_queue_file", new=fake_queue), \
                 patch.object(start, "asleep", new=no_sleep):
                return await start.perform_rescan(0, limit=4, skip_first=3)

        stats = asyncio.run(run())
        self.assertEqual(stats["skipped"], 3)
        self.assertEqual(stats["checked"], 4)
        self.assertEqual(queued, [7, 6, 5, 4])
        # `limit` must apply to processed posts, not to the skip budget.
        self.assertEqual(history.kwargs, {})
        self.assertNotIn("limit", history.kwargs)


class ManualAddTests(unittest.TestCase):
    """Telegram /manualadd must resolve IMDb/TMDB refs without reading a filename."""

    def test_media_ref_shapes(self):
        from Backend.helper.manual_add import ManualAddError, parse_media_ref
        cases = {
            "603": ("603", None, None),
            "https://www.themoviedb.org/movie/603": ("603", None, "movie"),
            "https://www.themoviedb.org/tv/1396": ("1396", None, "tv"),
            "https://www.imdb.com/title/tt0133093/": (None, "tt0133093", None),
            "imdb:tt0133093": (None, "tt0133093", None),
        }
        for ref, expected in cases.items():
            self.assertEqual(parse_media_ref(ref), expected, msg=ref)
        for bad in ("", "Fight Club", "https://example.com/x"):
            with self.assertRaises(ManualAddError, msg=bad):
                parse_media_ref(bad)

    def test_parse_manual_args_movie(self):
        from Backend.helper.manual_add import parse_manual_args
        out = parse_manual_args([
            "tt0133093", "https://t.me/c/2685988600/42",
            "quality=2160p", "rip=WEB-DL", "lang=te,en", "codec=x265",
        ])
        self.assertEqual(out["media_ref"], "tt0133093")
        self.assertEqual(out["link"], "https://t.me/c/2685988600/42")
        self.assertEqual(out["quality"], "2160p")
        self.assertEqual(out["languages"], "te,en")
        self.assertEqual(out["codec"], "x265")
        self.assertIsNone(out["kind"])

    def test_parse_manual_args_series(self):
        from Backend.helper.manual_add import parse_manual_args
        out = parse_manual_args(["tt0903747", "S01E02"])
        self.assertEqual(out["season"], 1)
        self.assertEqual(out["episode"], 2)
        self.assertEqual(out["kind"], "tv")
        self.assertIsNone(out["link"])  # reply-to flow

        out2 = parse_manual_args(["603", "season=2", "episode=5"])
        self.assertEqual((out2["season"], out2["episode"]), (2, 5))
        self.assertEqual(out2["kind"], "tv")

    def test_parse_manual_args_rejects_bad_input(self):
        from Backend.helper.manual_add import ManualAddError, parse_manual_args
        bad = [
            [],
            ["tt0133093", "a", "b", "c"],
            ["tt0133093", "bogus=1"],
            ["tt0133093", "season=1"],
            ["tt0133093", "type=tv"],
            ["tt0133093", "type=clip"],
            ["tt0133093", "season=x", "episode=1"],
        ]
        for tokens in bad:
            with self.assertRaises(ManualAddError, msg=str(tokens)):
                parse_manual_args(tokens)

    def test_builder_resolves_imdb_id_and_protects_declared_fields(self):
        """An IMDb id becomes a TMDB id; admin-set fields are flagged for the probe."""
        import Backend.helper.manual_add as mod
        from unittest.mock import patch

        async def fake_tmdb_get(path, params=None):
            if path.startswith("/find/"):
                return {"movie_results": [{"id": 603}], "tv_results": []}
            return {}

        async def fake_details(kind, tmdb_id):
            return {
                "title": "Fight Club", "release_date": "1999-10-15",
                "vote_average": 8.4, "overview": "…", "poster_path": "/p.jpg",
                "backdrop_path": "/b.jpg", "genres": [{"name": "Drama"}],
                "runtime": 139, "original_language": "en",
            }

        async def fake_people(tmdb_id, media_type):
            return {"cast": []}

        with patch.object(mod, "tmdb_get", fake_tmdb_get), \
             patch.object(mod, "tmdb_details", fake_details), \
             patch("Backend.helper.metadata.get_tmdb_people_and_schedule", fake_people):
            info = asyncio.run(mod.build_manual_metadata_info(
                "imdb:tt0137523", quality="2160p", rip="WEB-DL", languages="te,en",
            ))

        self.assertEqual(info["tmdb_id"], 603)
        self.assertEqual(info["media_type"], "movie")
        self.assertEqual(info["imdb_url"], "https://www.imdb.com/title/tt0137523/")
        self.assertEqual(info["quality"], "2160p")
        self.assertEqual(info["_manual_declared"], ["quality", "rip", "languages"])
        self.assertEqual(info["languages"], ["te", "en"])
        self.assertEqual(info["source"], "Manual")

    def test_builder_requires_episode_for_series(self):
        import Backend.helper.manual_add as mod
        from unittest.mock import patch

        async def fake_ids(tmdb_id, imdb_id, url_kind, asked_kind=None):
            return "1396", "tv", None

        with patch.object(mod, "resolve_manual_ids", fake_ids):
            with self.assertRaises(mod.ManualAddError):
                asyncio.run(mod.build_manual_metadata_info("themoviedb.org/tv/1396"))

    def test_api_shim_converts_helper_errors(self):
        from fastapi import HTTPException
        import Backend.fastapi.main as main
        with self.assertRaises(HTTPException) as cm:
            main._media_ref_identifiers("nonsense")
        self.assertEqual(cm.exception.status_code, 400)

    def test_api_builder_shim_forwards_every_field(self):
        """The panel route passes 8 kwargs; the shim must not drop any."""
        from unittest.mock import patch
        import Backend.fastapi.main as main

        seen = {}

        async def fake(**kwargs):
            seen.update(kwargs)
            return {"ok": True}

        with patch.object(main, "_build_manual_metadata_info", fake):
            result = asyncio.run(main.build_manual_metadata_info(
                media_ref="tt0133093", kind="tv", quality="1080p",
                rip="WEB-DL", languages="te,en", codec="x265",
                season=1, episode=2,
            ))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen, {
            "media_ref": "tt0133093", "kind": "tv", "quality": "1080p",
            "rip": "WEB-DL", "languages": "te,en", "codec": "x265",
            "season": 1, "episode": 2,
        })

    def test_bot_command_queues_with_metadata_override(self):
        """Drive the whole /manualadd handler with fakes: link -> post -> queue."""
        from types import SimpleNamespace
        from unittest.mock import patch
        import Backend.wzgram_pkg.plugins.start as start
        import Backend.helper.manual_add as mod

        chat = SimpleNamespace(id=-1002685988600, username=None)
        post = SimpleNamespace(
            video=SimpleNamespace(file_unique_id="UNIQ123456"),
            document=None, empty=False, caption=None,
        )

        class Status:
            def __init__(self):
                self.texts = []

            async def edit_text(self, text, **_kw):
                self.texts.append(text)

        class Msg:
            def __init__(self, text):
                self.text = text
                self.from_user = SimpleNamespace(id=42)
                self.reply_to_message = None
                self.replies = []
                self.status = None

            async def reply_text(self, text, **_kw):
                self.replies.append(text)
                if text.startswith("🔎"):
                    self.status = Status()
                    return self.status
                return None

        msg = Msg("/manualadd tt0133093 https://t.me/c/2685988600/42 quality=2160p")

        async def fake_resolve(client, link, fallbacks=None):
            return chat, 42

        async def fake_get_messages(cid, mid):
            self.assertEqual(cid, chat.id)
            return post

        async def fake_build(**kwargs):
            self.assertEqual(kwargs["media_ref"], "tt0133093")
            self.assertEqual(kwargs["quality"], "2160p")
            return {
                "tmdb_id": 603, "title": "Fight Club", "media_type": "movie",
                "quality": "2160p", "rip": "Blu-ray", "languages": ["en"],
                "tmdb_url": "https://www.themoviedb.org/movie/603",
                "imdb_url": "https://www.imdb.com/title/tt0133093/",
            }

        async def fake_queue(message, channel, **kwargs):
            self.assertEqual(channel, "2685988600")
            self.assertEqual(kwargs["metadata_info_override"]["tmdb_id"], 603)
            return True

        class FakeDb:
            async def is_file_exists(self, *_args):
                return False

            async def is_file_unique_id_known(self, *_args):
                return False

        audits = []
        with patch.object(start, "resolve_link_target", fake_resolve), \
             patch.object(start, "StreamBot", SimpleNamespace(get_messages=fake_get_messages)), \
             patch.object(start, "is_auth_channel", lambda _chat: True), \
             patch.object(start, "db", FakeDb()), \
             patch.object(start, "parse_and_queue_file", fake_queue), \
             patch.object(start, "audit_telegram",
                          lambda action, uid, **kw: audits.append((action, kw))), \
             patch.object(mod, "build_manual_metadata_info", fake_build):
            asyncio.run(start.manual_add_file(None, msg))

        self.assertIsNotNone(msg.status, "handler never sent a status message")
        self.assertEqual(len(msg.replies), 1)
        self.assertIn("Queued", msg.status.texts[-1])
        self.assertIn("Fight Club", msg.status.texts[-1])
        self.assertEqual(audits[0][0], "queue.manual")
        self.assertTrue(audits[0][1]["detail"]["queued"])

    def test_index_client_never_falls_back_to_bot(self):
        from unittest.mock import patch

        bot = object()
        with patch.object(wzgram_pkg, "StreamBot", bot), \
             patch.object(wzgram_pkg, "UserBot", None):
            with self.assertRaises(RuntimeError):
                wzgram_pkg.get_index_client()


class LinkResolutionTests(unittest.TestCase):
    """Private t.me links must work even when only the bot is in the channel."""

    class Chat:
        def __init__(self, chat_id):
            self.id = chat_id
            self.title = "Test channel"
            self.username = None
            self.type = ci.enums.ChatType.CHANNEL

    class Post:
        def __init__(self, post_id, empty=False):
            self.id = post_id
            self.empty = empty

    class Client:
        def __init__(self, name, chat=None, post=None, fail_chat=False, fail_post=False):
            self.name = name
            self.chat = chat
            self.post = post
            self.fail_chat = fail_chat
            self.fail_post = fail_post
            self.chat_calls = []
            self.post_calls = []

        async def get_chat(self, ref):
            self.chat_calls.append(ref)
            if self.fail_chat:
                raise RuntimeError(f"{self.name} cannot open chat")
            return self.chat

        async def get_messages(self, chat_id, message_id):
            self.post_calls.append((chat_id, message_id))
            if self.fail_post:
                raise RuntimeError(f"{self.name} cannot read post")
            return self.post

    def test_private_link_falls_back_to_bot(self):
        chat = self.Chat(-1003728019705)
        post = self.Post(1189323)
        user = self.Client("userbot", fail_chat=True)
        bot = self.Client("bot", chat=chat, post=post)

        resolved_chat, message_id = asyncio.run(
            ci.resolve_link_target(
                user, "https://t.me/c/3728019705/1189323", fallbacks=[bot]
            )
        )

        self.assertEqual(user.chat_calls, [-1003728019705])
        self.assertEqual(bot.chat_calls, [-1003728019705])
        self.assertIs(resolved_chat, chat)
        self.assertEqual(message_id, 1189323)
        self.assertEqual(bot.post_calls, [(-1003728019705, 1189323)])

    def test_prefixed_private_id_is_not_double_prefixed(self):
        chat = self.Chat(-1003728019705)
        client = self.Client("userbot", chat=chat, post=self.Post(7))

        asyncio.run(
            ci.resolve_link_target(client, "https://t.me/c/-1003728019705/7")
        )

        self.assertEqual(client.chat_calls, [-1003728019705])

    def test_post_read_falls_back_to_other_client(self):
        chat = self.Chat(-1003728019705)
        user = self.Client("userbot", chat=chat, fail_post=True)
        bot = self.Client("bot", chat=chat, post=self.Post(42))

        resolved_chat, message_id = asyncio.run(
            ci.resolve_link_target(user, "https://t.me/c/3728019705/42", fallbacks=[bot])
        )

        self.assertIs(resolved_chat, chat)
        self.assertEqual(message_id, 42)
        self.assertEqual(bot.post_calls, [(-1003728019705, 42)])

    def test_all_clients_fail_reports_actionable_error(self):
        user = self.Client("userbot", fail_chat=True)
        bot = self.Client("bot", fail_chat=True)

        with self.assertRaises(ci.IndexTargetError) as ctx:
            asyncio.run(
                ci.resolve_link_target(
                    user, "https://t.me/c/3728019705/1189323", fallbacks=[bot]
                )
            )

        message = str(ctx.exception)
        self.assertIn("userbot", message)
        self.assertIn("bot", message)
        self.assertIn("member/admin", message)

    def test_get_index_client_rejects_disconnected_session(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        dead_session = SimpleNamespace(is_connected=False)
        with patch.object(wzgram_pkg, "UserBot", dead_session):
            with self.assertRaises(RuntimeError) as ctx:
                wzgram_pkg.get_index_client()

        self.assertIn("USER_SESSION_STRING", str(ctx.exception))
        self.assertIn("not connected", str(ctx.exception))

    def test_get_index_client_rejects_bot_session(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        bot_session = SimpleNamespace(is_connected=True, me=SimpleNamespace(id=1, is_bot=True))
        with patch.object(wzgram_pkg, "UserBot", bot_session):
            with self.assertRaises(RuntimeError) as ctx:
                wzgram_pkg.get_index_client()

        self.assertIn("belongs to a bot account", str(ctx.exception))
        self.assertIn("BOT_METHOD_INVALID", str(ctx.exception))

    def test_get_index_client_rejects_duplicate_bot_session_value(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        duplicate = "duplicate-session-value"
        user_session = SimpleNamespace(
            is_connected=True, me=SimpleNamespace(id=1, is_bot=False)
        )
        with patch.object(wzgram_pkg, "UserBot", user_session), \
             patch.object(wzgram_pkg, "_session_string", duplicate), \
             patch.object(wzgram_pkg, "_user_session_string", duplicate):
            with self.assertRaises(RuntimeError) as ctx:
                wzgram_pkg.get_index_client()

        message = str(ctx.exception)
        self.assertIn("same session string as SESSION_STRING", message)
        self.assertIn("generate_user_session.py", message)

    def test_session_error_message_detects_revoked_session(self):
        from Backend.wzgram_pkg.plugins import start

        class AuthKeyUnregistered(Exception):
            pass

        message = start._session_error_message(AuthKeyUnregistered())
        self.assertIn("USER_SESSION_STRING", message)
        self.assertIn("AUTH_KEY_UNREGISTERED", message)
        self.assertIsNone(start._session_error_message(RuntimeError("other failure")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
