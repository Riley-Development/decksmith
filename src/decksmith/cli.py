"""Typer CLI application for Decksmith.

Bare ``decksmith`` runs setup wizard (no config) or dashboard (config exists).
Never defaults to a help wall.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from decksmith.config import config_exists, load_config

app = typer.Typer(
    name="decksmith",
    help="Decksmith \u2014 Transform your DJ library into a clean, set-ready collection.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run setup wizard or show the dashboard."""
    # Startup dependency check — runs for every invocation.
    # Only warn about required tools (ffmpeg / ffprobe).  fpcalc is optional
    # and surfaced contextually (wizard, settings, fingerprinting flows).
    from decksmith.utils.audio import check_dependencies, print_dependency_status
    deps = check_dependencies()
    if not deps["ffmpeg"] or not deps["ffprobe"]:
        print_dependency_status(verbose=False)

    if ctx.invoked_subcommand is not None:
        return
    if not config_exists():
        from decksmith.setup_wizard import run_setup_wizard
        run_setup_wizard()
    else:
        from decksmith.dashboard import show_dashboard
        show_dashboard()


# ---------------------------------------------------------------------------
# decksmith clean
# ---------------------------------------------------------------------------

@app.command()
def clean(
    preview: bool = typer.Option(False, "--preview", help="Show diffs without writing."),
    auto: bool = typer.Option(False, "--auto", help="Apply all changes without prompting."),
    interactive: bool = typer.Option(False, "--interactive", help="Confirm each track individually."),
) -> None:
    """Clean metadata across your library."""
    from datetime import datetime

    from decksmith.config import load_config, DecksmithConfig
    from decksmith.metadata.cleaner import scan_library, clean_track, apply_changes
    from decksmith.db import init_db
    from decksmith.utils.ui import (
        console,
        print_diff_table,
        print_success,
        print_warning,
        print_info,
        print_write_summary,
        print_next_step,
        get_progress,
    )
    from rich.prompt import Prompt

    config = load_config()
    if config is None:
        print_warning("No config found. Run [bold]decksmith[/bold] to start setup.")
        raise typer.Exit(1)

    # Ensure DB exists for backup operations
    init_db(config)

    files = scan_library(config)
    if not files:
        print_info("No audio files found in your library.")
        return

    # Compute changes for all files
    results = []
    with get_progress("Scanning tracks...") as progress:
        task = progress.add_task("Analyzing metadata...", total=len(files))
        for fp in files:
            result = clean_track(fp, config)
            if result.needs_write:
                results.append(result)
            progress.advance(task)

    if not results:
        print_success("All tracks look clean! Nothing to change.")
        return

    console.print()
    print_info(f"Found {len(results)} track{'s' if len(results) != 1 else ''} with metadata to clean.")
    console.print()

    # --- Preview mode ---
    if preview:
        for r in results:
            console.print(f"  [bold]{Path(r.filepath).name}[/bold]")
            print_diff_table(r.to_diff_dicts())
            console.print()
        print_info(f"{len(results)} track{'s' if len(results) != 1 else ''} would be updated.")
        print_next_step("decksmith clean --auto", "Apply these changes")
        return

    # --- Auto mode ---
    if auto:
        # Spec: --auto skips confirmation. Print a non-blocking warning instead.
        print_warning(
            f"Modifying metadata on {len(results)} track{'s' if len(results) != 1 else ''}. "
            "Undo with: [bold]decksmith undo[/bold]"
        )

        batch_ts = datetime.now().isoformat()
        written = 0
        with get_progress("Cleaning tracks...") as progress:
            task = progress.add_task("Writing metadata...", total=len(results))
            for r in results:
                apply_changes(r.filepath, r, config, batch_ts=batch_ts)
                written += 1
                progress.advance(task)

        print_write_summary(written)
        print_next_step("decksmith status", "Check your library health")
        return

    # --- Interactive mode ---
    # [Y]es  [n]o  [e]dit manually  [s]kip all similar  [q]uit
    #
    # "Similar" means the track's change_signature matches — i.e. it
    # touches the same fields and removes the same kind of dirt.
    # Tracks auto-applied by [s] are counted and reported.
    from decksmith.metadata.cleaner import CleanResult, FieldChange

    batch_ts = datetime.now().isoformat()
    written = 0
    skipped = 0
    auto_apply_sigs: set[frozenset[str]] = set()  # signatures to auto-apply

    for i, r in enumerate(results, 1):
        # Auto-apply if this track's signature was previously [s]kipped
        if r.change_signature in auto_apply_sigs:
            apply_changes(r.filepath, r, config, batch_ts=batch_ts)
            written += 1
            continue

        console.print(f"\n  [bold][{i}/{len(results)}] {Path(r.filepath).name}[/bold]")
        print_diff_table(r.to_diff_dicts())

        choice = Prompt.ask(
            "  ? [bold][Y][/bold]es  [bold][n][/bold]o  [bold][e][/bold]dit  [bold][s][/bold]kip similar  [bold][q][/bold]uit",
            choices=["y", "n", "e", "s", "q"],
            default="y",
            console=console,
        )

        if choice == "y":
            apply_changes(r.filepath, r, config, batch_ts=batch_ts)
            written += 1
            print_success("Updated.")

        elif choice == "n":
            skipped += 1

        elif choice == "e":
            # Minimal Phase 1 manual edit: let the user override each
            # proposed value inline.  Show the proposed value as the
            # default so pressing Enter accepts it.
            edited_changes: list[FieldChange] = []
            for c in r.changes:
                new_val = Prompt.ask(
                    f"    {c.field} [dim](was: {c.before})[/dim]",
                    default=c.after,
                    console=console,
                )
                if new_val != c.before:
                    edited_changes.append(FieldChange(field=c.field, before=c.before, after=new_val))
            edited = CleanResult(filepath=r.filepath, changes=edited_changes)
            if edited.needs_write:
                apply_changes(r.filepath, edited, config, batch_ts=batch_ts)
                written += 1
                print_success("Updated (edited).")
            else:
                print_info("No effective changes after edit.")
                skipped += 1

        elif choice == "s":
            # Apply this track AND auto-apply all future tracks with the
            # same change signature (same fields, same kind of dirt).
            apply_changes(r.filepath, r, config, batch_ts=batch_ts)
            written += 1
            auto_apply_sigs.add(r.change_signature)
            # Count how many remaining tracks will match
            remaining_similar = sum(
                1 for future in results[i:]  # i is already 1-based, results[i:] is the rest
                if future.change_signature in auto_apply_sigs
            )
            if remaining_similar:
                print_info(f"Will auto-apply {remaining_similar} similar track{'s' if remaining_similar != 1 else ''}.")
            print_success("Updated.")

        elif choice == "q":
            skipped += len(results) - i
            break

    print_write_summary(written, skipped)
    if written > 0:
        print_next_step("decksmith status", "Check your library health")


