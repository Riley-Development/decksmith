"""Spectral analysis — detect the frequency shelf where audio energy drops off.

Used by ``bitrate.py`` to determine whether a file's declared bitrate is
authentic.  A 320 kbps MP3 should carry energy up to ~20 kHz; if it cuts
off at 16 kHz it was likely re-encoded from a lower bitrate source.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SpectralResult:
    """Result of a frequency-shelf analysis."""
    cutoff_hz: float          # highest frequency with energy above noise floor
    energy_ratio_above: float # ratio of energy above cutoff vs total
    spectrum: np.ndarray      # representative magnitude spectrum (for reporting)
    freqs: np.ndarray         # frequency axis (Hz)

    def energy_ratio_at(self, reference_hz: float) -> float:
        """Compute the fraction of total energy that lies *above* a
        given reference frequency.

        Used by ``bitrate.py`` to evaluate the ``energy_ratio_floor``
        part of the spec thresholds.
        """
        if len(self.freqs) == 0:
            return 0.0
        total = float(np.sum(self.spectrum ** 2))
        if total == 0:
            return 0.0
        # Find the bin closest to reference_hz
        idx = int(np.searchsorted(self.freqs, reference_hz))
        if idx >= len(self.spectrum):
            return 0.0
        above = float(np.sum(self.spectrum[idx:] ** 2))
        return above / total


def compute_frequency_shelf(
    y: np.ndarray,
    sr: int,
    n_fft: int = 4096,
) -> SpectralResult:
    """Compute the frequency at which spectral energy effectively ends.

    Uses the 95th-percentile magnitude spectrum across the signal. That
    captures intermittent high-frequency content like hats, vocals, noise,
    and codec residue. A mean spectrum is too pessimistic for mastered dance
    and pop tracks and can falsely flag normal 320 kbps files.

    The "cutoff" is defined as the highest frequency bin above -70 dB from
    the 95th-percentile peak. This is a hard-lowpass detector, not proof that
    a track is genuinely sourced from a 320 kbps master.

    Parameters
    ----------
    y : np.ndarray
        Audio time series (mono). Must be loaded at a sample rate high
        enough that Nyquist covers the frequencies of interest (e.g.
        44100 Hz for thresholds up to 19500 Hz).
    sr : int
        Sample rate of *y*.
    n_fft : int
        FFT window size.
    """
    import librosa

    S = np.abs(librosa.stft(y, n_fft=n_fft))
    spectrum = np.percentile(S, 95, axis=1)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    peak = np.max(spectrum)
    if peak == 0:
        return SpectralResult(
            cutoff_hz=0.0, energy_ratio_above=0.0,
            spectrum=spectrum, freqs=freqs,
        )

    # Noise floor: -70 dB from peak magnitude. This is low enough to catch
    # codec residue/intermittent air-band content, while a hard transcode
    # low-pass still shows up as a clear cliff.
    threshold = peak * (10 ** (-70 / 20))

    # Walk from top of spectrum downward to find the shelf
    cutoff_bin = 0
    for i in range(len(spectrum) - 1, 0, -1):
        if spectrum[i] >= threshold:
            cutoff_bin = i
            break

    cutoff_hz = float(freqs[cutoff_bin]) if cutoff_bin > 0 else 0.0

    # Energy ratio above the detected cutoff
    total_energy = float(np.sum(spectrum ** 2))
    if total_energy > 0 and cutoff_bin < len(spectrum) - 1:
        above_energy = float(np.sum(spectrum[cutoff_bin + 1:] ** 2))
        energy_ratio = above_energy / total_energy
    else:
        energy_ratio = 0.0

    return SpectralResult(
        cutoff_hz=cutoff_hz,
        energy_ratio_above=energy_ratio,
        spectrum=spectrum,
        freqs=freqs,
    )
