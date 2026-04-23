"""Musical key detection via Krumhansl-Schmuckler algorithm.

The spec is explicit: *librosa has NO detect_key()*. This module
implements the K-S key-profile correlation manually and maps the
result to Camelot wheel notation for DJ use.

Algorithm:
1. Load audio, separate harmonic component (HPSS).
2. Compute chroma features (12-bin pitch class distribution).
3. Correlate the mean chroma vector against all 24 key profiles
   (12 major + 12 minor, Krumhansl-Schmuckler weights).
4. Pick the best-matching key.
5. Map to Camelot notation (e.g. C major -> 8B, A minor -> 8A).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Krumhansl-Schmuckler key profiles
# ---------------------------------------------------------------------------
# Weights represent the "stability" of each pitch class in a given key.
# Index 0 = C, 1 = C#, 2 = D, ..., 11 = B

_MAJOR_PROFILE = np.array([
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
    2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
])

_MINOR_PROFILE = np.array([
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
    2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
])

# Pitch class names (semitone index -> name)
_PITCH_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# Camelot wheel mapping
_MAJOR_CAMELOT = {
    "C": "8B",  "Db": "3B",  "D": "10B", "Eb": "5B",
    "E": "12B", "F": "7B",   "F#": "2B",  "G": "9B",
    "Ab": "4B", "A": "11B",  "Bb": "6B",  "B": "1B",
}
_MINOR_CAMELOT = {
    "C": "5A",  "Db": "12A", "D": "7A",  "Eb": "2A",
    "E": "9A",  "F": "4A",   "F#": "11A", "G": "6A",
    "Ab": "1A", "A": "8A",   "Bb": "3A",  "B": "10A",
}


@dataclass
class KeyResult:
    """Result of key detection."""
    key: str            # e.g. "C major", "A minor"
    camelot: str        # e.g. "8B", "8A"
    confidence: float   # 0.0–1.0 (correlation strength)


def detect_key(y: np.ndarray, sr: int = 22050) -> KeyResult:
    """Detect the musical key using Krumhansl-Schmuckler.

    Parameters
    ----------
    y : np.ndarray
        Audio time series (mono).
    sr : int
        Sample rate.
    """
    import librosa

    # Separate harmonic component for cleaner pitch analysis
    y_harmonic, _ = librosa.effects.hpss(y)

    # Compute chroma features
    chroma = librosa.feature.chroma_stft(y=y_harmonic, sr=sr)
    mean_chroma = np.mean(chroma, axis=1)  # shape: (12,)

    # Normalise chroma to zero mean for Pearson correlation
    chroma_norm = mean_chroma - np.mean(mean_chroma)

    best_corr = -2.0
    best_key_name = "C"
    best_mode = "major"

    for shift in range(12):
        # Rotate the profile to match each root note
        major_rotated = np.roll(_MAJOR_PROFILE, shift)
        minor_rotated = np.roll(_MINOR_PROFILE, shift)

        maj_norm = major_rotated - np.mean(major_rotated)
        min_norm = minor_rotated - np.mean(minor_rotated)

        # Pearson correlation
        maj_corr = float(np.corrcoef(chroma_norm, maj_norm)[0, 1])
        min_corr = float(np.corrcoef(chroma_norm, min_norm)[0, 1])

        if maj_corr > best_corr:
            best_corr = maj_corr
            best_key_name = _PITCH_NAMES[shift]
            best_mode = "major"

        if min_corr > best_corr:
            best_corr = min_corr
            best_key_name = _PITCH_NAMES[shift]
            best_mode = "minor"

    # Map to Camelot
    if best_mode == "major":
        camelot = _MAJOR_CAMELOT.get(best_key_name, "?")
    else:
        camelot = _MINOR_CAMELOT.get(best_key_name, "?")

    # Confidence from correlation (0–1 range, correlation can be -1 to 1)
    confidence = max(0.0, min(1.0, (best_corr + 1.0) / 2.0))

    key_label = f"{best_key_name} {'min' if best_mode == 'minor' else 'maj'}"

    return KeyResult(
        key=key_label,
        camelot=camelot,
        confidence=round(confidence, 2),
    )
