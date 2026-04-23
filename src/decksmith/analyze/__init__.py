"""Audio analysis pipeline — BPM, key, energy, bitrate authenticity.

The ``analyze_track`` function is the single entry point that runs all
analysis modules on one file and returns a unified result dict.

**Sample rate architecture:**

BPM, key, and energy analysis use ``sr=22050`` (mono) per the spec's
memory guidance (``librosa.load()`` = mono 22050 Hz).  This is fine
because those algorithms only need content up to ~11 kHz.

Bitrate authenticity analysis *cannot* use 22050 Hz because the spec
thresholds go up to 19500 Hz (Nyquist at 22050 is only 11025 Hz).
Bitrate analysis therefore loads a second copy at the file's native
sample rate (typically 44100 Hz, Nyquist = 22050 Hz) which covers the
full audible range.  This second load is released immediately after
spectral analysis completes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# numpy is an optional runtime dep (part of the ``analysis`` extra).  Importing
# it at module load time would break a partial install, so we reference it by
# name in type hints only via ``Any``.


@dataclass
class AnalysisResult:
    """Unified analysis result for one track."""
    filepath: str
    bpm: Optional[float] = None
    bpm_confidence: Optional[float] = None
    key: Optional[str] = None
    camelot: Optional[str] = None
    key_confidence: Optional[float] = None
    energy: Optional[int] = None
    rms_db: Optional[float] = None
    bitrate_declared: Optional[int] = None
    bitrate_authentic: Optional[bool] = None
    bitrate_confidence: Optional[float] = None
    bitrate_explanation: Optional[str] = None
    # Spectrum data retained for the HTML report (native-rate analysis).
    # Typed as Any so the dataclass import works without numpy.
    spectrum: Optional[Any] = None
    spectrum_freqs: Optional[Any] = None
    spectrum_sr: Optional[int] = None
    # Error tracking
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def _has_data(self) -> bool:
        """True if at least one analysis module produced a result."""
        return any([
            self.bpm is not None,
            self.camelot is not None,
            self.energy is not None,
            self.bitrate_authentic is not None,
        ])

    @property
    def ok(self) -> bool:
        """True if the track loaded and at least one module produced data.

        A track where the audio loaded but every module failed is NOT ok —
        it has no useful analysis output and should be counted as a failure.
        """
        return self.error is None and self._has_data

    @property
    def partial(self) -> bool:
        """True if some (but not all) modules succeeded."""
        return self.ok and len(self.warnings) > 0

    @property
    def failed(self) -> bool:
        """True if the track is a total failure (load error or no data)."""
        return not self.ok


def analyze_track(
    filepath: str,
    *,
    declared_kbps: Optional[int] = None,
    analysis_config: Optional[dict] = None,
) -> AnalysisResult:
    """Run all analysis modules on a single track.

    Parameters
    ----------
    filepath : str
        Path to the audio file.
    declared_kbps : int, optional
        Bitrate reported by ffprobe.  If None, bitrate check is skipped.
    analysis_config : dict, optional
        The ``analysis`` section from config (for threshold overrides).

    Returns an ``AnalysisResult``.  On fatal failure, ``result.error``
    is set.  Per-module failures are recorded in ``result.warnings``
    so the CLI summary can report partial analysis honestly.
    """
    try:
        import librosa
    except ImportError:
        return AnalysisResult(
            filepath=filepath,
            error="librosa is not installed. Run: pip install decksmith[analysis]",
        )

    result = AnalysisResult(filepath=filepath)

    # ------------------------------------------------------------------
    # Load 1: 22050 Hz mono for BPM, key, energy (memory-efficient)
    # ------------------------------------------------------------------
    try:
        y_lo, sr_lo = librosa.load(filepath, sr=22050, mono=True)
    except Exception as exc:
        result.error = f"Could not load audio: {exc}"
        return result

    # --- BPM ---
    try:
        from decksmith.analyze.bpm import detect_bpm
        bpm_result = detect_bpm(y_lo, sr_lo, bpm_voting=True)
        result.bpm = bpm_result.bpm
        result.bpm_confidence = bpm_result.confidence
    except Exception as exc:
        result.warnings.append(f"BPM detection failed: {exc}")

    # --- Key ---
    try:
        from decksmith.analyze.key import detect_key
        key_result = detect_key(y_lo, sr_lo)
        result.key = key_result.key
        result.camelot = key_result.camelot
        result.key_confidence = key_result.confidence
    except Exception as exc:
        result.warnings.append(f"Key detection failed: {exc}")

    # --- Energy ---
    try:
        from decksmith.analyze.energy import detect_energy
        energy_result = detect_energy(y_lo, sr_lo)
        result.energy = energy_result.energy
        result.rms_db = energy_result.rms_db
    except Exception as exc:
        result.warnings.append(f"Energy detection failed: {exc}")

    # Release the 22050 Hz array before loading the native-rate copy
    del y_lo

    # ------------------------------------------------------------------
    # Load 2: native sample rate for bitrate authenticity
    #
    # The spec thresholds go up to 19500 Hz.  At sr=22050 the Nyquist
    # limit is only 11025 Hz, so the shelf detection would be
    # physically meaningless.  We load at the file's native rate
    # (typically 44100 Hz → Nyquist 22050 Hz) which covers the full
    # audible range needed to evaluate the thresholds.
    # ------------------------------------------------------------------
    if declared_kbps and declared_kbps > 0:
        try:
            y_hi, sr_hi = librosa.load(filepath, sr=None, mono=True)

            from decksmith.analyze.bitrate import check_bitrate
            thresholds = None
            if analysis_config:
                raw = analysis_config.get("frequency_shelf_thresholds")
                if raw:
                    thresholds = {int(k): v for k, v in raw.items()}

            br_result = check_bitrate(y_hi, sr_hi, declared_kbps, thresholds)
            result.bitrate_declared = br_result.declared_kbps
            result.bitrate_authentic = br_result.authentic
            result.bitrate_confidence = br_result.confidence
            result.bitrate_explanation = br_result.explanation

            # Retain spectrum for the HTML report
            result.spectrum = br_result.spectral.spectrum
            result.spectrum_freqs = br_result.spectral.freqs
            result.spectrum_sr = sr_hi

            del y_hi
        except Exception as exc:
            result.warnings.append(f"Bitrate analysis failed: {exc}")

    return result
