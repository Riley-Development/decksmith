"""``decksmith settings`` — view and edit configuration and API keys.

Displays library path, Rekordbox XML, config file location, key status,
and feature availability.  Supports ``--key <name>`` for direct edit and
``--key all`` to walk through every key.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.prompt import Prompt
from rich.table import Table
from rich import box

from decksmith.config import (
    DecksmithConfig,
    load_config,
    save_config,
    get_config_path,
    expand_path,
)
from decksmith.utils.api_clients import (
    KEY_REGISTRY,
    FEATURE_KEY_MAP,
    is_key_configured,
)
from decksmith.utils.ui import (
    console,
    print_success,
    print_warning,
    print_info,
    print_skipped,
)


def _print_overview(config: DecksmithConfig) -> None:
    """Print the settings overview panel."""
    console.print()
    console.print("  [bold]Decksmith Settings[/bold]")
    console.print("  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    console.print()

    lib_paths = ", ".join(config.library.paths) if config.library.paths else "[dim]not set[/dim]"
    rb_xml = config.library.rekordbox_xml or "[dim]not set[/dim]"
    console.print(f"  Library path:    {lib_paths}")
    console.print(f"  Rekordbox XML:   {rb_xml}")
    console.print(f"  Config file:     {get_config_path()}")
    console.print()


def _print_key_status(config: DecksmithConfig) -> None:
    """Print configured / not-set status for each API key."""
    console.print("  [bold]API Keys:[/bold]")
    for key_name, info in KEY_REGISTRY.items():
        configured = is_key_configured(config, key_name)
        if configured:
            status = "[green]\u2713 configured[/green]"
        else:
            status = f"[red]\u2717 not set[/red]      [dim]({info['settings_command']})[/dim]"
        console.print(f"    {info['display_name']:<15s} {status}")
    console.print()


def _print_feature_availability(config: DecksmithConfig) -> None:
    """Print which features are available based on current key status."""
    console.print("  [bold]Features:[/bold]")
    for entry in FEATURE_KEY_MAP:
        key = entry["key"]
        if key is None:
            # No key needed — always available
            console.print(f"    {entry['feature']:<23s} [green]\u2713 available[/green] [dim](no key needed)[/dim]")
        elif is_key_configured(config, key):
            console.print(f"    {entry['feature']:<23s} [green]\u2713 available[/green] [dim]({entry['label']} set)[/dim]")
        else:
            console.print(f"    {entry['feature']:<23s} [red]\u2717 needs {entry['label']}[/red]")
    console.print()


def _edit_key(config: DecksmithConfig, key_name: str) -> DecksmithConfig:
    """Prompt for a single API key and update the config."""
    info = KEY_REGISTRY.get(key_name)
    if not info:
        print_warning(f"Unknown key: {key_name}")
        console.print(f"  Available keys: {', '.join(KEY_REGISTRY.keys())}")
        return config

    cost = "free" if info.get("free", True) else "paid"
    console.print()
    console.print(f"  [bold]{info['display_name']}[/bold] ({cost}) \u2014 {info['unlocks']}")
    console.print(f"    Get a key at: [link={info['signup_url']}]{info['signup_url']}[/link]")
    console.print()

    apis_dict = config.apis.model_dump()
    any_set = False
    for cf in info["config_fields"]:
        current = getattr(config.apis, cf["field"], "")
        hint = " [dim](currently set)[/dim]" if current else ""
        value = Prompt.ask(
            f"    ? {cf['prompt']}{hint} (Enter to {'keep' if current else 'skip'})",
            default="",
            password=True,
            console=console,
        )
        value = value.strip()
        if value:
            apis_dict[cf["field"]] = value
            any_set = True
        # If empty and already had a value, keep the old one

    from decksmith.config import ApisConfig
    config.apis = ApisConfig(**apis_dict)

    if any_set:
        save_config(config)
        print_success(f"{info['display_name']} key saved to {get_config_path()}")
    else:
        print_skipped("No changes made.")

    return config


def _edit_all_keys(config: DecksmithConfig) -> DecksmithConfig:
    """Walk through all keys one by one."""
    for key_name in KEY_REGISTRY:
        config = _edit_key(config, key_name)
    return config


def _edit_library_path(config: DecksmithConfig) -> DecksmithConfig:
    """Prompt for a new library path."""
    current = ", ".join(config.library.paths) if config.library.paths else "not set"
    console.print(f"\n  Current library path: {current}")
    new_path = Prompt.ask(
        "  ? New library path (Enter to keep current)",
        default="",
        console=console,
    )
    if new_path.strip():
        resolved = str(Path(new_path.strip()).expanduser().resolve())
        config.library.paths = [resolved]
        save_config(config)
        print_success(f"Library path updated to {resolved}")
    else:
        print_skipped("No changes made.")
    return config


def _edit_rekordbox_xml(config: DecksmithConfig) -> DecksmithConfig:
    """Prompt for a new Rekordbox XML path."""
    current = config.library.rekordbox_xml or "not set"
    console.print(f"\n  Current Rekordbox XML: {current}")
    new_path = Prompt.ask(
        "  ? New Rekordbox XML path (Enter to keep current)",
        default="",
        console=console,
    )
    if new_path.strip():
        resolved = str(Path(new_path.strip()).expanduser().resolve())
        config.library.rekordbox_xml = resolved
        save_config(config)
        print_success(f"Rekordbox XML updated to {resolved}")
    else:
        print_skipped("No changes made.")
    return config


def _open_config_file() -> None:
    """Open the config file in the system default editor."""
    path = get_config_path()
    if not path.exists():
        print_warning("Config file does not exist yet. Run setup first.")
        return
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    elif sys.platform == "linux":
        editor = os.environ.get("EDITOR", "xdg-open")
        subprocess.run([editor, str(path)])
    else:
        subprocess.run(["notepad", str(path)])
    print_info(f"Opened {path}")


def _interactive_menu(config: DecksmithConfig) -> None:
    """Main interactive settings menu."""
    _print_overview(config)
    _print_key_status(config)
    _print_feature_availability(config)

    console.print("  ? What would you like to change?")
    choice = Prompt.ask(
        "    > [bold][k][/bold]eys  [bold][l][/bold]ibrary path  [bold][r][/bold]ekordbox xml  [bold][o][/bold]pen config file  [bold][q][/bold]uit",
        choices=["k", "l", "r", "o", "q"],
        default="q",
        console=console,
    )

    if choice == "k":
        _edit_all_keys(config)
    elif choice == "l":
        _edit_library_path(config)
    elif choice == "r":
        _edit_rekordbox_xml(config)
    elif choice == "o":
        _open_config_file()
    # q = quit, just return


def show_settings(key: Optional[str] = None) -> None:
    """Entry point for ``decksmith settings``."""
    config = load_config()
    if config is None:
        print_warning("No config found. Run [bold]decksmith[/bold] to start setup.")
        return

    if key == "all":
        _edit_all_keys(config)
    elif key:
        _edit_key(config, key)
    else:
        _interactive_menu(config)
