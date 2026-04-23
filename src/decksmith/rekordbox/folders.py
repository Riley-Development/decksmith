"""Compute the organised folder for each track.

Default structure: ``<Genre> / <BPM range>``.  BPM ranges come from
config.rekordbox.genre_bpm_ranges.  Tracks without genre or BPM land
in ``Uncategorized``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.models import Track

DEFAULT_GENRE_BPM_RANGES: dict[str, dict] = {
    "Downtempo / Chill":   {"range": [60, 99]},
    "Hip-Hop":             {"range": [70, 115]},
    "R&B":                 {"range": [60, 110]},
    "House":               {"range": [118, 130]},
    "Tech House":          {"range": [124, 135]},
    "Techno":              {"range": [130, 155]},
    "Trance":              {"range": [128, 150]},
    "DnB / Jungle":        {"range": [160, 185]},
    "Dubstep / Bass":      {"range": [135, 155]},
    "Disco / Funk":        {"range": [105, 130]},
    "Pop / Dance":         {"range": [95, 135]},
    "Uncategorized":       {"range": [0, 300]},
}


def _genre_ranges(config: DecksmithConfig) -> dict[str, dict]:
    rb = config.rekordbox or {}
    return rb.get("genre_bpm_ranges") or DEFAULT_GENRE_BPM_RANGES


def _normalise_genre(raw: str) -> str:
    if not raw:
        return ""
    g = raw.strip().lower()
    aliases = {
        "tech-house": "Tech House",
        "tech house": "Tech House",
        "deep house": "House",
        "house": "House",
        "techno": "Techno",
        "trance": "Trance",
        "dnb": "DnB / Jungle",
        "drum and bass": "DnB / Jungle",
        "drum & bass": "DnB / Jungle",
        "jungle": "DnB / Jungle",
        "dubstep": "Dubstep / Bass",
        "bass": "Dubstep / Bass",
        "hip-hop": "Hip-Hop",
        "hip hop": "Hip-Hop",
        "rap": "Hip-Hop",
        "r&b": "R&B",
        "rnb": "R&B",
        "disco": "Disco / Funk",
        "funk": "Disco / Funk",
        "pop": "Pop / Dance",
        "dance": "Pop / Dance",
        "chill": "Downtempo / Chill",
        "downtempo": "Downtempo / Chill",
        "lofi": "Downtempo / Chill",
        "lo-fi": "Downtempo / Chill",
    }
    for alias, canonical in aliases.items():
        if alias in g:
            return canonical
    # Title-case fallback
    return raw.strip().title()


def bpm_bucket_label(bpm: Optional[float]) -> str:
    """Return a short BPM bucket label like ``120-130``."""
    if bpm is None or bpm <= 0:
        return "Unknown BPM"
    low = int(bpm // 5) * 5
    return f"{low}-{low + 5}"


def folder_for_track(track: Track, config: DecksmithConfig) -> str:
    """Return the relative folder path (POSIX form) for *track*.

    Uses genre_bpm_ranges from config.  If the track's BPM falls
    within the primary genre's range, the genre wins; otherwise
    fall back to "Uncategorized".
    """
    ranges = _genre_ranges(config)
    genre = _normalise_genre(track.genre)
    bpm = track.bpm or 0

    primary = genre if genre in ranges else None

    # If the tagged genre doesn't have an entry, try to infer from BPM.
    if not primary:
        for name, info in ranges.items():
            if name == "Uncategorized":
                continue
            lo, hi = info.get("range", [0, 300])
            if lo <= bpm <= hi:
                primary = name
                break

    if not primary:
        primary = "Uncategorized"

    # Genre names like "DnB / Jungle" contain a forward slash — replace it
    # before using the value as a path segment so we don't accidentally
    # create nested "DnB/ /Jungle" directories.
    safe_primary = primary.replace("/", "-").replace("\\", "-").strip()
    bucket = bpm_bucket_label(track.bpm)
    return f"{safe_primary}/{bucket}"


def plan_moves(
    tracks: list[Track],
    config: DecksmithConfig,
    organized_root: str,
) -> list[dict]:
    """Return a list of ``{src, dst, folder}`` dicts for each track.

    Does not touch the filesystem — use ``utils.fs.move_file`` to apply.
    """
    root = Path(organized_root).expanduser()
    plan: list[dict] = []
    for t in tracks:
        folder = folder_for_track(t, config)
        dst = root / folder / Path(t.filepath).name
        plan.append({
            "src": t.filepath,
            "dst": str(dst),
            "folder": folder,
        })
    return plan
