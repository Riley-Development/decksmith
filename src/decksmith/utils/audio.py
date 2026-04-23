"""System dependency checks and ffprobe wrapper.

Checks for ``ffmpeg``, ``ffprobe``, and ``fpcalc`` at startup.
Prints install instructions if missing.  Missing ``fpcalc`` must not
block non-fingerprinting flows.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

from decksmith.utils.ui import print_success, print_warning, print_info, console


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def check_dependencies() -> dict[str, bool]:
    """Check for ffmpeg, ffprobe, and fpcalc. Return availability dict."""
    return {
        "ffmpeg": _which("ffmpeg") is not None,
        "ffprobe": _which("ffprobe") is not None,
        "fpcalc": _which("fpcalc") is not None,
    }


def print_dependency_status(verbose: bool = False) -> None:
    """Print a user-friendly status of system dependencies.

    When *verbose* is False (startup path), only warn about missing
    required tools (ffmpeg, ffprobe).  fpcalc is optional and only
    shown when *verbose* is True (wizard, explicit dep-check flows).
    """
    deps = check_dependencies()

    if verbose and deps["ffmpeg"]:
        print_success("ffmpeg found")
    if not deps["ffmpeg"]:
        print_warning("ffmpeg not found \u2014 bitrate detection and audio analysis unavailable")
        print_info("  Install: [cyan]brew install ffmpeg[/cyan] (macOS) or [cyan]sudo apt install ffmpeg[/cyan] (Linux)")

    if verbose and deps["ffprobe"]:
        print_success("ffprobe found")
    if not deps["ffprobe"]:
        print_warning("ffprobe not found \u2014 audio info unavailable")
        print_info("  Included with ffmpeg: [cyan]brew install ffmpeg[/cyan]")

    # fpcalc is optional — only mention in verbose mode (wizard, explicit checks)
    if verbose:
        if deps["fpcalc"]:
            print_success("fpcalc (Chromaprint) found")
        else:
            print_info("fpcalc not found \u2014 track fingerprinting unavailable (optional)")
            print_info("  Install: [cyan]brew install chromaprint[/cyan] (macOS) or [cyan]sudo apt install libchromaprint-tools[/cyan]")


def get_audio_info(filepath: str) -> Optional[dict]:
    """Use ffprobe to get audio stream info for *filepath*.

    Returns a dict with keys like ``duration``, ``bit_rate``, ``sample_rate``,
    ``codec_name``, etc.  Returns ``None`` if ffprobe is unavailable or fails.
    """
    if not _which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        # Find the audio stream
        audio_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                audio_stream = stream
                break
        fmt = data.get("format", {})
        info: dict = {}
        if audio_stream:
            info["codec_name"] = audio_stream.get("codec_name", "")
            info["sample_rate"] = audio_stream.get("sample_rate", "")
            info["channels"] = audio_stream.get("channels", 0)
            info["bit_rate"] = audio_stream.get("bit_rate", fmt.get("bit_rate", ""))
        info["duration"] = fmt.get("duration", "")
        info["format_name"] = fmt.get("format_name", "")
        info["size"] = fmt.get("size", "")
        return info
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
