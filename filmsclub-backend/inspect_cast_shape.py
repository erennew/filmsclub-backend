"""
READ-ONLY: dump the exact shape of cast arrays in the production DB so the
backfill targets the real problem rather than a guess.
"""
from __future__ import annotations

import asyncio
import json
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
    client = AsyncIOMotorClient(Telegram.DATABASE_URIS[0], serverSelectionTimeoutMS=20000,
                                tlsCAFile=certifi.where())
    db = client["projectS"]
    await db.command("ping")

    for coll_name in ("movie", "tv"):
        coll = db[coll_name]
        print(f"\n{'='*70}\nCOLLECTION: {coll_name}\n{'='*70}")

        # distribution: how broken is each doc?
        buckets = {"0%": 0, "1-50%": 0, "51-99%": 0, "100%": 0, "no cast": 0}
        field_shapes = {}
        samples = []
        async for doc in coll.find({}, {"tmdb_id": 1, "title": 1, "name": 1, "cast": 1}).limit(400):
            cast = doc.get("cast") or []
            if not cast:
                buckets["no cast"] += 1
                continue
            have = sum(1 for m in cast if isinstance(m, dict) and has_photo(m))
            pct = have / len(cast) * 100
            if pct == 0:
                buckets["0%"] += 1
                if len(samples) < 3:
                    samples.append(doc)
            elif pct < 50:
                buckets["1-50%"] += 1
            elif pct < 100:
                buckets["51-99%"] += 1
            else:
                buckets["100%"] += 1
            for m in cast:
                if isinstance(m, dict):
                    sig = "|".join(sorted(m.keys()))
                    field_shapes[sig] = field_shapes.get(sig, 0) + 1

        print("  Photo coverage of cast lists (first 400 docs):")
        for k in ("100%", "51-99%", "1-50%", "0%", "no cast"):
            print(f"    {k:<8} {buckets[k]}")

        print("\n  Cast member field shapes (most common first):")
        for sig, n in sorted(field_shapes.items(), key=lambda x: -x[1])[:6]:
            print(f"    {n:>6}  {sig}")

        if samples:
            print("\n  RAW BROKEN DOCUMENT (cast truncated to 3):")
            s = dict(samples[0])
            s.pop("_id", None)
            s["cast"] = (s.get("cast") or [])[:3]
            print("   ", json.dumps(s, indent=2, default=str)[:900])

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
