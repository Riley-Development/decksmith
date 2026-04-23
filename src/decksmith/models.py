"""Shared pydantic models used across modules.

The DB is the source of truth for track state; these models are thin
value objects used when passing tracks between modules (rekordbox,
setbuilder, discover) or serialising results to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class Track(BaseModel):
    """A single track record, assembled from DB row + file metadata."""

    filepath: str
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    bpm: Optional[float] = None
    key_camelot: Optional[str] = None
    energy: Optional[int] = None
    bitrate_declared: Optional[int] = None
    bitrate_authentic: Optional[bool] = None
    duration_sec: Optional[float] = None
    year: Optional[str] = None
    comment: str = ""

    @property
    def filename(self) -> str:
        return Path(self.filepath).name

    @property
    def display(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} — {self.title}"
        return self.filename


class CuePoint(BaseModel):
    """One Rekordbox hot cue or memory cue."""

    num: int
    name: str
    position_sec: float
    rgb: tuple[int, int, int] = (40, 199, 70)
    hot: bool = True


class SetTrack(BaseModel):
    """A track slotted into a generated DJ set."""

    track: Track
    position: int
    transition_note: str = ""
    energy_slot: Optional[int] = None
