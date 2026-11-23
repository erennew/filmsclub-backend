"""
READ-ONLY audit of cast photos in the production clusters.

Connects to every configured MongoDB cluster, walks the `movie` and `tv`
collections, and reports which media documents have cast members whose
headshot is missing or blank. It NEVER writes.

Usage:
    python audit_cast_photos.py            # summary only
    python audit_cast_photos.py --list     # also list every broken title

Why this exists: `compact_metadata_for_queue()` used to rebuild cast entries as
{name, character, profile_path}, but metadata.py stores the headshot in
`profile`. Every title with more than 10 cast members therefore lost all its
cast photos (and the person ids) at insert time. This script measures how much
of the library is affected before we touch anything.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import certifi
from motor.motor_asyncio import AsyncIOMotorClient

from Backend.config import Telegram


def has_photo(member: dict) -> bool:
    """True when a cast member carries a usable headshot reference."""
    for key in ("profile", "profile_path", "profile_url", "avatar", "image"):
        value = member.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


async def scan_cluster(index: int, uri: str, db_name: str, list_all: bool) -> dict:
    stats = {
        "cluster": index,
        "reachable": False,
        "movie_total": 0, "movie_broken": 0,
        "tv_total": 0, "tv_broken": 0,
        "members_total": 0, "members_missing_photo": 0,
        "titles": [],
    }
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000, connectTimeoutMS=20000,
                                    tlsCAFile=certifi.where())
        db = client[db_name]
        await db.command("ping")
        stats["reachable"] = True
    except Exception as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
        return stats

    for kind, coll_name in (("movie", "movie"), ("tv", "tv")):
        coll = db[coll_name]
        cursor = coll.find({}, {"tmdb_id": 1, "title": 1, "name": 1, "cast": 1})
        async for doc in cursor:
            cast = doc.get("cast") or []
            if not isinstance(cast, list) or not cast:
                continue
            stats[f"{kind}_total"] += 1
            stats["members_total"] += len(cast)
            missing = sum(1 for m in cast if isinstance(m, dict) and not has_photo(m))
            stats["members_missing_photo"] += missing
            if missing == len(cast):
                # Entire cast has no photo: this is the broken shape.
                stats[f"{kind}_broken"] += 1
                if list_all:
                    stats["titles"].append({
                        "type": kind,
                        "tmdb_id": doc.get("tmdb_id"),
                        "title": doc.get("title") or doc.get("name"),
                        "cast": len(cast),
                        "missing": missing,
                    })
    client.close()
    return stats


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list every fully-broken title")
    args = ap.parse_args()

    uris = Telegram.DATABASE_URIS
    db_name = getattr(Telegram, "DATABASE_NAME", None) or "projectS"
    print(f"Clusters configured: {len(uris)}   database: {db_name}\n")

    totals = {"movie_total": 0, "movie_broken": 0, "tv_total": 0, "tv_broken": 0,
              "members_total": 0, "members_missing_photo": 0}
    all_titles = []

    for i, uri in enumerate(uris, start=1):
        s = await scan_cluster(i, uri, db_name, args.list)
        if not s["reachable"]:
            print(f"Cluster {i}: UNREACHABLE — {s.get('error', '')[:110]}")
            continue
        print(f"Cluster {i}: movies {s['movie_total']} ({s['movie_broken']} fully broken) "
              f"| tv {s['tv_total']} ({s['tv_broken']} fully broken) "
              f"| members missing photo {s['members_missing_photo']}/{s['members_total']}")
        for k in totals:
            totals[k] += s[k]
        all_titles.extend(s["titles"])

    print("\n================ TOTAL ================")
    print(f"  movie documents with cast : {totals['movie_total']}")
    print(f"  ...fully broken           : {totals['movie_broken']}")
    print(f"  tv documents with cast    : {totals['tv_total']}")
    print(f"  ...fully broken           : {totals['tv_broken']}")
    print(f"  cast members total        : {totals['members_total']}")
    print(f"  ...missing a photo        : {totals['members_missing_photo']}")
    broken = totals["movie_broken"] + totals["tv_broken"]
    print(f"\n  DOCUMENTS NEEDING REPAIR  : {broken}")

    if args.list and all_titles:
        print("\n================ BROKEN TITLES ================")
        for t in all_titles:
            print(f"  [{t['type']:5}] {str(t['tmdb_id']):>9}  {str(t['title'])[:46]:<46} cast={t['cast']} missing={t['missing']}")


if __name__ == "__main__":
    asyncio.run(main())
