"""
Read-only verification that the backfill actually landed.
Re-scans the clusters and reports photo coverage, so we can compare against the
pre-backfill baseline (84% of cast members had no photo).
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from Backend.config import Telegram


def has_photo(m: dict) -> bool:
    return any(isinstance(m.get(k), str) and m.get(k).strip()
               for k in ("profile", "profile_path", "profile_url", "avatar", "image"))


async def main():
    grand = {"docs": 0, "broken": 0, "members": 0, "missing": 0}
    for i, uri in enumerate(Telegram.DATABASE_URIS, start=1):
        try:
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000, tlsCAFile=certifi.where())
            db = client["projectS"]
            await db.command("ping")
        except Exception as exc:
            print(f"Cluster {i}: UNREACHABLE {type(exc).__name__}")
            continue

        c = {"docs": 0, "broken": 0, "members": 0, "missing": 0}
        for kind in ("movie", "tv"):
            async for doc in db[kind].find({"cast": {"$exists": True, "$ne": []}}, {"cast": 1}):
                cast = [m for m in (doc.get("cast") or []) if isinstance(m, dict)]
                if not cast:
                    continue
                c["docs"] += 1
                miss = sum(1 for m in cast if not has_photo(m))
                c["members"] += len(cast)
                c["missing"] += miss
                if miss == len(cast):
                    c["broken"] += 1
        pct = (1 - c["missing"] / c["members"]) * 100 if c["members"] else 0
        print(f"Cluster {i}: docs {c['docs']:>5}  fully-broken {c['broken']:>4}  "
              f"members {c['members']:>6}  missing {c['missing']:>6}  coverage {pct:5.1f}%")
        for k in grand:
            grand[k] += c[k]
        client.close()

    pct = (1 - grand["missing"] / grand["members"]) * 100 if grand["members"] else 0
    print("\n================ AFTER BACKFILL ================")
    print(f"  documents with cast    : {grand['docs']}")
    print(f"  fully broken           : {grand['broken']}")
    print(f"  cast members           : {grand['members']}")
    print(f"  missing photo          : {grand['missing']}")
    print(f"  COVERAGE               : {pct:.1f}%   (was 15.5%)")


if __name__ == "__main__":
    asyncio.run(main())