# ---------------------------------------------------------------------------
# decksmith undo
# ---------------------------------------------------------------------------

@app.command()
def undo(
    filepath: Optional[str] = typer.Argument(None, help="Path to the track to restore."),
    last: bool = typer.Option(False, "--last", help="Restore the most recent batch of changes."),
) -> None:
    """Restore original tags from backup."""
    from decksmith.db import get_backup, get_last_batch, mark_restored, init_db
    from decksmith.utils.tag_io import write_tags, json_to_tags, read_tags
    from decksmith.utils.ui import (
        console,
        print_diff_table,
        print_success,
        print_warning,
        print_info,
        confirm,
    )

    config = load_config()
    if config is None:
        print_warning("No config found. Run [bold]decksmith[/bold] to start setup.")
        raise typer.Exit(1)

    init_db(config)

    if filepath:
        # Undo a specific file
        resolved = str(Path(filepath).expanduser().resolve())
        backed_up = get_backup(resolved, config)
        if backed_up is None:
            print_warning(f"No backup found for: {Path(filepath).name}")
            return

        current = read_tags(resolved)
        # Show what will be restored
        changes = []
        all_fields = set(list(backed_up.keys()) + list(current.keys()))
        for fld in sorted(all_fields):
            old = current.get(fld, "")
            new = backed_up.get(fld, "")
            if old != new:
                changes.append({"field": fld, "before": old, "after": new})

        if not changes:
            print_info("Current tags already match the backup. Nothing to restore.")
            return

        console.print(f"\n  [bold]Restoring: {Path(filepath).name}[/bold]")
        print_diff_table(changes, title="Undo — restoring original tags")

        if not confirm("Restore these original tags?"):
            print_info("Cancelled.")
            return

        write_tags(resolved, backed_up)
        mark_restored(resolved, config)
        print_success(f"Restored original tags for {Path(filepath).name}")
        return

    if last:
        # Undo the most recent batch (all tracks sharing the same timestamp)
        batch = get_last_batch(config=config)
        if not batch:
            print_info("No backups found. Nothing to undo.")
            return

        console.print(f"\n  Found {len(batch)} track{'s' if len(batch) != 1 else ''} from the last operation.")

        if not confirm(f"Restore original tags for {len(batch)} track{'s' if len(batch) != 1 else ''}?"):
            print_info("Cancelled.")
            return

        restored = 0
        for b in batch:
            fp = b["filepath"]
            tags = json_to_tags(b["original_tags_json"])
            write_tags(fp, tags)
            mark_restored(fp, config)
            restored += 1

        print_success(f"Restored {restored} track{'s' if restored != 1 else ''}.")
        return

    # No argument and no --last: show help
    print_info("Usage:")
    console.print("  [cyan]decksmith undo <filepath>[/cyan]   Restore a specific track")
    console.print("  [cyan]decksmith undo --last[/cyan]       Restore the most recent batch")


# ---------------------------------------------------------------------------
# decksmith analyze
# ---------------------------------------------------------------------------

