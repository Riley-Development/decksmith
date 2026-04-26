"""SQLite tracking database and undo support.

Uses the exact schema from the spec plus a ``change_log`` table that
tracks every write operation with an immutable batch UUID.  This
prevents ``decksmith analyze`` from clobbering undo batches — analyze
updates ``last_processed`` on the tracks table, but the change_log
entries are keyed on their own batch_id and are never touched by
analysis.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from decksmith.config import DecksmithConfig, load_config, expand_path

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    filepath TEXT UNIQUE NOT NULL,
    file_hash TEXT NOT NULL,
    original_tags_json TEXT,
    last_processed TEXT,
    status TEXT DEFAULT 'pending',
    confidence REAL DEFAULT 0.0,
    bpm REAL,
    key_camelot TEXT,
    energy INTEGER,
    bitrate_declared INTEGER,
    bitrate_authentic BOOLEAN,
    bitrate_confidence REAL,
    cue_points_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tracks_hash ON tracks(file_hash);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);

CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    filepath TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_changelog_batch ON change_log(batch_id);
"""


def _db_path(config: Optional[DecksmithConfig] = None) -> Path:
    if config is None:
        config = load_config()
    if config is None:
        return Path.home() / ".decksmith" / "tracking.db"
    return config.db_path


def get_db(config: Optional[DecksmithConfig] = None) -> sqlite3.Connection:
    """Return a connection to the tracking database, creating it if needed."""
    path = _db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(config: Optional[DecksmithConfig] = None) -> None:
    """Create the schema if it doesn't exist."""
    conn = get_db(config)
    conn.executescript(_SCHEMA)
    conn.close()


