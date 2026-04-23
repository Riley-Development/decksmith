"""Find gaps in the library — genre/BPM buckets that feel under-stocked.

The score compares each bucket's track count to the median bucket size;
buckets with under 25% of the median are flagged as *gaps*.  This is
dumb but effective: it surfaces "you have barely any 128-130 BPM house"
without needing an external service.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median

from decksmith.config import DecksmithConfig
from decksmith.models import Track
from decksmith.rekordbox.folders import folder_for_track


@dataclass
class Gap:
    bucket: str
    count: int
    target: int
    deficit: int


def find_gaps(tracks: list[Track], config: DecksmithConfig) -> list[Gap]:
    buckets: Counter[str] = Counter()
    for t in tracks:
        buckets[folder_for_track(t, config)] += 1

    # Exclude Uncategorized — it's not a real genre bucket
    real = [c for name, c in buckets.items() if "Uncategorized" not in name]
    if not real:
        return []

    target = max(3, int(median(real)))
    threshold = max(1, target // 4)

    gaps: list[Gap] = []
    for name, count in buckets.items():
        if "Uncategorized" in name:
            continue
        if count < threshold:
            gaps.append(Gap(
                bucket=name,
                count=count,
                target=target,
                deficit=target - count,
            ))
    gaps.sort(key=lambda g: g.deficit, reverse=True)
    return gaps
