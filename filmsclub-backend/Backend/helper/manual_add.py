"""Manual ingest: identify a Telegram post by an explicit TMDB/IMDb reference.

The automatic pipeline guesses the release from the filename. Some uploads have
names it cannot read, so an admin supplies the reference instead. Both the admin
panel (``POST /admin/api/queue/manual``) and the Telegram ``/manualadd`` command
call :func:`build_manual_metadata_info`, so the two surfaces cannot diverge.

Deliberately free of FastAPI imports: errors are :class:`ManualAddError` with a
user-facing message and a suggested HTTP status. The API layer converts them to
``HTTPException``; the bot prints the message directly.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from Backend.config import Telegram


class ManualAddError(Exception):
    """A validation or upstream error an admin should see verbatim."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


async def tmdb_get(path: str, params: Optional[dict] = None) -> dict:
    """One-off TMDB read. Raises ManualAddError with a clear message."""
    if not Telegram.TMDB_API:
        raise ManualAddError("TMDB_API is not configured")
    url = f"https://api.themoviedb.org/3/{path.lstrip('/')}"
    query = {"api_key": Telegram.TMDB_API, "language": "en-US", **(params or {})}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url, params=query) as response:
                if response.status != 200:
                    raise ManualAddError(f"TMDB request failed ({response.status})", 502)
                return await response.json()
    except aiohttp.ClientError as e:
        raise ManualAddError(f"Could not reach TMDB: {e}", 502) from e


async def tmdb_details(kind: str, tmdb_id: int) -> dict:
    return await tmdb_get(
        f"/{'tv' if kind == 'tv' else 'movie'}/{tmdb_id}",
        {"append_to_response": "credits"},
    )


