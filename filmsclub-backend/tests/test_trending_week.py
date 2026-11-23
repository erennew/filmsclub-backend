"""Local checks for the homepage trending aggregation.

Runs against an in-memory fake — no MongoDB, no network. Run with::

    .venv/Scripts/python.exe -m tests.test_trending_week

Covers ``Database.get_trending_week``: the seven-day date window, per-media-type
ranking, and the ``view_count`` shape the frontend rails read.
"""

import asyncio
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from Backend.helper.database import Database  # noqa: E402


class FakeAggregateCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, _length):
        return list(self.docs)


class FakeViewCollection:
    """Executes the subset of the aggregation language ``get_trending_week`` uses."""

    def __init__(self, docs):
        self.docs = docs

    def aggregate(self, pipeline):
        rows = [dict(doc) for doc in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
                start = match["date"]["$gte"]
                rows = [
                    row for row in rows
                    if row["date"] >= start
                    and all(row.get(k) == v for k, v in match.items() if k != "date")
                ]
            elif "$group" in stage:
                totals = {}
                for row in rows:
                    key = (row["tmdb_id"], row["media_type"])
                    totals[key] = totals.get(key, 0) + row["count"]
                rows = [
                    {"_id": {"tmdb_id": k[0], "media_type": k[1]}, "total_count": total}
                    for k, total in totals.items()
                ]
            elif "$sort" in stage:
                rows.sort(key=lambda row: row["total_count"], reverse=True)
            elif "$limit" in stage:
                rows = rows[: stage["$limit"]]
        return FakeAggregateCursor(rows)


class TrendingDatabase(Database):
    def __init__(self, docs):
        self.views_collection = FakeViewCollection(docs)

    async def get_movie_doc(self, tmdb_id):
        return {"tmdb_id": tmdb_id, "title": f"movie-{tmdb_id}"}

    async def get_tv_doc(self, tmdb_id):
        return {"tmdb_id": tmdb_id, "title": f"tv-{tmdb_id}"}

    @staticmethod
    def _convert_object_id(document):
        return document


class TrendingWeekTests(unittest.TestCase):
    def test_totals_only_include_the_last_seven_days(self):
        today = datetime.utcnow().date()
        store = TrendingDatabase([
            {"tmdb_id": 1, "media_type": "movie",
             "date": (today - timedelta(days=1)).strftime("%Y-%m-%d"), "count": 3},
            {"tmdb_id": 1, "media_type": "movie",
             "date": today.strftime("%Y-%m-%d"), "count": 2},
            {"tmdb_id": 2, "media_type": "movie",
             "date": today.strftime("%Y-%m-%d"), "count": 4},
            {"tmdb_id": 3, "media_type": "tv",
             "date": (today - timedelta(days=8)).strftime("%Y-%m-%d"), "count": 100},
        ])

        result = asyncio.run(store.get_trending_week(limit=1))

        self.assertEqual([m["tmdb_id"] for m in result["movies"]], [1])
        self.assertEqual(result["movies"][0]["view_count"], 5)
        self.assertEqual(result["tv_shows"], [])

    def test_movie_and_tv_totals_are_ranked_separately(self):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        store = TrendingDatabase([
            {"tmdb_id": 1, "media_type": "movie", "date": today, "count": 5},
            {"tmdb_id": 1, "media_type": "tv", "date": today, "count": 7},
            {"tmdb_id": 2, "media_type": "tv", "date": today, "count": 3},
        ])

        result = asyncio.run(store.get_trending_week(limit=2))

        self.assertEqual([m["tmdb_id"] for m in result["movies"]], [1])
        self.assertEqual([t["tmdb_id"] for t in result["tv_shows"]], [1, 2])
        self.assertEqual(result["tv_shows"][0]["view_count"], 7)


if __name__ == "__main__":
    unittest.main()