@app.command()
def analyze(
    all_tracks: bool = typer.Option(False, "--all", help="Re-analyze every track, even previously analyzed ones."),
) -> None:
    """Analyze BPM, key, energy, and bitrate authenticity for your library."""
    from decksmith.config import load_config, expand_path
    from decksmith.metadata.cleaner import scan_library
    from decksmith.db import init_db, get_db, update_track_analysis
    from decksmith.utils.audio import check_dependencies, get_audio_info
    from decksmith.utils.ui import (
        console,
        print_success,
        print_warning,
        print_info,
        print_error,
        print_next_step,
        get_progress,
    )
    from rich.table import Table
    from rich import box

    config = load_config()
    if config is None:
        print_warning("No config found. Run [bold]decksmith[/bold] to start setup.")
        raise typer.Exit(1)

    # Check that librosa is available
    try:
        import librosa  # noqa: F401
    except ImportError:
        print_error(
            "Audio analysis requires librosa. "
            "Install with: [cyan]pip install decksmith\\[analysis][/cyan]"
        )
        raise typer.Exit(1)

    # Check ffmpeg AND ffprobe — both needed for format loading + bitrate info
    deps = check_dependencies()
    has_ffprobe = deps["ffprobe"]
    if not deps["ffmpeg"]:
        print_warning(
            "ffmpeg not found. Some audio formats may fail to load. "
            "Install: [cyan]brew install ffmpeg[/cyan]"
        )
    if not has_ffprobe:
        print_warning(
            "ffprobe not found. Bitrate authenticity cannot be assessed "
            "without declared bitrate info. Install: [cyan]brew install ffmpeg[/cyan]"
        )

    init_db(config)

    files = scan_library(config)
    if not files:
        print_info("No audio files found in your library.")
        return

    # Filter out already-analyzed tracks unless --all
    if not all_tracks:
        conn = get_db(config)
        cur = conn.cursor()
        cur.execute("SELECT filepath FROM tracks WHERE bpm IS NOT NULL")
        done = {row["filepath"] for row in cur.fetchall()}
        conn.close()
        files = [f for f in files if f not in done]
        if not files:
            print_success("All tracks already analyzed. Use [bold]--all[/bold] to re-analyze.")
            return

    console.print()
    print_info(f"Analyzing {len(files)} track{'s' if len(files) != 1 else ''}...")
    console.print()

    from decksmith.analyze import analyze_track

    results = []

    with get_progress("Analyzing...") as progress:
        task = progress.add_task("Analyzing audio...", total=len(files))
        for fp in files:
            # Get declared bitrate from ffprobe (None if ffprobe unavailable)
            declared_kbps = None
            if has_ffprobe:
                info = get_audio_info(fp)
                if info and info.get("bit_rate"):
                    try:
                        declared_kbps = int(float(info["bit_rate"])) // 1000
                    except (ValueError, TypeError):
                        pass

            ar = analyze_track(
                fp,
                declared_kbps=declared_kbps,
                analysis_config=config.analysis if config.analysis else None,
            )
            results.append(ar)

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

            progress.advance(task)

    # --- Summary: full / partial / failed ---
    full_count = sum(1 for r in results if r.ok and not r.partial)
    partial_count = sum(1 for r in results if r.partial)
    fail_count = sum(1 for r in results if r.failed)
    ok_count = full_count + partial_count
    console.print()
    print_success(f"Analyzed {ok_count} track{'s' if ok_count != 1 else ''}.")
    if partial_count:
        print_warning(f"{partial_count} with partial results (see warnings in report).")
    if fail_count:
        print_warning(f"{fail_count} track{'s' if fail_count != 1 else ''} could not be analyzed.")

    # --- Bitrate summary — three categories, all computed from results ---
    flagged = [r for r in results if r.ok and r.bitrate_authentic is False]
    verified = [r for r in results if r.ok and r.bitrate_authentic is True]
    br_skipped = [r for r in results if r.ok and r.bitrate_authentic is None]

    if flagged:
        console.print()
        print_warning(f"{len(flagged)} track{'s' if len(flagged) != 1 else ''} flagged as fake bitrate:")
        console.print()

        tbl = Table(box=box.ROUNDED, show_lines=False)
        tbl.add_column("Track", style="bold", max_width=40)
        tbl.add_column("Declared", justify="right")
        tbl.add_column("Status", justify="center")
        tbl.add_column("Explanation")

        for ar in flagged:
            tbl.add_row(
                Path(ar.filepath).name,
                f"{ar.bitrate_declared}kbps",
                "[red]\u2717 fake[/red]",
                ar.bitrate_explanation or "",
            )
        console.print(tbl)

    if verified:
        print_success(f"{len(verified)} track{'s' if len(verified) != 1 else ''} verified authentic bitrate.")
    if br_skipped:
        print_info(
            f"{len(br_skipped)} track{'s' if len(br_skipped) != 1 else ''} "
            "could not be assessed for bitrate"
            + (" (ffprobe unavailable)." if not has_ffprobe else ".")
        )

    # --- Quick stats ---
    analyzed_ok = [r for r in results if r.ok]
    if analyzed_ok:
        bpms = [r.bpm for r in analyzed_ok if r.bpm]
        energies = [r.energy for r in analyzed_ok if r.energy]
        console.print()
        if bpms:
            console.print(f"  BPM range:    {min(bpms):.0f} \u2013 {max(bpms):.0f}")
        if energies:
            console.print(f"  Energy range: {min(energies)} \u2013 {max(energies)}")
        keys_found = {r.camelot for r in analyzed_ok if r.camelot}
        if keys_found:
            console.print(f"  Keys found:   {len(keys_found)} distinct")

    # --- HTML report ---
    from decksmith.analyze.report import generate_report
    reports_dir = expand_path(config.output.reports_path)
    report_path = generate_report(results, Path(reports_dir) / "analysis_report.html")
    console.print()
    print_info(f"Report saved to [bold]{report_path}[/bold]")

    print_next_step("decksmith status", "See your updated library health")
    console.print()


