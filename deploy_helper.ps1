$old = "        await progress.update(stats, cursor, elapsed(), note=`"resumed after restart`, force=True)`n    else:`n        await progress.update(stats, cursor, elapsed(), note=`"scanning history`, force=True)`n`n    consecutive_failures = 0"
$new = @"
        await progress.update(stats, cursor, elapsed(), note="resumed after restart", force=True)
    else:
        await progress.update(stats, cursor, elapsed(), note="scanning history", force=True)

    # -- Skip the newest N messages at the top of the channel ------------------
    # Useful when a channel's top posts are pinned promos/welcome posts that
    # should not be indexed. Each skipped message is counted in `stats["skipped"]`
    # so it shows up in the progress and scan-table summaries.
    skip_n = int(runtime_config.get("INDEX_SKIP_FIRST_MESSAGES"))
    if skip_n > 0:
        batch_skip = min(int(runtime_config.get("scan_batch_size")), skip_n)
        skipped = 0
        kwargs = {"limit": batch_skip}
        batch = await history_client.get_chat_history(channel_id, **kwargs)
        async for msg in batch:
            if stop_event.is_set():
                await _finish(
                    progress, stats, cursor, elapsed(), "cancelled", "stopped by admin"
                )
                return
            try:
                _ = _is_video_message(msg)
            except Exception:
                pass
            stats["skipped"] += 1
            skipped += 1
            if stats["skipped"] >= skip_n:
                break
        if skipped > 0:
            await progress.update(
                stats,
                cursor,
                elapsed(),
                note=f"skipped top {skipped} messages - scanning history",
                force=True,
            )

    consecutive_failures = 0
"@
$content = Get-Content "C:\Users\Ravikiran\Downloads\filmsclub\filmsclub-backend\Backend\helper\channel_index.py" -Raw
if ($content -match [regex]::Escape($old)) {
    $content -replace [regex]::Escape($old), $new | Set-Content "C:\Users\Ravikiran\Downloads\filmsclub\filmsclub-backend\Backend\helper\channel_index.py" -NoNewline
    "MATCHED AND REPLACED"
} else {
    "NO MATCH - old text not found"
}