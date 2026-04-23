# Roadmap

Triaged from the first live run on a real 208-track DJ library
(2026-04-23). Items are ordered by user-visible impact, not by effort.

## The big one: Rekordbox direct DB writer

**Problem.** Rekordbox 7's XML import applies BPM, key, and beatgrid to
tracks that are already in your Collection — but it **silently refuses
to apply hot cues** to existing tracks. Hot cues only get in when the
XML adds a brand-new track. Since most DJs already have their library
in Rekordbox, our XML-based cue export never makes it to the hot cue
pads.

Decksmith's hot-cue detection is the biggest time-saver the tool offers.
Without a way to actually land those cues on A-H pads, we're delivering
half the value.

**The fix.** Write directly to Rekordbox's internal database rather than
going through XML import.

- Rekordbox 7 stores state at `~/Library/Pioneer/rekordbox6/` (the folder
  name didn't update for v7): an encrypted SQLCipher `master.db` plus
  per-track `ANLZ*.DAT`/`ANLZ*.EXT` binary analysis files.
- The community has reverse-engineered the SQLCipher key and the
  schema; see `pyrekordbox`, `crate-digger`, `rbox-py`.
- Third-party tools (Rekordcloud, LexicDJ) already ship DB writers
  successfully, so the feasibility is proven.

Scope of work:
1. Depend on `pyrekordbox` for decrypted reads of `master.db`.
2. Map Decksmith's tracked filepaths → `DjmdContent` rows.
3. Write `DjmdCue` entries for our 8-slot hot cue layout.
4. Regenerate `ANLZ0000.DAT` / `.EXT` so the cues sync to CDJs via USB.
5. Backup `master.db` before every write; `decksmith undo --rekordbox`
   reverses.

Not a weekend project. Plan for ~1–2 weeks of focused work including a
thorough dry-run mode and test fixtures.

## Enricher: stop picking compilation releases

**Problem.** `decksmith enrich` reached Discogs on every track but
chose the first search result, which for popular tracks is often a
compilation or greatest-hits package. The first live run replaced one
set of bogus albums ("Billboard USA Hits Of 2010") with a different set
("The Greatest Switch Box", "100 Greatest 00s R&B", "BRIT Awards 2013",
"Mainstream Club Nov").

**The fix.**
- Paginate Discogs results (grab the first 2-3 pages, not just 1).
- Filter out releases where `format` includes `Compilation`, `Mixed`,
  `DJ Mix`, `Promo`, or primary artist is `Various`.
- Prefer `type=Album` over singles, EPs, and mixes.
- Sort remaining candidates by year ascending — earliest is almost
  always the original.
- If no clean candidate remains, fall back to the MusicBrainz
  canonical recording → release-group API.
- Add `--overwrite-compilations` flag to replace bad albums without
  needing a wipe-and-fill two-step.

## Fix undo semantics

**Problem.** `undo --last` groups tracks by the `last_processed`
timestamp. But `update_track_analysis` overwrites `last_processed` on
every analyze call, which atomically splits the prior clean batch into
208 one-track "batches". After analyze, `undo --last` rolls back exactly
one track instead of the expected 96+.

**The fix.** Add a dedicated `change_log` table keyed by an immutable
batch UUID. Every write-path operation (clean, enrich, artwork,
fingerprint --apply) inserts a row referencing the snapshot and the
batch. `undo --last` becomes `DELETE FROM change_log WHERE batch_id =
(latest batch) RETURNING snapshot` — clean, explicit, resilient to
analyze passes.

## Artwork undo

**Problem.** Embedding cover art modifies the file but undo only restores
tag fields, not the embedded image. Undoing an artwork batch leaves the
images stuck in place.

**The fix.** Either full-file backup before embedding (expensive disk-
wise), or a separate `decksmith strip-art` command that removes embedded
images. Lean toward the second — cheaper and users rarely want to
reverse artwork.

## Smarter compilation detector

**Problem.** Our strip-patterns list is whack-a-mole: every new
compilation format ("Mastermix DJ Edits", "UK Singles Chart", "Urban
Radio November 23") needs a code change.

**The fix.** Classify albums with a lightweight heuristic instead of
exhaustive regexes:
- Contains a year or date suffix?
- Contains more than two of: `Best`, `Hits`, `Top`, `Greatest`,
  `Now`, `Billboard`, `Billboard`, `Ultimate`, `Essential`,
  `Essentials`, `Mastermix`, `Chart`, `Radio`, `Promo`, `Volume`?
- Primary artist field contains `Various`?

Score each candidate; if above a threshold, wipe the album tag and let
`enrich` fill the correct one. Users can still override via the
existing `metadata.strip_patterns` config.

## Nice-to-haves

- Typed `config.analysis`, `config.rekordbox`, `config.setbuilder` as
  pydantic models rather than raw dicts, so IDE auto-complete works.
- Report-page spectrograms (spec called for these; we ship mean-spectrum
  line plots instead).
- Progress bar ETAs that actually update during librosa passes.
- `--dry-run` on every write command, not just `clean --preview`.
- AcoustID fingerprint matches should write `musicbrainz_id` into
  comments for other tools to pick up.
- Install-time `brew install` hint for `chromaprint` and `ffmpeg` in a
  single bootstrap script.

## Non-goals

- Web UI.
- Replacing Rekordbox.
- Streaming-service integration beyond metadata lookups.
- Sync-across-devices. Decksmith is local-first; keep it that way.
