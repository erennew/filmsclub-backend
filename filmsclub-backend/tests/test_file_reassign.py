"""Regression tests for moving a mis-indexed file onto the correct title.

No MongoDB, no Telegram, no network. Run with::

    .venv/Scripts/python.exe -m unittest tests.test_file_reassign
"""
import asyncio
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")

from Backend.helper import file_reassign as fr  # noqa: E402
from Backend.helper.encrypt import encode_string  # noqa: E402
from Backend.helper.manual_add import ManualAddError  # noqa: E402


async def fid_for(channel=2685988600, msg_id=42, hash_val="abc123"):
    return await encode_string({"chat_id": channel, "msg_id": msg_id, "hash": hash_val})


class Result:
    def __init__(self, matched=0, modified=0, deleted=0):
        self.matched_count = matched
        self.modified_count = modified
        self.deleted_count = deleted


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, length=None):
        return list(self.docs[:length] if length else self.docs)


def qualities(doc):
    """Mirror file_reassign.iter_qualities but as a plain list of dicts."""
    out = []
    if doc.get("seasons") or doc.get("type") == "tv":
        for season in doc.get("seasons") or []:
            for episode in season.get("episodes") or []:
                out.extend(episode.get("telegram") or [])
    else:
        out.extend(doc.get("telegram") or [])
    return out


class FakeMedia:
    def __init__(self, docs):
        self.docs = docs
        self.fail_pull = False

    @staticmethod
    def _walk(cur, parts, value):
        if not parts:
            return cur == value
        head = parts[0]
        if isinstance(cur, list):
            return any(FakeMedia._walk(item, parts, value) for item in cur)
        if not isinstance(cur, dict):
            return False
        return FakeMedia._walk(cur.get(head), parts[1:], value)

    def _match(self, query):
        out = []
        for doc in self.docs:
            if query.get("type") and doc.get("type") != query["type"]:
                continue
            if query.get("db_index") is not None and str(doc.get("db_index")) != str(query["db_index"]):
                continue
            if "tmdb_id" in query:
                want = query["tmdb_id"]
                values = want["$in"] if isinstance(want, dict) and "$in" in want else [want]
                if not any(str(doc.get("tmdb_id")) == str(v) for v in values):
                    continue
            for key, value in query.items():
                if key in ("type", "db_index", "tmdb_id"):
                    continue
                if not self._walk(doc, key.split("."), value):
                    break
            else:
                out.append(doc)
        return out

    def find(self, query):
        return Cursor(self._match(query))

    async def find_one(self, query):
        rows = self._match(query)
        return rows[0] if rows else None

    async def update_one(self, query, update, array_filters=None):
        rows = self._match(query)
        if not rows:
            return Result()
        pull = (update.get("$pull") or {})
        setters = (update.get("$set") or {})
        changed = 0
        for doc in rows:
            if "$set" in update:
                for key, value in setters.items():
                    doc[key] = value
                changed += 1
            for path, condition in pull.items():
                if self.fail_pull:
                    continue
                if path == "telegram":
                    before = doc.get("telegram") or []
                    doc["telegram"] = [q for q in before if q.get("id") != condition["id"]]
                    changed += len(before) - len(doc["telegram"])
                elif "seasons.$[s].episodes.$[e].telegram" in path:
                    season_no = next(
                        (f["s.season_number"] for f in (array_filters or []) if "s.season_number" in f),
                        None,
                    )
                    episode_no = next(
                        (f["e.episode_number"] for f in (array_filters or []) if "e.episode_number" in f),
                        None,
                    )
                    for season in doc.get("seasons") or []:
                        if season.get("season_number") != season_no:
                            continue
                        for episode in season.get("episodes") or []:
                            if episode.get("episode_number") != episode_no:
                                continue
                            before = episode.get("telegram") or []
                            episode["telegram"] = [
                                q for q in before if q.get("id") != condition["id"]
                            ]
                            changed += len(before) - len(episode["telegram"])
        return Result(matched=len(rows), modified=changed)

    async def delete_one(self, query):
        rows = self._match(query)
        for doc in rows:
            self.docs.remove(doc)
        return Result(deleted=len(rows))


