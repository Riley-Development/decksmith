"""Additional metadata normalisation rules.

Companion to :mod:`decksmith.metadata.cleaner`.  These helpers are
used by the cleaner and by the enricher to merge AI/API suggestions
into existing tags safely (never overwrite a richer value with an
empty one).
"""

from __future__ import annotations

import re
from typing import Optional


def split_artists(raw: str, separators: dict[str, list[str]]) -> dict[str, list[str]]:
    """Split a raw artist string into ``primary``, ``featured``, ``remixer``.

    Example: "Artist A feat. Artist B (Remix by Artist C)" →
        {'primary': ['Artist A'], 'featured': ['Artist B'], 'remixer': ['Artist C']}

    *separators* mirrors ``config.metadata.artist_separators``.
    """
    result = {"primary": [], "featured": [], "remixer": []}
    if not raw:
        return result

    working = raw

    # Extract "(X Remix)" / "[X Remix]" style remixers first
    rmx_match = re.search(r"[\(\[]([^\)\]]+?)\s+(?:remix|mix|edit|bootleg|rework)[\)\]]", working, re.IGNORECASE)
    if rmx_match:
        remixer_str = rmx_match.group(1).strip()
        result["remixer"] = [a.strip() for a in re.split(r",|&| and ", remixer_str) if a.strip()]
        working = re.sub(r"\s*[\(\[][^\)\]]+?\s+(?:remix|mix|edit|bootleg|rework)[\)\]]", "", working, flags=re.IGNORECASE).strip()

    # Find a featuring separator
    feat_tokens = separators.get("featuring") or ["feat.", "feat", "ft.", "ft", "featuring"]
    for tok in feat_tokens:
        # Case-insensitive match at word boundary
        pat = re.compile(r"\s+" + re.escape(tok) + r"\.?\s+", re.IGNORECASE)
        m = pat.search(working)
        if m:
            head = working[: m.start()].strip()
            tail = working[m.end() :].strip()
            result["primary"] = [a.strip() for a in re.split(r",|&| and ", head) if a.strip()]
            result["featured"] = [a.strip() for a in re.split(r",|&| and ", tail) if a.strip()]
            return result

    # No featured → everything is primary
    result["primary"] = [a.strip() for a in re.split(r",|&", working) if a.strip()]
    return result


def merge_tags(
    existing: dict[str, str],
    suggested: dict[str, Optional[str]],
    overwrite: bool = False,
) -> dict[str, str]:
    """Return a merged tag dict.

    Unless ``overwrite=True``, suggested values only fill empty fields.
    Empty/None suggestions are dropped.
    """
    merged = dict(existing)
    for field, value in suggested.items():
        if value in (None, ""):
            continue
        if not overwrite and merged.get(field):
            continue
        merged[field] = str(value)
    return merged


def is_probably_remix(title: str) -> bool:
    return bool(re.search(r"\b(remix|bootleg|edit|rework|mashup)\b", title, re.IGNORECASE))
