"""Rekordbox XML export.

Produces a `rekordbox.xml` compatible with Rekordbox 5/6/7.  Track
locations use `file://localhost/<path>` on macOS/Linux and
`file://localhost/C:/<path>` on Windows, per the spec.

Hot cues (Num 0-7) and memory cues (Num -1) are written as
`<POSITION_MARK>` elements with RGB integers.
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET
from xml.dom import minidom

from decksmith.models import CuePoint, Track


def _file_url(filepath: str) -> str:
    """Return a Rekordbox-style ``file://localhost/...`` URL.

    - macOS/Linux: ``file://localhost/Users/...``
    - Windows:     ``file://localhost/C:/Users/...``
    """
    p = Path(filepath).expanduser().resolve()
    raw = str(p)
    if sys.platform.startswith("win"):
        # Ensure forward slashes and drive letter format C:/
        raw = raw.replace("\\", "/")
        # Path already starts with drive letter; URL-quote path portion
        quoted = urllib.parse.quote(raw, safe="/:")
        return f"file://localhost/{quoted}"
    # POSIX: encode the path portion; keep the leading slash
    quoted = urllib.parse.quote(raw)
    return f"file://localhost{quoted}"


def _track_element(track_id: int, track: Track, cues: list[CuePoint]) -> ET.Element:
    attrs = {
        "TrackID": str(track_id),
        "Name": track.title or Path(track.filepath).stem,
        "Artist": track.artist or "",
        "Album": track.album or "",
        "Genre": track.genre or "",
        "Kind": Path(track.filepath).suffix.lstrip(".").upper() + " File",
        "Location": _file_url(track.filepath),
    }
    if track.bpm:
        attrs["AverageBpm"] = f"{track.bpm:.2f}"
    if track.key_camelot:
        attrs["Tonality"] = track.key_camelot
    if track.year:
        attrs["Year"] = str(track.year)
    if track.bitrate_declared:
        attrs["BitRate"] = str(int(track.bitrate_declared))
    if track.duration_sec:
        attrs["TotalTime"] = str(int(track.duration_sec))
    if track.comment:
        attrs["Comments"] = track.comment

    el = ET.Element("TRACK", attrs)

    for cue in cues:
        ET.SubElement(el, "POSITION_MARK", {
            "Name": cue.name,
            "Type": "0",
            "Start": f"{cue.position_sec:.3f}",
            "Num": str(cue.num if cue.hot else -1),
            "Red": str(cue.rgb[0]),
            "Green": str(cue.rgb[1]),
            "Blue": str(cue.rgb[2]),
        })
    return el


def _playlist_folder(name: str, node_type: str = "0") -> ET.Element:
    return ET.Element("NODE", {"Name": name, "Type": node_type, "Count": "0"})


def export_xml(
    tracks: list[Track],
    out_path: str,
    cues_by_path: Optional[dict[str, list[CuePoint]]] = None,
    playlists: Optional[list[dict]] = None,
) -> str:
    """Write a Rekordbox XML file listing *tracks*.

    Parameters
    ----------
    tracks:
        Tracks to include.
    out_path:
        Destination XML path (parent dirs are created).
    cues_by_path:
        Optional mapping of ``filepath -> [CuePoint, ...]``.
    playlists:
        Optional list of ``{"name": str, "tracks": [filepath, ...]}``.

    Returns the written path.
    """
    cues_by_path = cues_by_path or {}
    playlists = playlists or []

    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {
        "Name": "Decksmith",
        "Version": "0.1.0",
        "Company": "Decksmith",
    })

    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(tracks))})

    path_to_id: dict[str, int] = {}
    for i, t in enumerate(tracks, start=1):
        path_to_id[t.filepath] = i
        collection.append(_track_element(i, t, cues_by_path.get(t.filepath, [])))

    # Playlists node: a ROOT folder that contains one leaf per playlist.
    pls_root = ET.SubElement(root, "PLAYLISTS")
    root_folder = ET.SubElement(pls_root, "NODE", {
        "Type": "0",
        "Name": "ROOT",
        "Count": str(len(playlists)),
    })
    for pl in playlists:
        name = pl.get("name", "Playlist")
        pl_tracks = pl.get("tracks", [])
        node = ET.SubElement(root_folder, "NODE", {
            "Name": name,
            "Type": "1",
            "KeyType": "0",
            "Entries": str(len(pl_tracks)),
        })
        for fp in pl_tracks:
            tid = path_to_id.get(fp)
            if tid is not None:
                ET.SubElement(node, "TRACK", {"Key": str(tid)})

    # Pretty-print and write
    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    rough = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="UTF-8")
    out.write_bytes(pretty)
    return str(out)


def import_instructions() -> str:
    """Return the exact human import steps for the generated XML."""
    return (
        "1. Open Rekordbox.\n"
        "2. Preferences → View → Layout → enable 'rekordbox xml'.\n"
        "3. Preferences → Advanced → Database → Imported Library, pick this file.\n"
        "4. Your tracks now appear under Collection → rekordbox xml.\n"
        "5. Drag tracks/playlists into Collection to merge into your main library."
    )