def parse_media_ref(media_ref: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Split a pasted reference into (tmdb_id, imdb_id, kind hint)."""
    text = str(media_ref or "").strip()
    if not text:
        raise ManualAddError("Paste a TMDB link, IMDb link or id")

    imdb_match = re.search(r"tt\d{7,10}", text, re.IGNORECASE)
    imdb_id = imdb_match.group(0).lower() if imdb_match else None

    tmdb_match = re.search(r"/(movie|tv)/(\d+)", text, re.IGNORECASE)
    if tmdb_match:
        return tmdb_match.group(2), imdb_id, tmdb_match.group(1).lower()
    if text.isdigit():
        return text, None, None
    if imdb_id:
        return None, imdb_id, None
    raise ManualAddError(
        "Use a themoviedb.org/movie|tv link, an imdb.com/title/tt.. link, "
        "a TMDB id, or a tt id"
    )


async def resolve_manual_ids(tmdb_id, imdb_id, url_kind, asked_kind=None) -> tuple:
    """Return (tmdb_id, media_type, imdb_id), translating an IMDb id when needed."""
    if not tmdb_id and imdb_id:
        found = await tmdb_get(f"/find/{imdb_id}", {"external_source": "imdb_id"})
        for key, kind in (("movie_results", "movie"), ("tv_results", "tv")):
            results = found.get(key) or []
            if results and results[0].get("id"):
                return str(results[0]["id"]), kind, imdb_id
        raise ManualAddError(f"TMDB has no entry for IMDb id {imdb_id}", 404)
    if not tmdb_id:
        raise ManualAddError("No TMDB id resolved from that reference")
    kind = str(url_kind or asked_kind or "").lower()
    return str(tmdb_id), ("tv" if kind == "tv" else "movie"), imdb_id


def manual_languages(raw, tmdb_data: dict) -> list:
    """Accept ISO codes or language names; fall back to TMDB's original language."""
    from Backend.helper.pyro import normalize_languages

    codes: List[str] = []
    for token in re.split(r"[,;|\s]+", str(raw or "")):
        token = token.strip()
        if not token:
            continue
        if len(token) == 2 and token.isalpha():
            code = token.lower()
        else:
            code = (normalize_languages(token) or [None])[0]
        if code and code not in codes:
            codes.append(code)
    if codes:
        return codes

    original = str(tmdb_data.get("original_language") or "").lower()
    if not original:
        return ["en"]
    return ["en"] if original == "en" else [original, "en"]


async def build_manual_metadata_info(
    media_ref: str,
    kind: Optional[str] = None,
    quality: Optional[str] = None,
    rip: Optional[str] = None,
    languages=None,
    codec: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> dict:
    """Build the metadata_info dict the queue worker inserts, straight from TMDB.

    Keys mirror fetch_movie_metadata / fetch_tv_metadata so insert_media keeps
    working unchanged. ``_manual_declared`` lists fields the admin set by hand,
    which the ffmpeg probe must not overwrite.
    """
    from Backend.helper.metadata import get_tmdb_people_and_schedule

    declared = [
        field
        for field, value in (
            ("quality", quality),
            ("rip", rip),
            ("languages", languages),
            ("codec", codec),
        )
        if value
    ]

    url_tmdb_id, imdb_id, url_kind = parse_media_ref(media_ref)
    tmdb_id, media_type, imdb_id = await resolve_manual_ids(
        url_tmdb_id, imdb_id, url_kind, kind
    )
    # Season 0 is TMDB's Specials season and is perfectly valid, so test for
    # "missing" rather than truthiness — `not (0 and 3)` used to reject every
    # Specials upload with "Series need a season and episode number".
    if media_type == "tv" and (season is None or episode is None):
        raise ManualAddError("Series need a season and episode number")

    data = await tmdb_details(media_type, int(tmdb_id))
    release = str(data.get("release_date") or data.get("first_air_date") or "")
    poster = f"https://image.tmdb.org/t/p/w500{data['poster_path']}" if data.get("poster_path") else ""
    backdrop = (
        f"https://image.tmdb.org/t/p/original{data['backdrop_path']}"
        if data.get("backdrop_path") else ""
    )

    info: Dict[str, Any] = {
        "tmdb_id": int(tmdb_id),
        "title": data.get("title") or data.get("name") or "",
        "year": int(release[:4]) if release[:4].isdigit() else 0,
        "rate": float(data.get("vote_average") or 0),
        "description": data.get("overview") or "",
        "poster": poster,
        "backdrop": backdrop,
        "media_type": media_type,
        "genres": [g.get("name") for g in data.get("genres") or [] if g.get("name")],
        "runtime": int(data.get("runtime") or 0),
        "quality": str(quality).lower() if quality else "1080p",
        "languages": manual_languages(languages, data),
        "rip": str(rip).strip() if rip else "Blu-ray",
        "source": "Manual",
        "imdb_url": f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None,
        "tmdb_url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
        "group": None,
        "release_group": None,
    }
    if codec:
        info["codec"] = str(codec).strip().lower()

    if media_type == "tv":
        episode_data = await tmdb_get(
            f"/tv/{tmdb_id}/season/{int(season)}/episode/{int(episode)}"
        )
        info.update({
            "status": data.get("status") or "",
            "total_seasons": int(data.get("number_of_seasons") or 0),
            "total_episodes": int(data.get("number_of_episodes") or 0),
            "season_number": int(season),
            "episode_number": int(episode),
            "episode_title": episode_data.get("name") or f"Episode {int(episode)}",
            "episode_backdrop": (
                f"https://image.tmdb.org/t/p/original{episode_data['still_path']}"
                if episode_data.get("still_path") else ""
            ),
        })

    info["cast"] = (await get_tmdb_people_and_schedule(int(tmdb_id), media_type)).get("cast", [])
    info["_manual_declared"] = declared
    if not info["title"]:
        raise ManualAddError("TMDB returned no title for that id", 404)
    return info


# ── Telegram command argument parsing ───────────────────────────────────────
OPTION_ALIASES = {
    "quality": "quality", "q": "quality",
    "rip": "rip",
    "lang": "languages", "languages": "languages", "audio": "languages",
    "codec": "codec",
    "type": "kind", "kind": "kind",
    "season": "season", "s": "season",
    "episode": "episode", "e": "episode", "ep": "episode",
}


def parse_manual_args(tokens) -> dict:
    """Parse ``/manualadd`` arguments into build_manual_metadata_info kwargs.

    Positionals: ``<ref> [post link]``. ``S01E02`` sets season/episode and
    implies a series. ``key=value`` options set quality/rip/lang/codec/etc.
    """
    positional: List[str] = []
    opts: Dict[str, Any] = {}

    for raw in tokens:
        token = str(raw).strip()
        if not token:
            continue
        ep = re.fullmatch(r"S(\d{1,3})E(\d{1,4})", token, re.IGNORECASE)
        if ep:
            opts["season"] = int(ep.group(1))
            opts["episode"] = int(ep.group(2))
            opts.setdefault("kind", "tv")
            continue
        if "=" in token and not token.lower().startswith("http"):
            key, _, value = token.partition("=")
            field = OPTION_ALIASES.get(key.strip().lower())
            if not field:
                raise ManualAddError(f"Unknown option '{key.strip()}'. Use quality=, rip=, lang=, codec=, season=, episode= or type=.")
            opts[field] = value.strip()
            continue
        positional.append(token)

    if not positional:
        raise ManualAddError("Give a TMDB/IMDb reference first")
    if len(positional) > 2:
        raise ManualAddError("Too many arguments — pass one reference and at most one post link")

    out: Dict[str, Any] = {
        "media_ref": positional[0],
        "link": positional[1] if len(positional) > 1 else None,
        "kind": None,
        "quality": None,
        "rip": None,
        "languages": None,
        "codec": None,
        "season": None,
        "episode": None,
    }
    out.update(opts)

    kind = out.get("kind")
    if kind:
        kind = str(kind).strip().lower()
        if kind not in ("movie", "tv"):
            raise ManualAddError("type must be movie or tv")
        out["kind"] = kind

    for field in ("season", "episode"):
        value = out.get(field)
        if value in (None, ""):
            out[field] = None
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ManualAddError(f"{field} must be a number")
        if number < 1:
            raise ManualAddError(f"{field} must be at least 1")
        out[field] = number

    if bool(out["season"]) != bool(out["episode"]):
        raise ManualAddError("Give both season and episode (or use S01E02)")
    if out["season"]:
        out["kind"] = "tv"
    elif out["kind"] == "tv":
        raise ManualAddError("Series need a season and episode number (e.g. S01E02)")

    for field in ("quality", "rip", "languages", "codec"):
        value = out.get(field)
        out[field] = str(value).strip() if value else None

    return out