# ---------------------------------------------------------------------------
# decksmith status
# ---------------------------------------------------------------------------

@app.command()
def status() -> None:
    """Show library status (same as the dashboard)."""
    from decksmith.dashboard import show_dashboard
    show_dashboard()


# ---------------------------------------------------------------------------
# decksmith settings
# ---------------------------------------------------------------------------

@app.command()
def settings(
    key: Optional[str] = typer.Option(None, "--key", help="Edit a specific key, or 'all' for all keys."),
) -> None:
    """View and edit configuration and API keys."""
    from decksmith.settings import show_settings
    show_settings(key=key)


# ---------------------------------------------------------------------------
# Helpers for the Phase 3-5 commands
# ---------------------------------------------------------------------------

def _require_config():
    """Load config or exit with a friendly message."""
    from decksmith.utils.ui import print_warning
    cfg = load_config()
    if cfg is None:
        print_warning("No config found. Run [bold]decksmith[/bold] to start setup.")
        raise typer.Exit(1)
    return cfg


def _library_tracks(config) -> list:
    """Build Track models from DB rows, enriched with tag data."""
    from decksmith.db import init_db, get_db
    from decksmith.utils.tag_io import read_tags
    from decksmith.models import Track
    init_db(config)
    conn = get_db(config)
    cur = conn.cursor()
    cur.execute("SELECT * FROM tracks")
    rows = cur.fetchall()
    conn.close()
    out: list[Track] = []
    for r in rows:
        tags = read_tags(r["filepath"])
        out.append(Track(
            filepath=r["filepath"],
            title=tags.get("title", ""),
            artist=tags.get("artist", ""),
            album=tags.get("album", ""),
            genre=tags.get("genre", ""),
            year=tags.get("year", ""),
            bpm=r["bpm"],
            key_camelot=r["key_camelot"],
            energy=r["energy"],
            bitrate_declared=r["bitrate_declared"],
            bitrate_authentic=r["bitrate_authentic"],
        ))
    return out


# ---------------------------------------------------------------------------
# decksmith cue
# ---------------------------------------------------------------------------

