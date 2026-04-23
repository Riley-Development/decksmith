"""End-to-end pipeline: clean → analyze → cue → export.

Exposed as ``decksmith run`` so a user can go from raw download to a
Rekordbox-ready XML in a single command.  Each stage is skippable; each
prints its own summary.  All writes honour the existing backup + undo
contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from decksmith.config import DecksmithConfig


@dataclass
class PipelineResult:
    cleaned: int = 0
    analyzed: int = 0
    cued: int = 0
    exported_xml: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def run_pipeline(
    config: DecksmithConfig,
    *,
    do_clean: bool = True,
    do_analyze: bool = True,
    do_cue: bool = True,
    do_export: bool = True,
) -> PipelineResult:
    """Run each stage in order.

    This is deliberately thin — the real work is done by the individual
    modules so the CLI subcommands and the pipeline share the same code
    paths.
    """
    from decksmith.metadata.cleaner import scan_library, clean_track, apply_changes
    from decksmith.db import init_db, get_db, update_track_analysis, file_hash
    from decksmith.utils.audio import check_dependencies, get_audio_info
    from datetime import datetime

    res = PipelineResult()
    init_db(config)
    files = scan_library(config)
    if not files:
        res.warnings.append("No audio files in configured library paths.")
        return res

    # --- Clean ---
    if do_clean:
        batch_ts = datetime.now().isoformat()
        for fp in files:
            r = clean_track(fp, config)
            if r.needs_write:
                apply_changes(fp, r, config, batch_ts=batch_ts)
                res.cleaned += 1

    # --- Analyze ---
    if do_analyze:
        try:
            import librosa  # noqa: F401
        except ImportError:
            res.warnings.append("librosa not installed — skipped analysis.")
        else:
            deps = check_dependencies()
            has_ffprobe = deps["ffprobe"]
            from decksmith.analyze import analyze_track
            for fp in files:
                declared = None
                if has_ffprobe:
                    info = get_audio_info(fp)
                    if info and info.get("bit_rate"):
                        try:
                            declared = int(float(info["bit_rate"])) // 1000
                        except (TypeError, ValueError):
                            pass
                ar = analyze_track(fp, declared_kbps=declared,
                                   analysis_config=config.analysis)
                if ar.ok:
                    update_track_analysis(
                        fp,
                        bpm=ar.bpm,
                        key_camelot=ar.camelot,
                        energy=ar.energy,
                        bitrate_declared=ar.bitrate_declared,
                        bitrate_authentic=ar.bitrate_authentic,
                        bitrate_confidence=ar.bitrate_confidence,
                        confidence=ar.bpm_confidence,
                        config=config,
                    )
                    res.analyzed += 1

    # --- Cue points ---
    if do_cue:
        from decksmith.rekordbox.cuepoints import detect_cues
        import json as _json
        conn = get_db(config)
        cur = conn.cursor()
        for fp in files:
            cr = detect_cues(fp)
            if cr.ok:
                cues_json = _json.dumps([c.model_dump() for c in cr.cues])
                cur.execute(
                    "UPDATE tracks SET cue_points_json = ?, updated_at = datetime('now') "
                    "WHERE filepath = ?",
                    (cues_json, fp),
                )
                res.cued += 1
        conn.commit()
        conn.close()

    # --- Export XML ---
    if do_export:
        from decksmith.rekordbox.xml_export import export_xml
        from decksmith.models import Track, CuePoint
        import json as _json
        conn = get_db(config)
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracks")
        rows = cur.fetchall()
        tracks: list[Track] = []
        cues_by_path: dict[str, list[CuePoint]] = {}
        for row in rows:
            # Pull title/artist from file tags for correctness
            from decksmith.utils.tag_io import read_tags
            tags = read_tags(row["filepath"])
            tracks.append(Track(
                filepath=row["filepath"],
                title=tags.get("title", ""),
                artist=tags.get("artist", ""),
                album=tags.get("album", ""),
                genre=tags.get("genre", ""),
                bpm=row["bpm"],
                key_camelot=row["key_camelot"],
                energy=row["energy"],
                bitrate_declared=row["bitrate_declared"],
                bitrate_authentic=row["bitrate_authentic"],
            ))
            if row["cue_points_json"]:
                try:
                    cues_by_path[row["filepath"]] = [
                        CuePoint(**c) for c in _json.loads(row["cue_points_json"])
                    ]
                except Exception:
                    pass
        conn.close()

        if tracks:
            out_path = config.output.rekordbox_xml_out or "~/.decksmith/rekordbox_import.xml"
            from decksmith.config import expand_path
            res.exported_xml = export_xml(
                tracks,
                expand_path(out_path),
                cues_by_path=cues_by_path,
            )

    return res
