"""``decksmith`` default view — Library Dashboard.

Shows library stats, health score, issues, and a recommended next step.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.panel import Panel
from rich.table import Table
from rich import box

from decksmith.config import DecksmithConfig, load_config, expand_path
from decksmith.utils.ui import (
    console,
    print_warning,
    print_success,
    print_info,
    print_next_step,
)

SUPPORTED_FORMATS = {".mp3", ".flac", ".aiff", ".aif", ".wav", ".m4a"}


def _count_tracks(config: DecksmithConfig) -> int:
    """Count audio files across all library paths."""
    total = 0
    for lib_path in config.library_paths:
        if not lib_path.is_dir():
            continue
        for root, _dirs, files in os.walk(lib_path):
            for fname in files:
                if Path(fname).suffix.lower() in SUPPORTED_FORMATS:
                    total += 1
    return total


def _get_db_stats(config: DecksmithConfig) -> dict:
    """Pull stats from the tracking database if it exists."""
    stats = {
        "cleaned": 0,
        "analyzed": 0,
        "organized": 0,
        "cue_points": 0,
        "last_processed": None,
        "issues_bad_metadata": 0,
        "issues_fake_bitrate": 0,
        "issues_missing_art": 0,
    }
    db_path = config.db_path
    if not db_path.exists():
        return stats

    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        # Check if tracks table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'")
        if not cur.fetchone():
            conn.close()
            return stats

        cur.execute("SELECT COUNT(*) FROM tracks WHERE status = 'cleaned'")
        stats["cleaned"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM tracks WHERE bpm IS NOT NULL")
        stats["analyzed"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM tracks WHERE cue_points_json IS NOT NULL")
        stats["cue_points"] = cur.fetchone()[0]

        cur.execute("SELECT MAX(last_processed) FROM tracks")
        row = cur.fetchone()
        if row and row[0]:
            stats["last_processed"] = row[0]

        cur.execute("SELECT COUNT(*) FROM tracks WHERE status = 'pending'")
        stats["issues_bad_metadata"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM tracks WHERE bitrate_authentic = 0")
        stats["issues_fake_bitrate"] = cur.fetchone()[0]

        conn.close()
    except Exception:
        pass

    return stats


def _format_last_run(ts: Optional[str]) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.fromisoformat(ts)
        delta = datetime.now() - dt
        if delta.days > 0:
            return f"{delta.days} day{'s' if delta.days != 1 else ''} ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        minutes = delta.seconds // 60
        if minutes > 0:
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        return "just now"
    except Exception:
        return ts


def _compute_health_score(total: int, stats: dict) -> int:
    """Simple health score out of 100."""
    if total == 0:
        return 0
    score = 0.0
    # Weight: cleaned 40%, analyzed 30%, organized 15%, cue points 15%
    if total > 0:
        score += 40 * (stats["cleaned"] / total)
        score += 30 * (stats["analyzed"] / total)
        score += 15 * (stats["organized"] / total)
        score += 15 * (stats["cue_points"] / total)
    return int(score)


def _pct(part: int, total: int) -> str:
    if total == 0:
        return " 0%"
    return f"{100 * part / total:>3.0f}%"


def show_dashboard() -> None:
    """Display the library dashboard."""
    config = load_config()
    if config is None:
        print_warning("No config found. Run [bold]decksmith[/bold] to start setup.")
        return

    lib_display = ", ".join(config.library.paths) if config.library.paths else "not set"
    total = _count_tracks(config)
    stats = _get_db_stats(config)
    health = _compute_health_score(total, stats)
    last_run = _format_last_run(stats["last_processed"])

    console.print()
    console.print("  [bold]Decksmith \u2014 Library Dashboard[/bold]")
    console.print("  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    console.print(f"  Library:   {lib_display}")
    console.print(f"  Tracks:    {total:,}")
    console.print(f"  Last run:  {last_run}")
    console.print()

    # Health score
    if health >= 70:
        color = "green"
    elif health >= 40:
        color = "yellow"
    else:
        color = "red"
    console.print(f"  Health Score: [{color}]{health}/100[/{color}]")

    # Progress panel
    lines = []
    c = stats["cleaned"]
    a = stats["analyzed"]
    o = stats["organized"]
    q = stats["cue_points"]
    icon_c = "\u2713" if c > 0 else "\u25cb"
    icon_a = "\u2713" if a > 0 else "\u25cb"
    icon_o = "\u2713" if o > 0 else "\u25cb"
    icon_q = "\u2713" if q > 0 else "\u25cb"
    w = len(str(total)) if total else 1
    lines.append(f"  {icon_c} Cleaned      {c:>{w},} / {total:,}  ({_pct(c, total)})")
    lines.append(f"  {icon_a} Analyzed     {a:>{w},} / {total:,}  ({_pct(a, total)})")
    lines.append(f"  {icon_o} Organized    {o:>{w},} / {total:,}  ({_pct(o, total)})")
    lines.append(f"  {icon_q} Cue points   {q:>{w},} / {total:,}  ({_pct(q, total)})")

    console.print(Panel("\n".join(lines), expand=False))

    # Issues
    has_issues = False
    if stats["issues_bad_metadata"] > 0:
        print_warning(f"{stats['issues_bad_metadata']:,} tracks still have bad metadata")
        has_issues = True
    if stats["issues_fake_bitrate"] > 0:
        print_warning(f"{stats['issues_fake_bitrate']:,} tracks flagged as fake bitrate")
        has_issues = True
    if stats["issues_missing_art"] > 0:
        print_warning(f"{stats['issues_missing_art']:,} tracks missing cover art")
        has_issues = True

    if not has_issues and total > 0 and stats["cleaned"] == 0:
        print_info("Your library hasn't been cleaned yet.")

    # Next step recommendation — progressive disclosure:
    # clean first, then analyze, then review
    if total == 0:
        print_next_step("decksmith settings", "Check your library path and settings")
    elif stats["cleaned"] == 0:
        print_next_step("decksmith clean --preview", "See what Decksmith would fix")
    elif stats["cleaned"] < total:
        print_next_step("decksmith clean --auto", "Clean remaining tracks")
    elif stats["analyzed"] == 0:
        print_next_step("decksmith analyze", "Detect BPM, key, energy, and fake bitrates")
    elif stats["analyzed"] < total:
        print_next_step("decksmith analyze", "Analyze remaining tracks")
    else:
        print_next_step("decksmith clean --preview", "Review your library")

    console.print()
    console.print("  Run [bold]decksmith --help[/bold] for all commands.")
    console.print()