@app.command()
def cue(
    preview: bool = typer.Option(False, "--preview", help="Show detected cue points without writing."),
    export: bool = typer.Option(False, "--export", help="Write a Rekordbox XML with cue points."),
    limit: int = typer.Option(0, "--limit", help="Only process the first N tracks (0 = all)."),
) -> None:
    """Detect hot cues and write them into a Rekordbox XML."""
    import json as _json

    from decksmith.config import expand_path
    from decksmith.metadata.cleaner import scan_library
    from decksmith.db import init_db, get_db
    from decksmith.rekordbox.cuepoints import detect_cues, cue_strategy_blurb, DEFAULT_SLOTS
    from decksmith.rekordbox.xml_export import export_xml, import_instructions
    from decksmith.models import CuePoint
    from decksmith.utils.ui import (
        console, print_info, print_success, print_warning,
        get_progress, print_next_step,
    )

    config = _require_config()
    init_db(config)

    files = scan_library(config)
    if limit:
        files = files[:limit]
    if not files:
        print_info("No audio files found.")
        return

    # Honour the rekordbox.cue_points config block — slot list, max cues,
    # skip-if-cues-exist — so users can customise the 8-slot layout from
    # config.yaml without touching code.
    rb_cfg = config.rekordbox or {}
    cue_cfg = rb_cfg.get("cue_points") or {}
    slot_config = cue_cfg.get("slots") or DEFAULT_SLOTS
    max_cues = int(cue_cfg.get("max_cues", 8))
    skip_if_exists = bool(cue_cfg.get("skip_if_cues_exist", False))

    strategy_by_num = {s["num"]: s.get("strategy", "") for s in slot_config}

    # If skip_if_cues_exist is set, filter out tracks that already have cues
    # in the DB.  Callers who really want to re-detect can clear cue_points_json
    # manually or run with skip_if_cues_exist=False in config.
    files_to_process = files
    if skip_if_exists:
        conn = get_db(config)
        cur = conn.cursor()
        cur.execute(
            "SELECT filepath FROM tracks WHERE cue_points_json IS NOT NULL"
        )
        existing = {row["filepath"] for row in cur.fetchall()}
        conn.close()
        files_to_process = [f for f in files if f not in existing]
        if len(files_to_process) < len(files):
            print_info(
                f"Skipping {len(files) - len(files_to_process)} tracks that already have cues."
            )

    results = []
    dep_error: Optional[str] = None
    with get_progress("Detecting cues...") as progress:
        task = progress.add_task("Analysing audio...", total=len(files_to_process))
        for fp in files_to_process:
            r = detect_cues(fp, slot_config=slot_config, max_cues=max_cues)
            results.append(r)
            if r.error and "librosa" in r.error and dep_error is None:
                dep_error = r.error
            progress.advance(task)

    if dep_error:
        print_warning(dep_error)
        return

    ok = [r for r in results if r.ok]
    print_success(f"Generated cues for {len(ok)} track{'s' if len(ok) != 1 else ''}.")

    if preview:
        for r in ok[:10]:  # cap preview to 10 to keep output readable
            console.print(f"\n  [bold]{Path(r.filepath).name}[/bold]"
                          f"  [dim]{r.duration_sec:.0f}s · {r.bpm:.0f} BPM[/dim]"
                          if r.bpm else f"\n  [bold]{Path(r.filepath).name}[/bold]")
            for c in r.cues:
                strat = strategy_by_num.get(c.num, "")
                console.print(f"    [cyan]{c.num}[/cyan] {c.name:12} "
                              f"[yellow]{c.position_sec:7.2f}s[/yellow]  "
                              f"[dim]{cue_strategy_blurb(strat)}[/dim]")
        if len(ok) > 10:
            console.print(f"\n  [dim]… and {len(ok) - 10} more (use --export to write them all)[/dim]")
        print_next_step("decksmith cue --export", "Write these cue points to a Rekordbox XML")
        return

    # Persist cues to DB
    conn = get_db(config)
    cur = conn.cursor()
    for r in ok:
        cur.execute("SELECT id FROM tracks WHERE filepath = ?", (r.filepath,))
        row = cur.fetchone()
        if row is None:
            from decksmith.db import file_hash
            cur.execute(
                "INSERT INTO tracks (filepath, file_hash, cue_points_json, status) "
                "VALUES (?, ?, ?, 'cued')",
                (r.filepath, file_hash(r.filepath),
                 _json.dumps([c.model_dump() for c in r.cues])),
            )
        else:
            cur.execute(
                "UPDATE tracks SET cue_points_json = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (_json.dumps([c.model_dump() for c in r.cues]), row["id"]),
            )
    conn.commit()
    conn.close()

    if export:
        tracks = _library_tracks(config)
        cues_by_path = {r.filepath: r.cues for r in ok}
        out_path = config.output.rekordbox_xml_out or "~/.decksmith/rekordbox_import.xml"
        written = export_xml(tracks, expand_path(out_path), cues_by_path=cues_by_path)
        print_success(f"Rekordbox XML written to [bold]{written}[/bold]")
        console.print()
        console.print("  [bold]To import:[/bold]")
        for line in import_instructions().splitlines():
            console.print(f"    {line}")
    else:
        print_next_step("decksmith cue --export", "Write a Rekordbox XML with these cue points")


# ---------------------------------------------------------------------------
# decksmith organize
# ---------------------------------------------------------------------------

@app.command()
def organize(
    preview: bool = typer.Option(False, "--preview", help="Show the plan without moving files."),
    auto: bool = typer.Option(False, "--auto", help="Apply without confirmation."),
) -> None:
    """Sort the library into genre/BPM folders."""
    from decksmith.config import expand_path
    from decksmith.rekordbox.folders import plan_moves
    from decksmith.utils.fs import move_file
    from decksmith.utils.ui import (
        console, print_info, print_success, print_warning,
        print_next_step, get_progress, confirm_destructive,
    )
    from rich.table import Table
    from rich import box

    config = _require_config()
    target = config.output.organized_path
    if not target:
        print_warning("No organized_path set. Edit config via [bold]decksmith settings[/bold].")
        raise typer.Exit(1)
    target = expand_path(target)

    tracks = _library_tracks(config)
    if not tracks:
        print_info("No tracks tracked yet. Run [bold]decksmith analyze[/bold] first for the best results.")
        return

    plan = plan_moves(tracks, config, target)

    # Skip no-ops (src already in target folder)
    effective = [p for p in plan if p["src"] != p["dst"]]
    if not effective:
        print_success("Library is already organized.")
        return

    # Preview table (first 20 + summary)
    from collections import Counter
    folder_counts = Counter(p["folder"] for p in effective)
    console.print()
    print_info(f"Would move {len(effective)} track{'s' if len(effective) != 1 else ''} into {len(folder_counts)} folder{'s' if len(folder_counts) != 1 else ''}.")
    tbl = Table(box=box.ROUNDED, show_lines=False)
    tbl.add_column("Folder", style="cyan")
    tbl.add_column("Count", justify="right")
    for folder, count in sorted(folder_counts.items(), key=lambda x: -x[1]):
        tbl.add_row(folder, str(count))
    console.print(tbl)

    if preview:
        print_next_step("decksmith organize --auto", "Apply the organisation plan")
        return

    if not auto and not confirm_destructive(
        f"Move {len(effective)} files into {target}?"
    ):
        print_info("Cancelled.")
        return

    # Update the DB after each successful move so subsequent commands
    # (cue, export-xml, undo) point at the new filepath rather than stale
    # pre-move paths.
    from decksmith.db import get_db, init_db
    init_db(config)
    conn = get_db(config)
    cur = conn.cursor()

    moved = 0
    with get_progress("Organizing...") as progress:
        task = progress.add_task("Moving files...", total=len(effective))
        for p in effective:
            try:
                final_dst = move_file(p["src"], p["dst"])
                cur.execute(
                    "UPDATE tracks SET filepath = ?, updated_at = datetime('now') "
                    "WHERE filepath = ?",
                    (final_dst, p["src"]),
                )
                moved += 1
            except Exception as exc:
                print_warning(f"Could not move {Path(p['src']).name}: {exc}")
            progress.advance(task)
    conn.commit()
    conn.close()

    print_success(f"Organized {moved} tracks into {target}.")
    print_next_step("decksmith cue --export", "Generate cues and an import XML")


