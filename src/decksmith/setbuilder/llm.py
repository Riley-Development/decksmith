"""Groq-backed LLM helpers for set building and AI genre tagging.

Lazy imports keep the ``groq`` package optional.  If the key or
package is missing, functions return None and callers surface a
friendly key-missing message via the UI layer.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from decksmith.config import DecksmithConfig
from decksmith.utils.api_clients import _resolve_key

PRIMARY_MODEL = "llama-3.3-70b-versatile"
BATCH_MODEL = "llama-3.1-8b-instant"


def get_client(config: DecksmithConfig) -> Optional[Any]:
    key = _resolve_key(config, "groq_key")
    if not key:
        return None
    try:
        from groq import Groq  # type: ignore
    except ImportError:
        return None
    try:
        return Groq(api_key=key)
    except Exception:
        return None


def _chat(
    client: Any,
    *,
    model: str,
    system: str,
    user: str,
    json_mode: bool = False,
    max_tokens: int = 2048,
) -> Optional[str]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content
    except Exception:
        return None


def suggest_set(
    client: Any,
    prompt: str,
    candidates: list[dict],
    target_length_min: int = 60,
    energy_curve: Optional[list[int]] = None,
) -> Optional[list[dict]]:
    """Return an ordered list of track dicts forming a set.

    *candidates* are filtered library rows (artist/title/bpm/key/energy).
    Returns None on any failure so the caller can degrade gracefully.
    """
    sys_msg = (
        "You are a senior DJ assembling a set. Pick tracks from the provided library. "
        "Respect harmonic mixing (Camelot), BPM compatibility (drift <= 6), and the "
        "target energy curve. Output strictly as JSON with an 'tracks' array of "
        "{filepath, position, transition_note}. Include no prose."
    )
    body = {
        "prompt": prompt,
        "target_length_min": target_length_min,
        "energy_curve": energy_curve,
        "library": candidates,
    }
    raw = _chat(
        client,
        model=PRIMARY_MODEL,
        system=sys_msg,
        user=json.dumps(body),
        json_mode=True,
    )
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if isinstance(data, dict) and "tracks" in data:
        return data["tracks"]
    if isinstance(data, list):
        return data
    return None


def ai_genre_tag(
    client: Any,
    title: str,
    artist: str,
    album: str = "",
) -> Optional[str]:
    """Return a single canonical genre label for *title*/*artist*."""
    sys_msg = (
        "You classify music into one of these canonical genres (exactly one word "
        "per response, nothing else): House, Tech House, Techno, Trance, Progressive, "
        "DnB, Dubstep, Hip-Hop, R&B, Pop, Disco, Funk, Soul, Rock, Ambient, Downtempo."
    )
    user = f"Artist: {artist}\nTitle: {title}\nAlbum: {album}"
    raw = _chat(client, model=BATCH_MODEL, system=sys_msg, user=user, max_tokens=32)
    if not raw:
        return None
    return raw.strip().splitlines()[0].strip()
