"""Energy curves and transition rules for set building.

Separated from :mod:`builder` so the selection algorithm stays pure
and the LLM layer can call into this module for validation.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_ENERGY_CURVES: dict[str, list[int]] = {
    "wave":        [3, 5, 7, 9, 7, 5, 8, 10, 6, 4],
    "ascending":   [2, 3, 4, 5, 6, 7, 8, 9, 10, 10],
    "peak_valley": [5, 8, 10, 6, 9, 10, 7, 10, 8, 5],
    "slow_burn":   [3, 3, 4, 5, 5, 6, 7, 8, 9, 10],
}

# 12-note Camelot wheel — harmonic neighbours are +/-1 on the same letter
# and the same number with the opposite letter.
_CAMELOT_LETTERS = ("A", "B")


def parse_camelot(key: Optional[str]) -> Optional[tuple[int, str]]:
    if not key:
        return None
    key = key.strip().upper()
    if len(key) < 2 or key[-1] not in _CAMELOT_LETTERS:
        return None
    try:
        return int(key[:-1]), key[-1]
    except ValueError:
        return None


def harmonic_neighbours(key: str) -> list[str]:
    """Return the set of Camelot keys that mix harmonically with *key*.

    Rules: same number same letter, +/-1 same letter, same number opposite letter.
    """
    parsed = parse_camelot(key)
    if parsed is None:
        return []
    num, letter = parsed
    other = "B" if letter == "A" else "A"
    def wrap(n: int) -> int:
        return ((n - 1) % 12) + 1
    return [
        f"{num}{letter}",
        f"{wrap(num - 1)}{letter}",
        f"{wrap(num + 1)}{letter}",
        f"{num}{other}",
    ]


def is_harmonic(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return True  # Unknown keys are never "disallowed"
    return b in harmonic_neighbours(a)


def bpm_drift_ok(a: Optional[float], b: Optional[float], max_drift: float = 6.0) -> bool:
    if not a or not b:
        return True
    return abs(a - b) <= max_drift


def energy_slot_for_position(
    position: int,
    total: int,
    curve: list[int],
) -> int:
    """Return the target energy for slot *position* (0-based) out of *total*.

    Interpolates into the curve so a 5-slot set and a 20-slot set both
    sample the curve evenly.
    """
    if total <= 1:
        return curve[len(curve) // 2]
    # Map position 0..total-1 onto curve indices 0..len(curve)-1
    ratio = position / (total - 1)
    idx = int(round(ratio * (len(curve) - 1)))
    idx = max(0, min(idx, len(curve) - 1))
    return curve[idx]


def validate_transition(
    prev_bpm: Optional[float],
    prev_key: Optional[str],
    next_bpm: Optional[float],
    next_key: Optional[str],
    bpm_drift_max: float = 6.0,
) -> tuple[bool, str]:
    """Return ``(is_good, note)`` describing the transition quality."""
    reasons: list[str] = []
    if not bpm_drift_ok(prev_bpm, next_bpm, bpm_drift_max):
        reasons.append(f"BPM jumps from {prev_bpm:.0f} to {next_bpm:.0f}")
    if not is_harmonic(prev_key, next_key):
        reasons.append(f"{prev_key} → {next_key} is not harmonic")
    if reasons:
        return False, "; ".join(reasons)
    if prev_bpm and next_bpm and abs(prev_bpm - next_bpm) < 1:
        return True, "Perfect BPM match"
    return True, "Clean harmonic transition"
