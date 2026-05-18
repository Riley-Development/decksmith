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

from dataclasses import dataclass, field
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


@dataclass
class CueQualityReport:
    """Small audit summary for generated cue sets."""

    issue_counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issue_counts

    def add(self, issue: str) -> None:
        self.issue_counts[issue] = self.issue_counts.get(issue, 0) + 1


_SEMANTIC_PRIORITY: dict[str, int] = {
    "Intro": 0,
    "Build": 1,
    "Drop 1": 2,
    "Breakdown": 3,
    "Drop 2": 4,
    "Mix Point": 5,
    "Vocal": 6,
    "Outro": 7,
}


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def detect_cues(
    filepath: str,
    slot_config: Optional[list[dict]] = None,
    max_cues: int = 8,
    min_gap_sec: float = 2.0,
    chronological: bool = True,
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
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
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

    cues = polish_cues(
        cues,
        duration_sec=duration,
        bpm=bpm if bpm else None,
        beat_times=beat_times,
        min_gap_sec=min_gap_sec,
        chronological=chronological,
    )

    return CueDetectionResult(
        filepath=filepath,
        cues=cues,
        duration_sec=duration,
        bpm=bpm if bpm else None,
    )


def cue_strategy_blurb(strategy: str) -> str:
    return STRATEGY_BLURBS.get(strategy, strategy.replace("_", " "))


# ---------------------------------------------------------------------------
# Cue QA / polishing
# ---------------------------------------------------------------------------

def audit_cues(
    cues: list[CuePoint],
    *,
    duration_sec: float = 0.0,
    min_gap_sec: float = 2.0,
) -> CueQualityReport:
    """Return simple quality issues for a cue list.

    The audit intentionally checks structural problems Decksmith can fix
    safely: duplicate/clustered timestamps, non-chronological pad order, and
    obviously inverted semantic anchors.
    """
    report = CueQualityReport()
    if not cues:
        report.add("empty")
        return report

    ordered = sorted(cues, key=lambda c: c.num)
    if any(ordered[i].position_sec > ordered[i + 1].position_sec for i in range(len(ordered) - 1)):
        report.add("pads_not_chronological")

    for i, cue in enumerate(cues):
        if cue.position_sec < 0:
            report.add("negative_time")
        if duration_sec and cue.position_sec > duration_sec:
            report.add("past_duration")
        for other in cues[i + 1:]:
            if abs(cue.position_sec - other.position_sec) < min_gap_sec:
                report.add("too_close")
                break

    by_name = {c.name: c.position_sec for c in cues}
    order_names = [c.name for c in ordered]
    if order_names and order_names[0] == "Intro" and by_name.get("Intro", 0) > max(8.0, duration_sec * 0.08):
        report.add("intro_late")
    if "Build" in by_name and "Drop 1" in by_name and by_name["Build"] >= by_name["Drop 1"]:
        report.add("build_after_drop")
    if "Breakdown" in by_name and "Drop 1" in by_name and by_name["Breakdown"] <= by_name["Drop 1"]:
        report.add("breakdown_before_drop")
    if "Drop 1" in by_name and "Drop 2" in by_name and by_name["Drop 2"] <= by_name["Drop 1"] + min_gap_sec:
        report.add("drop2_too_close")
    if duration_sec and "Outro" in by_name and by_name["Outro"] < duration_sec * 0.70:
        report.add("outro_early")

    return report


def polish_cues(
    cues: list[CuePoint],
    *,
    duration_sec: float,
    bpm: Optional[float] = None,
    beat_times=None,
    min_gap_sec: float = 2.0,
    chronological: bool = True,
) -> list[CuePoint]:
    """Make generated cues safer before export/import.

    Detection strategies are deliberately independent, which can cause two
    useful labels to land on the same beat.  This pass preserves labels/colors,
    moves only generated cue timestamps, keeps anchors beat-snapped when a grid
    exists, and finally assigns pads in time order.
    """
    if not cues:
        return cues

    clean: list[CuePoint] = []
    for cue in cues:
        clean.append(cue.model_copy(update={
            "position_sec": _clamp_time(cue.position_sec, duration_sec),
        }))

    for _ in range(3):
        clean = _repair_semantic_order(clean, duration_sec, bpm, beat_times, min_gap_sec)
        clean = _repair_close_cues(clean, duration_sec, bpm, beat_times, min_gap_sec)
        if audit_cues(clean, duration_sec=duration_sec, min_gap_sec=min_gap_sec).ok:
            break

    if chronological:
        clean = sorted(clean, key=lambda c: (c.position_sec, _SEMANTIC_PRIORITY.get(c.name, 99), c.num))
        clean = [c.model_copy(update={"num": idx}) for idx, c in enumerate(clean)]

    return clean


def _clamp_time(value: float, duration_sec: float) -> float:
    if duration_sec <= 0:
        return max(0.0, float(value))
    return max(0.0, min(float(value), max(0.0, duration_sec - 0.25)))


def _snap_to_grid(t: float, beat_times, duration_sec: float) -> float:
    try:
        if beat_times is not None and len(beat_times):
            import numpy as np
            idx = int(np.argmin(np.abs(beat_times - t)))
            return _clamp_time(float(beat_times[idx]), duration_sec)
    except Exception:
        pass
    return _clamp_time(t, duration_sec)


def _bar_seconds(bpm: Optional[float]) -> float:
    if bpm and bpm > 1:
        return (60.0 / bpm) * 4.0
    return 2.0


def _candidate_times(
    name: str,
    positions: dict[str, float],
    duration_sec: float,
    bpm: Optional[float],
    beat_times,
) -> list[float]:
    bar = _bar_seconds(bpm)
    intro = positions.get("Intro", 0.0)
    drop1 = positions.get("Drop 1", duration_sec * 0.35)
    drop2 = positions.get("Drop 2", duration_sec * 0.68)

    raw: list[float]
    if name == "Intro":
        raw = [0.0, intro]
    elif name == "Build":
        raw = [drop1 - 8 * bar, drop1 - 16 * bar, drop1 - 4 * bar, duration_sec * 0.18, duration_sec * 0.25]
    elif name == "Drop 1":
        raw = [drop1, duration_sec * 0.30, duration_sec * 0.38, duration_sec * 0.45]
    elif name == "Breakdown":
        raw = [drop1 + 8 * bar, drop1 + 16 * bar, duration_sec * 0.52, duration_sec * 0.60]
    elif name == "Drop 2":
        raw = [drop2, drop1 + 16 * bar, drop1 + 32 * bar, duration_sec * 0.66, duration_sec * 0.74]
    elif name == "Outro":
        raw = [duration_sec - 16 * bar, duration_sec - 8 * bar, duration_sec * 0.88, duration_sec * 0.92, duration_sec * 0.96]
    elif name == "Vocal":
        raw = [positions.get("Vocal", intro + 16 * bar), intro + 8 * bar, intro + 16 * bar, duration_sec * 0.18, duration_sec * 0.28]
    elif name == "Mix Point":
        raw = [intro + 16 * bar, intro + 32 * bar, intro + 64 * bar, duration_sec * 0.50]
    else:
        raw = [positions.get(name, 0.0), duration_sec * 0.25, duration_sec * 0.50, duration_sec * 0.75]

    raw.extend(duration_sec * frac for frac in (0.08, 0.14, 0.20, 0.30, 0.42, 0.56, 0.70, 0.84, 0.92))

    snapped: list[float] = []
    for t in raw:
        if 0 <= t <= duration_sec:
            s = _snap_to_grid(t, beat_times, duration_sec)
            if all(abs(s - x) > 0.25 for x in snapped):
                snapped.append(s)
    return snapped


def _is_free(t: float, others: list[float], min_gap_sec: float) -> bool:
    return all(abs(t - other) >= min_gap_sec for other in others)


def _fallback_time(
    earliest: float,
    latest: float,
    others: list[float],
    duration_sec: float,
    beat_times,
    min_gap_sec: float,
) -> Optional[float]:
    earliest = _clamp_time(earliest, duration_sec)
    latest = _clamp_time(latest, duration_sec)
    if latest < earliest:
        return None

    raw = [earliest, (earliest + latest) / 2, latest]
    if latest > earliest:
        step = max(min_gap_sec, (latest - earliest) / 12)
        raw.extend(earliest + step * i for i in range(1, 12))

    for t in raw:
        cand = _snap_to_grid(t, beat_times, duration_sec)
        if earliest <= cand <= latest and _is_free(cand, others, min_gap_sec):
            return cand

    for t in raw:
        cand = _clamp_time(t, duration_sec)
        if earliest <= cand <= latest and _is_free(cand, others, min_gap_sec):
            return cand

    return None


def _repair_semantic_order(
    cues: list[CuePoint],
    duration_sec: float,
    bpm: Optional[float],
    beat_times,
    min_gap_sec: float,
) -> list[CuePoint]:
    by_name = {cue.name: cue for cue in cues}
    positions = {cue.name: cue.position_sec for cue in cues}

    def move(name: str, earliest: float = 0.0, latest: Optional[float] = None) -> None:
        latest = duration_sec if latest is None else latest
        others = [pos for key, pos in positions.items() if key != name]
        for cand in _candidate_times(name, positions, duration_sec, bpm, beat_times):
            if earliest <= cand <= latest and _is_free(cand, others, min_gap_sec):
                positions[name] = cand
                return
        fallback = _fallback_time(earliest, latest, others, duration_sec, beat_times, min_gap_sec)
        if fallback is not None:
            positions[name] = fallback

    if "Build" in positions and "Drop 1" in positions and positions["Build"] >= positions["Drop 1"]:
        move("Build", 0.0, positions["Drop 1"] - min_gap_sec)
    if "Breakdown" in positions and "Drop 1" in positions and positions["Breakdown"] <= positions["Drop 1"]:
        move("Breakdown", positions["Drop 1"] + min_gap_sec, duration_sec)
    if "Drop 2" in positions and "Drop 1" in positions and positions["Drop 2"] <= positions["Drop 1"] + min_gap_sec:
        move("Drop 2", positions["Drop 1"] + min_gap_sec, duration_sec)
    if "Outro" in positions:
        latest_non_outro = max((pos for key, pos in positions.items() if key != "Outro"), default=0.0)
        if positions["Outro"] <= latest_non_outro or positions["Outro"] < duration_sec * 0.70:
            move("Outro", max(duration_sec * 0.70, latest_non_outro + min_gap_sec), duration_sec)

    return [by_name[c.name].model_copy(update={"position_sec": positions.get(c.name, c.position_sec)}) for c in cues]


def _repair_close_cues(
    cues: list[CuePoint],
    duration_sec: float,
    bpm: Optional[float],
    beat_times,
    min_gap_sec: float,
) -> list[CuePoint]:
    positions = {cue.name: cue.position_sec for cue in cues}
    by_name = {cue.name: cue for cue in cues}

    for _ in range(40):
        names = list(positions)
        close_pair: tuple[float, str, str] | None = None
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                d = abs(positions[a] - positions[b])
                if d < min_gap_sec and (close_pair is None or d < close_pair[0]):
                    close_pair = (d, a, b)

        if close_pair is None:
            break

        _d, a, b = close_pair
        move_name = a if _SEMANTIC_PRIORITY.get(a, 99) > _SEMANTIC_PRIORITY.get(b, 99) else b
        others = [pos for key, pos in positions.items() if key != move_name]

        moved = False
        for cand in _candidate_times(move_name, positions, duration_sec, bpm, beat_times):
            if _is_free(cand, others, min_gap_sec):
                positions[move_name] = cand
                moved = True
                break

        if not moved:
            base = positions[move_name]
            for delta in (min_gap_sec, -min_gap_sec, min_gap_sec * 2, -min_gap_sec * 2, 8.0, -8.0):
                cand = _clamp_time(base + delta, duration_sec)
                if _is_free(cand, others, min_gap_sec):
                    positions[move_name] = cand
                    moved = True
                    break

        if not moved:
            break

    return [by_name[c.name].model_copy(update={"position_sec": positions.get(c.name, c.position_sec)}) for c in cues]
