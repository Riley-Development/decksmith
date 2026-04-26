"""Heuristic compilation album detector.

Replaces regex whack-a-mole with a score-based classifier.
Albums scoring above the threshold are flagged as compilations so the
cleaner can wipe them and the enricher can replace them.
"""

from __future__ import annotations

import re

_COMPILATION_KEYWORDS = [
    "best of", "hits", "top ", "greatest", "now that",
    "billboard", "ultimate", "essential", "essentials",
    "mastermix", "chart", "radio", "promo", "throwback",
    "ministry of sound", "mainstream", "brit awards",
    "urban radio", "club hits", "dj edits", "dj mix",
    "uk singles", "hot 100", "beatport top", "year end",
    "100 greatest", "mixed by", "mixed & compiled",
    "awards", "switch box", "anthems", "annual",
]

_VARIOUS_ARTISTS = {"various", "various artists", "va", "v/a", "v.a."}

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

DEFAULT_THRESHOLD = 3


def compilation_score(album: str, album_artist: str = "") -> int:
    """Return a heuristic score for how likely *album* is a compilation.

    Higher = more likely.  Typical threshold is 3.
    """
    if not album:
        return 0

    score = 0
    lower = album.lower()

    for kw in _COMPILATION_KEYWORDS:
        if kw in lower:
            score += 1

    if _YEAR_RE.search(album):
        score += 1

    if album_artist.strip().lower() in _VARIOUS_ARTISTS:
        score += 2

    if re.search(r"\b(vol\.?\s*\d+|volume\s*\d+)\b", lower):
        score += 1

    if re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{2,4}\b", lower):
        score += 1

    if re.search(r"^\d+\s+(greatest|best|top|essential)", lower):
        score += 1

    return score


def is_compilation_album(
    album: str,
    album_artist: str = "",
    threshold: int = DEFAULT_THRESHOLD,
) -> bool:
    """Return True if the album looks like a compilation."""
    return compilation_score(album, album_artist) >= threshold
