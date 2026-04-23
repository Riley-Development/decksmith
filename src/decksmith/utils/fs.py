"""Filesystem helpers.

Safe filename sanitisation and organised-library move planning.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s{2,}")


def sanitise_filename(name: str, max_len: int = 180) -> str:
    """Return a filesystem-safe version of *name*.

    Strips control chars, path separators, and reserved characters;
    collapses whitespace; trims trailing dots (Windows-unsafe).
    """
    cleaned = _UNSAFE_CHARS.sub("_", name)
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip(" .")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned or "untitled"


def unique_path(target: Path) -> Path:
    """Return *target*, or *target* with ``-2``, ``-3`` suffix if taken."""
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    i = 2
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def move_file(src: str, dst: str, dry_run: bool = False) -> str:
    """Move *src* to *dst*, creating parents and avoiding collisions.

    Returns the final destination path (may differ from *dst* if the
    target was renamed to avoid overwriting).  In ``dry_run``, no
    filesystem changes are made and the planned final path is returned.
    """
    dst_path = unique_path(Path(dst))
    if dry_run:
        return str(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, str(dst_path))
    return str(dst_path)


def ensure_dir(path: str) -> Path:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def relpath_or_abs(filepath: str, base: str) -> str:
    """Return a relative path if *filepath* is under *base*, else absolute."""
    try:
        return os.path.relpath(filepath, base)
    except ValueError:
        return filepath
