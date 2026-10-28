"""
cleanup_db.py  —  Emergency MongoDB space reclaimer (multi-cluster aware)
=========================================================================
Run from filmsclub-backend directory:
    python cleanup_db.py

IMPORTANT: On MongoDB Atlas, `delete_many()` does NOT return disk space to
the quota (WiredTiger keeps the file). Only `drop()` on a collection reclaims
space immediately. So this script DROPS the safe-to-remove collections:

  1. Reports size of every collection on EVERY cluster (DATABASE..DATABASE_5)
  2. DROPS  : file_queue, processed_files, replaced_versions
              (queue can be rebuilt with /rescan; these are pure bookkeeping)
  3. PURGES : failed_files older than 3 days, views older than 60 days
              (purge only; small collections)
  4. Ensures TTL indexes so these auto-expire going forward
  5. Reports sizes AFTER cleanup per cluster
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
import motor.motor_asyncio

# ── Load .env ────────────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
DB_NAME = "projectS"


def build_uris():
    uris = []

    def clean(raw):
        return (raw or "").strip().strip('"').strip("'")

    primary = clean(os.environ.get("DATABASE"))
    if primary:
        uris.append(primary)
    for i in range(2, 6):
        u = clean(os.environ.get(f"DATABASE_{i}"))
        if u and u not in uris:
            uris.append(u)
    return uris


MONGO_URIS = build_uris()
if not MONGO_URIS:
    print("ERROR: No DATABASE/DATABASE_2..5 env vars set. Check your .env file.")
    sys.exit(1)


def fmt_mb(bytes_val):
    if bytes_val is None:
        return "?"
    return f"{bytes_val / 1024 / 1024:.2f} MB"


async def get_collection_sizes(db):
    sizes = {}
    try:
        names = await db.list_collection_names()
        for name in names:
            try:
                stats = await db.command("collStats", name)
                sizes[name] = stats.get("storageSize", 0) + stats.get("totalIndexSize", 0)
            except Exception:
                sizes[name] = None
    except Exception as e:
        print(f"  WARNING: Could not list collections: {e}")
    return sizes


async def ensure_ttl_indexes(db):
    """Make sure the auto-expire indexes exist going forward."""
    results = []
    queue_col = db["file_queue"]
    processed_col = db["processed_files"]
    failed_col = db["failed_files"]
    views_col = db["views"]

    try:
        await queue_col.create_index(
            [("created_at", 1)], expireAfterSeconds=2 * 86400, name="ttl_queue_created")
        results.append("file_queue TTL: all items expire after 2 days")
    except Exception as e:
        results.append(f"file_queue TTL WARN: {e}")

    try:
        await processed_col.create_index(
            [("processed_at", 1)], expireAfterSeconds=3 * 86400, name="ttl_processed")
        results.append("processed_files TTL: expire after 3 days")
    except Exception as e:
        results.append(f"processed_files TTL WARN: {e}")

    try:
        await failed_col.create_index(
            [("timestamp", 1)], expireAfterSeconds=7 * 86400, name="ttl_failed_files")
        results.append("failed_files TTL: expire after 7 days")
    except Exception as e:
        results.append(f"failed_files TTL WARN: {e}")

    try:
        await views_col.create_index(
            [("created_at", 1)], expireAfterSeconds=90 * 86400, name="ttl_views")
        results.append("views TTL: expire after 90 days (new docs)")
    except Exception as e:
        results.append(f"views TTL WARN: {e}")

    return results


async def process_cluster(index, uri, confirm_all):
    client = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=15000)
    db = client[DB_NAME]

    try:
        await db.command("ping")
    except Exception as e:
        print(f"\n[Cluster {index}] UNREACHABLE — skipping ({e})")
        client.close()
        return 0

    print(f"\n{'=' * 60}")
    print(f"  Cluster {index}  —  database '{DB_NAME}'")
    print(f"{'=' * 60}")

    # ── Report current sizes ─────────────────────────────
    print("Current collection sizes:")
    before = await get_collection_sizes(db)
    total_before = 0
    for name, size in sorted(before.items(), key=lambda x: x[1] or 0, reverse=True):
        print(f"   {name:<30} {fmt_mb(size)}")
        total_before += (size or 0)
    print(f"   {'TOTAL':<30} {fmt_mb(total_before)}")

    now = datetime.utcnow()
    queue_col = db["file_queue"]
    processed_col = db["processed_files"]
    replaced_col = db["replaced_versions"]
    failed_col = db["failed_files"]
    views_col = db["views"]

    # ── Count what we'll delete ──────────────────────────
    async def safe_count(col, filt):
        try:
            return await col.count_documents(filt)
        except Exception:
            return 0

    q_total = await safe_count(queue_col, {})
    p_count = await processed_col.estimated_document_count() if processed_col is not None else 0
    r_count = await replaced_col.estimated_document_count() if replaced_col is not None else 0
    failed_old = await safe_count(failed_col, {"timestamp": {"$lt": now - timedelta(days=3)}})
    views_old = await safe_count(views_col, {"date": {"$lt": (now - timedelta(days=60)).strftime("%Y-%m-%d")}})

    print(f"\nPlan for this cluster:")
    print(f"   DROP file_queue (all, incl. pending)     {q_total:,} docs")
    print(f"   DROP processed_files (entire)            {p_count:,} docs")
    print(f"   DROP replaced_versions (entire)          {r_count:,} docs")
    print(f"   purge failed_files older than 3 days     {failed_old:,} docs")
    print(f"   purge views older than 60 days           {views_old:,} docs")

    confirm = confirm_all
    if not confirm:
        print()
        answer = input("Proceed for THIS cluster? [y/N/a(=all)]: ").strip().lower()
        if answer == "a":
            confirm_all = True
            confirm = True
        elif answer != "y":
            print("Skipped.")
            client.close()
            return 0
    else:
        confirm = True

    if confirm:
        print("\nCleaning up...")

        # DROP collections — the ONLY way to reclaim Atlas quota space
        for col, label, cnt in ((queue_col, "file_queue", q_total),
                                (processed_col, "processed_files", p_count),
                                (replaced_col, "replaced_versions", r_count)):
            try:
                await col.drop()
                print(f"   OK DROPPED {label} ({cnt:,} docs)")
            except Exception as e:
                print(f"   FAIL {label} drop: {e}")

        # Purge small collections (delete is fine here — small docs)
        try:
            r = await failed_col.delete_many({"timestamp": {"$lt": now - timedelta(days=3)}})
            print(f"   OK failed_files: purged {r.deleted_count:,} old entries")
        except Exception as e:
            print(f"   FAIL failed_files purge: {e}")

        try:
            r = await views_col.delete_many({"date": {"$lt": (now - timedelta(days=60)).strftime("%Y-%m-%d")}})
            print(f"   OK views: purged {r.deleted_count:,} old records")
        except Exception as e:
            print(f"   FAIL views purge: {e}")

    # ── TTL indexes going forward ─────────────────────────
    print("\nEnsuring TTL indexes...")
    for line in await ensure_ttl_indexes(db):
        print(f"   {line}")

    # ── Report after ──────────────────────────────────────
    print("\nCollection sizes AFTER cleanup:")
    after = await get_collection_sizes(db)
    total_after = 0
    for name, size in sorted(after.items(), key=lambda x: x[1] or 0, reverse=True):
        before_size = before.get(name, 0) or 0
        saved = before_size - (size or 0)
        saved_str = f"  (saved {fmt_mb(saved)})" if saved > 0 else ""
        print(f"   {name:<30} {fmt_mb(size)}{saved_str}")
        total_after += (size or 0)

    print(f"\n   TOTAL BEFORE: {fmt_mb(total_before)}")
    print(f"   TOTAL AFTER:  {fmt_mb(total_after)}")
    print(f"   SPACE FREED:  {fmt_mb(total_before - total_after)}")

    client.close()
    return total_before - total_after


async def main():
    print("\n" + "=" * 60)
    print("  FilmsClub — MongoDB Emergency Cleanup (multi-cluster)")
    print("=" * 60)
    print(f"\nClusters found: {len(MONGO_URIS)}")
    for i, _ in enumerate(MONGO_URIS, start=1):
        # never print the full URI (contains credentials)
        print(f"   Cluster {i}")

    print("\n⚠️  DROPPING file_queue removes PENDING items too.")
    print("   They are NOT lost — re-add them later with the /rescan bot command.")
    print("   DROP is required (not delete) — only drop() returns space on Atlas.\n")

    confirm_all = False
    answer = input("Continue? [y/N]: ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return

    total_freed = 0
    for i, uri in enumerate(MONGO_URIS, start=1):
        total_freed += await process_cluster(i, uri, confirm_all)

    print("\n" + "=" * 60)
    print(f"  DONE — total space freed across clusters: {fmt_mb(total_freed)}")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Wait 1-2 mins for the Atlas storage meter to update")
    print("  2. Restart your backend (python -m Backend OR heroku restart)")
    print("  3. Re-index old files with the /rescan bot command if needed")
    print("  4. Streaming works again once quota is no longer exceeded")


if __name__ == "__main__":
    asyncio.run(main())
