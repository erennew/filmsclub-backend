"""
Backfill crew credits (director, writers, producers, key department heads) into
the production clusters from TMDB.

WHY
    The UI renders a "Cast & Crew" section, but no code path has ever stored a
    `crew` array, so every title showed cast only. TMDB already returns a full
    crew list on the same /credits call used for cast; it was simply discarded.

WHAT IT WRITES
    doc["crew"] = [{id, name, character: <job>, profile, profile_path,
                    known_for_department, order}, ...]

    `character` carries the JOB title so the existing cast-card component renders
    it as the subtitle without needing any change.

WHAT IT WILL NOT DO
    - Never touches `cast`.
    - Never overwrites an existing non-empty `crew` array unless --overwrite.
    - Skips people with no TMDB photo only when we already have enough names
      (keeps cards from being all-initials while staying complete for small crews).

USAGE
    python backfill_crew.py                  # dry run
    python backfill_crew.py --apply          # write
    python backfill_crew.py --apply --limit 5   # trial batch per cluster
    python backfill_crew.py --apply --kind tv
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

# Jobs worth showing, in the order a viewer expects to see them. TMDB labels the
# director of photography as "Director of Photography" on film credits and the
# composer as "Original Music Composer"; both are matched exactly.
KEY_JOBS = [
    "Director",
    "Creator",
    "Screenplay",
    "Writer",
    "Story",
    "Director of Photography",
    "Original Music Composer",
    "Editor",
    "Production Design",
    "Producer",
    "Executive Producer",
]

# A person can hold several of the key jobs at once; keep the first (highest
# priority) one so each name appears exactly once in the row.
MAX_CREW = 12


def pick_crew(tmdb_crew: list) -> list[dict]:
    """Reduce a TMDB crew list to the key roles, one entry per person."""
    rank = {job: i for i, job in enumerate(KEY_JOBS)}
    best: dict[str, tuple[int, dict]] = {}

    for person in tmdb_crew or []:
        job = (person.get("job") or "").strip()
        if job not in rank:
            continue
        name = (person.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        current = best.get(key)
        # Keep the highest-priority job for this person.
        if current is None or rank[job] < current[0]:
            best[key] = (rank[job], person)

    ordered = sorted(best.values(), key=lambda pair: pair[0])
    out: list[dict] = []
    for index, (_, person) in enumerate(ordered[:MAX_CREW]):
        profile_path = person.get("profile_path") or ""
        out.append({
            "id": person.get("id"),
            "name": (person.get("name") or "").strip(),
            "character": (person.get("job") or "").strip(),
            "profile": f"{IMG_BASE}{profile_path}" if profile_path else "",
            "profile_path": profile_path,
            "known_for_department": (person.get("department") or "Production").strip(),
            "order": index,
        })
    return out


async def fetch_crew(session: aiohttp.ClientSession, kind: str, tmdb_id, sem: asyncio.Semaphore):
    tmdb_kind = "movie" if kind == "movie" else "tv"
    url = f"{TMDB_BASE}/{tmdb_kind}/{tmdb_id}/credits"
    async with sem:
        for attempt in range(4):
            try:
                async with session.get(url, params={"api_key": TMDB_KEY, "language": "en-US"},
                                       timeout=aiohttp.ClientTimeout(total=25)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return data.get("crew") or []
                    if r.status == 404:
                        return None
                    if r.status == 429:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    await asyncio.sleep(1 + attempt)
            except Exception:
                await asyncio.sleep(1 + attempt)
    return None


async def process_cluster(index: int, uri: str, args, session, sem) -> dict:
    stats = {"cluster": index, "reachable": False, "docs_seen": 0, "docs_changed": 0,
             "crew_written": 0, "with_photo": 0, "skipped_has_crew": 0,
             "tmdb_miss": 0, "errors": 0}
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000,
                                    connectTimeoutMS=20000, tlsCAFile=certifi.where())
        db = client[DB_NAME]
        await db.command("ping")
        stats["reachable"] = True
    except Exception as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"[:120]
        return stats

    for kind in ([args.kind] if args.kind else ["movie", "tv"]):
        coll = db[kind]
        async for doc in coll.find({}, {"tmdb_id": 1, "title": 1, "name": 1, "crew": 1}):
            if args.limit and stats["docs_seen"] >= args.limit:
                break
            existing = doc.get("crew")
            if existing and not args.overwrite:
                stats["skipped_has_crew"] += 1
                continue
            tmdb_id = doc.get("tmdb_id")
            if not tmdb_id:
                continue

            stats["docs_seen"] += 1
            tmdb_crew = await fetch_crew(session, kind, tmdb_id, sem)
            if tmdb_crew is None:
                stats["tmdb_miss"] += 1
                continue

            crew = pick_crew(tmdb_crew)
            if not crew:
                stats["tmdb_miss"] += 1
                continue

            stats["docs_changed"] += 1
            stats["crew_written"] += len(crew)
            stats["with_photo"] += sum(1 for c in crew if c["profile"])
            title = str(doc.get("title") or doc.get("name"))[:42]
            photos = sum(1 for c in crew if c["profile"])
            print(f"    [{kind:5}] {title:<42} {len(crew)} crew ({photos} with photo)", flush=True)

            if args.apply:
                try:
                    await coll.update_one({"_id": doc["_id"]}, {"$set": {"crew": crew}})
                except Exception as exc:
                    stats["errors"] += 1
                    print(f"      ! write failed: {type(exc).__name__}: {exc}"[:120], flush=True)

    client.close()
    return stats


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cluster", type=int, default=0)
    ap.add_argument("--kind", choices=["movie", "tv"])
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--overwrite", action="store_true",
                    help="replace crew that already exists")
    args = ap.parse_args()

    if not TMDB_KEY:
        print("ERROR: no TMDB API key (TMDB_API).")
        return

    mode = "APPLY (writing)" if args.apply else "DRY RUN (no writes)"
    print(f"Mode: {mode}\nDatabase: {DB_NAME}   concurrency: {args.concurrency}\n")

    sem = asyncio.Semaphore(args.concurrency)
    totals = {"docs_seen": 0, "docs_changed": 0, "crew_written": 0, "with_photo": 0,
              "skipped_has_crew": 0, "tmdb_miss": 0, "errors": 0}
    started = time.time()

    async with aiohttp.ClientSession() as session:
        for i, uri in enumerate(Telegram.DATABASE_URIS, start=1):
            if args.cluster and i != args.cluster:
                continue
            print(f"--- Cluster {i} ---")
            s = await process_cluster(i, uri, args, session, sem)
            if not s["reachable"]:
                print(f"  UNREACHABLE: {s.get('error', '')}")
                continue
            print(f"  docs {s['docs_seen']} → {s['docs_changed']} updated, "
                  f"{s['crew_written']} crew rows ({s['with_photo']} with photo), "
                  f"skipped-existing {s['skipped_has_crew']}, tmdb-miss {s['tmdb_miss']}, errors {s['errors']}")
            for k in totals:
                totals[k] += s[k]

    print("\n================ SUMMARY ================")
    print(f"  mode            : {mode}")
    print(f"  documents seen  : {totals['docs_seen']}")
    print(f"  documents updated: {totals['docs_changed']}")
    print(f"  crew rows written: {totals['crew_written']}")
    print(f"  ...with a photo : {totals['with_photo']}")
    print(f"  skipped (has crew): {totals['skipped_has_crew']}")
    print(f"  no TMDB crew    : {totals['tmdb_miss']}")
    print(f"  write errors    : {totals['errors']}")
    print(f"  elapsed         : {time.time() - started:.1f}s")
    if not args.apply:
        print("\n  Nothing was written. Re-run with --apply to commit.")


if __name__ == "__main__":
    asyncio.run(main())
