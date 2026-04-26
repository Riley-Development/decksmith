"""Direct Rekordbox master.db cue writer via pyrekordbox.

Writes DeckSmith-detected hot cues into Rekordbox's djmdCue table
so they appear on the hot cue pads without XML import.  Requires
Rekordbox to be closed during writes.
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from decksmith.config import DecksmithConfig
from decksmith.models import CuePoint

_RB_PALETTE = [
    (1, (227, 0, 103)),
    (2, (255, 0, 0)),
    (3, (255, 165, 0)),
    (4, (255, 255, 0)),
    (5, (0, 255, 0)),
    (6, (0, 209, 255)),
    (7, (0, 0, 255)),
    (8, (153, 0, 255)),
]


def _rgb_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def rgb_to_rb_color(rgb: tuple[int, int, int]) -> tuple[int, int]:
    """Map an RGB triple to (Color int, ColorTableIndex) for DjmdCue."""
    best_idx = 1
    best_dist = float("inf")
    for idx, palette_rgb in _RB_PALETTE:
        d = _rgb_distance(rgb, palette_rgb)
        if d < best_dist:
            best_dist = d
            best_idx = idx
    _, best_rgb = _RB_PALETTE[best_idx - 1]
    r, g, b = best_rgb
    color_int = (r << 16) | (g << 8) | b
    return color_int, best_idx


def is_rekordbox_running() -> bool:
    try:
        from pyrekordbox.utils import get_rekordbox_pid
        pid = get_rekordbox_pid()
        return pid is not None and pid > 0
    except Exception:
        return False


def _backup_dir(config: DecksmithConfig) -> Path:
    d = config.db_path.parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_master_db(rb_db_dir: Path, config: DecksmithConfig) -> Path:
    src = rb_db_dir / "master.db"
    dest_dir = _backup_dir(config)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"master.db.{stamp}.bak"
    shutil.copy2(src, dest)
    return dest


def find_latest_backup(config: DecksmithConfig) -> Optional[Path]:
    d = _backup_dir(config)
    backups = sorted(d.glob("master.db.*.bak"), reverse=True)
    return backups[0] if backups else None


@dataclass
class ExistingCue:
    kind: int
    comment: str
    position_sec: float
    is_auto: bool


@dataclass
class TrackCueMapping:
    filepath: str
    rb_content_id: str
    rb_content_uuid: str
    rb_title: str
    cues: list[CuePoint]
    existing_hot_cue_count: int
    has_custom_cues: bool = False
    existing_custom: list[ExistingCue] = field(default_factory=list)


@dataclass
class PushCueResult:
    matched: int = 0
    unmatched: list[str] = field(default_factory=list)
    written: int = 0
    skipped_custom: int = 0
    cues_created: int = 0
    cues_deleted: int = 0
    backup_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.written > 0


def load_decksmith_cues(config: DecksmithConfig) -> dict[str, list[CuePoint]]:
    db_path = config.db_path
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT filepath, cue_points_json FROM tracks "
        "WHERE cue_points_json IS NOT NULL AND cue_points_json != ''"
    ).fetchall()
    conn.close()

    result: dict[str, list[CuePoint]] = {}
    for fp, cj in rows:
        try:
            raw = json.loads(cj)
        except (json.JSONDecodeError, TypeError):
            continue
        cues = []
        for c in raw:
            if not c.get("hot", True):
                continue
            cues.append(CuePoint(
                num=c["num"],
                name=c["name"],
                position_sec=c["position_sec"],
                rgb=tuple(c.get("rgb", (40, 199, 70))),
                hot=True,
            ))
        if cues:
            result[fp] = cues
    return result


def match_tracks(
    ds_cues: dict[str, list[CuePoint]],
    rb_db,
) -> tuple[list[TrackCueMapping], list[str]]:
    rb_contents = list(rb_db.get_content())
    rb_by_path: dict[str, object] = {}
    for c in rb_contents:
        fp = c.FolderPath
        if fp:
            rb_by_path[fp] = c

    matched: list[TrackCueMapping] = []
    unmatched: list[str] = []

    for ds_path, cues in ds_cues.items():
        content = rb_by_path.get(ds_path)
        if content is None:
            unmatched.append(ds_path)
            continue
        hot_cues = [cue for cue in content.Cues if cue.Kind > 0]
        existing_custom = []
        has_custom = False
        for cue in hot_cues:
            is_auto = (
                cue.Kind == 1
                and (cue.Comment or "").strip() in ("1.1Bars", "")
            )
            if not is_auto:
                has_custom = True
            existing_custom.append(ExistingCue(
                kind=cue.Kind,
                comment=(cue.Comment or "").strip(),
                position_sec=cue.InMsec / 1000.0,
                is_auto=is_auto,
            ))
        matched.append(TrackCueMapping(
            filepath=ds_path,
            rb_content_id=str(content.ID),
            rb_content_uuid=str(content.UUID),
            rb_title=content.Title or Path(ds_path).stem,
            cues=cues,
            existing_hot_cue_count=len(hot_cues),
            has_custom_cues=has_custom,
            existing_custom=existing_custom,
        ))

    return matched, unmatched


def _smart_assign(
    ds_cues: list[CuePoint],
    custom_cues: list[ExistingCue],
) -> list[tuple[CuePoint, int]]:
    """Pick which DeckSmith cues to write and which pads to put them on.

    Drops the DeckSmith cue closest in time to each custom cue (most
    redundant), then assigns the survivors to available pads — preferring
    their original slot when it's free.
    """
    if not custom_cues:
        return [(c, c.num + 1) for c in ds_cues]

    dropped: set[int] = set()
    for ec in sorted(custom_cues, key=lambda x: x.position_sec):
        best_idx = -1
        best_dist = float("inf")
        for i, dc in enumerate(ds_cues):
            if i in dropped:
                continue
            d = abs(dc.position_sec - ec.position_sec)
            if d < best_dist:
                best_dist = d
                best_idx = i
        if best_idx >= 0:
            dropped.add(best_idx)

    survivors = [c for i, c in enumerate(ds_cues) if i not in dropped]
    occupied_pads = {ec.kind for ec in custom_cues}
    free_pads = sorted(k for k in range(1, 9) if k not in occupied_pads)

    result: list[tuple[CuePoint, int]] = []
    used_pads: set[int] = set()

    for c in survivors:
        preferred = c.num + 1
        if preferred in free_pads and preferred not in used_pads:
            result.append((c, preferred))
            used_pads.add(preferred)

    unassigned = [c for c in survivors if all(c is not r[0] for r in result)]
    remaining_pads = [p for p in free_pads if p not in used_pads]
    for c, p in zip(unassigned, remaining_pads):
        result.append((c, p))

    result.sort(key=lambda x: x[1])
    return result


def write_cues(
    mappings: list[TrackCueMapping],
    rb_db,
    *,
    dry_run: bool = False,
    keep_existing: bool = False,
    skip_kinds: Optional[dict[str, set[int]]] = None,
    on_progress=None,
) -> PushCueResult:
    from pyrekordbox.db6 import tables

    result = PushCueResult(matched=len(mappings))

    for i, m in enumerate(mappings):
        existing = [
            cue for cue in rb_db.get_cue(ContentID=m.rb_content_id)
            if cue.Kind > 0
        ]
        occupied_kinds: set[int] = set()

        track_skip = (skip_kinds or {}).get(m.rb_content_id, set())

        if keep_existing and m.has_custom_cues:
            if track_skip:
                kept_customs = [ec for ec in m.existing_custom if ec.kind in track_skip]
            else:
                kept_customs = [ec for ec in m.existing_custom if not ec.is_auto]

            assignments = _smart_assign(m.cues, kept_customs)
            keep_kinds = {ec.kind for ec in kept_customs}

            for old_cue in existing:
                if old_cue.Kind not in keep_kinds:
                    if not dry_run:
                        rb_db.delete(old_cue)
                        result.cues_deleted += 1
            result.skipped_custom += 1
        else:
            if not dry_run:
                for old_cue in existing:
                    rb_db.delete(old_cue)
                    result.cues_deleted += 1
            assignments = [(c, c.num + 1) for c in m.cues]

        wrote_any = False
        for cue, kind in assignments:
            in_msec = int(cue.position_sec * 1000)
            in_frame = int(in_msec * 150 / 1000)
            color_int, color_idx = rgb_to_rb_color(cue.rgb)

            if not dry_run:
                new_id = str(rb_db.generate_unused_id(tables.DjmdCue))
                new_uuid = str(uuid4())
                new_cue = tables.DjmdCue.create(
                    ID=new_id,
                    ContentID=m.rb_content_id,
                    InMsec=in_msec,
                    InFrame=in_frame,
                    InMpegFrame=0,
                    InMpegAbs=0,
                    OutMsec=-1,
                    OutFrame=0,
                    OutMpegFrame=0,
                    OutMpegAbs=0,
                    Kind=kind,
                    Color=color_int,
                    ColorTableIndex=color_idx,
                    ActiveLoop=0,
                    Comment=cue.name or "",
                    BeatLoopSize=0,
                    CueMicrosec=0,
                    InPointSeekInfo="",
                    OutPointSeekInfo="",
                    ContentUUID=m.rb_content_uuid,
                    UUID=new_uuid,
                )
                rb_db.add(new_cue)

            result.cues_created += 1
            wrote_any = True

        if wrote_any:
            result.written += 1
        if on_progress:
            on_progress(i + 1)

    if not dry_run and result.written > 0:
        rb_db.commit()

    return result


def push_cues_to_rekordbox(
    config: DecksmithConfig,
    *,
    dry_run: bool = False,
    keep_existing: bool = False,
    skip_kinds: Optional[dict[str, set[int]]] = None,
    on_progress=None,
) -> PushCueResult:
    if is_rekordbox_running():
        return PushCueResult(error="Rekordbox is running. Close it first, then retry.")

    try:
        from pyrekordbox import Rekordbox6Database
    except ImportError:
        return PushCueResult(
            error="pyrekordbox is not installed. Run: pip install pyrekordbox"
        )

    ds_cues = load_decksmith_cues(config)
    if not ds_cues:
        return PushCueResult(error="No cue points found in DeckSmith. Run `decksmith cue` first.")

    try:
        rb_db = Rekordbox6Database()
    except Exception as exc:
        return PushCueResult(error=f"Cannot open Rekordbox database: {exc}")

    result = PushCueResult()

    if not dry_run:
        try:
            backup_path = backup_master_db(Path(rb_db.db_directory), config)
            result.backup_path = str(backup_path)
        except Exception as exc:
            rb_db.close()
            return PushCueResult(error=f"Failed to back up master.db: {exc}")

    try:
        matched, unmatched = match_tracks(ds_cues, rb_db)
        result.unmatched = unmatched

        if not matched:
            rb_db.close()
            result.error = "No DeckSmith tracks found in Rekordbox."
            return result

        write_result = write_cues(
            matched, rb_db,
            dry_run=dry_run,
            keep_existing=keep_existing,
            skip_kinds=skip_kinds,
            on_progress=on_progress,
        )
        result.matched = write_result.matched
        result.written = write_result.written
        result.skipped_custom = write_result.skipped_custom
        result.cues_created = write_result.cues_created
        result.cues_deleted = write_result.cues_deleted
    except RuntimeError as exc:
        result.error = str(exc)
    except Exception as exc:
        result.error = f"Write failed: {exc}"
    finally:
        rb_db.close()

    return result


def restore_master_db(config: DecksmithConfig, backup_path: Optional[Path] = None) -> str:
    if is_rekordbox_running():
        return "Rekordbox is running. Close it first, then retry."

    if backup_path is None:
        backup_path = find_latest_backup(config)
    if backup_path is None or not backup_path.exists():
        return "No Rekordbox backup found."

    try:
        from pyrekordbox import Rekordbox6Database
        rb_db = Rekordbox6Database()
        dest = Path(rb_db.db_directory) / "master.db"
        rb_db.close()
    except Exception:
        dest = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"

    if not dest.exists():
        return f"Rekordbox database not found at {dest}."

    shutil.copy2(backup_path, dest)
    return ""
