"""Beat grid detection for Rekordbox.

Produces a list of beat timestamps (seconds) and the first-beat offset
so Rekordbox can anchor a grid at a constant BPM.  We don't attempt
variable-BPM tempo maps — Rekordbox's own analysis handles that after
import.  The grid we produce is sufficient for constant-tempo dance
music and lets users skip Rekordbox's slow initial analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class BeatGrid:
    bpm: float
    first_beat_sec: float
    beat_times: list[float]

    @property
    def count(self) -> int:
        return len(self.beat_times)


def detect_beatgrid(filepath: str) -> Optional[BeatGrid]:
    """Run librosa beat tracking on *filepath*.

    Returns None if librosa is unavailable or the file can't be loaded.
    Uses mono 22050 Hz per the spec's memory guidance.
    """
    try:
        import librosa
    except ImportError:
        return None

    try:
        y, sr = librosa.load(filepath, sr=22050, mono=True)
    except Exception:
        return None

    try:
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    except Exception:
        return None

    if not beat_times:
        return None

    # ``tempo`` from librosa may come back as a numpy array; coerce to float.
    try:
        bpm_val = float(tempo)
    except (TypeError, ValueError):
        try:
            bpm_val = float(tempo[0])
        except Exception:
            bpm_val = 0.0

    return BeatGrid(
        bpm=bpm_val,
        first_beat_sec=beat_times[0],
        beat_times=beat_times,
    )
