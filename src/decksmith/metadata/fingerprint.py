"""Audio fingerprinting via AcoustID.

Requires:
  - ``fpcalc`` on PATH (Chromaprint)
  - ``pyacoustid`` Python package (optional)
  - An AcoustID API key in config (optional)

Graceful degradation: if any piece is missing, return a
:class:`FingerprintResult` with ``ok=False`` and a helpful reason — no
exceptions.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.utils.api_clients import get_acoustid_key


@dataclass
class FingerprintResult:
    filepath: str
    ok: bool = False
    reason: Optional[str] = None
    # Top match from AcoustID (None if no lookup ran)
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    matched_album: Optional[str] = None
    matched_score: Optional[float] = None
    acoustid_id: Optional[str] = None
    musicbrainz_id: Optional[str] = None


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def compute_fingerprint(filepath: str) -> Optional[tuple[int, str]]:
    """Return ``(duration_sec, fingerprint)`` using fpcalc, or None.

    Uses the pyacoustid package if available, otherwise shells out.
    Returns None on any error so callers can surface a helpful message.
    """
    try:
        import acoustid  # type: ignore
        try:
            duration, fp = acoustid.fingerprint_file(filepath)
            return int(duration), fp.decode() if isinstance(fp, bytes) else fp
        except Exception:
            return None
    except ImportError:
        pass

    if not fpcalc_available():
        return None

    try:
        out = subprocess.run(
            ["fpcalc", "-json", filepath],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode != 0:
            return None
        import json as _json
        data = _json.loads(out.stdout)
        return int(float(data.get("duration", 0))), data.get("fingerprint", "")
    except Exception:
        return None


def identify_track(
    filepath: str,
    config: DecksmithConfig,
) -> FingerprintResult:
    """Compute the fingerprint and look it up against AcoustID.

    Never raises for missing deps/keys — the result carries the reason.
    """
    res = FingerprintResult(filepath=filepath)

    api_key = get_acoustid_key(config)
    if not api_key:
        res.reason = "AcoustID key not set."
        return res

    if not fpcalc_available():
        res.reason = "fpcalc (Chromaprint) is not installed."
        return res

    fp = compute_fingerprint(filepath)
    if fp is None:
        res.reason = "Fingerprint computation failed."
        return res
    duration, fingerprint = fp

    try:
        import acoustid  # type: ignore
    except ImportError:
        res.reason = "pyacoustid not installed — pip install decksmith[discovery]"
        return res

    try:
        matches = acoustid.lookup(api_key, fingerprint, duration, meta="recordings+releases")
    except Exception as exc:
        res.reason = f"AcoustID lookup failed: {exc}"
        return res

    # ``matches`` is a dict with "results" list
    results = (matches or {}).get("results", []) if isinstance(matches, dict) else []
    if not results:
        res.reason = "No match found."
        return res

    best = results[0]
    res.acoustid_id = best.get("id")
    res.matched_score = best.get("score")
    recordings = best.get("recordings", [])
    if recordings:
        rec = recordings[0]
        res.musicbrainz_id = rec.get("id")
        res.matched_title = rec.get("title")
        artists = rec.get("artists") or []
        if artists:
            res.matched_artist = artists[0].get("name")
        releases = rec.get("releases") or []
        if releases:
            res.matched_album = releases[0].get("title")

    res.ok = res.matched_title is not None
    if not res.ok and not res.reason:
        res.reason = "Match had no recording metadata."
    return res
