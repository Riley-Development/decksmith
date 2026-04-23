"""Unified tag I/O for all supported audio formats.

Key contract from the spec:
- MP3 must use ``v2_version=3`` on both load and save (Rekordbox needs ID3v2.3).
- WAV must explicitly use ``mutagen.wave.WAVE``.
- Handlers: MP3, FLAC, AIFF, WAV, M4A.

The public API is ``read_tags(filepath)`` and ``write_tags(filepath, tags)``.
Tags are dicts with normalised field names:
``title``, ``artist``, ``album``, ``album_artist``, ``genre``,
``comment``, ``bpm``, ``key``, ``year``, ``track_number``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import mutagen
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.aiff import AIFF
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

# ---------------------------------------------------------------------------
# ID3 field mapping (shared by MP3 / AIFF / WAV)
# ---------------------------------------------------------------------------

_ID3_READ_MAP: dict[str, str] = {
    "TIT2": "title",
    "TPE1": "artist",
    "TALB": "album",
    "TPE2": "album_artist",
    "TCON": "genre",
    "TBPM": "bpm",
    "TKEY": "key",
    "TDRC": "year",
    "TRCK": "track_number",
    "TENC": "encoded_by",
    "WXXX": "url",
    "TCOP": "copyright",
}

_ID3_WRITE_MAP: dict[str, str] = {v: k for k, v in _ID3_READ_MAP.items()}

# Vorbis comment mapping (FLAC)
_VORBIS_MAP: dict[str, str] = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "albumartist": "album_artist",
    "genre": "genre",
    "bpm": "bpm",
    "comment": "comment",
    "date": "year",
    "tracknumber": "track_number",
}

# MP4 atom mapping
_MP4_MAP: dict[str, str] = {
    "\xa9nam": "title",
    "\xa9ART": "artist",
    "\xa9alb": "album",
    "aART": "album_artist",
    "\xa9gen": "genre",
    "tmpo": "bpm",
    "\xa9day": "year",
    "trkn": "track_number",
    "\xa9cmt": "comment",
}
_MP4_WRITE_MAP: dict[str, str] = {v: k for k, v in _MP4_MAP.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _id3_to_dict(tags: ID3) -> dict[str, str]:
    """Extract normalised tag dict from an ID3 object."""
    result: dict[str, str] = {}
    for frame_id, field_name in _ID3_READ_MAP.items():
        frame = tags.get(frame_id)
        if frame:
            result[field_name] = str(frame)
    # Comments are special — may be in COMM frames
    for key in tags:
        if key.startswith("COMM"):
            result["comment"] = str(tags[key])
            break
    return result


def _dict_to_id3_frames(tags_dict: dict[str, str]) -> dict[str, Any]:
    """Convert a normalised tag dict to ID3 frame objects."""
    from mutagen.id3 import (
        TIT2, TPE1, TALB, TPE2, TCON, TBPM, TKEY, TDRC, TRCK,
        TENC, TCOP, COMM,
    )
    frame_map = {
        "title": lambda v: TIT2(encoding=3, text=[v]),
        "artist": lambda v: TPE1(encoding=3, text=[v]),
        "album": lambda v: TALB(encoding=3, text=[v]),
        "album_artist": lambda v: TPE2(encoding=3, text=[v]),
        "genre": lambda v: TCON(encoding=3, text=[v]),
        "bpm": lambda v: TBPM(encoding=3, text=[v]),
        "key": lambda v: TKEY(encoding=3, text=[v]),
        "year": lambda v: TDRC(encoding=3, text=[v]),
        "track_number": lambda v: TRCK(encoding=3, text=[v]),
        "encoded_by": lambda v: TENC(encoding=3, text=[v]),
        "copyright": lambda v: TCOP(encoding=3, text=[v]),
        "comment": lambda v: COMM(encoding=3, lang="eng", desc="", text=[v]),
    }
    frames = {}
    for field, value in tags_dict.items():
        factory = frame_map.get(field)
        if factory and value is not None:
            frame = factory(str(value))
            frames[frame.HashKey] = frame
    return frames


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_tags(filepath: str) -> dict[str, str]:
    """Read tags from *filepath* and return a normalised dict."""
    ext = Path(filepath).suffix.lower()

    if ext == ".mp3":
        try:
            audio = MP3(filepath, v2_version=3)
        except Exception:
            return {}
        if audio.tags is None:
            return {}
        return _id3_to_dict(audio.tags)

    if ext in (".aiff", ".aif"):
        try:
            audio = AIFF(filepath)
        except Exception:
            return {}
        if audio.tags is None:
            return {}
        return _id3_to_dict(audio.tags)

    if ext == ".wav":
        try:
            audio = WAVE(filepath)
        except Exception:
            return {}
        if audio.tags is None:
            return {}
        return _id3_to_dict(audio.tags)

    if ext == ".flac":
        try:
            audio = FLAC(filepath)
        except Exception:
            return {}
        result: dict[str, str] = {}
        if audio.tags:
            for vorbis_key, field_name in _VORBIS_MAP.items():
                vals = audio.tags.get(vorbis_key, [])
                if vals:
                    result[field_name] = vals[0]
        return result

    if ext == ".m4a":
        try:
            audio = MP4(filepath)
        except Exception:
            return {}
        result = {}
        if audio.tags:
            for atom, field_name in _MP4_MAP.items():
                vals = audio.tags.get(atom)
                if vals:
                    if atom == "tmpo":
                        result[field_name] = str(vals[0])
                    elif atom == "trkn":
                        result[field_name] = str(vals[0][0])
                    else:
                        result[field_name] = str(vals[0])
        return result

    return {}


def write_tags(filepath: str, tags: dict[str, str]) -> None:
    """Write *tags* to *filepath*, preserving format-specific requirements."""
    ext = Path(filepath).suffix.lower()

    if ext == ".mp3":
        try:
            audio = MP3(filepath, v2_version=3)
        except Exception:
            audio = MP3(filepath)
        if audio.tags is None:
            audio.add_tags()
        frames = _dict_to_id3_frames(tags)
        for key, frame in frames.items():
            audio.tags.setall(frame.FrameID, [frame])
        audio.save(v2_version=3, v23_sep="/")
        return

    if ext in (".aiff", ".aif"):
        try:
            audio = AIFF(filepath)
        except Exception:
            return
        if audio.tags is None:
            audio.add_tags()
        frames = _dict_to_id3_frames(tags)
        for key, frame in frames.items():
            audio.tags.setall(frame.FrameID, [frame])
        audio.save()
        return

    if ext == ".wav":
        try:
            audio = WAVE(filepath)
        except Exception:
            return
        if audio.tags is None:
            audio.add_tags()
        frames = _dict_to_id3_frames(tags)
        for key, frame in frames.items():
            audio.tags.setall(frame.FrameID, [frame])
        audio.save()
        return

    if ext == ".flac":
        try:
            audio = FLAC(filepath)
        except Exception:
            return
        reverse_vorbis = {v: k for k, v in _VORBIS_MAP.items()}
        for field, value in tags.items():
            vorbis_key = reverse_vorbis.get(field)
            if vorbis_key and value is not None:
                audio[vorbis_key] = [str(value)]
        audio.save()
        return

    if ext == ".m4a":
        try:
            audio = MP4(filepath)
        except Exception:
            return
        if audio.tags is None:
            audio.add_tags()
        for field, value in tags.items():
            atom = _MP4_WRITE_MAP.get(field)
            if atom and value is not None:
                if atom == "tmpo":
                    try:
                        audio.tags[atom] = [int(float(value))]
                    except (ValueError, TypeError):
                        pass
                elif atom == "trkn":
                    try:
                        audio.tags[atom] = [(int(value), 0)]
                    except (ValueError, TypeError):
                        pass
                else:
                    audio.tags[atom] = [str(value)]
        audio.save()
        return


def tags_to_json(tags: dict[str, str]) -> str:
    """Serialise a tags dict to JSON for database backup."""
    return json.dumps(tags, ensure_ascii=False)


def json_to_tags(json_str: str) -> dict[str, str]:
    """Deserialise a tags dict from JSON."""
    return json.loads(json_str)
