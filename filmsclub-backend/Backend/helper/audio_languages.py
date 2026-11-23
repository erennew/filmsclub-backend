"""Audio-language normalisation shared by the ingest path and the backfill job.

``QualityDetail.audio_tracks[].language`` holds a *display name* produced by
``media_probe.get_language_name`` ("English", "Telugu", …). Two files can
spell the same language differently, so languages are compared on a
casefolded, whitespace-collapsed key while the canonical display form is what
gets stored. That keeps the panel readable and still collapses duplicates.

``None``, empty values and the unknown/untagged sentinel ("Unknown",
"Original Audio") are excluded — they are not a known language, so a title
with only those reports no audio language rather than a phantom entry.
"""

import re
from typing import Any, Dict, Iterable, List, Optional

from Backend.helper.media_probe import LANGUAGE_MAP

# Display names that mean "no known language".
UNKNOWN_DISPLAY_NAMES = {"unknown", "original audio"}

_WHITESPACE = re.compile(r"\s+")

# Canonical display form per casefolded key ("english" -> "English").
CANONICAL: Dict[str, str] = {}
for _display in LANGUAGE_MAP.values():
    CANONICAL.setdefault(_display.casefold(), _display)


def normalize_key(name: Any) -> str:
    """Casefolded, whitespace-collapsed comparison key for a display name."""
    if name is None:
        return ""
    return _WHITESPACE.sub(" ", str(name)).strip().casefold()


def is_known_language(name: Any) -> bool:
    """False for None, empty values and the unknown/untagged sentinel."""
    key = normalize_key(name)
    if not key:
        return False
    return key not in UNKNOWN_DISPLAY_NAMES


def canonical_display(name: Any) -> str:
    """Canonical display form of a language name."""
    cleaned = _WHITESPACE.sub(" ", str(name or "")).strip()
    return CANONICAL.get(normalize_key(cleaned), cleaned)


def collect_languages(names: Iterable[Any]) -> List[str]:
    """Distinct, sorted display names of the known languages in ``names``."""
    found: Dict[str, str] = {}
    for name in names or []:
        if not is_known_language(name):
            continue
        key = normalize_key(name)
        found.setdefault(key, canonical_display(name))
    return sorted(found.values(), key=lambda v: (v.casefold(), v))


def quality_languages(quality: Optional[Dict]) -> List[str]:
    """Distinct known languages of one file entry."""
    if not quality:
        return []
    return collect_languages(
        track.get("language") for track in (quality.get("audio_tracks") or [])
    )


def movie_language_names(doc: Optional[Dict]) -> List[str]:
    """Distinct known audio languages of a movie document."""
    if not doc:
        return []
    names: List[Any] = []
    for quality in doc.get("telegram") or []:
        for track in (quality or {}).get("audio_tracks") or []:
            names.append((track or {}).get("language"))
    return collect_languages(names)


def tv_language_names(doc: Optional[Dict]) -> List[str]:
    """Distinct known audio languages of a TV document."""
    if not doc:
        return []
    names: List[Any] = []
    for season in doc.get("seasons") or []:
        for episode in (season or {}).get("episodes") or []:
            for quality in (episode or {}).get("telegram") or []:
                for track in (quality or {}).get("audio_tracks") or []:
                    names.append((track or {}).get("language"))
    return collect_languages(names)


def language_names_for(kind: str, doc: Optional[Dict]) -> List[str]:
    """Language set of a media document, whichever collection it lives in."""
    if (kind or "").lower() == "tv":
        return tv_language_names(doc)
    return movie_language_names(doc)


def all_file_entries(doc: Optional[Dict], kind: str) -> List[Dict]:
    """Every file entry of a media document, flattened."""
    entries: List[Dict] = []
    if not doc:
        return entries
    if (kind or "").lower() == "tv":
        for season in doc.get("seasons") or []:
            for episode in (season or {}).get("episodes") or []:
                entries.extend((episode or {}).get("telegram") or [])
    else:
        entries.extend(doc.get("telegram") or [])
    return [entry for entry in entries if isinstance(entry, dict)]


def is_unprobeable(doc: Optional[Dict], kind: str) -> bool:
    """True when no file of the title carries audio track information.

    Such a title can only be fixed by re-fetching and re-probing the file —
    the cheap backfill cannot recover its languages.
    """
    entries = all_file_entries(doc, kind)
    if not entries:
        return True
    return all(not (entry.get("audio_tracks")) for entry in entries)


# ── Backfill read path ──────────────────────────────────────────────────────
#
# The backfill only needs the audio languages, but a media document also carries
# every file entry, every subtitle and every Telegram reference — for a TV show
# that is tens of kilobytes per title. The projection below keeps only the
# ``audio_tracks`` arrays (and the three fields the backfill compares against),
# so the walk over a library transfers a fraction of the data.
#
# The projection is deliberately *shape-agnostic*: a movie keeps its languages
# under ``telegram[].audio_tracks`` and a TV show under
# ``seasons[].episodes[].telegram[].audio_tracks``, and MongoDB is free to
# re-nest an array-of-arrays however it likes. The reader below therefore does
# not assume a shape — it simply looks for every ``audio_tracks`` array
# anywhere in the document. The same reader handles a full document and a
# projected one, so there is only one implementation of the rule.

BACKFILL_PROJECTION = {
    "audio_languages": 1,
    "audio_language_count": 1,
    "dropped_audio_languages": 1,
    "telegram.audio_tracks": 1,
    "seasons.episodes.telegram.audio_tracks": 1,
}


def read_audio_tracks(node: Any, names: List[Any], state: Dict[str, bool]) -> None:
    """Collect every ``audio_tracks`` array found anywhere under ``node``.

    ``names`` receives the raw language names, ``state["probed"]`` is set when
    at least one non-empty track list was seen. Works on a full media document
    and on a projected one, because it never assumes where the arrays sit.
    """
    if isinstance(node, list):
        for item in node:
            read_audio_tracks(item, names, state)
        return
    if not isinstance(node, dict):
        return

    tracks = node.get("audio_tracks")
    if isinstance(tracks, list):
        if tracks:
            state["probed"] = True
        for track in tracks:
            if isinstance(track, dict):
                names.append(track.get("language"))

    for key, value in node.items():
        if key == "audio_tracks":
            continue
        read_audio_tracks(value, names, state)


def languages_from_document(doc: Optional[Dict]) -> List[str]:
    """Distinct known audio languages of any media document, projected or not."""
    names: List[Any] = []
    state: Dict[str, bool] = {"probed": False}
    read_audio_tracks(doc, names, state)
    return collect_languages(names)


def document_is_probed(doc: Optional[Dict]) -> bool:
    """True when at least one file of the document carries audio tracks."""
    names: List[Any] = []
    state: Dict[str, bool] = {"probed": False}
    read_audio_tracks(doc, names, state)
    return state["probed"]