def new_batch_id() -> str:
    """Generate an immutable batch UUID for grouping write operations."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# File hash
# ---------------------------------------------------------------------------

def file_hash(filepath: str, chunk_size: int = 65536) -> str:
    """Return a SHA-256 hex digest of the first chunk of *filepath*."""
    h = hashlib.sha256()
    with open(filepath, "rb") as fh:
        h.update(fh.read(chunk_size))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_tags(
    filepath: str,
    tags_json: str,
    fhash: Optional[str] = None,
    config: Optional[DecksmithConfig] = None,
    batch_ts: Optional[str] = None,
    batch_id: Optional[str] = None,
    operation: str = "clean",
) -> None:
    """Store original tags JSON for *filepath* so undo can restore them.

    If a record already exists for this filepath, update only if
    ``original_tags_json`` is still NULL (first backup wins).

    *batch_id* is the preferred grouping mechanism — an immutable UUID
    that is never overwritten by analyze.  *batch_ts* is kept for
    backward compat but ignored when batch_id is set.
    """
    if fhash is None:
        fhash = file_hash(filepath)
    now = batch_ts or datetime.now().isoformat()
    conn = get_db(config)
    cur = conn.cursor()
    cur.execute("SELECT id, original_tags_json FROM tracks WHERE filepath = ?", (filepath,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO tracks (filepath, file_hash, original_tags_json, last_processed, status) "
            "VALUES (?, ?, ?, ?, 'cleaned')",
            (filepath, fhash, tags_json, now),
        )
    else:
        if row["original_tags_json"] is None:
            cur.execute(
                "UPDATE tracks SET original_tags_json = ?, file_hash = ?, "
                "last_processed = ?, updated_at = datetime('now') WHERE id = ?",
                (tags_json, fhash, now, row["id"]),
            )
        else:
            cur.execute(
                "UPDATE tracks SET last_processed = ?, status = 'cleaned', "
                "updated_at = datetime('now') WHERE id = ?",
                (now, row["id"]),
            )

    if batch_id:
        cur.execute(
            "INSERT INTO change_log (batch_id, operation, filepath, snapshot_json) "
            "VALUES (?, ?, ?, ?)",
            (batch_id, operation, filepath, tags_json),
        )

    conn.commit()
    conn.close()


def get_backup(filepath: str, config: Optional[DecksmithConfig] = None) -> Optional[dict]:
    """Return the backed-up tags for *filepath*, or None.

    Checks change_log first (most recent snapshot), then falls back to
    tracks.original_tags_json for pre-migration data.
    """
    conn = get_db(config)
    cur = conn.cursor()
    cur.execute(
        "SELECT snapshot_json FROM change_log WHERE filepath = ? "
        "ORDER BY id DESC LIMIT 1",
        (filepath,),
    )
    row = cur.fetchone()
    if row:
        conn.close()
        return json.loads(row["snapshot_json"])
    cur.execute(
        "SELECT original_tags_json FROM tracks WHERE filepath = ?", (filepath,)
    )
    row = cur.fetchone()
    conn.close()
    if row and row["original_tags_json"]:
        return json.loads(row["original_tags_json"])
    return None


def get_last_batch(config: Optional[DecksmithConfig] = None) -> list[dict]:
    """Return every track from the most recent write batch.

    Uses the change_log table (keyed on batch_id) so that analyze
    passes can never fragment the undo grouping.  Falls back to the
    legacy last_processed approach if change_log is empty.
    """
    conn = get_db(config)
    cur = conn.cursor()

    cur.execute(
        "SELECT batch_id FROM change_log ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row:
        bid = row["batch_id"]
        cur.execute(
            "SELECT filepath, snapshot_json FROM change_log WHERE batch_id = ?",
            (bid,),
        )
        rows = [
            {"filepath": r["filepath"], "original_tags_json": r["snapshot_json"]}
            for r in cur.fetchall()
        ]
        conn.close()
        return rows

    # Legacy fallback for databases without change_log entries
    cur.execute(
        "SELECT MAX(last_processed) FROM tracks "
        "WHERE original_tags_json IS NOT NULL"
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        conn.close()
        return []
    latest_ts = row[0]
    cur.execute(
        "SELECT filepath, original_tags_json, last_processed FROM tracks "
        "WHERE original_tags_json IS NOT NULL AND last_processed = ?",
        (latest_ts,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def update_track_analysis(
    filepath: str,
    *,
    bpm: Optional[float] = None,
    key_camelot: Optional[str] = None,
    energy: Optional[int] = None,
    bitrate_declared: Optional[int] = None,
    bitrate_authentic: Optional[bool] = None,
    bitrate_confidence: Optional[float] = None,
    confidence: Optional[float] = None,
    config: Optional[DecksmithConfig] = None,
) -> None:
    """Store analysis results for a track.

    Creates the row if it doesn't exist yet (keyed on filepath).
    Only updates fields that are not None.  Does NOT touch
    change_log — analysis is read-only from an undo perspective.
    """
    fhash = file_hash(filepath)
    now = datetime.now().isoformat()
    conn = get_db(config)
    cur = conn.cursor()

    cur.execute("SELECT id FROM tracks WHERE filepath = ?", (filepath,))
    row = cur.fetchone()

    if row is None:
        cur.execute(
            "INSERT INTO tracks (filepath, file_hash, status, last_processed, "
            "bpm, key_camelot, energy, bitrate_declared, bitrate_authentic, "
            "bitrate_confidence, confidence) "
            "VALUES (?, ?, 'analyzed', ?, ?, ?, ?, ?, ?, ?, ?)",
            (filepath, fhash, now, bpm, key_camelot, energy,
             bitrate_declared, bitrate_authentic, bitrate_confidence,
             confidence),
        )
    else:
        parts = ["updated_at = datetime('now')"]
        vals: list = []
        if bpm is not None:
            parts.append("bpm = ?"); vals.append(bpm)
        if key_camelot is not None:
            parts.append("key_camelot = ?"); vals.append(key_camelot)
        if energy is not None:
            parts.append("energy = ?"); vals.append(energy)
        if bitrate_declared is not None:
            parts.append("bitrate_declared = ?"); vals.append(bitrate_declared)
        if bitrate_authentic is not None:
            parts.append("bitrate_authentic = ?"); vals.append(bitrate_authentic)
        if bitrate_confidence is not None:
            parts.append("bitrate_confidence = ?"); vals.append(bitrate_confidence)
        if confidence is not None:
            parts.append("confidence = ?"); vals.append(confidence)
        parts.append("status = CASE WHEN status = 'pending' THEN 'analyzed' ELSE status END")
        vals.append(row["id"])
        cur.execute(f"UPDATE tracks SET {', '.join(parts)} WHERE id = ?", vals)

    conn.commit()
    conn.close()


def mark_restored(
    filepath: str,
    config: Optional[DecksmithConfig] = None,
    batch_id: Optional[str] = None,
) -> None:
    """Clear the backup for *filepath* after undo, reset status to pending.

    If *batch_id* is given, also remove the change_log entries for that
    batch so the same batch can't be undone twice.
    """
    conn = get_db(config)
    cur = conn.cursor()
    cur.execute(
        "UPDATE tracks SET original_tags_json = NULL, status = 'pending', "
        "updated_at = datetime('now') WHERE filepath = ?",
        (filepath,),
    )
    if batch_id:
        cur.execute(
            "DELETE FROM change_log WHERE batch_id = ? AND filepath = ?",
            (batch_id, filepath),
        )
    else:
        cur.execute(
            "DELETE FROM change_log WHERE filepath = ?",
            (filepath,),
        )
    conn.commit()
    conn.close()


def get_last_batch_id(config: Optional[DecksmithConfig] = None) -> Optional[str]:
    """Return the batch_id of the most recent change_log entry, or None."""
    conn = get_db(config)
    cur = conn.cursor()
    cur.execute("SELECT batch_id FROM change_log ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row["batch_id"] if row else None
