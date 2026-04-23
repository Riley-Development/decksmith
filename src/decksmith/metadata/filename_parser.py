"""Parse artist, title, and remix info from filenames.

Supports the patterns listed in the spec's ``metadata.filename_patterns``:
    {artist} - {title}
    {artist} - {title} ({remix_info})
    {track_num}. {artist} - {title}
    {track_num} - {artist} - {title}
    {track_num} {artist} - {title}
    {artist} _ {title}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def parse_filename(filepath: str) -> dict[str, str]:
    """Extract artist, title, and optional remix_info from the filename stem.

    Returns a dict with keys ``artist``, ``title``, and optionally
    ``remix_info`` and ``track_number``.  Values are stripped of
    leading/trailing whitespace.
    """
    stem = Path(filepath).stem
    result: dict[str, str] = {}

    # Remove common file-quality suffixes that pollute the stem
    stem = re.sub(r"\s*\[.*?\]\s*$", "", stem)

    # Try to extract parenthesised remix info at the end
    remix_match = re.search(r"\(([^)]+)\)\s*$", stem)
    if remix_match:
        result["remix_info"] = remix_match.group(1).strip()
        stem = stem[: remix_match.start()].strip()

    # Pattern: {track_num}. {artist} - {title}
    m = re.match(r"^(\d{1,3})\.\s*(.+?)\s*[-\u2013]\s*(.+)$", stem)
    if m:
        result["track_number"] = m.group(1)
        result["artist"] = m.group(2).strip()
        result["title"] = m.group(3).strip()
        return result

    # Pattern: {track_num} - {artist} - {title}
    m = re.match(r"^(\d{1,3})\s*[-\u2013]\s*(.+?)\s*[-\u2013]\s*(.+)$", stem)
    if m:
        result["track_number"] = m.group(1)
        result["artist"] = m.group(2).strip()
        result["title"] = m.group(3).strip()
        return result

    # Pattern: {track_num} {artist} - {title}  (track number with space, no dash)
    m = re.match(r"^(\d{1,3})\s+(.+?)\s*[-\u2013]\s*(.+)$", stem)
    if m:
        result["track_number"] = m.group(1)
        result["artist"] = m.group(2).strip()
        result["title"] = m.group(3).strip()
        return result

    # Pattern: {artist} - {title}
    m = re.match(r"^(.+?)\s*[-\u2013]\s*(.+)$", stem)
    if m:
        result["artist"] = m.group(1).strip()
        result["title"] = m.group(2).strip()
        return result

    # Pattern: {artist} _ {title}
    m = re.match(r"^(.+?)\s*_\s*(.+)$", stem)
    if m:
        result["artist"] = m.group(1).strip()
        result["title"] = m.group(2).strip()
        return result

    # Fallback: entire stem as title
    result["title"] = stem.strip()
    return result
