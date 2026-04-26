# Roadmap

Triaged from the first live run on a real 208-track DJ library
(2026-04-23). Items are ordered by user-visible impact, not by effort.

## Done (v0.2)

### Enricher: stop picking compilation releases ✓

Discogs results now paginate up to 3 pages and filter out releases
where `format` includes Compilation/Mixed/DJ Mix, primary artist is
Various, or the album title heuristically scores as a compilation.
Remaining candidates prefer Album type over singles and sort by year
ascending. MusicBrainz fallback also skips compilations and prefers
original releases.

New flag: `decksmith enrich --overwrite-compilations` replaces albums
that score as compilations without needing a wipe-and-fill two-step.

### Fix undo semantics ✓

Added `change_log` table with immutable batch UUIDs. Every write-path
operation (clean, enrich, artwork, fingerprint --apply) inserts a row
referencing the snapshot and the batch. `undo --last` now pulls from
`change_log` keyed on `batch_id` — analyze passes can no longer
fragment prior batches.

Legacy databases without `change_log` entries fall back to the old
`last_processed` timestamp approach.

### Artwork undo ✓

New `decksmith strip-art` command removes embedded cover art from all
supported formats (MP3, FLAC, AIFF, WAV, M4A). Supports `--preview`
to check which files have art before removing.

### Smarter compilation detector ✓

Score-based heuristic classifier replaces regex whack-a-mole for
compilation album detection. Integrated into both the metadata cleaner
(auto-wipes compilation albums) and the enricher (filters Discogs
results). Configurable threshold via `metadata.strip_patterns` in
config.

### Typed config submodels ✓

`metadata`, `analysis`, `rekordbox`, and `setbuilder` sections in
`DecksmithConfig` are now proper pydantic models (`MetadataConfig`,
`AnalysisConfig`, `RekordboxConfig`, `SetbuilderConfig`) with typed
fields for IDE autocomplete. Backward-compatible with existing dict-
style `.get()` access.

### --dry-run on write commands ���

`decksmith enrich --dry-run` and `decksmith artwork --dry-run` show
what would change without writing. `clean --preview`, `organize --preview`,
and `fingerprint` (no-apply default) already had equivalent modes.

### Fingerprint writes musicbrainz_id ✓

`decksmith fingerprint --apply` now writes the MusicBrainz recording ID
into the comment field as `MBID:<recording-id>` so other tools can
pick it up.

### Bootstrap install hints ✓

Platform-aware install commands (macOS/Linux) for system dependencies.
`bootstrap_command()` generates a single `brew install` / `apt install`
command for all missing deps. Shown in the setup wizard dependency
check.

### Rekordbox direct DB writer ✓

Bypasses XML import entirely by writing DjmdCue entries directly into
Rekordbox's encrypted `master.db` via pyrekordbox. Hot cues now land
on A-H pads for tracks already in the Collection — the #1 limitation
of the XML workflow.

New command: `decksmith push-cues` writes all 8 detected hot cues
(Intro, Build, Drop 1, Breakdown, Drop 2, Outro, Vocal, Mix Point)
per track. Supports `--preview` to inspect before writing. Backs up
`master.db` before every write; `decksmith undo --rekordbox` restores.

`--keep-existing` preserves user-placed cues and merges DeckSmith
cues into the empty slots. Smart assignment drops the DeckSmith cue
closest in time to each custom cue (most redundant), then reassigns
survivors to available pads. Interactive per-track resolution when
slots conflict: keep yours, use DeckSmith's, or skip.

ANLZ file regeneration is not needed — Rekordbox reads local cues
from the djmdCue table, not ANLZ files. ANLZ is only populated when
exporting to USB.

## Nice-to-haves (remaining)

- Report-page spectrograms (spec called for these; we ship mean-spectrum
  line plots instead).
- Progress bar ETAs that actually update during librosa passes.

## Non-goals

- Web UI.
- Replacing Rekordbox.
- Streaming-service integration beyond metadata lookups.
- Sync-across-devices. Decksmith is local-first; keep it that way.