class FakeDb:
    def __init__(self, docs):
        self.media = FakeMedia(docs)
        self.insert_should_fail = False
        self.inserted = []

    async def insert_media(self, info, hash, channel, msg_id, size, name,
                           codec=None, file_unique_id=None, size_bytes=None, duration=None):
        if self.insert_should_fail:
            raise RuntimeError("simulated DB outage")
        fid = await encode_string({"chat_id": channel, "msg_id": msg_id, "hash": hash})
        entry = {
            "id": fid, "quality": info["quality"], "name": name, "size": size,
            "codec": codec, "file_unique_id": file_unique_id,
            "size_bytes": size_bytes, "duration": duration,
            "audio_tracks": info.get("audio_tracks"),
            "subtitle_tracks": info.get("subtitle_tracks"),
        }
        kind, tmdb_id = info["media_type"], info["tmdb_id"]
        doc = next(
            (d for d in self.media.docs
             if d["type"] == kind and str(d["tmdb_id"]) == str(tmdb_id)),
            None,
        )
        if doc is None:
            doc = {
                "tmdb_id": tmdb_id, "type": kind, "db_index": 1,
                "title": info["title"], "rip": info["rip"], "languages": info["languages"],
                "telegram": [] if kind == "movie" else None,
            }
            if kind == "tv":
                doc["seasons"] = []
            self.media.docs.append(doc)

        if kind == "movie":
            slot = doc.setdefault("telegram", [])
        else:
            season = next(
                (s for s in doc["seasons"] if s["season_number"] == info["season_number"]),
                None,
            )
            if season is None:
                season = {"season_number": info["season_number"], "episodes": []}
                doc["seasons"].append(season)
            episode = next(
                (e for e in season["episodes"] if e["episode_number"] == info["episode_number"]),
                None,
            )
            if episode is None:
                episode = {
                    "episode_number": info["episode_number"],
                    "title": info.get("episode_title", ""), "telegram": [],
                }
                season["episodes"].append(episode)
            slot = episode.setdefault("telegram", [])

        existing = next((q for q in slot if q.get("quality") == info["quality"]), None)
        if existing:
            existing.update(entry)
        else:
            slot.append(entry)
        self.inserted.append((kind, tmdb_id, fid))
        return "fake-object-id"


async def fake_metadata(media_ref, kind=None, quality=None, rip=None, languages=None,
                        codec=None, season=None, episode=None):
    """Stand in for TMDB: ``movie:603`` / ``tv:1396``."""
    wanted_kind, tid = str(media_ref).split(":")
    media_type = "tv" if wanted_kind == "tv" else "movie"
    if media_type == "tv" and not (season and episode):
        raise ManualAddError("Series need a season and episode number")
    info = {
        "tmdb_id": int(tid), "title": f"Title {tid}", "media_type": media_type,
        "quality": str(quality).lower() if quality else "1080p",
        "rip": rip or "Blu-ray", "languages": languages or ["en"],
        "tmdb_url": f"https://www.themoviedb.org/{media_type}/{tid}",
        "genres": [], "description": "", "rate": 0.0, "year": 2020,
        "poster": "", "backdrop": "", "runtime": 100, "cast": [],
    }
    if media_type == "tv":
        info.update({
            "status": "Returning Series", "total_seasons": 1, "total_episodes": 1,
            "season_number": int(season), "episode_number": int(episode),
            "episode_title": f"Episode {episode}", "episode_backdrop": "",
        })
    return info


def movie_doc(tmdb_id, title, entries):
    return {
        "tmdb_id": tmdb_id, "type": "movie", "db_index": 1, "title": title,
        "telegram": list(entries), "rip": "WEB-DL", "languages": ["ta", "en"],
    }


