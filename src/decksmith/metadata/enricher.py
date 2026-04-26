"""Metadata enrichment — Discogs first, MusicBrainz fallback.

Both clients are optional.  When a key/package is missing, the
enricher returns an :class:`EnrichmentResult` with ``ok=False`` and a
reason; callers surface it as a friendly key-missing message.

Rate limits (per spec):
  - Discogs: 60/min unauth, 240/min with token.
  - MusicBrainz: 1 req/sec.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.utils.api_clients import _resolve_key

_MB_LAST_CALL = 0.0
_MB_MIN_INTERVAL = 1.05

_COMPILATION_FORMATS = {"Compilation", "Mixed", "DJ Mix", "Promo"}

_COMPILATION_KEYWORDS = {
    "best", "hits", "top", "greatest", "now", "billboard",
    "ultimate", "essential", "essentials", "mastermix", "chart",
    "radio", "promo", "volume", "throwback", "ministry",
    "switch", "mainstream", "brit awards",
}


@dataclass
class EnrichmentResult:
    filepath: str
    ok: bool = False
    reason: Optional[str] = None
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


def _is_compilation(release) -> bool:
    """Return True if a Discogs release looks like a compilation."""
    primary_artist = ""
    artists = getattr(release, "artists", None)
    if artists:
        try:
            primary_artist = artists[0].name if artists else ""
        except Exception:
            pass
    if primary_artist.lower() in ("various", "various artists", "va"):
        return True

    formats = getattr(release, "formats", None) or []
    for fmt in formats:
        descriptions = fmt.get("descriptions") or []
        for desc in descriptions:
            if desc in _COMPILATION_FORMATS:
                return True

    album_title = str(getattr(release, "title", "") or "").lower()
    keyword_hits = sum(1 for kw in _COMPILATION_KEYWORDS if kw in album_title)
    if keyword_hits >= 2:
        return True
    if keyword_hits >= 1 and re.search(r"\b(19|20)\d{2}\b", album_title):
        return True

    return False


def _extract_result(release) -> EnrichmentResult:
    """Pull enrichment fields from a Discogs release."""
    style = ""
    genre = ""
    if getattr(release, "styles", None):
        style = release.styles[0] if release.styles else ""
    if getattr(release, "genres", None):
        genre = release.genres[0] if release.genres else ""
    return EnrichmentResult(
        filepath="",
        ok=True,
        source="discogs",
        album=(str(release.title) if getattr(release, "title", None) else None),
        genre=genre or None,
        style=style or None,
        year=str(getattr(release, "year", "") or "") or None,
        label=(release.labels[0].name if getattr(release, "labels", None) else None),
        catalog_number=(release.labels[0].catno if getattr(release, "labels", None) else None),
    )


def _artist_matches(release, expected_artist: str) -> bool:
    """Check if any artist on the release roughly matches the expected one."""
    expected_lower = expected_artist.lower().strip()
    if not expected_lower:
        return False
    artists = getattr(release, "artists", None) or []
    for a in artists:
        name = (getattr(a, "name", "") or "").lower().strip()
        if not name:
            continue
        if name == expected_lower:
            return True
        shorter, longer = sorted([name, expected_lower], key=len)
        if shorter and len(shorter) >= len(longer) * 0.6 and shorter in longer:
            return True
    credit = str(getattr(release, "credits_string", "") or "").lower()
    if credit and re.search(r"\b" + re.escape(expected_lower) + r"\b", credit):
        return True
    return False


def _enrich_discogs(
    artist: str,
    title: str,
    client,
) -> Optional[EnrichmentResult]:
    try:
        results = client.search(
            f"{artist} {title}", type="release",
            artist=artist, track=title,
        )
        if not hasattr(results, "page"):
            return None

        candidates = []
        position = 0
        for page_num in range(1, 4):
            try:
                page = list(results.page(page_num))
            except Exception:
                break
            if not page:
                break
            for rel in page:
                position += 1
                if _is_compilation(rel):
                    continue
                if not _artist_matches(rel, artist):
                    continue
                rel_type = getattr(rel, "type", "") or ""
                candidates.append((rel, rel_type, position))
            if candidates:
                break

        if not candidates:
            return None

        def _sort_key(item):
            rel, rel_type, pos = item
            type_rank = 0 if rel_type.lower() == "album" else 1
            return (type_rank, pos)

        candidates.sort(key=_sort_key)
        best = candidates[0][0]
        return _extract_result(best)
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
            artist=artist, recording=title, limit=5,
        )
        _MB_LAST_CALL = time.time()
        recs = found.get("recording-list", [])
        if not recs:
            return None

        best_release = None
        best_year = 9999
        for rec in recs:
            for rel in rec.get("release-list", []) or []:
                rg = rel.get("release-group", {})
                rg_type = (rg.get("type") or "").lower()
                if rg_type in ("compilation", "dj-mix"):
                    continue
                yr_str = (rel.get("date") or "").split("-")[0]
                try:
                    yr = int(yr_str) if yr_str else 9999
                except ValueError:
                    yr = 9999
                type_bonus = 0 if rg_type == "album" else 1
                rank = (type_bonus, yr)
                if rank < (0 if best_release else 1, best_year):
                    best_release = rel
                    best_year = yr

        if best_release is None and recs:
            releases = recs[0].get("release-list", [])
            if releases:
                best_release = releases[0]
                yr_str = (best_release.get("date") or "").split("-")[0]
                try:
                    best_year = int(yr_str) if yr_str else 9999
                except ValueError:
                    best_year = 9999

        year = str(best_year) if best_year < 9999 else None
        album = best_release.get("title") if best_release else None

        return EnrichmentResult(
            filepath="",
            ok=True,
            source="musicbrainz",
            year=year,
            album=album,
        )
    except Exception:
        return None


def enrich_track(
    filepath: str,
    artist: str,
    title: str,
    config: DecksmithConfig,
    overwrite_compilations: bool = False,
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
