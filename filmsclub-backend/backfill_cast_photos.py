"""
Backfill missing cast headshots in the production clusters from TMDB.

WHAT IT DOES
    For every media document whose cast members have no headshot, look the title
    up on TMDB, match cast members by name, and fill in the photo URL, the TMDB
    person id, the department and the ordering. It only FILLS BLANKS.

WHAT IT DELIBERATELY DOES NOT DO
    - It never removes a cast member.
    - It never reorders the cast list.
    - It never overwrites a `name` or `character` that is already set (so a
      hand-corrected role label in the admin panel survives).
    - It never overwrites an existing photo or id.
    Repairs are strictly additive, which makes a re-run harmless and makes a
    partial failure safe.

USAGE
    python backfill_cast_photos.py                 # dry run, shows what would change
    python backfill_cast_photos.py --apply         # actually write
    python backfill_cast_photos.py --apply --limit 20      # small trial batch
    python backfill_cast_photos.py --apply --cluster 1     # one cluster only
    python backfill_cast_photos.py --apply --kind movie    # movies only

    Always start with the default dry run. Nothing is written without --apply.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aiohttp
import certifi
from motor.motor_asyncio import AsyncIOMotorClient

from Backend.config import Telegram

TMDB_KEY = os.getenv("TMDB_API") or getattr(Telegram, "TMDB_API", "")
DB_NAME = "projectS"
TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w185"


def photo_of(member: dict) -> str:
    for key in ("profile", "profile_path", "profile_url", "avatar", "image"):
        value = member.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalise_name(name: str) -> str:
    return " ".join((name or "").split()).casefold()


async def fetch_credits(session: aiohttp.ClientSession, kind: str, tmdb_id, sem: asyncio.Semaphore):
    """TMDB cast list for a title, or None when unavailable."""
    tmdb_kind = "movie" if kind == "movie" else "tv"
    url = f"{TMDB_BASE}/{tmdb_kind}/{tmdb_id}/credits"
    async with sem:
        for attempt in range(4):
            try:
                async with session.get(url, params={"api_key": TMDB_KEY, "language": "en-US"},
                                       timeout=aiohttp.ClientTimeout(total=25)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return data.get("cast") or []
                    if r.status == 404:
                        return None
                    if r.status == 429:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    await asyncio.sleep(1 + attempt)
            except Exception:
                await asyncio.sleep(1 + attempt)
    return None


def build_repair(cast: list, tmdb_cast: list) -> tuple[list, int]:
    """
    Return (new_cast, filled_count). Additive only.

    Matching is by normalised name. TMDB sometimes lists the same person twice
    for different characters, so we consume matches from a queue to avoid
    giving two identical-name entries the same photo by accident.
    """
    by_name: dict[str, list[dict]] = {}
    for person in tmdb_cast:
        key = normalise_name(person.get("name"))
        if key:
            by_name.setdefault(key, []).append(person)

    new_cast = []
    filled = 0
    for member in cast:
        if not isinstance(member, dict):
            new_cast.append(member)
            continue
        updated = dict(member)

        if photo_of(updated):
            # Already has a headshot: leave it completely alone.
            new_cast.append(updated)
            continue

        queue = by_name.get(normalise_name(updated.get("name")), [])
        if not queue:
            new_cast.append(updated)
            continue

        person = queue.pop(0)
        profile_path = person.get("profile_path")
        if profile_path:
            updated["profile"] = f"{IMG_BASE}{profile_path}"
            updated["profile_path"] = profile_path
            filled += 1
        # Identity fields are safe to add when absent.
        if updated.get("id") is None and person.get("id") is not None:
            updated["id"] = person.get("id")
        if not updated.get("known_for_department") and person.get("known_for_department"):
            updated["known_for_department"] = person["known_for_department"]
        if updated.get("order") is None and person.get("order") is not None:
            updated["order"] = person.get("order")
        new_cast.append(updated)

    return new_cast, filled


async def process_cluster(index: int, uri: str, args, session, sem) -> dict:
    stats = {"cluster": index, "docs_seen": 0, "docs_changed": 0,
             "photos_added": 0, "tmdb_miss": 0, "no_match": 0, "errors": 0,
             "reachable": False}
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000,
                                    connectTimeoutMS=20000, tlsCAFile=certifi.where())
        db = client[DB_NAME]
        await db.command("ping")
        stats["reachable"] = True
    except Exception as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"[:120]
        return stats

    kinds = [args.kind] if args.kind else ["movie", "tv"]
    for kind in kinds:
        coll = db[kind]
        # Only documents that actually have cast, and where at least one member
        # is missing a photo. Pulled in batches so a huge library does not sit
        # in memory at once.
        query = {"cast": {"$exists": True, "$ne": []}}
        projected = {"tmdb_id": 1, "title": 1, "name": 1, "cast": 1}
        async for doc in coll.find(query, projected):
            if args.limit and stats["docs_seen"] >= args.limit:
                break
            cast = doc.get("cast") or []
            if not isinstance(cast, list) or not cast:
                continue
            missing = sum(1 for m in cast if isinstance(m, dict) and not photo_of(m))
            if missing == 0:
                continue

            stats["docs_seen"] += 1
            tmdb_id = doc.get("tmdb_id")
            if not tmdb_id:
                continue

            tmdb_cast = await fetch_credits(session, kind, tmdb_id, sem)
            if tmdb_cast is None:
                stats["tmdb_miss"] += 1
                continue

            new_cast, filled = build_repair(cast, tmdb_cast)
            if filled == 0:
                stats["no_match"] += 1
                continue

            stats["docs_changed"] += 1
            stats["photos_added"] += filled
            title = str(doc.get("title") or doc.get("name"))[:44]
            print(f"    [{kind:5}] {title:<44} +{filled}/{missing} photos", flush=True)

            if args.apply:
                try:
                    await coll.update_one({"_id": doc["_id"]}, {"$set": {"cast": new_cast}})
                except Exception as exc:
                    stats["errors"] += 1
                    print(f"      ! write failed: {type(exc).__name__}: {exc}"[:130], flush=True)

    client.close()
    return stats


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default is dry run)")
    ap.add_argument("--limit", type=int, default=0, help="max docs per cluster (0 = all)")
    ap.add_argument("--cluster", type=int, default=0, help="only this cluster number (1-6)")
    ap.add_argument("--kind", choices=["movie", "tv"], help="only this collection")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel TMDB requests")
    args = ap.parse_args()

    if not TMDB_KEY:
        print("ERROR: no TMDB API key found (TMDB_API).")
        return

    mode = "APPLY (writing)" if args.apply else "DRY RUN (no writes)"
    print(f"Mode: {mode}\nDatabase: {DB_NAME}   concurrency: {args.concurrency}\n")

    uris = Telegram.DATABASE_URIS
    sem = asyncio.Semaphore(args.concurrency)
    totals = {"docs_seen": 0, "docs_changed": 0, "photos_added": 0,
              "tmdb_miss": 0, "no_match": 0, "errors": 0}
    started = time.time()

    async with aiohttp.ClientSession() as session:
        for i, uri in enumerate(uris, start=1):
            if args.cluster and i != args.cluster:
                continue
            print(f"--- Cluster {i} ---")
            s = await process_cluster(i, uri, args, session, sem)
            if not s["reachable"]:
                print(f"  UNREACHABLE: {s.get('error', '')}")
                continue
            print(f"  docs scanned with gaps: {s['docs_seen']}  →  repairable: {s['docs_changed']}  "
                  f"photos: +{s['photos_added']}  (tmdb miss {s['tmdb_miss']}, no name match {s['no_match']}, errors {s['errors']})")
            for k in totals:
                totals[k] += s[k]

    elapsed = time.time() - started
    print("\n================ SUMMARY ================")
    print(f"  mode                 : {mode}")
    print(f"  documents with gaps  : {totals['docs_seen']}")
    print(f"  documents repairable : {totals['docs_changed']}")
    print(f"  photos added         : {totals['photos_added']}")
    print(f"  TMDB lookup failed   : {totals['tmdb_miss']}")
    print(f"  no name match        : {totals['no_match']}")
    print(f"  write errors         : {totals['errors']}")
    print(f"  elapsed              : {elapsed:.1f}s")
    if not args.apply:
        print("\n  Nothing was written. Re-run with --apply to commit.")


if __name__ == "__main__":
    asyncio.run(main())