# ---------------------------------------------------------------------------
# decksmith export-xml
# ---------------------------------------------------------------------------

@app.command("export-xml")
def export_xml_cmd(
    out: Optional[str] = typer.Option(None, "--out", help="Output path (default from config)."),
) -> None:
    """Write a full Rekordbox XML of the tracked library."""
    import json as _json
    from decksmith.config import expand_path
    from decksmith.db import get_db, init_db
    from decksmith.models import CuePoint
    from decksmith.rekordbox.xml_export import export_xml, import_instructions
    from decksmith.utils.ui import console, print_success, print_warning, print_info

    config = _require_config()
    init_db(config)
    tracks = _library_tracks(config)
    if not tracks:
        print_warning("No tracks tracked yet. Run [bold]decksmith analyze[/bold] first.")
        return

    # Load cue points from DB
    cues_by_path: dict[str, list[CuePoint]] = {}
    conn = get_db(config)
    cur = conn.cursor()
    cur.execute("SELECT filepath, cue_points_json FROM tracks WHERE cue_points_json IS NOT NULL")
    for row in cur.fetchall():
        try:
            cues_by_path[row["filepath"]] = [
                CuePoint(**c) for c in _json.loads(row["cue_points_json"])
            ]
        except Exception:
            continue
    conn.close()

    out_path = expand_path(out or config.output.rekordbox_xml_out
                           or "~/.decksmith/rekordbox_import.xml")
    written = export_xml(tracks, out_path, cues_by_path=cues_by_path)
    print_success(f"Rekordbox XML written to [bold]{written}[/bold]")
    print_info(f"Included {len(tracks)} tracks, {len(cues_by_path)} with cue points.")
    console.print()
    console.print("  [bold]To import:[/bold]")
    for line in import_instructions().splitlines():
        console.print(f"    {line}")


# ---------------------------------------------------------------------------
# decksmith setbuild
# ---------------------------------------------------------------------------

@app.command()
def setbuild(
    prompt: str = typer.Argument(..., help='Natural-language set description, e.g. "90 min tech house set".'),
    no_llm: bool = typer.Option(False, "--no-llm", help="Force the local fallback (skip Groq)."),
) -> None:
    """Build a DJ set from your library."""
    from decksmith.setbuilder.builder import build_set
    from decksmith.utils.api_clients import is_key_configured
    from decksmith.utils.ui import (
        console, print_info, print_success, print_warning,
        print_key_missing, print_next_step,
    )
    from rich.table import Table
    from rich import box

    config = _require_config()

    tracks = _library_tracks(config)
    if not tracks:
        print_warning(
            "Your library has no analyzed tracks yet. Run [bold]decksmith analyze[/bold] first."
        )
        return

    use_llm = not no_llm
    if use_llm and not is_key_configured(config, "groq"):
        # Per the spec, setbuild "requires" Groq — the graceful path is to
        # show the key-missing message and suggest --no-llm for the local
        # fallback, not to silently run a lesser version.
        print_key_missing("groq")
        console.print()
        print_info(
            "Or run without AI: [cyan]decksmith setbuild "
            f"\"{prompt}\" --no-llm[/cyan]"
        )
        return

    result = build_set(prompt, tracks, config, use_llm=use_llm)
    if not result.tracks:
        print_warning("No tracks matched your prompt. Try a different genre or BPM range.")
        return

    console.print()
    label = "AI-assisted" if result.used_llm else "local algorithm"
    console.print(
        f"  [bold]{result.prompt}[/bold]  "
        f"[dim]({result.target_length_min} min · {result.energy_curve_name} · {label})[/dim]"
    )
    console.print()

    tbl = Table(box=box.ROUNDED, show_lines=False)
    tbl.add_column("#", justify="right")
    tbl.add_column("Artist — Title", style="bold")
    tbl.add_column("BPM", justify="right")
    tbl.add_column("Key")
    tbl.add_column("Energy", justify="center")
    tbl.add_column("Transition", style="dim")
    for i, st in enumerate(result.tracks, 1):
        t = st.track
        bars = "█" * (t.energy or 0) + "·" * (10 - (t.energy or 0))
        tbl.add_row(
            str(i),
            t.display,
            f"{t.bpm:.0f}" if t.bpm else "?",
            t.key_camelot or "?",
            f"[green]{bars}[/green]",
            st.transition_note,
        )
    console.print(tbl)

    if result.warning:
        console.print()
        print_warning(result.warning)

    print_next_step("decksmith export-xml", "Export a Rekordbox XML containing the full library")


