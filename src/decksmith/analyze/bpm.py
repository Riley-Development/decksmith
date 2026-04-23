"""BPM detection using librosa.

Spec notes:
- ``bpm_tolerance``: 0.5 (from config)
- ``bpm_voting``: true — use multiple estimation methods and pick the
  consensus value.
- Memory: ``librosa.load()`` = mono 22050 Hz. One track at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class BpmResult:
    """Result of BPM detection."""
    bpm: float
    confidence: float   # 0.0–1.0

    @property
    def bpm_rounded(self) -> float:
        """BPM rounded to one decimal place."""
        return round(self.bpm, 1)


def detect_bpm(
    y: np.ndarray,
    sr: int = 22050,
    bpm_voting: bool = True,
) -> BpmResult:
    """Detect the tempo (BPM) of an audio signal.

    When *bpm_voting* is True, uses multiple estimation approaches
    and picks the median for robustness.

    Parameters
    ----------
    y : np.ndarray
        Audio time series (mono).
    sr : int
        Sample rate.
    bpm_voting : bool
        If True, combine multiple estimates for a more stable result.
    """
    import librosa

    if bpm_voting:
        estimates = []

        # Method 1: librosa.beat.beat_track (default onset envelope)
        tempo1, _ = librosa.beat.beat_track(y=y, sr=sr)
        t1 = float(np.atleast_1d(tempo1)[0])
        if t1 > 0:
            estimates.append(t1)

        # Method 2: onset-strength based tempogram
        oenv = librosa.onset.onset_strength(y=y, sr=sr)
        tempo2 = librosa.feature.tempo(onset_envelope=oenv, sr=sr)
        t2 = float(np.atleast_1d(tempo2)[0])
        if t2 > 0:
            estimates.append(t2)

        # Method 3: percussive component only
        _, y_perc = librosa.effects.hpss(y)
        tempo3, _ = librosa.beat.beat_track(y=y_perc, sr=sr)
        t3 = float(np.atleast_1d(tempo3)[0])
        if t3 > 0:
            estimates.append(t3)

        if not estimates:
            return BpmResult(bpm=0.0, confidence=0.0)

        # Take the median as the consensus BPM
        bpm = float(np.median(estimates))

        # Confidence: how close are the estimates to each other?
        # Low spread = high confidence
        if len(estimates) >= 2:
            spread = float(np.std(estimates))
            # Normalise: spread of 0 -> confidence 1.0, spread of 10+ -> ~0.5
            confidence = max(0.0, min(1.0, 1.0 - (spread / 20.0)))
        else:
            confidence = 0.6
    else:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(np.atleast_1d(tempo)[0])
        confidence = 0.7 if bpm > 0 else 0.0

    return BpmResult(bpm=round(bpm, 1), confidence=round(confidence, 2))
