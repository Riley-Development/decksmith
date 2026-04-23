"""Cover artwork retrieval.

Source order (per spec): Deezer → Spotify → Discogs → MusicBrainz.

- Deezer needs no auth (uses their public search API).
- Spotify needs client_id/client_secret.
- Discogs/MusicBrainz via the enricher's clients.

All downloads go through `urllib` so we don't pick up an extra dep.
Embedding writes to the audio file as cover art using mutagen.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from decksmith.config import DecksmithConfig
from decksmith.utils.api_clients import _resolve_key, is_key_configured

_USER_AGENT = "Decksmith/0.1 (+https://github.com/decksmith)"
_HTTP_TIMEOUT = 10


@dataclass
class ArtworkResult:
    filepath: str
    ok: bool = False
    reason: Optional[str] = None
    source: Optional[str] = None
    image_bytes: Optional[bytes] = None
    image_mime: str = "image/jpeg"
    resolution: Optional[int] = None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: Optional[dict] = None) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            return r.read()
    except Exception:
        return None


def _http_get_json(url: str, headers: Optional[dict] = None) -> Optional[dict]:
    data = _http_get(url, headers)
    if data is None:
        return None
    try:
        return json.loads(data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Deezer (no auth)
# ---------------------------------------------------------------------------

def _deezer_lookup(artist: str, title: str, min_size: int) -> Optional[ArtworkResult]:
    q = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
    url = f"https://api.deezer.com/search?q={q}&limit=1"
    data = _http_get_json(url)
    if not data or not data.get("data"):
        return None
    first = data["data"][0]
    album = first.get("album", {}) or {}
    # Try xl (1000px) → big (500) → medium (250)
    for key, size in [("cover_xl", 1000), ("cover_big", 500), ("cover_medium", 250)]:
        art_url = album.get(key)
        if art_url and size >= min_size:
            img = _http_get(art_url)
            if img:
                return ArtworkResult(
                    filepath="",
                    ok=True,
                    source="deezer",
                    image_bytes=img,
                    image_mime="image/jpeg",
                    resolution=size,
                )
    return None


# ---------------------------------------------------------------------------
# Spotify (client credentials flow)
# ---------------------------------------------------------------------------

_SPOTIFY_TOKEN: Optional[str] = None


def _spotify_token(config: DecksmithConfig) -> Optional[str]:
    global _SPOTIFY_TOKEN
    if _SPOTIFY_TOKEN:
        return _SPOTIFY_TOKEN
    cid = _resolve_key(config, "spotify_client_id")
    sec = _resolve_key(config, "spotify_client_secret")
    if not (cid and sec):
        return None
    import base64
    creds = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            data = json.loads(r.read())
            _SPOTIFY_TOKEN = data.get("access_token")
            return _SPOTIFY_TOKEN
    except Exception:
        return None


def _spotify_lookup(config: DecksmithConfig, artist: str, title: str, min_size: int) -> Optional[ArtworkResult]:
    tok = _spotify_token(config)
    if not tok:
        return None
    q = urllib.parse.quote(f"artist:{artist} track:{title}")
    url = f"https://api.spotify.com/v1/search?q={q}&type=track&limit=1"
    data = _http_get_json(url, headers={"Authorization": f"Bearer {tok}"})
    if not data:
        return None
    items = (data.get("tracks") or {}).get("items", [])
    if not items:
        return None
    album = items[0].get("album", {}) or {}
    images = album.get("images", [])
    # Spotify returns images sorted largest-first
    for img_meta in images:
        size = img_meta.get("width") or 0
        if size >= min_size:
            img = _http_get(img_meta["url"])
            if img:
                return ArtworkResult(
                    filepath="",
                    ok=True,
                    source="spotify",
                    image_bytes=img,
                    image_mime="image/jpeg",
                    resolution=size,
                )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _discogs_lookup(config: DecksmithConfig, artist: str, title: str, min_size: int) -> Optional[ArtworkResult]:
    """Discogs cover art via the ``images`` field on a release."""
    from decksmith.utils.api_clients import _resolve_key
    token = _resolve_key(config, "discogs_token")
    if not token:
        return None
    try:
        import discogs_client  # type: ignore
    except ImportError:
        return None
    try:
        client = discogs_client.Client("Decksmith/0.1", user_token=token)
        results = client.search(f"{artist} {title}", type="release")
        first = next(iter(results.page(1)), None) if hasattr(results, "page") else None
        if first is None:
            return None
        images = getattr(first, "images", None) or []
        for im in images:
            uri = im.get("uri") or im.get("resource_url")
            width = im.get("width") or 0
            if uri and width >= min_size:
                img = _http_get(uri)
                if img:
                    return ArtworkResult(
                        filepath="",
                        ok=True,
                        source="discogs",
                        image_bytes=img,
                        image_mime="image/jpeg",
                        resolution=width,
                    )
    except Exception:
        return None
    return None


def _musicbrainz_lookup(artist: str, title: str, min_size: int) -> Optional[ArtworkResult]:
    """MusicBrainz → Cover Art Archive chain (no key needed)."""
    try:
        import musicbrainzngs  # type: ignore
    except ImportError:
        return None
    try:
        musicbrainzngs.set_useragent("Decksmith", "0.1.0")
        found = musicbrainzngs.search_recordings(
            artist=artist, recording=title, limit=1,
        )
        recs = found.get("recording-list", [])
        if not recs:
            return None
        release_id = None
        for rec in recs:
            for rel in rec.get("release-list", []) or []:
                release_id = rel.get("id")
                if release_id:
                    break
            if release_id:
                break
        if not release_id:
            return None
        # Cover Art Archive serves the front image at this stable URL
        caa_url = f"https://coverartarchive.org/release/{release_id}/front"
        img = _http_get(caa_url)
        if img:
            return ArtworkResult(
                filepath="",
                ok=True,
                source="musicbrainz",
                image_bytes=img,
                image_mime="image/jpeg",
                resolution=min_size,  # unknown, assume it meets threshold
            )
    except Exception:
        return None
    return None


def fetch_artwork(
    filepath: str,
    artist: str,
    title: str,
    config: DecksmithConfig,
    min_size: int = 600,
) -> ArtworkResult:
    """Try sources in order (spec: Deezer → Spotify → Discogs → MusicBrainz)."""
    if not (artist and title):
        return ArtworkResult(filepath=filepath, reason="Missing artist/title.")

    # 1) Deezer (no key)
    try:
        r = _deezer_lookup(artist, title, min_size)
        if r and r.ok:
            r.filepath = filepath
            return r
    except Exception:
        pass

    # 2) Spotify (needs client credentials)
    if is_key_configured(config, "spotify"):
        try:
            r = _spotify_lookup(config, artist, title, min_size)
            if r and r.ok:
                r.filepath = filepath
                return r
        except Exception:
            pass

    # 3) Discogs (needs token)
    try:
        r = _discogs_lookup(config, artist, title, min_size)
        if r and r.ok:
            r.filepath = filepath
            return r
    except Exception:
        pass

    # 4) MusicBrainz / Cover Art Archive (no key, slowest)
    try:
        r = _musicbrainz_lookup(artist, title, min_size)
        if r and r.ok:
            r.filepath = filepath
            return r
    except Exception:
        pass

    return ArtworkResult(filepath=filepath, reason="No artwork found from available sources.")


def embed_artwork(filepath: str, image_bytes: bytes, mime: str = "image/jpeg") -> bool:
    """Write *image_bytes* as cover art into *filepath*.

    Returns True on success.  Supports MP3, FLAC, M4A, AIFF, WAV.
    """
    ext = Path(filepath).suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.mp3 import MP3
            from mutagen.id3 import APIC
            audio = MP3(filepath, v2_version=3)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(APIC(
                encoding=3, mime=mime, type=3, desc="Cover",
                data=image_bytes,
            ))
            audio.save(v2_version=3)
            return True

        if ext in (".aiff", ".aif"):
            from mutagen.aiff import AIFF
            from mutagen.id3 import APIC
            audio = AIFF(filepath)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(APIC(
                encoding=3, mime=mime, type=3, desc="Cover",
                data=image_bytes,
            ))
            audio.save()
            return True

        if ext == ".wav":
            from mutagen.wave import WAVE
            from mutagen.id3 import APIC
            audio = WAVE(filepath)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(APIC(
                encoding=3, mime=mime, type=3, desc="Cover",
                data=image_bytes,
            ))
            audio.save()
            return True

        if ext == ".flac":
            from mutagen.flac import FLAC, Picture
            audio = FLAC(filepath)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = image_bytes
            # Clear existing art and add ours
            audio.clear_pictures()
            audio.add_picture(pic)
            audio.save()
            return True

        if ext == ".m4a":
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(filepath)
            fmt = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
            audio.tags["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
            audio.save()
            return True
    except Exception:
        return False

    return False