# ---------------------------------------------------------------------------
# decksmith fingerprint
# ---------------------------------------------------------------------------

@app.command()
def fingerprint(
    limit: int = typer.Option(0, "--limit", help="Only fingerprint the first N unknown tracks (0 = all)."),
) -> None:
    """Identify tracks with missing/bad tags via AcoustID."""
    from decksmith.metadata.fingerprint import identify_track, fpcalc_available
    from decksmith.metadata.cleaner import scan_library
    from decksmith.utils.api_clients import is_key_configured
    from decksmith.utils.ui import (
        console, print_info, print_success, print_warning,
        print_key_missing, get_progress,
    )
    from decksmith.utils.tag_io import read_tags

    config = _require_config()

    if not is_key_configured(config, "acoustid"):
        print_key_missing("acoustid")
        return
    if not fpcalc_available():
        print_warning(
            "fpcalc (Chromaprint) is not installed. "
            "Install: [cyan]brew install chromaprint[/cyan] (macOS) or "
            "[cyan]apt install libchromaprint-tools[/cyan] (Linux)."
        )
        return

    files = scan_library(config)
    # Prioritise files whose current tags are missing title/artist
    unknown = []
    for fp in files:
        tags = read_tags(fp)
        if not (tags.get("title") and tags.get("artist")):
            unknown.append(fp)
    if limit:
        unknown = unknown[:limit]
    if not unknown:
        print_success("All tracks already have artist + title. Nothing to fingerprint.")
        return

    print_info(f"Fingerprinting {len(unknown)} track{'s' if len(unknown) != 1 else ''}...")
    hits = 0
    reasons_seen: dict[str, int] = {}
    with get_progress("Fingerprinting...") as progress:
        task = progress.add_task("Identifying...", total=len(unknown))
        for fp in unknown:
            r = identify_track(fp, config)
            if r.ok:
                hits += 1
                console.print(
                    f"  [green]✓[/green] {Path(fp).name}  →  "
                    f"{r.matched_artist} — {r.matched_title}  "
                    f"[dim](score {r.matched_score:.2f})[/dim]"
                )
            elif r.reason:
                reasons_seen[r.reason] = reasons_seen.get(r.reason, 0) + 1
            progress.advance(task)

    print_success(f"Identified {hits} of {len(unknown)} tracks.")
    # Surface aggregated failure reasons so the user isn't left guessing
    # why 0 matched (e.g. "pyacoustid not installed", "No match found").
    if reasons_seen:
        for reason, count in sorted(reasons_seen.items(), key=lambda x: -x[1]):
            print_info(f"{count} — {reason}")
    if hits < len(unknown):
        print_info("Tracks without a match are left unchanged. Try [bold]decksmith clean[/bold] to tidy what's there.")


# ---------------------------------------------------------------------------
# decksmith enrich
# ---------------------------------------------------------------------------

@app.command()
def enrich() -> None:
    """Fill in missing genre/year/label from Discogs or MusicBrainz."""
    from datetime import datetime

    from decksmith.metadata.enricher import enrich_track
    from decksmith.metadata.cleaner import scan_library
    from decksmith.utils.api_clients import is_key_configured
    from decksmith.utils.tag_io import read_tags, write_tags, tags_to_json
    from decksmith.utils.ui import (
        console, print_info, print_success, print_warning, print_write_summary,
        print_key_missing, get_progress,
    )
    from decksmith.db import init_db, backup_tags, file_hash

    config = _require_config()
    init_db(config)

    if not is_key_configured(config, "discogs"):
        print_key_missing("discogs")
        print_info("Falling back to MusicBrainz (slower, fewer fields).")

    files = scan_library(config)
    updated = 0
    skipped = 0
    batch_ts = datetime.now().isoformat()
    with get_progress("Enriching...") as progress:
        task = progress.add_task("Looking up...", total=len(files))
        for fp in files:
            tags = read_tags(fp)
            artist = tags.get("artist", "")
            title = tags.get("title", "")
            if not (artist and title):
                skipped += 1
                progress.advance(task)
                continue
            r = enrich_track(fp, artist, title, config)
            if r.ok:
                new = dict(tags)
                if r.genre and not tags.get("genre"):
                    new["genre"] = r.genre
                if r.year and not tags.get("year"):
                    new["year"] = r.year
                if new != tags:
                    # Back up original tags to SQLite before writing so
                    # `decksmith undo` can roll this back (hard spec rule).
                    backup_tags(
                        fp, tags_to_json(tags),
                        fhash=file_hash(fp), config=config, batch_ts=batch_ts,
                    )
                    write_tags(fp, new)
                    updated += 1
            progress.advance(task)

    if updated:
        print_write_summary(updated, skipped)
    else:
        print_success(f"Enriched {updated} track{'s' if updated != 1 else ''}.")
    if skipped:
        print_info(f"{skipped} skipped (missing artist/title — try [bold]decksmith fingerprint[/bold]).")


