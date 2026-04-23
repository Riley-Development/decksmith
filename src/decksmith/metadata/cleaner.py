"""Metadata cleaning pipeline.

Applies the strip patterns from the spec to produce before/after diffs.
Supports three modes:
- **preview** — show diffs, no writes
- **auto** — backup + write all, print summary
- **interactive** — per-track confirm
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from decksmith.config import (
    DecksmithConfig,
    get_metadata_config,
    DEFAULT_STRIP_PATTERNS,
    load_config,
)
from decksmith.metadata.filename_parser import parse_filename
from decksmith.utils.tag_io import read_tags, write_tags, tags_to_json
from decksmith.db import backup_tags, init_db, file_hash

SUPPORTED_FORMATS = {".mp3", ".flac", ".aiff", ".aif", ".wav", ".m4a"}


@dataclass
class FieldChange:
    field: str
    before: str
    after: str


@dataclass
class CleanResult:
    filepath: str
    changes: list[FieldChange] = field(default_factory=list)

    @property
    def needs_write(self) -> bool:
        return len(self.changes) > 0

    def to_diff_dicts(self) -> list[dict]:
        return [
            {"field": c.field, "before": c.before, "after": c.after}
            for c in self.changes
        ]

    @property
    def change_signature(self) -> frozenset[str]:
        """A hashable 'shape' of this result's changes.

        Two results are considered *similar* when they touch the same
        set of fields via the same strip-pattern effects.  Concretely
        the signature is the set of ``(field, before_stripped_suffix)``
        pairs — i.e. the trailing text that was removed from each field.
        This keeps "skip all similar" scoped to tracks that share the
        same *kind* of dirt, not just any track with changes.
        """
        parts: list[str] = []
        for c in self.changes:
            # Represent the type of change: field + what was removed
            removed = c.before.replace(c.after, "", 1).strip() if c.after else c.before
            parts.append(f"{c.field}:{removed}")
        return frozenset(parts)


# ---------------------------------------------------------------------------
# Core cleaning logic
# ---------------------------------------------------------------------------

def _apply_patterns(value: str, field_name: str, patterns: list[dict]) -> str:
    """Apply strip patterns to *value* for the given *field_name*."""
    result = value
    for pat in patterns:
        if field_name not in pat.get("apply_to", []):
            continue
        try:
            result = re.sub(pat["pattern"], "", result)
        except re.error:
            continue
    # Normalise whitespace
    result = re.sub(r"\s{2,}", " ", result).strip()
    return result


def _title_case_if_all_caps(value: str) -> str:
    """Historically re-cased ALL-CAPS → Title Case.

    Disabled: many artists/titles are intentionally all-caps (SOPHIE,
    LUCØ, XOXO, UTOPIA, ANTI, 3OH!3, DONTTRUSTME, etc.).  Silently
    recasing them is a correctness regression.  The function is kept
    as a pass-through so callers don't need to change.
    """
    return value


def clean_track(
    filepath: str,
    config: Optional[DecksmithConfig] = None,
) -> CleanResult:
    """Compute proposed metadata changes for *filepath* without writing.

    Returns a ``CleanResult`` with a list of field-level diffs.
    """
    if config is None:
        config = load_config() or DecksmithConfig()

    meta_cfg = get_metadata_config(config)
    patterns = meta_cfg.get("strip_patterns", DEFAULT_STRIP_PATTERNS)
    clean_fields = meta_cfg.get("clean_fields", ["title", "artist", "album", "album_artist", "genre"])
    nuke_fields = meta_cfg.get("nuke_fields", ["encoded_by", "url", "copyright"])

    tags = read_tags(filepath)
    parsed = parse_filename(filepath)
    changes: list[FieldChange] = []

    # --- Apply strip patterns to clean_fields ---
    for fld in clean_fields:
        original = tags.get(fld, "")
        if not original:
            continue
        cleaned = _apply_patterns(original, fld, patterns)
        cleaned = _title_case_if_all_caps(cleaned)
        if cleaned != original:
            changes.append(FieldChange(field=fld, before=original, after=cleaned))

    # --- Nuke fields ---
    for fld in nuke_fields:
        original = tags.get(fld, "")
        if original:
            changes.append(FieldChange(field=fld, before=original, after=""))

    # --- Fill missing artist/title from filename ---
    current_title = tags.get("title", "")
    current_artist = tags.get("artist", "")

    # Apply any already-computed changes
    for c in changes:
        if c.field == "title":
            current_title = c.after
        if c.field == "artist":
            current_artist = c.after

    if not current_title and parsed.get("title"):
        changes.append(FieldChange(field="title", before="", after=parsed["title"]))

    if not current_artist and parsed.get("artist"):
        changes.append(FieldChange(field="artist", before="", after=parsed["artist"]))

    return CleanResult(filepath=filepath, changes=changes)


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

def scan_library(config: DecksmithConfig) -> list[str]:
    """Return all supported audio file paths in the configured library."""
    files: list[str] = []
    for lib_path in config.library_paths:
        if not lib_path.is_dir():
            continue
        for root, _dirs, filenames in os.walk(lib_path):
            for fname in filenames:
                if Path(fname).suffix.lower() in SUPPORTED_FORMATS:
                    files.append(os.path.join(root, fname))
    files.sort()
    return files


def apply_changes(
    filepath: str,
    result: CleanResult,
    config: Optional[DecksmithConfig] = None,
    batch_ts: Optional[str] = None,
) -> None:
    """Write the proposed changes, backing up original tags to SQLite first.

    Pass *batch_ts* to group multiple writes under a single timestamp
    so ``decksmith undo --last`` restores the whole batch.
    """
    if not result.needs_write:
        return

    if config is None:
        config = load_config() or DecksmithConfig()

    init_db(config)

    # Read current tags and back them up
    current_tags = read_tags(filepath)
    fhash = file_hash(filepath)
    backup_tags(filepath, tags_to_json(current_tags), fhash=fhash, config=config, batch_ts=batch_ts)

    # Build the new tag set
    new_tags = dict(current_tags)
    for change in result.changes:
        if change.after == "":
            # Nuke: keep the key but set empty (write_tags will handle)
            new_tags[change.field] = ""
        else:
            new_tags[change.field] = change.after

    write_tags(filepath, new_tags)
