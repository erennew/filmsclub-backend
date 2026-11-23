"""Local checks for the cross-cluster listing fan-out.

Runs against in-memory fakes — no MongoDB, no network. Run with::

    .venv/Scripts/python.exe -m tests.test_list_fanout

Covers ``Database._paged_fanout``: that clusters are queried concurrently
rather than one after another, that a failing cluster degrades to an empty
result instead of killing the page, that totals add up, and that the global
merge/paging still returns the same window as the old serial version.
"""

import asyncio
import sys
import time
import unittest

sys.path.insert(0, ".")

from Backend.helper.database import Database  # noqa: E402
from pymongo import ASCENDING, DESCENDING  # noqa: E402


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, sort_list):
        from functools import cmp_to_key

        def cmp(a, b):
            for field, direction in sort_list:
                va, vb = a.get(field), b.get(field)
                if va == vb:
                    continue
                if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                    r = 1 if va > vb else -1
                else:
                    r = 1 if str(va or "") > str(vb or "") else -1
                return r * (1 if direction == ASCENDING else -1)
            return 0

        self._docs = sorted(self._docs, key=cmp_to_key(cmp))
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n):
        return list(self._docs)


class FakeCollection:
    """Minimal find/count surface, with an artificial per-query latency."""

    def __init__(self, docs, delay=0.0, fail=False):
        self._docs = docs
        self._delay = delay
        self._fail = fail
        self.calls = 0

    async def count_documents(self, match):
        self.calls += 1
        await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("cluster down")
        return len(self._docs)

    def find(self, match, projection=None):
        self.calls += 1
        if self._fail:
            delay = self._delay

            # Mirror motor: the error surfaces when the cursor is drained.
            class Boom(FakeCursor):
                async def to_list(self, n):
                    await asyncio.sleep(delay)
                    raise RuntimeError("cluster down")

            return Boom([])
        return FakeCursor(list(self._docs))


class FakeCluster:
    def __init__(self, index, collection):
        self.index = index
        self._collection = collection

    def collection(self, media_type):
        return self._collection


class ListingDatabase(Database):
    def __init__(self, clusters):
        self.clusters = clusters


def make_docs(prefix, ratings):
    return [
        {"tmdb_id": i, "title": f"{prefix}-{r}", "rating": r, "media_type": "movie"}
        for i, r in enumerate(ratings)
    ]


def build(delays, fail_flags=None):
    """One cluster per entry in `delays`; ratings are deliberately interleaved."""
    fail_flags = fail_flags or [False] * len(delays)
    per_cluster_ratings = [[9.0, 7.0], [8.5, 6.5], [8.0, 6.0], [7.5, 5.5], [7.2, 5.0], [7.1, 4.5]]
    clusters, collections = [], []
    for i, delay in enumerate(delays):
        col = FakeCollection(make_docs(f"c{i}", per_cluster_ratings[i]), delay, fail_flags[i])
        collections.append(col)
        clusters.append(FakeCluster(i + 1, col))
    return ListingDatabase(clusters), collections


class PagedFanoutTests(unittest.TestCase):
    def test_clusters_are_queried_concurrently_not_sequentially(self):
        delay = 0.10
        db, _ = build([delay] * 6)
        started = time.perf_counter()
        asyncio.run(db._paged_fanout("movie", {}, [("rating", DESCENDING)], 1, 20))
        elapsed = time.perf_counter() - started
        # Six sequential round trips would cost >= 0.6s; concurrent should be
        # closer to a single delay. Allow generous headroom for CI noise.
        self.assertLess(elapsed, delay * 6 * 0.6, f"fan-out was serial: {elapsed:.3f}s")

    def test_total_counts_sum_across_clusters(self):
        db, _ = build([0.0] * 6)
        total, docs = asyncio.run(db._paged_fanout("movie", {}, [("rating", DESCENDING)], 1, 12))
        self.assertEqual(total, 12)
        self.assertEqual(len(docs), 12)

    def test_global_merge_orders_across_clusters(self):
        db, _ = build([0.0] * 6)
        _, docs = asyncio.run(db._paged_fanout("movie", {}, [("rating", DESCENDING)], 1, 3))
        ratings = [d["rating"] for d in docs]
        self.assertEqual(ratings, sorted(ratings, reverse=True))
        self.assertEqual(ratings[0], 9.0)

    def test_second_page_walks_the_merged_order(self):
        db, _ = build([0.0] * 6)
        _, first = asyncio.run(db._paged_fanout("movie", {}, [("rating", DESCENDING)], 1, 2))
        _, second = asyncio.run(db._paged_fanout("movie", {}, [("rating", DESCENDING)], 2, 2))
        first_ratings = [d["rating"] for d in first]
        second_ratings = [d["rating"] for d in second]
        self.assertEqual(first_ratings, [9.0, 8.5])
        self.assertEqual(second_ratings, [8.0, 7.5])
        # No overlap between consecutive pages.
        self.assertFalse(set(first_ratings) & set(second_ratings))

    def test_one_dead_cluster_does_not_break_the_page(self):
        db, _ = build([0.0] * 6, fail_flags=[False, True, False, False, False, False])
        total, docs = asyncio.run(db._paged_fanout("movie", {}, [("rating", DESCENDING)], 1, 20))
        # Five healthy clusters x 2 docs each.
        self.assertEqual(total, 10)
        self.assertEqual(len(docs), 10)

    def test_ascending_sort_is_honoured(self):
        db, _ = build([0.0] * 6)
        _, docs = asyncio.run(db._paged_fanout("movie", {}, [("rating", ASCENDING)], 1, 3))
        ratings = [d["rating"] for d in docs]
        self.assertEqual(ratings, sorted(ratings))

    def test_no_clusters_returns_empty(self):
        db = ListingDatabase([])
        total, docs = asyncio.run(db._paged_fanout("movie", {}, [("rating", DESCENDING)], 1, 20))
        self.assertEqual(total, 0)
        self.assertEqual(docs, [])


if __name__ == "__main__":
    unittest.main()
