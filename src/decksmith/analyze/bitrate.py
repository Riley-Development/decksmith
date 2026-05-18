"""Fake bitrate detection.

Compares a track's declared bitrate (from ffprobe) against its actual
frequency shelf (from spectral analysis).  A file claiming 320 kbps
should not show a hard low-pass around 16 kHz. If it does, it was probably
re-encoded from a lower-quality source.

Uses ``analysis.frequency_shelf_thresholds`` as hard-lowpass guardrails::

    320: { min_cutoff_hz: 18500, energy_ratio_floor: 0.0 }

A track is flagged as fake only when the detected shelf is below the declared
tier's minimum. DeckSmith intentionally does not require a fixed amount of
energy above 19.5 kHz; mastered club tracks can have very little energy up
there while still being genuine 320 kbps files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from decksmith.analyze.spectral import SpectralResult, compute_frequency_shelf

# Default thresholds from the spec
DEFAULT_THRESHOLDS: dict[int, dict[str, float]] = {
    320: {"min_cutoff_hz": 18500, "energy_ratio_floor": 0.0},
    256: {"min_cutoff_hz": 17500, "energy_ratio_floor": 0.0},
    192: {"min_cutoff_hz": 16000, "energy_ratio_floor": 0.0},
    128: {"min_cutoff_hz": 14500, "energy_ratio_floor": 0.0},
}

# Plain-language explanations for DJs
_EXPLANATIONS: dict[tuple[int, int], str] = {
    (320, 128): (
        "Claims 320kbps but audio cuts off at ~16kHz. "
        "Probably re-encoded from 128. "
        "Your crowd will hear the difference on a club system."
    ),
    (320, 192): (
        "Claims 320kbps but audio drops off around 17.5kHz. "
        "Likely upconverted from 192. "
        "Decent for practice but not ideal for big rooms."
    ),
    (256, 128): (
        "Claims 256kbps but frequency content says 128. "
        "Re-encoded from a low-quality source."
    ),
}


@dataclass
class BitrateResult:
    """Result of a bitrate authenticity check."""
    declared_kbps: int
    authentic: bool
    confidence: float         # 0.0–1.0
    cutoff_hz: float
    energy_ratio: float       # energy above the declared tier's threshold freq
    estimated_true_kbps: Optional[int]
    explanation: str
    spectral: SpectralResult  # retained for HTML report


def _estimate_true_bitrate(cutoff_hz: float, thresholds: dict) -> Optional[int]:
    """Given a frequency cutoff, guess the actual source bitrate."""
    sorted_tiers = sorted(thresholds.items())
    best = None
    for kbps, t in sorted_tiers:
        if cutoff_hz >= t["min_cutoff_hz"]:
            best = kbps
    return best


def _get_explanation(
    declared: int,
    estimated: Optional[int],
    cutoff_hz: float,
    energy_ratio: float,
    energy_floor: float,
) -> str:
    """Build a plain-language DJ-facing explanation."""
    if estimated is None and cutoff_hz < 5000:
        return (
            f"Declared {declared}kbps but frequency shelf is very low "
            f"({cutoff_hz:.0f}Hz). This file may be heavily degraded."
        )
    key = (declared, estimated) if estimated is not None else None
    if key and key in _EXPLANATIONS:
        return _EXPLANATIONS[key]
    if estimated is not None and estimated < declared:
        return (
            f"Claims {declared}kbps but frequency content is more consistent "
            f"with {estimated}kbps (shelf at {cutoff_hz:.0f}Hz)."
        )
    return (
        f"No hard low-pass detected for declared {declared}kbps "
        f"(shelf at {cutoff_hz:.0f}Hz)."
    )


def check_bitrate(
    y: np.ndarray,
    sr: int,
    declared_kbps: int,
    thresholds: Optional[dict] = None,
) -> BitrateResult:
    """Check whether a track's declared bitrate matches its spectral content.

    Uses a hard-lowpass signal:
    - ``min_cutoff_hz``: the shelf must reach at least this high.

    Parameters
    ----------
    y : np.ndarray
        Audio time series (mono).  Must be loaded at a sample rate whose
        Nyquist covers the highest threshold (44100 Hz for 19500 Hz).
    sr : int
        Sample rate.
    declared_kbps : int
        Bitrate reported by ffprobe / mutagen.
    thresholds : dict, optional
        Override the default ``frequency_shelf_thresholds``.
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    spectral = compute_frequency_shelf(y, sr)
    cutoff = spectral.cutoff_hz

    # Find the threshold for the declared bitrate
    declared_tier = thresholds.get(declared_kbps)
    if declared_tier is None:
        closest = min(thresholds.keys(), key=lambda k: abs(k - declared_kbps))
        declared_tier = thresholds[closest]

    min_cutoff = declared_tier["min_cutoff_hz"]
    energy_floor = declared_tier.get("energy_ratio_floor", 0.0)

    # Frequency shelf must reach min_cutoff_hz. This is a strong "fake"
    # signal only when the top end visibly disappears below the expected tier.
    shelf_ok = cutoff >= min_cutoff

    # Keep this metric for reports, but do not use it as a pass/fail gate.
    # Fixed air-band energy floors produced massive false positives because
    # many legitimate masters have quiet content above 18-20 kHz.
    energy_ratio = spectral.energy_ratio_at(min_cutoff)

    authentic = shelf_ok

    # Confidence: distance from threshold. It is still a heuristic, not proof
    # that a file came from a true 320 kbps source.
    if min_cutoff > 0:
        shelf_score = min(cutoff / min_cutoff, 1.0)
    else:
        shelf_score = 1.0
    confidence = round(shelf_score, 3)

    estimated = _estimate_true_bitrate(cutoff, thresholds) if not authentic else None
    explanation = _get_explanation(
        declared_kbps, estimated, cutoff, energy_ratio, energy_floor,
    )

    return BitrateResult(
        declared_kbps=declared_kbps,
        authentic=authentic,
        confidence=confidence,
        cutoff_hz=cutoff,
        energy_ratio=energy_ratio,
        estimated_true_kbps=estimated,
        explanation=explanation,
        spectral=spectral,
    )
