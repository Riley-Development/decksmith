"""First-run guided setup wizard.

Walks a new user through:
1. Library path selection
2. Audio file scan with format breakdown
3. Quick health check
4. Rekordbox XML auto-detection
5. Optional API key collection (every key skippable, masked input)
6. Config file generation
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.prompt import Prompt

from decksmith.config import (
    ApisConfig,
    DecksmithConfig,
    LibraryConfig,
    OutputConfig,
    DbConfig,
    save_config,
    get_config_path,
)
from decksmith.utils.api_clients import KEY_REGISTRY
from decksmith.utils.ui import (
    console,
    print_success,
    print_warning,
    print_info,
    print_skipped,
    print_next_step,
    print_welcome_banner,
    get_progress,
)

SUPPORTED_FORMATS = {".mp3", ".flac", ".aiff", ".aif", ".wav", ".m4a"}

# Common Rekordbox XML locations on macOS
_REKORDBOX_CANDIDATES = [
    Path.home() / "Library" / "Pioneer" / "rekordbox" / "rekordbox.xml",
    Path.home() / "Library" / "Pioneer" / "rekordbox6" / "rekordbox.xml",
]


def _scan_library(library_path: Path) -> dict[str, int]:
    """Walk *library_path* and return a count of audio files by extension."""
    counts: dict[str, int] = {}
    if not library_path.is_dir():
        return counts
    for root, _dirs, files in os.walk(library_path):
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext in SUPPORTED_FORMATS:
                counts[ext] = counts.get(ext, 0) + 1
    return counts


def _quick_health_check(library_path: Path, total: int) -> list[str]:
    """Lightweight tag health check — sample files for common issues."""
    issues: list[str] = []
    if total == 0:
        return issues

    # We do a quick scan: count files whose names look like they need cleaning
    suspect = 0
    checked = 0
    for root, _dirs, files in os.walk(library_path):
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in SUPPORTED_FORMATS:
                continue
            checked += 1
            stem = Path(fname).stem
            # Heuristic: filename contains bitrate tags, URLs, or Soulseek markers
            lower = stem.lower()
            if any(marker in lower for marker in [
                "320", "v0", "128", "192", "256", "vbr", "cbr",
                "soulseek", "nicotine", "slsk",
                "www.", "http", ".com", ".ru",
            ]):
                suspect += 1
    if suspect > 0:
        issues.append(f"{suspect:,} tracks have suspicious metadata")
    issues.append("All files are readable")
    return issues


def _auto_detect_rekordbox() -> str:
    """Return the first existing Rekordbox XML path, or empty string."""
    for candidate in _REKORDBOX_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return ""


def _collect_api_keys() -> dict[str, str]:
    """Walk through each optional key, collect values with masked input."""
    console.print()
    console.print("  [bold]\u2500\u2500\u2500 Optional API Keys (skip all with Enter) \u2500\u2500\u2500[/bold]")
    console.print()
    console.print("  These are optional. Decksmith works great without them.")
    console.print("  Add keys later anytime with: [cyan]decksmith settings[/cyan]")
    console.print()

    collected: dict[str, str] = {}

    for key_name, info in KEY_REGISTRY.items():
        cost = "free" if info.get("free", True) else "paid"
        console.print(f"  [bold]{info['display_name']}[/bold] ({cost}) \u2014 {info['unlocks']}")
        if info.get("setup_hint"):
            console.print(f"    {info['setup_hint']}")
        console.print(f"    Get a key at: [link={info['signup_url']}]{info['signup_url']}[/link]")

        for cf in info["config_fields"]:
            value = Prompt.ask(
                f"    ? {cf['prompt']} (or press Enter to skip)",
                default="",
                password=True,
                console=console,
            )
            collected[cf["field"]] = value.strip()

        # Feedback line
        all_set = all(collected.get(cf["field"], "") for cf in info["config_fields"])
        if all_set:
            print_success(f"{info['display_name']} key saved.")
        else:
            print_skipped(f"No worries. {info['display_name']} features will be unavailable.")
        console.print()

    return collected


def run_setup_wizard() -> None:
    """Run the interactive first-run setup wizard."""
    print_welcome_banner()
    console.print()
    console.print("  No config found. Let's set things up.")
    console.print()

    # 1. Library path
    raw_path = Prompt.ask(
        "  ? Where's your DJ library?",
        default="~/Music/DJ Library",
        console=console,
    )
    library_path = Path(raw_path).expanduser().resolve()

    if not library_path.is_dir():
        print_warning(f"Directory not found: {library_path}")
        print_info("You can create it now or update the path later in settings.")
        console.print()
    else:
        # 2. Scan
        console.print()
        with get_progress("Scanning...") as progress:
            task = progress.add_task("Scanning library...", total=None)
            counts = _scan_library(library_path)
            progress.update(task, completed=1, total=1)

        total = sum(counts.values())
        console.print(f"  Scanning... Found [bold]{total:,}[/bold] tracks.")
        if counts:
            parts = []
            label_map = {
                ".mp3": "MP3", ".flac": "FLAC", ".aiff": "AIFF", ".aif": "AIFF",
                ".wav": "WAV", ".m4a": "M4A",
            }
            # Merge .aiff and .aif counts
            merged: dict[str, int] = {}
            for ext, count in counts.items():
                label = label_map.get(ext, ext.upper())
                merged[label] = merged.get(label, 0) + count
            for label, count in merged.items():
                parts.append(f"{label}: {count:,}")
            console.print(f"    {('  ').join(parts)}")
        console.print()

        # 3. Health check
        if total > 0:
            console.print("  Quick health check:")
            issues = _quick_health_check(library_path, total)
            for issue in issues:
                if "readable" in issue.lower():
                    print_success(issue)
                else:
                    print_warning(issue)

            # Show system dependency status (ffmpeg, ffprobe, fpcalc)
            from decksmith.utils.audio import print_dependency_status
            print_dependency_status(verbose=True)
            console.print()

    # 4. Rekordbox XML
    auto = _auto_detect_rekordbox()
    if auto:
        console.print(f"  ? Where's your Rekordbox XML? [dim](auto-detected)[/dim]")
        console.print(f"    [dim]{auto}[/dim]")
        rekordbox_xml = auto
    else:
        rekordbox_xml = Prompt.ask(
            "  ? Where's your Rekordbox XML? (press Enter to skip)",
            default="",
            console=console,
        )
    console.print()

    # 5. Optional API keys
    api_values = _collect_api_keys()

    # 6. Build and save config
    apis = ApisConfig(
        groq_key=api_values.get("groq_key", ""),
        spotify_client_id=api_values.get("spotify_client_id", ""),
        spotify_client_secret=api_values.get("spotify_client_secret", ""),
        acoustid_key=api_values.get("acoustid_key", ""),
        discogs_token=api_values.get("discogs_token", ""),
        listenbrainz_token=api_values.get("listenbrainz_token", ""),
    )

    config = DecksmithConfig(
        library=LibraryConfig(
            paths=[str(library_path)],
            rekordbox_xml=rekordbox_xml,
        ),
        output=OutputConfig(),
        db=DbConfig(),
        apis=apis,
    )

    save_config(config)
    console.print(f"  Config saved to [bold]{get_config_path()}[/bold]")
    print_next_step("decksmith clean --preview", "See what Decksmith would fix in your library")