def tv_doc(tmdb_id, title, entries, season=1, episode=2):
    return {
        "tmdb_id": tmdb_id, "type": "tv", "db_index": 1, "title": title,
        "telegram": None, "rip": "WEB-DL", "languages": ["en"],
        "seasons": [{
            "season_number": season,
            "episodes": [{
                "episode_number": episode, "title": f"Episode {episode}",
                "telegram": list(entries),
            }],
        }],
    }


class FileReassignTests(unittest.TestCase):
    def setUp(self):
        import Backend

        self.fid = asyncio.run(fid_for())
        self.entry = {
            "id": self.fid, "quality": "1080p", "name": "wrong.mkv",
            "size": "1.4 GB", "codec": "h264", "file_unique_id": "ABC123xyz",
            "size_bytes": 1500000000, "duration": 7200,
            "audio_tracks": [{"language": "ta"}],
            "subtitle_tracks": [{"language": "en"}],
        }
        self.docs = [movie_doc(11, "Wrong Movie", [self.entry])]
        self.fake = FakeDb(self.docs)
        self._saved_db = Backend.db
        Backend.db = self.fake
        self.addCleanup(self._restore)
        patcher = patch.object(fr, "build_manual_metadata_info", new=fake_metadata)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        import Backend

        Backend.db = self._saved_db

    def _lookup(self, **kwargs):
        return asyncio.run(fr.find_library_files(**kwargs))

    def _matches(self, **kwargs):
        return self._lookup(**kwargs)["matches"]

    def test_lookup_by_file_id_finds_the_wrong_title(self):
        rows = self._matches(file_id=self.fid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Wrong Movie")
        self.assertEqual(rows[0]["db_index"], 1)
        self.assertIsNone(rows[0]["season_number"])
        self.assertEqual(rows[0]["quality_entry"]["audio_tracks"], [{"language": "ta"}])

    def test_lookup_falls_back_to_file_unique_id(self):
        rows = self._matches(file_id="not-the-stored-id", file_unique_id="ABC123xyz")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file_id"], self.fid)

    def test_lookup_by_link_reproduces_the_stored_id(self):
        from wzgram import enums

        class File:
            file_unique_id = "abc123XYZ"  # first six chars must match fid_for()

        class Post:
            empty = False
            video = File()
            document = None

        class Chat:
            id = -1002685988600
            type = enums.ChatType.CHANNEL
            username = None

        class Bot:
            async def get_chat(self, chat_id):
                return Chat()

            async def get_messages(self, chat_id, message_id):
                return Post()

        found = self._lookup(link="https://t.me/c/2685988600/42", bot=Bot())
        self.assertEqual(found["channel"], "2685988600")
        self.assertEqual(found["message_id"], 42)
        self.assertEqual(found["file_id"], self.fid)
        self.assertEqual(len(found["matches"]), 1)
        self.assertEqual(found["matches"][0]["title"], "Wrong Movie")

    def test_lookup_requires_an_identifier(self):
        with self.assertRaises(fr.ReassignError):
            self._lookup()

    def test_movie_to_movie_moves_file_and_prunes_empty_source(self):
        rows = self._matches(file_id=self.fid)
        result = asyncio.run(fr.reassign_file(rows[0], media_ref="movie:603"))
        self.assertTrue(result["moved"])
        self.assertIsNone(result["warning"])
        self.assertTrue(result["old_title_removed"])
        self.assertEqual(result["file_id"], self.fid)
        self.assertEqual(result["from"]["title"], "Wrong Movie")
        target = next(d for d in self.fake.media.docs if str(d["tmdb_id"]) == "603")
        self.assertEqual(len(qualities(target)), 1)
        self.assertEqual(qualities(target)[0]["id"], self.fid, "stream id must be preserved")
        self.assertEqual(qualities(target)[0]["audio_tracks"], [{"language": "ta"}])
        self.assertEqual(qualities(target)[0]["subtitle_tracks"], [{"language": "en"}])
        self.assertEqual(target["rip"], "Blu-ray")
        self.assertFalse(any(str(d["tmdb_id"]) == "11" for d in self.fake.media.docs))

    def test_movie_to_tv_episode_creates_the_episode(self):
        rows = self._matches(file_id=self.fid)
        result = asyncio.run(fr.reassign_file(
            rows[0], media_ref="tv:1396", season=1, episode=3
        ))
        self.assertEqual(result["media_type"], "tv")
        self.assertEqual((result["season_number"], result["episode_number"]), (1, 3))
        target = next(d for d in self.fake.media.docs if str(d["tmdb_id"]) == "1396")
        episode = target["seasons"][0]["episodes"][0]
        self.assertEqual(episode["episode_number"], 3)
        self.assertEqual(episode["telegram"][0]["id"], self.fid)

    def test_tv_episode_to_movie_detaches_from_the_episode(self):
        self.fake.media.docs = [tv_doc(21, "Wrong Show", [self.entry])]
        rows = self._matches(file_id=self.fid)
        self.assertEqual((rows[0]["season_number"], rows[0]["episode_number"]), (1, 2))
        result = asyncio.run(fr.reassign_file(rows[0], media_ref="movie:603"))
        self.assertTrue(result["moved"])
        self.assertTrue(result["old_title_removed"])
        self.assertEqual(qualities(next(d for d in self.fake.media.docs
                                        if str(d["tmdb_id"]) == "603"))[0]["id"], self.fid)

    def test_same_show_different_episode_is_a_real_move(self):
        """The id is still in the doc after the pull (new episode) — the
        read-back check must look at the *source slot*, not the whole doc."""
        self.fake.media.docs = [tv_doc(21, "Right Show", [self.entry], season=1, episode=2)]
        rows = self._matches(file_id=self.fid)
        result = asyncio.run(fr.reassign_file(
            rows[0], media_ref="tv:21", season=1, episode=3
        ))
        self.assertTrue(result["moved"])
        self.assertIsNone(result["warning"])
        doc = self.fake.media.docs[0]
        episodes = doc["seasons"][0]["episodes"]
        self.assertEqual(len(qualities({**doc, "seasons": [{"episodes": episodes}]})), 1)
        self.assertEqual(episodes[0]["telegram"], [])
        self.assertEqual(episodes[1]["telegram"][0]["id"], self.fid)
        self.assertEqual(episodes[1]["episode_number"], 3)

    def test_same_slot_is_a_noop(self):
        rows = self._matches(file_id=self.fid)
        with self.assertRaises(fr.ReassignError) as ctx:
            asyncio.run(fr.reassign_file(rows[0], media_ref="movie:11"))
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(len(self.fake.inserted), 0, "nothing must be written")
        self.assertEqual(len(qualities(self.docs[0])), 1)

    def test_same_quality_conflict_is_refused_unless_opted_in(self):
        other = {**self.entry, "id": "OTHERID", "name": "existing.mkv"}
        self.fake.media.docs.append(movie_doc(603, "Right Movie", [other]))
        rows = self._matches(file_id=self.fid)
        with self.assertRaises(fr.ReassignError) as ctx:
            asyncio.run(fr.reassign_file(rows[0], media_ref="movie:603"))
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("Replace same-quality file", str(ctx.exception))
        self.assertEqual(qualities(self.fake.media.docs[1])[0]["id"], "OTHERID")

        result = asyncio.run(fr.reassign_file(
            rows[0], media_ref="movie:603", replace_existing_quality=True
        ))
        self.assertEqual(result["replaced_file_id"], "OTHERID")
        target = next(d for d in self.fake.media.docs if str(d["tmdb_id"]) == "603")
        self.assertEqual(qualities(target)[0]["id"], self.fid)

    def test_title_level_metadata_is_preserved_on_an_existing_target(self):
        self.fake.media.docs.append(movie_doc(603, "Right Movie", [
            {"id": "OTHERID", "quality": "720p", "name": "keep.mkv"}
        ]))
        rows = self._matches(file_id=self.fid)
        asyncio.run(fr.reassign_file(rows[0], media_ref="movie:603"))
        target = next(d for d in self.fake.media.docs if str(d["tmdb_id"]) == "603")
        self.assertEqual(target["rip"], "WEB-DL")
        self.assertEqual(target["languages"], ["ta", "en"])

    def test_keep_empty_title_leaves_the_wrong_doc(self):
        rows = self._matches(file_id=self.fid)
        result = asyncio.run(fr.reassign_file(
            rows[0], media_ref="movie:603", remove_empty_title=False
        ))
        self.assertFalse(result["old_title_removed"])
        old = next(d for d in self.fake.media.docs if str(d["tmdb_id"]) == "11")
        self.assertEqual(qualities(old), [])

    def test_attach_failure_leaves_the_source_untouched(self):
        self.fake.insert_should_fail = True
        rows = self._matches(file_id=self.fid)
        with self.assertRaises(fr.ReassignError) as ctx:
            asyncio.run(fr.reassign_file(rows[0], media_ref="movie:603"))
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(qualities(self.docs[0])[0]["id"], self.fid, "no data loss")

    def test_failed_attach_verification_never_detaches(self):
        # insert_media succeeds but the file does not appear on the target
        original = self.fake.insert_media

        async def silent(*args, **kwargs):
            await original(*args, **kwargs)
            for doc in list(self.fake.media.docs):
                if str(doc["tmdb_id"]) == "603":
                    self.fake.media.docs.remove(doc)

        self.fake.insert_media = silent
        rows = self._matches(file_id=self.fid)
        with self.assertRaises(fr.ReassignError) as ctx:
            asyncio.run(fr.reassign_file(rows[0], media_ref="movie:603"))
        self.assertIn("Nothing was removed", str(ctx.exception))
        self.assertEqual(qualities(self.docs[0])[0]["id"], self.fid)

    def test_failed_detach_returns_a_warning_not_an_error(self):
        self.fake.media.fail_pull = True
        rows = self._matches(file_id=self.fid)
        result = asyncio.run(fr.reassign_file(rows[0], media_ref="movie:603"))
        self.assertFalse(result["moved"])
        self.assertIn("Delete it manually", result["warning"])
        self.assertEqual(len(qualities(self.docs[0])), 1)
        self.assertEqual(len(qualities(next(d for d in self.fake.media.docs
                                            if str(d["tmdb_id"]) == "603"))), 1)

    def test_quality_override_is_applied(self):
        rows = self._matches(file_id=self.fid)
        result = asyncio.run(fr.reassign_file(
            rows[0], media_ref="movie:603", quality="2160P", codec="X265"
        ))
        self.assertEqual(result["quality"], "2160p")
        target = next(d for d in self.fake.media.docs if str(d["tmdb_id"]) == "603")
        self.assertEqual(qualities(target)[0]["quality"], "2160p")
        self.assertEqual(qualities(target)[0]["codec"], "x265")

    def test_missing_match_is_rejected(self):
        with self.assertRaises(fr.ReassignError):
            asyncio.run(fr.reassign_file({}, media_ref="movie:603"))


class ReassignFlagTests(unittest.TestCase):
    def test_flags_are_stripped_before_manual_arg_parsing(self):
        from Backend.helper.manual_add import parse_manual_args

        rest, flags = fr.split_reassign_flags([
            "tt0133093", "https://t.me/c/2685988600/42", "replace", "keepempty"
        ])
        self.assertEqual(flags, {"replace_existing_quality": True, "remove_empty_title": False})
        args = parse_manual_args(rest)
        self.assertEqual(args["media_ref"], "tt0133093")
        self.assertEqual(args["link"], "https://t.me/c/2685988600/42")
        self.assertEqual(set(args) & set(flags), set(), "flags must not leak into metadata kwargs")

    def test_defaults(self):
        rest, flags = fr.split_reassign_flags(["tt0133093", "https://t.me/c/1/2"])
        self.assertEqual(len(rest), 2)
        self.assertFalse(flags["replace_existing_quality"])
        self.assertTrue(flags["remove_empty_title"])


class ReassignEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Backend
        import Backend.fastapi.main as main

        cls._saved_backend_db = Backend.db
        cls._saved_main_db = main.db

    @classmethod
    def tearDownClass(cls):
        import Backend
        import Backend.fastapi.main as main

        Backend.db = cls._saved_backend_db
        main.db = cls._saved_main_db

    def setUp(self):
        import Backend
        import Backend.fastapi.main as main

        self.fid = asyncio.run(fid_for())
        entry = {"id": self.fid, "quality": "1080p", "name": "wrong.mkv", "size": "1 GB"}
        self.fake = FakeDb([movie_doc(11, "Wrong Movie", [entry])])
        Backend.db = self.fake
        main.db = self.fake
        self.patchers = [
            patch.object(fr, "build_manual_metadata_info", new=fake_metadata),
            patch.object(main, "clear_all_caches", new=self._noop),
            patch.object(main, "audit", new=lambda *a, **k: None),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    async def _noop(self, *args, **kwargs):
        return None

    def _client(self):
        from fastapi.testclient import TestClient
        import Backend.fastapi.main as main
        import Backend.fastapi.auth as auth

        token = auth.create_session(auth.Telegram.ADMIN_WEB_USERNAME)
        return TestClient(main.app, raise_server_exceptions=False), {"X-Admin-Session": token}

    def test_lookup_and_reassign_are_registered(self):
        import Backend.fastapi.main as main

        paths = {r.path for r in main.app.routes}
        self.assertIn("/admin/api/files/lookup", paths)
        self.assertIn("/admin/api/files/reassign", paths)

    def test_lookup_requires_auth(self):
        client, _ = self._client()
        self.assertEqual(client.post("/admin/api/files/lookup", json={"file_id": self.fid}).status_code, 401)

    def test_lookup_preview_hides_the_bulk_quality_entry(self):
        client, headers = self._client()
        res = client.post("/admin/api/files/lookup", headers=headers, json={"file_id": self.fid})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["matches"][0]["title"], "Wrong Movie")
        self.assertNotIn("quality_entry", body["matches"][0])

    def test_lookup_rejects_empty_payload(self):
        client, headers = self._client()
        self.assertEqual(client.post("/admin/api/files/lookup", headers=headers, json={}).status_code, 400)

    def test_reassign_requires_a_reference(self):
        client, headers = self._client()
        res = client.post("/admin/api/files/reassign", headers=headers,
                          json={"file_id": self.fid, "media_ref": ""})
        self.assertEqual(res.status_code, 400)

    def test_reassign_unknown_file_points_at_manual_add(self):
        client, headers = self._client()
        res = client.post("/admin/api/files/reassign", headers=headers,
                          json={"file_id": "NOPE", "media_ref": "movie:603"})
        self.assertEqual(res.status_code, 404)
        self.assertIn("Manual add", res.json()["detail"])

    def test_reassign_endpoint_moves_the_file(self):
        client, headers = self._client()
        res = client.post("/admin/api/files/reassign", headers=headers, json={
            "file_id": self.fid, "media_ref": "movie:603",
            "source": {"db_index": 1, "tmdb_id": 11, "collection": "movie"},
        })
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["moved"])
        self.assertEqual(body["from"]["title"], "Wrong Movie")
        self.assertEqual(body["title"], "Title 603")
        self.assertEqual(body["file_id"], self.fid)


if __name__ == "__main__":
    unittest.main()
