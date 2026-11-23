"""
Dry-run that PRINTS the actual before/after values for a handful of documents,
so the repair can be eyeballed before anything is written to production.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aiohttp, certifi
from motor.motor_asyncio import AsyncIOMotorClient
from Backend.config import Telegram
from backfill_cast_photos import fetch_credits, build_repair, photo_of, TMDB_KEY, DB_NAME


async def main():
    client = AsyncIOMotorClient(Telegram.DATABASE_URIS[0], serverSelectionTimeoutMS=20000,
                                tlsCAFile=certifi.where())
    db = client[DB_NAME]
    sem = asyncio.Semaphore(6)
    async with aiohttp.ClientSession() as session:
        coll = db["movie"]
        shown = 0
        async for doc in coll.find({"cast": {"$exists": True, "$ne": []}},
                                   {"tmdb_id": 1, "title": 1, "cast": 1}):
            cast = doc.get("cast") or []
            if not cast or all(photo_of(m) for m in cast if isinstance(m, dict)):
                continue
            tmdb_cast = await fetch_credits(session, "movie", doc.get("tmdb_id"), sem)
            if not tmdb_cast:
                continue
            new_cast, filled = build_repair(cast, tmdb_cast)
            if filled == 0:
                continue

            print(f"\n{'='*74}")
            print(f"{doc.get('title')}   (tmdb {doc.get('tmdb_id')})   +{filled} photos")
            print(f"{'='*74}")
            for before, after in zip(cast, new_cast):
                if not isinstance(before, dict):
                    continue
                added = photo_of(after) and not photo_of(before)
                if not added:
                    continue
                got_id = "id" in after and "id" not in before
                dept = after.get("known_for_department", "")
                print(f"  {str(before.get('name'))[:26]:<28} {str(before.get('character'))[:22]:<24}")
                print(f"      photo: {(photo_of(after) or '')[:64]}")
                print(f"      id added: {got_id:<5} dept: {dept}")
            shown += 1
            if shown >= 3:
                break
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
