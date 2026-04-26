# Decksmith

A Python CLI that turns a messy Soulseek / Nicotine+ DJ library into a clean,
Rekordbox‑ready collection — all local by default, with optional online
upgrades when you bring your own API keys.

![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Python ≥ 3.11](https://img.shields.io/badge/python-%E2%89%A5%203.11-blue)

---

## What it does

- **Cleans metadata** — strips `[320]`, `(WEB)`, site tags, VA prefixes, and the other dirt that comes with illegally-sourced music
- **Flags fake bitrates** — checks if a file claiming "320 kbps" really has content above ~15 kHz
- **BPM / key / energy analysis** — using local librosa + a Krumhansl‑Schmuckler key profile → Camelot
- **Generates hot cues** — Intro, Build, Drops, Breakdown, Outro, Mix Point
- **Pushes cues directly to Rekordbox** — writes to `master.db` via pyrekordbox, bypassing XML import limitations
- **Organises into genre / BPM folders**
- **Exports a Rekordbox XML** (5/6/7-compatible) with tracks, cues, and optional playlists
- **Builds DJ sets** from natural-language prompts (`"90 min tech house set"`) — optional LLM (Groq)
- **Fills missing tags** via AcoustID fingerprinting (optional)
- **Fetches cover art** from Deezer (no auth) and Spotify (optional)
- **Finds gaps in your library** and suggests similar artists

Core features run 100% locally with **zero network calls**. API keys are optional
and strictly unlock additional features — not required for anything essential.

---

## Install

```bash
git clone https://github.com/<you>/decksmith.git
cd decksmith
python -m venv .venv && source .venv/bin/activate
pip install -e ".[analysis]"          # core + audio analysis
# or, everything:
pip install -e ".[all]"
```

System dependencies:

| Tool     | Needed for                       | macOS                     |
|----------|----------------------------------|---------------------------|
| ffmpeg   | audio loading, some formats      | `brew install ffmpeg`     |
| ffprobe  | declared-bitrate detection       | ships with ffmpeg         |
| fpcalc   | AcoustID fingerprinting (opt.)   | `brew install chromaprint` |

---

## Quick start

```bash
decksmith                       # first run → guided setup wizard
                                # subsequent runs → dashboard

decksmith clean --preview       # show cleanup diffs, no writes
decksmith clean --auto          # apply all (original tags backed up in SQLite)
decksmith undo --last           # restore the last batch

decksmith analyze               # BPM, key, energy, bitrate report
decksmith cue --export          # generate cues + Rekordbox XML
decksmith push-cues             # write cues directly to Rekordbox DB
decksmith push-cues --keep-existing  # merge with your hand-placed cues

decksmith organize --auto       # sort into genre/BPM folders
decksmith setbuild "90 min tech house"
decksmith discover --gaps       # where your library is thin
```

Full pipeline:

```bash
decksmith run                   # clean → analyze → cue → export
```

---

## Commands

```
decksmith              Setup wizard (first run) or dashboard (configured)
decksmith clean        --preview  --auto  --interactive
decksmith undo         <filepath>  |  --last  |  --rekordbox
decksmith analyze      [--all]
decksmith cue          [--preview] [--export] [--limit N]
decksmith organize     [--preview] [--auto]
decksmith export-xml   [--out PATH]
decksmith setbuild     "prompt"  [--no-llm]
decksmith fingerprint  [--limit N]
decksmith enrich       [--dry-run] [--overwrite-compilations]
decksmith artwork      [--min-size 600] [--dry-run]
decksmith strip-art    [--preview]
decksmith push-cues    [--preview] [--keep-existing] [--force]
decksmith discover     --gaps  |  --seed "Artist"
decksmith run          [--skip-clean] [--skip-analyze] [--skip-cue] [--skip-export]
decksmith status
decksmith settings     [--key groq|spotify|acoustid|discogs|listenbrainz|all]
```

---

## Optional API keys

Decksmith never requires a key. Each key unlocks one tier of extra features.
All stored in `~/.decksmith/config.yaml`, never logged, never printed.

No API key is needed for `push-cues` — it talks directly to the local
Rekordbox database. Rekordbox must be closed during writes.

| Key           | Unlocks                                    | Free? |
|---------------|--------------------------------------------|-------|
| Groq          | AI genre tagging, smart set building       | Yes   |
| Spotify       | Cover art, track metadata search           | Yes   |
| AcoustID      | Audio fingerprinting for unknown tracks    | Yes   |
| Discogs       | Richer electronic-music metadata           | Yes   |
| ListenBrainz  | Personalised recommendations               | Yes   |

Add or replace keys at any time:

```bash
decksmith settings --key groq
decksmith settings --key all        # walk through every key
```

The config file also supports `${ENV_VAR}` interpolation:

```yaml
apis:
  groq_key: "${GROQ_API_KEY}"
```

---

## Safety

- Every write path **backs up original tags to SQLite** before touching the file.
- `decksmith undo --last` restores the whole most recent batch.
- `decksmith undo <path>` restores a specific file.
- `decksmith push-cues` **backs up Rekordbox's master.db** before every write.
- `decksmith undo --rekordbox` restores the Rekordbox database from backup.
- `*.db`, `config.yaml`, `reports/`, `backups/`, and `.env` are all gitignored.

---

## Project layout

```
src/decksmith/
├── cli.py               # typer CLI (bare decksmith → wizard or dashboard)
├── config.py            # pydantic config + YAML + ${ENV_VAR}
├── setup_wizard.py      # guided first run
├── dashboard.py         # default view
├── settings.py          # key management UI
├── db.py                # SQLite tracking + undo
├── models.py            # shared Track / CuePoint / SetTrack
├── pipeline.py          # full clean → analyze → cue → export run
├── analyze/             # bpm, key, energy, bitrate, spectral, report
├── metadata/            # cleaner, enricher, artwork, fingerprint, rules
├── rekordbox/           # xml_export, cuepoints, folders, grids, db_writer
├── setbuilder/          # builder, flow, llm
├── discover/            # listenbrainz, spotify_meta, scraper, gaps
└── utils/               # ui, tag_io, api_clients, audio, fs
```

---

## Development

Verify imports and the CLI registration:

```bash
python -c "from decksmith import cli"
decksmith --help
```

The test matrix is deliberately small; Decksmith is a personal-use tool, not an
enterprise library. Smoke tests cover the patterns that are easy to break
(metadata I/O round-trips, XML export well-formedness, harmonic rules,
missing-key graceful degradation).

---

## License

MIT.
