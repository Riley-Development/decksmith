"""Metadata enrichment — Discogs first, MusicBrainz fallback.

Both clients are optional.  When a key/package is missing, the
enricher returns an :class:`EnrichmentResult` with ``ok=False`` and a
reason; callers surface it as a friendly key-missing message.

Rate limits (per spec):
  - Discogs: 60/min unauth, 240/min with token.
  - MusicBrainz: 1 req/sec.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.utils.api_clients import _resolve_key

_MB_LAST_CALL = 0.0
_MB_MIN_INTERVAL = 1.05


@dataclass
class EnrichmentResult:
    filepath: str
    ok: bool = False
    reason: Optional[str] = None
    # Enriched fields — only populated if ok
    album: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[str] = None
    label: Optional[str] = None
    catalog_number: Optional[str] = None
    style: Optional[str] = None
    remix_info: Optional[str] = None
    source: Optional[str] = None  # "discogs" or "musicbrainz"


def _discogs_client(config: DecksmithConfig):
    token = _resolve_key(config, "discogs_token")
    if not token:
        return None
    try:
        import discogs_client  # type: ignore
    except ImportError:
        return None
    try:
        return discogs_client.Client("Decksmith/0.1", user_token=token)
    except Exception:
        return None


def _enrich_discogs(
    artist: str,
    title: str,
    client,
) -> Optional[EnrichmentResult]:
    try:
        results = client.search(f"{artist} {title}", type="release")
        # `results` is a paginated object; grab the first page
        first = next(iter(results.page(1)), None) if hasattr(results, "page") else None
        if first is None:
            return None
        style = ""
        genre = ""
        if getattr(first, "styles", None):
            style = first.styles[0] if first.styles else ""
        if getattr(first, "genres", None):
            genre = first.genres[0] if first.genres else ""
        # `title` on a Discogs release is the release/album name (e.g. "Stankonia").
        return EnrichmentResult(
            filepath="",
            ok=True,
            source="discogs",
            album=(str(first.title) if getattr(first, "title", None) else None),
            genre=genre or None,
            style=style or None,
            year=str(getattr(first, "year", "") or "") or None,
            label=(first.labels[0].name if getattr(first, "labels", None) else None),
            catalog_number=(first.labels[0].catno if getattr(first, "labels", None) else None),
        )
    except Exception:
        return None


def _enrich_musicbrainz(artist: str, title: str) -> Optional[EnrichmentResult]:
    try:
        import musicbrainzngs  # type: ignore
    except ImportError:
        return None

    global _MB_LAST_CALL
    gap = time.time() - _MB_LAST_CALL
    if gap < _MB_MIN_INTERVAL:
        time.sleep(_MB_MIN_INTERVAL - gap)

    try:
        musicbrainzngs.set_useragent("Decksmith", "0.1.0")
        found = musicbrainzngs.search_recordings(
            artist=artist, recording=title, limit=1,
        )
        _MB_LAST_CALL = time.time()
        recs = found.get("recording-list", [])
        if not recs:
            return None
        rec = recs[0]
        releases = rec.get("release-list", [])
        year = ""
        if releases:
            rel = releases[0]
            year = (rel.get("date") or "").split("-")[0]
        return EnrichmentResult(
            filepath="",
            ok=True,
            source="musicbrainz",
            year=year or None,
        )
    except Exception:
        return None


def enrich_track(
    filepath: str,
    artist: str,
    title: str,
    config: DecksmithConfig,
) -> EnrichmentResult:
    """Try Discogs first (richer electronic-music data), then MusicBrainz."""
    if not (artist and title):
        return EnrichmentResult(filepath=filepath, reason="Missing artist/title.")

    client = _discogs_client(config)
    if client is not None:
        r = _enrich_discogs(artist, title, client)
        if r and r.ok:
            r.filepath = filepath
            return r

    r = _enrich_musicbrainz(artist, title)
    if r and r.ok:
        r.filepath = filepath
        return r

    reason = "No enrichment available."
    if client is None:
        reason = "Discogs token not set (or python3-discogs-client not installed)."
    return EnrichmentResult(filepath=filepath, reason=reason)
