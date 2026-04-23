"""Cue point detection.

Heuristics aim for the 8-slot layout defined in the spec:

    0 Intro       first_beat
    1 Build       energy_rise
    2 Drop 1      first_drop
    3 Breakdown   energy_dip_after_drop
    4 Drop 2      second_drop
    5 Outro       outro_start
    6 Vocal       vocal_onset
    7 Mix Point   safe_transition

Algorithms are simple and local — no ML.  Per the spec they're
approximations, good enough to save a DJ's time; the human still
reviews before import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from decksmith.models import CuePoint

DEFAULT_SLOTS: list[dict] = [
    {"num": 0, "name": "Intro",     "rgb": [40, 199, 70],   "strategy": "first_beat"},
    {"num": 1, "name": "Build",     "rgb": [255, 165, 0],   "strategy": "energy_rise"},
    {"num": 2, "name": "Drop 1",    "rgb": [255, 0, 0],     "strategy": "first_drop"},
    {"num": 3, "name": "Breakdown", "rgb": [0, 128, 255],   "strategy": "energy_dip_after_drop"},
    {"num": 4, "name": "Drop 2",    "rgb": [255, 0, 0],     "strategy": "second_drop"},
    {"num": 5, "name": "Outro",     "rgb": [155, 89, 182],  "strategy": "outro_start"},
    {"num": 6, "name": "Vocal",     "rgb": [255, 255, 0],   "strategy": "vocal_onset"},
    {"num": 7, "name": "Mix Point", "rgb": [255, 105, 180], "strategy": "safe_transition"},
]

# Short human-readable blurb per strategy (used by --preview)
STRATEGY_BLURBS: dict[str, str] = {
    "first_beat":             "First beat — safe intro point to mix in here.",
    "energy_rise":            "Energy ramps up — pre-drop build.",
    "first_drop":             "First big drop.",
    "energy_dip_after_drop":  "Breakdown after the drop — good to cut vocals.",
    "second_drop":            "Second drop.",
    "outro_start":            "Outro begins — safe to start mixing out.",
    "vocal_onset":            "First vocal entry.",
    "safe_transition":        "Low-risk 16/32-bar transition anchor.",
}


@dataclass
class CueDetectionResult:
    filepath: str
    cues: list[CuePoint]
    duration_sec: float
    bpm: Optional[float]
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and len(self.cues) > 0


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def detect_cues(
    filepath: str,
    slot_config: Optional[list[dict]] = None,
    max_cues: int = 8,
) -> CueDetectionResult:
    """Run librosa-based heuristics on *filepath* to produce cue points.

    Returns a :class:`CueDetectionResult` — ``cues`` may be empty and
    ``error`` set if librosa is missing or the file won't load.
    """
    if slot_config is None:
        slot_config = DEFAULT_SLOTS

    try:
        import librosa
        import numpy as np
    except ImportError:
        return CueDetectionResult(
            filepath=filepath,
            cues=[],
            duration_sec=0.0,
            bpm=None,
            error="librosa not installed — install with: pip install decksmith[analysis]",
        )

    try:
        y, sr = librosa.load(filepath, sr=22050, mono=True)
    except Exception as exc:
        return CueDetectionResult(
            filepath=filepath,
            cues=[],
            duration_sec=0.0,
            bpm=None,
            error=f"Could not load audio: {exc}",
        )

    duration = float(len(y) / sr) if sr else 0.0

    # Beat tracking → anchor cues to beat boundaries for clean mixing
    try:
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        try:
            bpm = float(tempo)
        except (TypeError, ValueError):
            bpm = float(tempo[0]) if len(tempo) else 0.0
    except Exception:
        beat_times = np.array([])
        bpm = 0.0

    # RMS energy envelope for "where's the drop" detection
    try:
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        rms_times = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=512)
    except Exception:
        rms = np.array([])
        rms_times = np.array([])

    cues: list[CuePoint] = []

    def _snap_to_beat(t: float) -> float:
        if len(beat_times) == 0:
            return t
        idx = int(np.argmin(np.abs(beat_times - t)))
        return float(beat_times[idx])

    def _make_cue(slot: dict, t: float) -> CuePoint:
        rgb = slot.get("rgb") or [40, 199, 70]
        return CuePoint(
            num=int(slot["num"]),
            name=str(slot["name"]),
            position_sec=max(0.0, float(t)),
            rgb=(int(rgb[0]), int(rgb[1]), int(rgb[2])),
            hot=True,
        )

    for slot in slot_config[:max_cues]:
        strat = slot.get("strategy")
        pos: Optional[float] = None

        if strat == "first_beat":
            pos = float(beat_times[0]) if len(beat_times) else 0.0

        elif strat == "energy_rise" and len(rms) > 10:
            # Largest increase in the first 40 % of the track
            window = rms[: max(1, int(len(rms) * 0.4))]
            diffs = np.diff(window)
            if len(diffs):
                idx = int(np.argmax(diffs))
                pos = _snap_to_beat(float(rms_times[idx]))

        elif strat == "first_drop" and len(rms) > 20:
            # Peak in the first 60 % of the track — often the first drop
            window = rms[: int(len(rms) * 0.6)]
            if len(window):
                idx = int(np.argmax(window))
                pos = _snap_to_beat(float(rms_times[idx]))

        elif strat == "energy_dip_after_drop" and len(rms) > 20:
            # Lowest RMS between first drop and 75 % mark
            first_drop_frame = int(np.argmax(rms[: int(len(rms) * 0.6)]))
            tail_end = int(len(rms) * 0.75)
            if tail_end > first_drop_frame + 20:
                window = rms[first_drop_frame + 20 : tail_end]
                if len(window):
                    idx = first_drop_frame + 20 + int(np.argmin(window))
                    pos = _snap_to_beat(float(rms_times[idx]))

        elif strat == "second_drop" and len(rms) > 20:
            # Peak in the back half of the track
            half = int(len(rms) * 0.5)
            tail = rms[half:]
            if len(tail):
                idx = half + int(np.argmax(tail))
                pos = _snap_to_beat(float(rms_times[idx]))

        elif strat == "outro_start":
            # 90 % of the track, snapped to a beat
            if duration:
                pos = _snap_to_beat(duration * 0.9)

        elif strat == "vocal_onset":
            # Rough proxy: first big spectral-centroid spike in the first half
            try:
                sc = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
                sc_times = librosa.frames_to_time(range(len(sc)), sr=sr)
                half = len(sc) // 2 or 1
                if half > 5:
                    idx = int(np.argmax(sc[:half]))
                    pos = _snap_to_beat(float(sc_times[idx]))
            except Exception:
                pos = None

        elif strat == "safe_transition":
            # 32-bar anchor from the first beat — useful for mixing out
            if bpm and len(beat_times):
                bars_32_sec = (60.0 / bpm) * 4 * 32  # 32 bars of 4 beats
                pos = _snap_to_beat(float(beat_times[0]) + bars_32_sec)
                if duration and pos > duration - 8:
                    pos = _snap_to_beat(duration * 0.5)

        if pos is not None and 0 <= pos <= duration:
            cues.append(_make_cue(slot, pos))

    return CueDetectionResult(
        filepath=filepath,
        cues=cues,
        duration_sec=duration,
        bpm=bpm if bpm else None,
    )


def cue_strategy_blurb(strategy: str) -> str:
    return STRATEGY_BLURBS.get(strategy, strategy.replace("_", " "))
