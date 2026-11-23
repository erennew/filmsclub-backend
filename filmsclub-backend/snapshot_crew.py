"""
Snapshot existing `crew` arrays before the crew backfill writes anything.

The backfill is additive (it only adds a `crew` key where none exists), but a
snapshot makes the whole operation trivially reversible and documents the
pre-change state. Writes crew_snapshot_<timestamp>.json next to this script.

Usage:
    python snapshot_crew.py
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import certifi
from motor.motor_asyncio import AsyncIOMotorClient

from Backend.config import Telegram

DB_NAME = "projectS"


async def main() -> None:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"crew_snapshot_{stamp}.json")
    rows = []
    totals = {"docs": 0, "with_crew": 0, "crew_rows": 0}

    for i, uri in enumerate(Telegram.DATABASE_URIS, start=1):
        try:
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000, tlsCAFile=certifi.where())
            db = client[DB_NAME]
            await db.command("ping")
        except Exception as exc:
            print(f"Cluster {i}: UNREACHABLE {type(exc).__name__}: {exc}"[:100])
            continue

        for kind in ("movie", "tv"):
            async for doc in db[kind].find({}, {"tmdb_id": 1, "title": 1, "name": 1, "crew": 1}):
                totals["docs"] += 1
                crew = doc.get("crew")
                if crew:
                    totals["with_crew"] += 1
                    totals["crew_rows"] += len(crew)
                    rows.append({
                        "cluster": i,
                        "kind": kind,
                        "tmdb_id": doc.get("tmdb_id"),
                        "title": doc.get("title") or doc.get("name"),
                        "crew": crew,
                    })
        print(f"Cluster {i}: scanned")
        client.close()

    payload = {"created": stamp, "database": DB_NAME, "totals": totals, "rows": rows}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    print("\n================ SNAPSHOT ================")
    print(f"  documents scanned      : {totals['docs']}")
    print(f"