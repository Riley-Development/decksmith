"""HTML analysis report with inline SVG frequency spectrum plots.

Generates a self-contained HTML file with:
- Summary statistics (full / partial / failed)
- Per-track table: BPM, key, energy, bitrate status
- Per-track warnings for partial analysis results
- Inline SVG frequency spectrum for each track that has spectral data

The spectrum plots show the **mean magnitude spectrum** (amplitude in dB
vs frequency in Hz) — a frequency-domain view averaged over the full
track.  These are *not* time-frequency spectrograms; they show overall
frequency content rather than how it changes over time.  A vertical
reference line marks the declared bitrate tier's threshold frequency.

No external dependencies beyond numpy.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from decksmith.analyze import AnalysisResult


def _svg_spectrum(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    width: int = 600,
    height: int = 100,
    ref_hz: float | None = None,
) -> str:
    """Render a mean-magnitude frequency spectrum as an inline SVG polyline.

    The x-axis is frequency (linear up to Nyquist), the y-axis is
    magnitude in dB.  An optional vertical line marks a reference
    frequency (e.g. the bitrate tier's min_cutoff_hz).
    """
    if len(spectrum) == 0 or len(freqs) == 0:
        return ""

    max_freq = float(freqs[-1])
    if max_freq == 0:
        return ""

    db = 20.0 * np.log10(spectrum + 1e-10)
    db_min = float(np.min(db))
    db_max = float(np.max(db))
    db_range = db_max - db_min if db_max > db_min else 1.0

    step = max(1, len(freqs) // width)
    points = []
    for i in range(0, len(freqs), step):
        x = freqs[i] / max_freq * width
        y = height - ((db[i] - db_min) / db_range * height)
        points.append(f"{x:.1f},{y:.1f}")

    polyline = " ".join(points)

    ref_line = ""
    if ref_hz is not None and ref_hz > 0:
        cx = ref_hz / max_freq * width
        ref_line = (
            f'<line x1="{cx:.1f}" y1="0" x2="{cx:.1f}" y2="{height}" '
            f'stroke="#e74c3c" stroke-width="1.5" stroke-dasharray="4,2"/>'
            f'<text x="{cx + 3:.1f}" y="12" fill="#e74c3c" font-size="10">'
            f'{ref_hz:.0f}Hz</text>'
        )

    return (
        f'<svg width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="background:#1a1a2e;border-radius:4px">'
        f'<polyline points="{polyline}" fill="none" stroke="#00d2ff" stroke-width="1"/>'
        f'{ref_line}'
        f'</svg>'
    )


def _bitrate_badge(authentic: bool | None) -> str:
    if authentic is None:
        return '<span style="color:#888">\u2014</span>'
    if authentic:
        return '<span style="color:#2ecc71">&#x2713; OK</span>'
    return '<span style="color:#e74c3c">&#x2717; fake</span>'


def _energy_bar(energy: int | None) -> str:
    if energy is None:
        return "\u2014"
    filled = "&#x2588;" * energy
    empty = "&#x2591;" * (10 - energy)
    if energy >= 7:
        color = "#e74c3c"
    elif energy >= 4:
        color = "#f39c12"
    else:
        color = "#2ecc71"
    return f'<span style="color:{color};font-family:monospace">{filled}{empty}</span> {energy}'


def _warnings_html(warnings: list[str]) -> str:
    """Render per-track warnings as a compact list under the track row."""
    if not warnings:
        return ""
    items = "".join(
        f"<li>{html.escape(w)}</li>" for w in warnings
    )
    return (
        f'<ul style="margin:0.2rem 0 0 0;padding-left:1.2rem;'
        f'color:#f39c12;font-size:0.8rem;list-style:none">'
        f'{items}</ul>'
    )


def generate_report(
    results: list[AnalysisResult],
    output_path: str | Path,
) -> Path:
    """Write an HTML analysis report to *output_path*.

    Returns the resolved Path so the caller can print it.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    full = [r for r in results if r.ok and not r.partial]
    partial = [r for r in results if r.partial]
    failed = [r for r in results if r.failed]
    flagged = [r for r in results if r.ok and r.bitrate_authentic is False]
    bpms = [r.bpm for r in results if r.ok and r.bpm]
    energies = [r.energy for r in results if r.ok and r.energy]

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = []
    for r in results:
        name = html.escape(Path(r.filepath).name)

        # --- Failed track: error spans entire row ---
        if r.failed:
            err_msg = html.escape(r.error or "No analysis modules produced data")
            warn_block = _warnings_html(r.warnings)
            rows.append(
                f'<tr><td>{name}{warn_block}</td>'
                f'<td colspan="6" style="color:#e74c3c">{err_msg}</td></tr>'
            )
            continue

        # --- OK or partial track ---
        bpm_str = f"{r.bpm:.1f}" if r.bpm else "\u2014"
        key_str = html.escape(r.camelot or "\u2014")
        energy_cell = _energy_bar(r.energy)
        br_str = f"{r.bitrate_declared}k" if r.bitrate_declared else "\u2014"
        badge = _bitrate_badge(r.bitrate_authentic)
        explanation = html.escape(r.bitrate_explanation or "")

        # Warnings block (shown inline under the track name)
        warn_block = _warnings_html(r.warnings)

        # Frequency spectrum SVG
        if r.spectrum is not None and r.spectrum_freqs is not None:
            ref_hz = None
            if r.bitrate_declared:
                from decksmith.analyze.bitrate import DEFAULT_THRESHOLDS
                tier = DEFAULT_THRESHOLDS.get(r.bitrate_declared)
                if tier:
                    ref_hz = tier["min_cutoff_hz"]
            svg = _svg_spectrum(r.spectrum, r.spectrum_freqs, ref_hz=ref_hz)
        else:
            svg = '<span style="color:#888">no spectral data</span>'

        rows.append(
            f"<tr>"
            f"<td>{name}{warn_block}</td>"
            f"<td>{bpm_str}</td>"
            f"<td>{key_str}</td>"
            f"<td>{energy_cell}</td>"
            f"<td>{br_str} {badge}</td>"
            f"<td>{explanation}</td>"
            f"<td>{svg}</td>"
            f"</tr>"
        )

    table_html = "\n".join(rows)

    # Pre-compute stat labels outside the f-string so Python 3.11 doesn't
    # choke on backslash-containing unicode escapes inside expression parts.
    bpm_range_str = f"{min(bpms):.0f}–{max(bpms):.0f}" if bpms else "—"
    energy_range_str = f"{min(energies)}–{max(energies)}" if energies else "—"

    page = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Decksmith Analysis Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0f0f1a; color: #e0e0e0; margin: 2rem; }}
  h1 {{ color: #00d2ff; }}
  h2 {{ color: #aaa; font-weight: normal; }}
  .stats {{ display: flex; gap: 2rem; margin: 1rem 0 2rem; flex-wrap: wrap; }}
  .stat {{ background: #1a1a2e; padding: 1rem 1.5rem; border-radius: 8px; }}
  .stat .num {{ font-size: 1.8rem; font-weight: bold; color: #00d2ff; }}
  .stat .label {{ color: #888; font-size: 0.85rem; }}
  .stat.warn .num {{ color: #f39c12; }}
  .stat.err .num {{ color: #e74c3c; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th {{ text-align: left; padding: 0.6rem 0.8rem; border-bottom: 2px solid #333;
       color: #888; font-size: 0.85rem; text-transform: uppercase; }}
  td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid #222; vertical-align: top; }}
  tr:hover {{ background: #1a1a2e; }}
  .footer {{ margin-top: 2rem; color: #555; font-size: 0.8rem; }}
</style>
</head>
<body>
<h1>Decksmith Analysis Report</h1>
<h2>{timestamp}</h2>

<div class="stats">
  <div class="stat"><div class="num">{len(full)}</div><div class="label">Fully analyzed</div></div>
  <div class="stat warn"><div class="num">{len(partial)}</div><div class="label">Partial (warnings)</div></div>
  <div class="stat err"><div class="num">{len(failed)}</div><div class="label">Failed</div></div>
  <div class="stat"><div class="num">{len(flagged)}</div><div class="label">Fake bitrate</div></div>
  <div class="stat"><div class="num">{bpm_range_str}</div><div class="label">BPM range</div></div>
  <div class="stat"><div class="num">{energy_range_str}</div><div class="label">Energy range</div></div>
</div>

<table>
<thead>
<tr>
  <th>Track</th><th>BPM</th><th>Key</th><th>Energy</th>
  <th>Bitrate</th><th>Notes</th><th>Frequency Spectrum</th>
</tr>
</thead>
<tbody>
{table_html}
</tbody>
</table>

<div class="footer">
  Generated by Decksmith &middot; {timestamp}<br>
  Frequency spectrum plots show mean magnitude (dB) vs frequency (Hz).
  Red dashed line marks the expected threshold for the declared bitrate.
</div>
</body>
</html>
"""

    output_path.write_text(page, encoding="utf-8")
    return output_path