# ---------------------------------------------------------------------------
# decksmith artwork
# ---------------------------------------------------------------------------

@app.command()
def artwork(
    min_size: int = typer.Option(600, "--min-size", help="Minimum image resolution in px."),
) -> None:
    """Download and embed missing cover art."""
    from datetime import datetime

    from decksmith.metadata.artwork import fetch_artwork, embed_artwork
    from decksmith.metadata.cleaner import scan_library
    from decksmith.utils.tag_io import read_tags, tags_to_json
    from decksmith.utils.ui import (
        console, print_info, print_success, get_progress, print_undo_reminder,
    )
    from decksmith.db import init_db, backup_tags, file_hash

    config = _require_config()
    init_db(config)

    files = scan_library(config)
    hits = 0
    batch_ts = datetime.now().isoformat()
    with get_progress("Downloading artwork...") as progress:
        task = progress.add_task("Searching...", total=len(files))
        for fp in files:
            tags = read_tags(fp)
            artist = tags.get("artist", "")
            title = tags.get("title", "")
            if not (artist and title):
                progress.advance(task)
                continue
            r = fetch_artwork(fp, artist, title, config, min_size=min_size)
            if r.ok and r.image_bytes:
                # Artwork writes touch the audio file; back up tags first so
                # `decksmith undo` can reverse metadata changes on the same
                # files even if cover art embedding itself isn't reversed.
                backup_tags(
                    fp, tags_to_json(tags),
                    fhash=file_hash(fp), config=config, batch_ts=batch_ts,
                )
                if embed_artwork(fp, r.image_bytes, r.image_mime):
                    hits += 1
                    console.print(
                        f"  [green]✓[/green] {Path(fp).name}  "
                        f"[dim]{r.resolution}px via {r.source}[/dim]"
                    )
            progress.advance(task)

    print_success(f"Embedded artwork on {hits} track{'s' if hits != 1 else ''}.")
    if hits:
        print_undo_reminder()


# ---------------------------------------------------------------------------
# decksmith discover
# ---------------------------------------------------------------------------

@app.command()
def discover(
    gaps_flag: bool = typer.Option(False, "--gaps", help="Show under-stocked genre/BPM buckets."),
    seed: Optional[str] = typer.Option(None, "--seed", help="Seed artist for recommendations."),
) -> None:
    """Find holes in your library or get recommendations."""
    from decksmith.discover.gaps import find_gaps
    from decksmith.discover.listenbrainz import recommend_tracks, similar_artists
    from decksmith.utils.ui import console, print_info, print_success, print_warning
    from rich.table import Table
    from rich import box

    config = _require_config()

    if gaps_flag:
        tracks = _library_tracks(config)
        if not tracks:
            print_warning("No tracks analyzed yet. Run [bold]decksmith analyze[/bold] first.")
            return
        gaps = find_gaps(tracks, config)
        if not gaps:
            print_success("Your library looks balanced — no obvious gaps.")
            return
        tbl = Table(box=box.ROUNDED, title="Library gaps")
        tbl.add_column("Bucket", style="cyan")
        tbl.add_column("Have", justify="right")
        tbl.add_column("Target", justify="right")
        tbl.add_column("Deficit", justify="right", style="yellow")
        for g in gaps[:20]:
            tbl.add_row(g.bucket, str(g.count), str(g.target), str(g.deficit))
        console.print(tbl)
        return

    if seed:
        recs = recommend_tracks(config, [seed])
        if not recs:
            print_warning(f"No similar artists found for '{seed}'.")
            return
        console.print(f"\n  [bold]Similar to {seed}:[/bold]")
        for r in recs:
            console.print(f"    • {r.artist}")
        return

    print_info("Usage: [cyan]decksmith discover --gaps[/cyan] or [cyan]decksmith discover --seed 'Artist'[/cyan]")


# ---------------------------------------------------------------------------
# decksmith run
# ---------------------------------------------------------------------------

@app.command()
def run(
    skip_clean: bool = typer.Option(False, "--skip-clean"),
    skip_analyze: bool = typer.Option(False, "--skip-analyze"),
    skip_cue: bool = typer.Option(False, "--skip-cue"),
    skip_export: bool = typer.Option(False, "--skip-export"),
) -> None:
    """Run the full pipeline: clean, analyze, cue, and export a Rekordbox XML."""
    from decksmith.pipeline import run_pipeline
    from decksmith.utils.ui import console, print_success, print_info, print_warning, confirm_destructive

    config = _require_config()
    if not confirm_destructive(
        "This will clean tags, analyse audio, generate cues, and write a Rekordbox XML. "
        "Backups are stored in SQLite."
    ):
        print_info("Cancelled.")
        return

    res = run_pipeline(
        config,
        do_clean=not skip_clean,
        do_analyze=not skip_analyze,
        do_cue=not skip_cue,
        do_export=not skip_export,
    )

    console.print()
    print_success(
        f"Pipeline complete: {res.cleaned} cleaned, {res.analyzed} analyzed, {res.cued} cued."
    )
    if res.exported_xml:
        print_success(f"Rekordbox XML: [bold]{res.exported_xml}[/bold]")
    for w in res.warnings:
        print_warning(w)
