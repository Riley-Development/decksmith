"""Assemble a DJ set from the library, optionally guided by the LLM.

Local-first algorithm (no LLM required):

1. Filter candidates by BPM/genre/energy derived from the prompt.
2. Walk the target energy curve slot-by-slot.
3. For each slot, pick the best-fit track that:
   - matches the target energy (+/- 1),
   - is harmonic with the previous track,
   - has BPM within ``bpm_drift_max`` of the previous,
   - has not appeared in the last ``avoid_same_artist_within`` slots.

If a Groq client is available and the user passes ``use_llm=True``,
the LLM is consulted for the initial ordering; the local validator
then sanity-checks every transition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.models import SetTrack, Track
from decksmith.setbuilder.flow import (
    DEFAULT_ENERGY_CURVES,
    energy_slot_for_position,
    is_harmonic,
    bpm_drift_ok,
    validate_transition,
)


@dataclass
class SetResult:
    prompt: str
    tracks: list[SetTrack]
    target_length_min: int
    energy_curve_name: str
    transitions: list[str]
    used_llm: bool
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Prompt parsing
# ---------------------------------------------------------------------------

_BPM_RE = re.compile(r"(\d{2,3})\s*(?:-\s*(\d{2,3}))?\s*bpm", re.IGNORECASE)
_LEN_RE = re.compile(r"(\d{1,3})\s*(?:min|minute|hr|hour)", re.IGNORECASE)
_GENRES = [
    "tech house", "techno", "house", "trance", "dnb", "drum and bass",
    "dubstep", "hip-hop", "hip hop", "disco", "funk", "r&b",
    "downtempo", "ambient", "progressive",
]


def parse_prompt(prompt: str, default_length: int = 60) -> dict:
    """Extract BPM range, length, genre from a natural-language prompt."""
    q = prompt.lower()
    info: dict = {"prompt": prompt, "length_min": default_length}

    m = _LEN_RE.search(q)
    if m:
        val = int(m.group(1))
        if "hr" in m.group(0) or "hour" in m.group(0):
            val *= 60
        info["length_min"] = val

    m = _BPM_RE.search(q)
    if m:
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else low + 10
        info["bpm_range"] = (low, high)

    for g in _GENRES:
        if g in q:
            info["genre"] = g.title()
            break

    # Energy hints
    if "peak" in q or "festival" in q:
        info["energy_curve"] = "peak_valley"
    elif "chill" in q or "sunset" in q or "warm" in q:
        info["energy_curve"] = "slow_burn"
    elif "rise" in q or "ascending" in q:
        info["energy_curve"] = "ascending"
    else:
        info["energy_curve"] = "wave"

    return info


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _matches(track: Track, parsed: dict) -> bool:
    if "bpm_range" in parsed and track.bpm:
        lo, hi = parsed["bpm_range"]
        if not (lo - 2 <= track.bpm <= hi + 2):
            return False
    if "genre" in parsed and track.genre:
        if parsed["genre"].lower() not in track.genre.lower():
            return False
    return True


def _slot_count(length_min: int) -> int:
    # Assume ~4 min per track
    return max(5, length_min // 4)


def _pick_best(
    candidates: list[Track],
    prev: Optional[Track],
    target_energy: int,
    bpm_drift_max: float,
    recent_artists: list[str],
) -> Optional[Track]:
    best = None
    best_score = -1.0
    for t in candidates:
        if prev and not bpm_drift_ok(prev.bpm, t.bpm, bpm_drift_max):
            continue
        if prev and not is_harmonic(prev.key_camelot, t.key_camelot):
            continue
        if t.artist and t.artist in recent_artists:
            continue
        score = 0.0
        if t.energy:
            score += 10 - abs((t.energy or 5) - target_energy)
        if prev and t.bpm and prev.bpm:
            score += 3 - min(3, abs(t.bpm - prev.bpm) / 2)
        if score > best_score:
            best_score = score
            best = t
    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_set(
    prompt: str,
    library: list[Track],
    config: DecksmithConfig,
    use_llm: bool = True,
) -> SetResult:
    """Build a set matching *prompt* from *library*.

    If ``use_llm`` and a Groq key is configured, consult the LLM for a
    first-pass ordering.  A local validator then filters out any
    transitions that violate harmonic/BPM rules.
    """
    sb_cfg = config.setbuilder or {}
    bpm_drift_max = float(sb_cfg.get("bpm_drift_max", 6))
    avoid_within = int(sb_cfg.get("avoid_same_artist_within", 4))
    default_length = int(sb_cfg.get("default_length_minutes", 60))

    parsed = parse_prompt(prompt, default_length)
    curves = sb_cfg.get("energy_curves") or DEFAULT_ENERGY_CURVES
    curve = curves.get(parsed["energy_curve"]) or DEFAULT_ENERGY_CURVES["wave"]
    total = _slot_count(parsed["length_min"])

    candidates = [t for t in library if _matches(t, parsed)]

    used_llm = False
    chosen: list[Track] = []

    if use_llm and candidates:
        from decksmith.setbuilder.llm import get_client, suggest_set
        client = get_client(config)
        if client is not None:
            lean = [
                {
                    "filepath": t.filepath,
                    "artist": t.artist,
                    "title": t.title,
                    "bpm": t.bpm,
                    "key": t.key_camelot,
                    "energy": t.energy,
                    "genre": t.genre,
                }
                for t in candidates
            ]
            picks = suggest_set(
                client, prompt, lean,
                target_length_min=parsed["length_min"],
                energy_curve=curve,
            )
            if picks:
                by_path = {t.filepath: t for t in candidates}
                for p in picks:
                    fp = p.get("filepath")
                    if fp in by_path:
                        chosen.append(by_path[fp])
                if chosen:
                    used_llm = True

    # Greedy local fill if no LLM or LLM failed / returned partial set
    if len(chosen) < total:
        prev = chosen[-1] if chosen else None
        recent_artists: list[str] = [c.artist for c in chosen[-avoid_within:] if c.artist]
        pool = [t for t in candidates if t not in chosen]
        while len(chosen) < total and pool:
            target_energy = energy_slot_for_position(len(chosen), total, curve)
            pick = _pick_best(pool, prev, target_energy, bpm_drift_max, recent_artists)
            if pick is None:
                break
            chosen.append(pick)
            pool.remove(pick)
            prev = pick
            if pick.artist:
                recent_artists.append(pick.artist)
                recent_artists = recent_artists[-avoid_within:]

    # Build SetTrack entries with transition notes
    set_tracks: list[SetTrack] = []
    transitions: list[str] = []
    prev: Optional[Track] = None
    for i, t in enumerate(chosen):
        note = ""
        if prev:
            ok, reason = validate_transition(
                prev.bpm, prev.key_camelot, t.bpm, t.key_camelot, bpm_drift_max,
            )
            note = reason
            transitions.append(reason)
        set_tracks.append(SetTrack(
            track=t,
            position=i,
            transition_note=note,
            energy_slot=energy_slot_for_position(i, len(chosen), curve) if chosen else None,
        ))
        prev = t

    warning = None
    if len(chosen) < total:
        warning = (
            f"Only {len(chosen)} tracks matched your filters "
            f"(targeted {total}). Try widening the BPM range or analysing more tracks."
        )

    return SetResult(
        prompt=prompt,
        tracks=set_tracks,
        target_length_min=parsed["length_min"],
        energy_curve_name=parsed["energy_curve"],
        transitions=transitions,
        used_llm=used_llm,
        warning=warning,
    )
