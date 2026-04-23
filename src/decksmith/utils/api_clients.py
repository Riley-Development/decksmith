"""Lazy API client accessors and the central key registry.

Every optional API key is described once here so that setup wizard,
settings display, and missing-key messages all stay in sync.

Phase 1: clients return ``None`` — no real API calls.  The wrappers
and key-presence checks are scaffolded for later phases.
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Key registry — single source of truth for optional-key UX
# ---------------------------------------------------------------------------

KEY_REGISTRY: dict[str, dict[str, Any]] = {
    "groq": {
        "display_name": "Groq",
        "config_fields": [
            {"field": "groq_key", "prompt": "Paste your Groq API key"},
        ],
        "signup_url": "https://console.groq.com/keys",
        "unlocks": "Unlocks AI genre tagging and smart set building.",
        "requires_msg": "This feature requires a Groq API key for AI-powered track selection.",
        "settings_command": "decksmith settings --key groq",
        "pip_packages": ["groq"],
        "pip_install": "pip install decksmith[llm]",
        "free": True,
    },
    "spotify": {
        "display_name": "Spotify",
        "config_fields": [
            {"field": "spotify_client_id", "prompt": "Client ID"},
            {"field": "spotify_client_secret", "prompt": "Client Secret"},
        ],
        "signup_url": "https://developer.spotify.com/dashboard",
        "unlocks": "Unlocks cover art and metadata search via Spotify.",
        "requires_msg": "This feature requires Spotify credentials for cover art and metadata search.",
        "setup_hint": "Create an app at the URL above.",
        "settings_command": "decksmith settings --key spotify",
        # Spotify is hit via stdlib urllib — no extra package required
        "pip_packages": [],
        "pip_install": "",
        "free": True,
    },
    "acoustid": {
        "display_name": "AcoustID",
        "config_fields": [
            {"field": "acoustid_key", "prompt": "API key"},
        ],
        "signup_url": "https://acoustid.org/new-application",
        "unlocks": "Unlocks track fingerprinting to ID unknown files.",
        "requires_msg": "Track fingerprinting requires an AcoustID API key to identify unknown files.",
        "settings_command": "decksmith settings --key acoustid",
        "pip_packages": ["acoustid"],
        "pip_install": "pip install decksmith[discovery]",
        "free": True,
    },
    "discogs": {
        "display_name": "Discogs",
        "config_fields": [
            {"field": "discogs_token", "prompt": "Token"},
        ],
        "signup_url": "https://www.discogs.com/settings/developers",
        "unlocks": "Better metadata for electronic music via Discogs.",
        "requires_msg": "Metadata enrichment requires a Discogs token for electronic music lookups.",
        "settings_command": "decksmith settings --key discogs",
        "pip_packages": ["discogs_client"],
        "pip_install": "pip install decksmith[discovery]",
        "free": True,
    },
    "listenbrainz": {
        "display_name": "ListenBrainz",
        "config_fields": [
            {"field": "listenbrainz_token", "prompt": "Token"},
        ],
        "signup_url": "https://listenbrainz.org/settings/",
        "unlocks": "Personalized recommendations via ListenBrainz.",
        "requires_msg": "Recommendations require a ListenBrainz token for personalized suggestions.",
        "settings_command": "decksmith settings --key listenbrainz",
        "pip_packages": [],  # uses stdlib urllib
        "pip_install": "",
        "free": True,
    },
}


def missing_packages_for_key(key_name: str) -> list[str]:
    """Return a list of package names that are missing for *key_name*.

    Empty list = all required packages are importable (or the key doesn't
    need any).  Used at startup to warn when a key is configured but the
    Python package backing it isn't installed — avoids silent 0-result
    failures like the ones we hit during the first live run.
    """
    info = KEY_REGISTRY.get(key_name) or {}
    missing: list[str] = []
    for pkg in info.get("pip_packages", []):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def configured_keys_missing_packages(config: Any) -> dict[str, dict[str, Any]]:
    """Return ``{key_name: {missing: [...], pip_install: "..."}}``.

    Only includes keys that are configured AND have missing packages.
    """
    out: dict[str, dict[str, Any]] = {}
    for key_name, info in KEY_REGISTRY.items():
        if not is_key_configured(config, key_name):
            continue
        missing = missing_packages_for_key(key_name)
        if missing:
            out[key_name] = {
                "missing": missing,
                "pip_install": info.get("pip_install", ""),
            }
    return out

# Feature-to-key mapping for the settings feature availability table.
FEATURE_KEY_MAP: list[dict[str, Any]] = [
    {"feature": "Metadata cleanup", "key": None, "label": "no key needed"},
    {"feature": "Bitrate detection", "key": None, "label": "no key needed"},
    {"feature": "BPM / Key / Energy", "key": None, "label": "no key needed"},
    {"feature": "Auto cue points", "key": None, "label": "no key needed"},
    {"feature": "Cover art (Deezer)", "key": None, "label": "no key needed"},
    {"feature": "AI genre tagging", "key": "groq", "label": "Groq key"},
    {"feature": "Smart set building", "key": "groq", "label": "Groq key"},
    {"feature": "Cover art (Spotify)", "key": "spotify", "label": "Spotify key"},
    {"feature": "Track fingerprinting", "key": "acoustid", "label": "AcoustID key"},
    {"feature": "Discogs enrichment", "key": "discogs", "label": "Discogs token"},
    {"feature": "Recommendations", "key": "listenbrainz", "label": "ListenBrainz token"},
]


def _resolve_key(config: Any, field_name: str) -> str:
    """Return the resolved value of a config API field, or empty string."""
    if config is None:
        return ""
    apis = getattr(config, "apis", None)
    if apis is None:
        return ""
    return getattr(apis, field_name, "") or ""


def is_key_configured(config: Any, key_name: str) -> bool:
    """Check whether all required fields for *key_name* have non-empty values."""
    info = KEY_REGISTRY.get(key_name)
    if not info:
        return False
    for cf in info["config_fields"]:
        if not _resolve_key(config, cf["field"]):
            return False
    return True


# ---------------------------------------------------------------------------
# Lazy client accessors — Phase 1 stubs that return None
# ---------------------------------------------------------------------------

def get_groq_client(config: Any) -> Optional[Any]:
    if not is_key_configured(config, "groq"):
        return None
    # Phase 2+: from groq import Groq; return Groq(api_key=...)
    return None


def get_spotify_client(config: Any) -> Optional[Any]:
    if not is_key_configured(config, "spotify"):
        return None
    return None


def get_acoustid_key(config: Any) -> Optional[str]:
    val = _resolve_key(config, "acoustid_key")
    return val if val else None


def get_discogs_client(config: Any) -> Optional[Any]:
    if not is_key_configured(config, "discogs"):
        return None
    return None


def get_listenbrainz_client(config: Any) -> Optional[Any]:
    if not is_key_configured(config, "listenbrainz"):
        return None
    return None
