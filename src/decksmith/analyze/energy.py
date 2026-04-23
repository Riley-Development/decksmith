"""Energy level analysis (1–10 scale).

Combines RMS loudness and spectral brightness into a single integer
energy rating that DJs can use for set planning:
- 1–3: Chill / ambient / downtempo
- 4–6: Mid-energy / groovy
- 7–9: Peak time / driving
- 10:  Maximum intensity
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class EnergyResult:
    """Result of energy analysis."""
    energy: int         # 1–10
    rms_db: float       # average RMS in dB
    brightness: float   # mean spectral centroid (Hz)


def detect_energy(y: np.ndarray, sr: int = 22050) -> EnergyResult:
    """Compute an energy level (1–10) for an audio signal.

    The energy score is a weighted blend of:
    - **RMS loudness** (70%): louder = higher energy.
    - **Spectral centroid** (30%): brighter = higher energy.

    Both are normalised to [0, 1] using empirical ranges typical of
    electronic/DJ music, then combined and mapped to 1–10.

    Parameters
    ----------
    y : np.ndarray
        Audio time series (mono).
    sr : int
        Sample rate.
    """
    import librosa

    # --- RMS loudness ---
    rms = librosa.feature.rms(y=y)[0]
    mean_rms = float(np.mean(rms))
    # Convert to dB (relative to 1.0)
    rms_db = float(20 * np.log10(mean_rms + 1e-10))

    # Normalise RMS dB to [0, 1].
    # Typical range for normalised audio: -40 dB (very quiet) to -6 dB (loud).
    rms_norm = np.clip((rms_db + 40) / 34, 0.0, 1.0)

    # --- Spectral centroid (brightness) ---
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    mean_centroid = float(np.mean(centroid))

    # Normalise centroid to [0, 1].
    # Typical range: 500 Hz (bassy) to 6000 Hz (bright/harsh).
    centroid_norm = np.clip((mean_centroid - 500) / 5500, 0.0, 1.0)

    # --- Combine ---
    combined = 0.70 * rms_norm + 0.30 * centroid_norm

    # Map to 1–10 (never 0)
    energy = int(np.clip(np.round(combined * 9 + 1), 1, 10))

    return EnergyResult(
        energy=energy,
        rms_db=round(rms_db, 1),
        brightness=round(mean_centroid, 0),
    )
