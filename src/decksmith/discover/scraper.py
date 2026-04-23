"""yt-dlp wrappers — search and info only (no downloading by default).

We never download audio unless the caller explicitly opts in by
passing ``download=True``.  Even then, this module respects the
user's local law on downloading — the caller is responsible for that
decision.

The intent in Decksmith is *discovery*: finding tracks that exist
somewhere but aren't yet in the library, so the user can buy them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScrapeResult:
    title: str
    uploader: str
    duration: float
    url: str


def search(query: str, limit: int = 5) -> list[ScrapeResult]:
    """Search via yt-dlp's ``ytsearch`` URL.

    Returns an empty list if ``yt-dlp`` is not installed or the search
    fails — never raises.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError:
        return []

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception:
        return []

    entries = info.get("entries", []) if isinstance(info, dict) else []
    out: list[ScrapeResult] = []
    for e in entries[:limit]:
        if not e:
            continue
        out.append(ScrapeResult(
            title=e.get("title", ""),
            uploader=e.get("uploader", ""),
            duration=float(e.get("duration") or 0),
            url=e.get("url") or e.get("webpage_url", ""),
        ))
    return out


def is_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False
