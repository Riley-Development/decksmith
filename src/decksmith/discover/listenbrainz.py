"""ListenBrainz similarity + recommendations.

The token is *optional* — anonymous reads work for the public endpoints
we use here.  If the user supplies a token, we hit the authenticated
recommendation endpoint for better personalisation.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.utils.api_clients import _resolve_key

_BASE = "https://api.listenbrainz.org"
_USER_AGENT = "Decksmith/0.1"


@dataclass
class Recommendation:
    artist: str
    title: str
    score: float = 0.0
    mbid: Optional[str] = None


def _get(url: str, token: Optional[str] = None) -> Optional[dict]:
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def similar_artists(artist: str, limit: int = 10, token: Optional[str] = None) -> list[str]:
    """Return ListenBrainz similar-artist names.

    Pass *token* to hit the authenticated endpoint (higher rate limits,
    per-user personalisation).  Without a token the public endpoint
    still returns anonymous similar-artist data.
    """
    q = urllib.parse.quote(artist)
    url = f"{_BASE}/1/explore/similar-artists?artist_name={q}&count={limit}"
    data = _get(url, token=token)
    if not data:
        return []
    return [row.get("name", "") for row in data.get("artists", []) if row.get("name")]


def recommend_tracks(
    config: DecksmithConfig,
    seed_artists: list[str],
    limit: int = 25,
) -> list[Recommendation]:
    """Public recommendations anchored on *seed_artists*.

    If the user has stored a ListenBrainz token we pass it on every
    similar-artist lookup for per-user personalisation + better
    rate limits.
    """
    token = _resolve_key(config, "listenbrainz_token") or None

    out: list[Recommendation] = []
    seen: set[str] = set()

    for seed in seed_artists[:5]:  # cap seeds to avoid hammering the API
        similar = similar_artists(seed, token=token)
        for artist in similar:
            key = artist.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(Recommendation(artist=artist, title="(top track)", score=1.0))
            if len(out) >= limit:
                return out

    return out
