"""Regression checks for filename -> metadata routing.

The anime parser must not hijack ordinary releases: before this fix,
"Death Note S01E14 WEBDL - DDP2 0.mkv" was read as episode 0 with the whole
release string as its title, so TMDB searched for "Death Note S01E14 WEBDL -
DDP2" and every file was logged as a metadata failure.
"""

import asyncio
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")

from Backend.helper import metadata as md  # noqa: E402


class MetadataRoutingTests(unittest.TestCase):
    def _run(self, filename, tv_capture, movie_capture):
        async def fake_tv(title, season, episode, *_args, **_kwargs):
            tv_capture.update(title=title, season=season, episode=episode)
            return {"title": title}

        async def fake_movie(title, year, *_args, **_kwargs):
            movie_capture.update(title=title, year=year)
            return {"title": title}

        async def fail_anime(*_args, **_kwargs):
            raise AssertionError("the anime parser must not claim this release")

        with patch.object(md, "fetch_tv_metadata", new=fake_tv), \
             patch.object(md, "fetch_movie_metadata", new=fake_movie), \
             patch.object(md, "fetch_anime_metadata", new=fail_anime):
            return asyncio.run(md.metadata(filename, None))

    def test_sxxexx_release_takes_the_tv_path_with_a_clean_title(self):
        tv, movie = {}, {}
        result = self._run("Death Note S01E14 WEBDL - DDP2 0.mkv", tv, movie)
        self.assertIsNotNone(result)
        self.assertEqual(tv, {"title": "Death Note", "season": 1, "episode": 14})
        self.assertEqual(movie, {})

    def test_year_release_takes_the_movie_path_with_a_clean_title(self):
        tv, movie = {}, {}
        result = self._run("University 2025 AHA DDP5 1 -PMi.mkv", tv, movie)
        self.assertIsNotNone(result)
        self.assertEqual(movie, {"title": "University", "year": 2025})
        self.assertEqual(tv, {})

    def test_absolute_anime_episode_still_uses_the_anime_path(self):
        captured = {}

        async def fake_anime(title, season, episode, *_args, **_kwargs):
            captured.update(title=title, season=season, episode=episode)
            return {"title": title}

        with patch.object(md, "fetch_anime_metadata", new=fake_anime):
            result = asyncio.run(md.metadata("One Piece 1085.mkv", None))

        self.assertIsNotNone(result)
        self.assertEqual(captured, {"title": "One Piece", "season": 1, "episode": 1085})

    def test_tv_lookup_failure_falls_back_to_anime_sources(self):
        captured = {}

        async def no_tv(*_args, **_kwargs):
            return None

        async def fake_anime(title, season, episode, *_args, **kwargs):
            captured.update(title=title, season=season, episode=episode,
                            skip_standard=kwargs.get("skip_standard"))
            return {"title": title}

        with patch.object(md, "fetch_tv_metadata", new=no_tv), \
             patch.object(md, "fetch_anime_metadata", new=fake_anime):
            result = asyncio.run(md.metadata("Jujutsu Kaisen S02E05.mkv", None))

        self.assertIsNotNone(result)
        self.assertEqual((captured["title"], captured["season"], captured["episode"]),
                         ("Jujutsu Kaisen", 2, 5))
        self.assertTrue(captured["skip_standard"])


class AnimeSourceOrderTests(unittest.TestCase):
    def _run(self, standard_result, skip_standard=False):
        calls = []

        async def standard(*_args, **_kwargs):
            calls.append("standard")
            return standard_result

        async def lists(*_args, **_kwargs):
            calls.append("lists")
            return {"title": "from-lists"}

        with patch.object(md, "_anime_from_standard", new=standard), \
             patch.object(md, "_anime_from_lists", new=lists):
            result = asyncio.run(
                md.fetch_anime_metadata("One Piece", 1, 1085, skip_standard=skip_standard)
            )
        return calls, result

    def test_standard_sources_are_tried_first(self):
        calls, result = self._run({"title": "from-standard"})
        self.assertEqual(calls, ["standard"])
        self.assertEqual(result["title"], "from-standard")

    def test_anime_apis_only_when_standard_finds_nothing(self):
        calls, result = self._run(None)
        self.assertEqual(calls, ["standard", "lists"])
        self.assertEqual(result["title"], "from-lists")

    def test_callers_that_already_tried_standard_skip_it(self):
        calls, result = self._run(None, skip_standard=True)
        self.assertEqual(calls, ["lists"])
        self.assertEqual(result["title"], "from-lists")


class IndexScanSerializationTests(unittest.TestCase):
    def test_list_index_scans_is_json_safe(self):
        from bson import ObjectId
        from Backend.helper.database import Database

        class _Cursor:
            def __init__(self, docs):
                self.docs = docs

            def sort(self, *_args, **_kwargs):
                return self

            def limit(self, n):
                self.docs = self.docs[:n]
                return self

            async def to_list(self, length=None):
                return list(self.docs[:length] if length else self.docs)

        class _Collection:
            def __init__(self, docs):
                self.docs = docs

            def find(self, *_args, **_kwargs):
                return _Cursor(self.docs)

        scan_ids = []

        def make_db():
            db = Database(connection_uri="mongodb://unused")
            db.index_scans_collection = _Collection([
                {"_id": ObjectId(), "channel_id": -100123, "status": "completed"},
            ])
            return db

        db = make_db()
        scans = asyncio.run(db.list_index_scans(10))
        self.assertEqual(len(scans), 1)
        self.assertNotIn("_id", scans[0])
        self.assertEqual(scans[0]["channel_id"], -100123)
        scan_ids.append(scans[0])

    def test_waiting_index_scans_is_json_safe(self):
        from bson import ObjectId
        from Backend.helper.database import Database

        class _Cursor:
            def __init__(self, docs):
                self.docs = docs

            def sort(self, *_args, **_kwargs):
                return self

            async def to_list(self, length=None):
                return list(self.docs[:length] if length else self.docs)

        class _Collection:
            def __init__(self, docs):
                self.docs = docs

            def find(self, *_args, **_kwargs):
                return _Cursor(self.docs)

        db = Database(connection_uri="mongodb://unused")
        db.index_scans_collection = _Collection([
            {"_id": ObjectId(), "channel_id": -100456, "status": "waiting"},
        ])
        scans = asyncio.run(db.get_waiting_index_scans())
        self.assertEqual(len(scans), 1)
        self.assertNotIn("_id", scans[0])


if __name__ == "__main__":
    unittest.main()
