"""Telegram ``/reassign`` — move a mis-indexed file onto the correct title.

The automatic pipeline guesses the release from its filename. When that guess is
wrong the file is already in the library, so ``/manualadd`` refuses it as a
duplicate. ``/reassign`` finds the existing quality entry and *moves* it,
keeping the encoded stream id (and therefore existing links) intact.

Loaded automatically: ``Backend/wzgram_pkg/plugins`` is the wzgram plugins root.
"""
import html

from wzgram import enums, filters
from wzgram.types import Message

from Backend.logger import LOGGER
from Backend.wzgram_pkg import StreamBot
from Backend.helper.audit import audit_telegram
from Backend.helper.channel_index import clean_channel_id
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.file_reassign import (
    ReassignError,
    find_library_files,
    reassign_file,
    split_reassign_flags,
)
from Backend.helper.manual_add import ManualAddError, parse_manual_args


REASSIGN_USAGE = (
    "🔀 <b>Reassign a file</b>\n\n"
    "Move a file that is already in the library but sits under the "
    "<i>wrong title or wrong episode</i>. The Telegram post itself is not "
    "touched and the stream link keeps working.\n\n"
    "<code>/reassign &lt;ref&gt; &lt;post link&gt; [options]</code>\n"
    "<code>/reassign &lt;ref&gt;</code> — reply to the forwarded channel post\n\n"
    "<b>ref</b>: <code>tt0133093</code>, an imdb.com/title/tt… link, a TMDB id "
    "(<code>603</code>), or a themoviedb.org/movie|tv link.\n"
    "<b>options</b>: <code>S01E02</code> (series), <code>quality=2160p</code>, "
    "<code>rip=WEB-DL</code>, <code>lang=te,en</code>, <code>codec=x265</code>, "
    "<code>type=movie|tv</code>.\n"
    "<code>replace</code> — replace a same-quality file already on the target.\n"
    "<code>keepempty</code> — keep the old title even if it has no files left.\n\n"
    "<b>Example</b>\n"
    "<code>/reassign tt0133093 https://t.me/c/2685988600/42</code>\n\n"
    "<i>The file must already be indexed. If it is not, use /manualadd.</i>"
)


def _reply_link(message: Message) -> str:
    """Turn a forwarded channel post into a t.me link (like /manualadd)."""
    reply = message.reply_to_message
    if reply is None:
        return ""
    fwd_chat = getattr(reply, "forward_from_chat", None)
    fwd_id = getattr(reply, "forward_from_message_id", None)
    if fwd_chat and fwd_id:
        return f"https://t.me/c/{clean_channel_id(fwd_chat.id)}/{int(fwd_id)}"
    chat = getattr(reply, "chat", None)
    if chat is not None and getattr(chat, "type", None) is not None and getattr(reply, "id", None):
        return f"https://t.me/c/{clean_channel_id(chat.id)}/{int(reply.id)}"
    return ""


@StreamBot.on_message(
    filters.command(["reassign", "movefile", "refile"]) & filters.private & CustomFilters.admin
)
async def reassign_indexed_file(bot, message: Message):
    """Move one already-indexed Telegram file onto the correct TMDB/IMDb title."""
    raw = (message.text or "").split()[1:]
    if not raw or raw[0].lower() in ("help", "?", "usage"):
        return await message.reply_text(REASSIGN_USAGE, parse_mode=enums.ParseMode.HTML)

    tokens, flags = split_reassign_flags(raw)
    try:
        args = parse_manual_args(tokens)
    except ManualAddError as e:
        return await message.reply_text(
            f"❌ {e}\n\n{REASSIGN_USAGE}", parse_mode=enums.ParseMode.HTML
        )

    link = str(args.pop("link") or "").strip() or _reply_link(message)
    if not link:
        return await message.reply_text(
            "❌ Paste the post link, or reply to the forwarded channel post.\n\n"
            + REASSIGN_USAGE,
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text("🔎 Looking up that file in the library…")
    try:
        found = await find_library_files(link=link, bot=StreamBot)
    except ReassignError as e:
        return await status.edit_text(f"❌ {e}")
    except Exception as e:  # pragma: no cover - client/network dependent
        LOGGER.error(f"[/reassign] lookup failed: {e}")
        return await status.edit_text(f"❌ Lookup failed: {e}")

    rows = found["matches"]
    if not rows:
        return await status.edit_text(
            "❌ That file is not in the library yet. Use /manualadd to add it."
        )
    if len(rows) > 1:
        return await status.edit_text(
            "❌ That file is indexed under several titles. Use the admin console "
            "→ Manual add → Reassign to pick the exact entry."
        )

    try:
        result = await reassign_file(rows[0], **args, **flags)
    except ReassignError as e:
        audit_telegram(
            "media.reassign_file", message.from_user.id,
            target=f"media_file:{rows[0].get('file_id')}",
            detail={"ref": args.get("media_ref"), "error": str(e)},
            result="error",
        )
        return await status.edit_text(f"❌ {e}")
    except Exception as e:
        LOGGER.error(f"[/reassign] move failed: {e}")
        return await status.edit_text(f"❌ Could not move that file: {e}")

    try:
        from Backend.helper.cache import clear_all_caches

        await clear_all_caches()
    except Exception:  # pragma: no cover - cache is best effort
        pass

    audit_telegram(
        "media.reassign_file", message.from_user.id,
        target=f"media:{result['media_type']}:{result['tmdb_id']}",
        detail={
            "source": "telegram", "file_id": result["file_id"],
            "from": result["from"], "ref": args.get("media_ref"),
            "quality": result["quality"],
            "old_title_removed": result["old_title_removed"],
            "moved": result["moved"],
        },
        result="ok" if result["moved"] else "partial",
    )

    to = html.escape(str(result["title"]))
    old = html.escape(str(result["from"]["title"]))
    ep = ""
    if result.get("season_number") is not None:
        ep = (
            f"• Episode: S{int(result['season_number']):02d}"
            f"E{int(result['episode_number']):02d}\n"
        )
    text = (
        f"✅ <b>File reassigned</b>\n\n"
        f"• From: <b>{old}</b>\n"
        f"• To: <b>{to}</b> ({result['media_type']})\n"
        f"• TMDB: <code>{result['tmdb_id']}</code>\n"
        f"{ep}"
        f"• Quality: {html.escape(str(result['quality']))}\n"
        f"• File: <code>{html.escape(str(result['file_name'] or result['file_id']))}</code>\n"
        f"• <a href=\"{html.escape(result['tmdb_url'])}\">Open on TMDB</a>"
        + (
            f"\n\n<i>Old title removed (it had no files left).</i>"
            if result["old_title_removed"] else ""
        )
        + (f"\n\n⚠️ <i>{html.escape(result['warning'])}</i>" if result["warning"] else "")
    )
    await status.edit_text(
        text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True
    )
