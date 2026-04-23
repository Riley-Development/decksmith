"""Spotify search for metadata (album/year/ISRC).

Spotify Audio Features/Analysis endpoints are deprecated for new
apps (per the spec); this module sticks to search+track metadata,
which still works with standard client credentials.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.utils.api_clients import is_key_configured
from decksmith.metadata.artwork import _spotify_token  # re-use token cache


@dataclass
class SpotifyTrackInfo:
    title: str
    artist: str
    album: str
    release_date: str
    isrc: Optional[str] = None
    spotify_id: Optional[str] = None


def search_track(
    config: DecksmithConfig,
    artist: str,
    title: str,
) -> Optional[SpotifyTrackInfo]:
    """Return the top Spotify match for *artist* / *title*, or None."""
    if not is_key_configured(config, "spotify"):
        return None
    tok = _spotify_token(config)
    if not tok:
        return None
    q = urllib.parse.quote(f"artist:{artist} track:{title}")
    url = f"https://api.spotify.com/v1/search?q={q}&type=track&limit=1"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    items = (data.get("tracks") or {}).get("items", [])
    if not items:
        return None
    it = items[0]
    album = it.get("album", {}) or {}
    return SpotifyTrackInfo(
        title=it.get("name", ""),
        artist=(it.get("artists") or [{}])[0].get("name", ""),
        album=album.get("name", ""),
        release_date=album.get("release_date", ""),
        isrc=(it.get("external_ids") or {}).get("isrc"),
        spotify_id=it.get("id"),
    )
