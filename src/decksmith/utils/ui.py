"""Shared Rich UI primitives for Decksmith.

Provides a consistent visual language across all commands:
traffic-light colors, diff tables, confirm prompts, progress helpers,
next-step recommendations, undo reminders, and key-missing messages.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)
from rich.prompt import Confirm
from rich.text import Text
from rich import box

console = Console()


# ---------------------------------------------------------------------------
# Traffic-light message printers
# ---------------------------------------------------------------------------

def print_success(message: str) -> None:
    console.print(f"  [green]\u2713[/green] {message}")


def print_warning(message: str) -> None:
    console.print(f"  [yellow]\u26a0[/yellow]  {message}")


def print_error(message: str) -> None:
    console.print(f"  [red]\u2717[/red] {message}")


def print_info(message: str) -> None:
    console.print(f"  [blue]\u2139[/blue] {message}")


def print_skipped(message: str) -> None:
    console.print(f"  [dim]\u25cb[/dim] {message}")


# ---------------------------------------------------------------------------
# Diff table
# ---------------------------------------------------------------------------

def print_diff_table(changes: list[dict], title: str = "") -> None:
    """Print a Field / Before / After diff table.

    Each entry in *changes* should be a dict with keys:
    ``field``, ``before``, ``after``.
    """
    if not changes:
        return
    tbl = Table(box=box.ROUNDED, title=title if title else None, show_lines=True)
    tbl.add_column("Field", style="bold", min_width=12)
    tbl.add_column("Before", style="red", min_width=20)
    tbl.add_column("After", style="green", min_width=20)
    for c in changes:
        tbl.add_row(
            str(c.get("field", "")),
            str(c.get("before", "")),
            str(c.get("after", "")),
        )
    console.print(tbl)


# ---------------------------------------------------------------------------
# Confirm prompts
# ---------------------------------------------------------------------------

def confirm(message: str, default: bool = False) -> bool:
    return Confirm.ask(f"  ? {message}", default=default, console=console)


def confirm_destructive(message: str) -> bool:
    """Confirm for risky or broad changes — reminds the user that undo exists."""
    console.print()
    console.print(
        Panel(
            f"[yellow]{message}[/yellow]\n\n"
            "[dim]You can undo this later with: [bold]decksmith undo[/bold][/dim]",
            border_style="yellow",
            title="\u26a0 Confirm",
        )
    )
    return Confirm.ask("  ? Proceed?", default=False, console=console)


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def get_progress(description: str = "Processing...") -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    )


# ---------------------------------------------------------------------------
# Next-step recommendations
# ---------------------------------------------------------------------------

def print_next_step(command: str, description: str = "") -> None:
    console.print()
    console.print("  [bold]Next step:[/bold]")
    if description:
        console.print(f"    [cyan]{command}[/cyan]  [dim]\u2014 {description}[/dim]")
    else:
        console.print(f"    [cyan]{command}[/cyan]")


# ---------------------------------------------------------------------------
# Undo reminders
# ---------------------------------------------------------------------------

def print_undo_reminder() -> None:
    console.print()
    console.print(
        "  [dim]Changed your mind? Run [bold]decksmith undo[/bold] to restore original tags.[/dim]"
    )


# ---------------------------------------------------------------------------
# Write summary
# ---------------------------------------------------------------------------

def print_write_summary(changed: int, skipped: int = 0) -> None:
    """Print after any successful write operation."""
    parts = [f"[green]{changed}[/green] track{'s' if changed != 1 else ''} updated"]
    if skipped:
        parts.append(f"[dim]{skipped} skipped[/dim]")
    console.print()
    console.print(f"  {', '.join(parts)}.")
    print_undo_reminder()


# ---------------------------------------------------------------------------
# Key-missing messages
# ---------------------------------------------------------------------------

def print_key_missing(key_name: str) -> None:
    """Print a helpful missing-key message using the central key registry.

    Matches the spec's graceful-degradation pattern::

        \u26a0 <feature> requires a <key> API key for <purpose>.

        Without it, you can still:
          \u2022 Clean and organize your library locally
          \u2022 Detect BPM, key, and energy
          \u2022 Generate cue points

        To add your <key> key (free):
          decksmith settings --key <name>
          or get one at <url>
    """
    from decksmith.utils.api_clients import KEY_REGISTRY

    info = KEY_REGISTRY.get(key_name)
    if not info:
        print_warning(f"Unknown key: {key_name}")
        return

    console.print()
    console.print(f"  [yellow]\u26a0[/yellow]  {info['requires_msg']}")
    console.print()
    console.print("  Without it, you can still:")
    console.print("    \u2022 Clean and organize your library locally")
    console.print("    \u2022 Detect BPM, key, and energy")
    console.print("    \u2022 Generate cue points")
    console.print()
    cost = "free" if info.get("free", True) else "paid"
    console.print(f"  To add your {info['display_name']} key ({cost}):")
    console.print(f"    [cyan]{info['settings_command']}[/cyan]")
    console.print(
        f"    or get one at [link={info['signup_url']}]{info['signup_url']}[/link]"
    )


# ---------------------------------------------------------------------------
# Welcome banner
# ---------------------------------------------------------------------------

def print_welcome_banner() -> None:
    console.print()
    console.print(
        Panel(
            "[bold]Welcome to Decksmith[/bold]\n"
            "Your DJ library, cleaned up.",
            border_style="cyan",
            padding=(1, 4),
        )
    )
