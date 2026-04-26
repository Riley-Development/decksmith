"""Pydantic configuration models with YAML persistence and env-var interpolation.

Key behaviours:
- ``${ENV_VAR}`` syntax is resolved at load time.
- ``~`` is expanded in file paths.
- All API key fields default to empty strings.
- Missing optional keys validate cleanly — never crash.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".decksmith"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


def get_config_dir() -> Path:
    return CONFIG_DIR


def get_config_path() -> Path:
    return CONFIG_PATH


def config_exists() -> bool:
    return CONFIG_PATH.exists()


# ---------------------------------------------------------------------------
# Env-var interpolation
# ---------------------------------------------------------------------------

_ENV_RE = re.compile(r"\$\{(\w+)\}")


def interpolate_env_vars(value: str) -> str:
    """Replace ``${VAR}`` with the corresponding environment variable."""
    def _replacer(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return _ENV_RE.sub(_replacer, value)


def _interpolate_recursive(data: Any) -> Any:
    if isinstance(data, str):
        return interpolate_env_vars(data)
    if isinstance(data, dict):
        return {k: _interpolate_recursive(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_interpolate_recursive(i) for i in data]
    return data


# ---------------------------------------------------------------------------
# Path expansion helper
# ---------------------------------------------------------------------------

def expand_path(path: str) -> str:
    if not path:
        return path
    return str(Path(path).expanduser())


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LibraryConfig(BaseModel):
    paths: list[str] = []
    rekordbox_xml: str = ""
    supported_formats: list[str] = [".mp3", ".flac", ".aiff", ".aif", ".wav", ".m4a"]
    backup_before_modify: bool = True
    backup_dir: str = "~/.decksmith/backups"


class OutputConfig(BaseModel):
    organized_path: str = ""
    reports_path: str = "~/.decksmith/reports"
    rekordbox_xml_out: str = "~/.decksmith/rekordbox_import.xml"


class DbConfig(BaseModel):
    path: str = "~/.decksmith/tracking.db"


class ApisConfig(BaseModel):
    groq_key: str = ""
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    acoustid_key: str = ""
    discogs_token: str = ""
    listenbrainz_token: str = ""


class _DictCompatModel(BaseModel):
    """Base that supports dict-style .get() for backward compat."""
    model_config = {"extra": "allow"}

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = getattr(self, key, default)
            return val if val is not None else default
        except Exception:
            return default

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key) and getattr(self, key) is not None

    def __bool__(self) -> bool:
        return True


class StripPattern(_DictCompatModel):
    pattern: str = ""
    apply_to: list[str] = []


class ArtistSeparators(_DictCompatModel):
    featuring: list[str] = ["feat.", "feat", "ft.", "ft", "Feat.", "Feat", "Ft.", "Ft", "featuring", "Featuring"]
    versus: list[str] = ["vs.", "vs", "VS.", "VS", "versus", "Versus"]
    b2b: list[str] = ["b2b", "B2B"]


class MetadataConfig(_DictCompatModel):
    strip_patterns: list[dict] = []
    preserve_as_comment: list[str] = []
    clean_fields: list[str] = ["title", "artist", "album", "album_artist", "genre"]
    nuke_fields: list[str] = ["encoded_by", "url", "copyright"]
    comment_preserve_keywords: list[str] = ["BPM", "Key", "bpm", "key", "Camelot", "energy", "mix"]
    artist_separators: dict = {}
    artwork_sources: list[str] = ["deezer", "spotify", "discogs", "musicbrainz"]
    min_artwork_size: int = 600
    preferred_artwork_size: int = 1000
    filename_patterns: list[str] = []


class AnalysisConfig(_DictCompatModel):
    frequency_shelf_thresholds: dict = {}
    bpm_tolerance: float = 0.5
    bpm_voting: bool = True


class CueSlot(_DictCompatModel):
    num: int = 0
    name: str = ""
    rgb: list[int] = [255, 255, 255]
    strategy: str = ""


class CuePointsConfig(_DictCompatModel):
    enabled: bool = True
    max_cues: int = 8
    skip_if_cues_exist: bool = True
    slots: list[dict] = []


class FolderStructure(_DictCompatModel):
    primary: str = "genre"
    secondary: str = "bpm_range"


class RekordboxConfig(_DictCompatModel):
    folder_structure: dict = {}
    genre_bpm_ranges: dict = {}
    smart_playlists: list[dict] = []
    cue_points: Optional[dict] = None


class GroqConfig(_DictCompatModel):
    primary_model: str = "llama-3.3-70b-versatile"
    batch_model: str = "llama-3.1-8b-instant"


class SetbuilderConfig(_DictCompatModel):
    default_length_minutes: int = 60
    energy_curves: dict = {}
    harmonic_mixing: bool = True
    transition_bars: int = 16
    bpm_drift_max: float = 6.0
    avoid_same_artist_within: int = 4
    groq: Optional[dict] = None


class DecksmithConfig(BaseModel):
    library: LibraryConfig = LibraryConfig()
    output: OutputConfig = OutputConfig()
    db: DbConfig = DbConfig()
    apis: ApisConfig = ApisConfig()
    metadata: MetadataConfig = MetadataConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    rekordbox: RekordboxConfig = RekordboxConfig()
    setbuilder: SetbuilderConfig = SetbuilderConfig()

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return Path(expand_path(self.db.path))

    @property
    def backup_dir(self) -> Path:
        return Path(expand_path(self.library.backup_dir))

    @property
    def library_paths(self) -> list[Path]:
        return [Path(expand_path(p)) for p in self.library.paths]

    @property
    def rekordbox_xml_path(self) -> Optional[Path]:
        if self.library.rekordbox_xml:
            return Path(expand_path(self.library.rekordbox_xml))
        return None


# ---------------------------------------------------------------------------
# Default metadata config (matches config.example.yaml / spec)
# ---------------------------------------------------------------------------

DEFAULT_STRIP_PATTERNS: list[dict] = [
    {"pattern": r"\[.*?(320|V0|FLAC|MP3|CBR|VBR|AAC|OGG|WEB-DL|WEB).*?\]", "apply_to": ["title", "album"]},
    {"pattern": r"\(.*?(WEB|CDR|CD|VINYL|Promo|Remaster|Remastered).*?\)", "apply_to": ["album"]},
    {"pattern": r"\b(www\.|https?://|\.(com|net|org|ru|info|co\.uk))\S*", "apply_to": ["title", "artist", "album"]},
    {"pattern": r"^[A-Z]?\d{1,3}\s*[-._]\s*", "apply_to": ["title"]},
    {"pattern": r"[-_\s]+(320|V0|192|128|256|CBR|VBR)\s*$", "apply_to": ["title", "album"]},
    # Wipe compilation-rip albums entirely — they are never the track's real
    # album and the user's enrich pass will fill the correct one.  The broad
    # patterns match both the original "Now That's What I Call Music 49" form
    # and the ungrammatical leftover "'s What I Call Music 49" that earlier
    # versions of this cleaner produced.
    {"pattern": r".*[’']?s\s*What\s*I\s*Call\s*Music.*", "apply_to": ["album"]},
    {"pattern": r".*Now\s*That[’']?s\s*What\s*I\s*Call\s*Music.*", "apply_to": ["album"]},
    {"pattern": r"^Billboard.*", "apply_to": ["album"]},
    {"pattern": r".*Throwback.*", "apply_to": ["album"]},
    {"pattern": r".*(Year[-\s]*End|Top\s*\d+|Hot\s*\d+|Greatest\s*Hits|Best\s*of|Ministry\s*of\s*Sound|Beatport\s*Top).*", "apply_to": ["album"]},
    {"pattern": r"[-_\s]+(Soulseek|Nicotine|SLSK|SoulSeek|slsk).*$", "apply_to": ["title", "artist", "album", "comment"]},
    {"pattern": r"\s*\[\d{4}\]\s*$", "apply_to": ["title", "album"]},
    {"pattern": r"^VA\s*[-_]\s*", "apply_to": ["artist"]},
    {"pattern": r"\s+[-\u2013]\s+[A-Z]{2,}\d{2,}\s*$", "apply_to": ["album"]},
    {"pattern": r"^[/\\].*[/\\]", "apply_to": ["album"]},
]

DEFAULT_METADATA_CONFIG: dict = {
    "strip_patterns": DEFAULT_STRIP_PATTERNS,
    "preserve_as_comment": [
        "Clean", "Dirty", "Explicit", "Radio Edit", "Extended Mix",
        "Club Mix", "Original Mix", "Instrumental", "Acapella", "Dub Mix",
    ],
    "clean_fields": ["title", "artist", "album", "album_artist", "genre"],
    "nuke_fields": ["encoded_by", "url", "copyright"],
    "comment_preserve_keywords": ["BPM", "Key", "bpm", "key", "Camelot", "energy", "mix"],
    "artist_separators": {
        "featuring": ["feat.", "feat", "ft.", "ft", "Feat.", "Feat", "Ft.", "Ft", "featuring", "Featuring"],
        "versus": ["vs.", "vs", "VS.", "VS", "versus", "Versus"],
        "b2b": ["b2b", "B2B"],
    },
    "filename_patterns": [
        "{artist} - {title}",
        "{artist} - {title} ({remix_info})",
        "{track_num}. {artist} - {title}",
        "{track_num} - {artist} - {title}",
        "{track_num} {artist} - {title}",
        "{artist} _ {title}",
    ],
}


def get_metadata_config(config: DecksmithConfig) -> dict:
    """Return the merged metadata config, falling back to defaults."""
    merged = dict(DEFAULT_METADATA_CONFIG)
    user = config.metadata.model_dump(exclude_defaults=False)
    for k, v in user.items():
        if v:
            merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_config() -> Optional[DecksmithConfig]:
    """Load and return the user config, or ``None`` if it doesn't exist."""
    if not config_exists():
        return None
    with open(CONFIG_PATH) as fh:
        raw = yaml.safe_load(fh)
    if raw is None:
        return DecksmithConfig()
    raw = _interpolate_recursive(raw)
    return DecksmithConfig(
        library=LibraryConfig(**raw.get("library", {})),
        output=OutputConfig(**raw.get("output", {})),
        db=DbConfig(**raw.get("db", {})),
        apis=ApisConfig(**raw.get("apis", {})),
        metadata=MetadataConfig(**raw.get("metadata", {})) if raw.get("metadata") else MetadataConfig(),
        analysis=AnalysisConfig(**raw.get("analysis", {})) if raw.get("analysis") else AnalysisConfig(),
        rekordbox=RekordboxConfig(**raw.get("rekordbox", {})) if raw.get("rekordbox") else RekordboxConfig(),
        setbuilder=SetbuilderConfig(**raw.get("setbuilder", {})) if raw.get("setbuilder") else SetbuilderConfig(),
    )


def save_config(config: DecksmithConfig) -> None:
    """Persist config to ``~/.decksmith/config.yaml``."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "library": config.library.model_dump(),
        "output": config.output.model_dump(),
        "db": config.db.model_dump(),
        "apis": config.apis.model_dump(),
    }
    meta_dump = config.metadata.model_dump(exclude_defaults=True)
    if meta_dump:
        data["metadata"] = meta_dump
    analysis_dump = config.analysis.model_dump(exclude_defaults=True)
    if analysis_dump:
        data["analysis"] = analysis_dump
    rb_dump = config.rekordbox.model_dump(exclude_defaults=True)
    if rb_dump:
        data["rekordbox"] = rb_dump
    sb_dump = config.setbuilder.model_dump(exclude_defaults=True)
    if sb_dump:
        data["setbuilder"] = sb_dump
    with open(CONFIG_PATH, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
