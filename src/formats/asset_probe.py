"""asset_probe.py — local, measurable analysis of a downloaded candidate.

Nothing here is allowed to guess: every number is computed from decoded frames or
from ffprobe, and an analysis that cannot run returns None (the scorer then marks
the candidate `needs_human_review` instead of pretending it is clean).

What is measured
* luma / saturation          — same implementation as the format style profile
* motion                     — mean frame difference + hard scene-cut count
* text-safe busyness         — gradient energy inside the template's text band
* burned-in text suspicion   — persistent horizontal bands of glyph-like strokes,
                               sampled across three frames. This is a heuristic,
                               not OCR: it can say "suspected", and "unknown" when
                               frames cannot be decoded. It never upgrades an
                               uncertain clip to clean. Uniform dark areas are NOT
                               text: measured evidence showed a legitimate dark
                               interior being flagged, so uniform bars are reported
                               separately as framing (letterbox/banner), never as
                               a caption.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .. import config, render_gate

# Bands are fractions of frame height, matching the two template contracts.
TEXT_BAND = {"caption_list_title_v1": (0.41, 0.59),
             "social_moment_loop_v1": (0.30, 0.61)}


def _ffmpeg() -> str:
    return config.resolve_ffmpeg()


def probe(path) -> dict:
    return render_gate.probe(path)


def _grab(path, n: int, width: int, out_dir: Path) -> list[np.ndarray]:
    """Decode n evenly spaced frames at a small width. Returns [] on failure."""
    info = probe(path)
    dur = float(info.get("format", {}).get("duration") or 0)
    if dur <= 0:
        return []
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        dest = out_dir / f"f{i:02d}.png"
        r = subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={width}:-2", str(dest)],
            capture_output=True, timeout=120)
        if r.returncode != 0 or not dest.exists():
            continue
        try:
            frames.append(np.asarray(Image.open(dest).convert("RGB"), dtype=np.uint8))
        except Exception:  # noqa: BLE001
            continue
    return frames


def _gray(rgb: np.ndarray) -> np.ndarray:
    return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])


def motion_profile(path) -> dict:
    """Mean/max frame difference and cut count. None when undecodable."""
    with tempfile.TemporaryDirectory(prefix="beast-motion-") as td:
        frames = _grab(path, 9, 160, Path(td))
    if len(frames) < 2:
        return {"ok": False, "motion": None, "cuts": None, "max_jump": None}
    diffs = [float(np.abs(_gray(frames[i]).astype(np.int16) -
                          _gray(frames[i - 1]).astype(np.int16)).mean()) / 255.0
             for i in range(1, len(frames))]
    return {"ok": True, "motion": round(float(np.mean(diffs)), 4),
            "max_jump": round(float(np.max(diffs)), 4),
            "cuts": int(sum(1 for d in diffs if d > 0.22)), "samples": len(diffs)}


def _edge_rows(gray: np.ndarray) -> np.ndarray:
    gx = np.abs(np.diff(gray, axis=1))
    gy = np.abs(np.diff(gray, axis=0))
    g = np.zeros_like(gray)
    g[:, :-1] += gx
    g[:-1, :] += gy
    return g


def _text_bands(gray: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous row bands with text-like edge coverage (moderate, not full)."""
    h, w = gray.shape
    cover = (_edge_rows(gray) > 45).sum(axis=1) / max(w, 1)
    rows = cover > 0.18
    bands, start = [], None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if 6 <= y - start <= int(h * 0.22):
                seg = cover[start:y]
                # text rows are moderately covered; a solid horizon/edge is not
                if float(np.mean((seg > 0.18) & (seg < 0.75))) > 0.55:
                    bands.append((start, y))
            start = None
    if start is not None and 6 <= h - start <= int(h * 0.22):
        bands.append((start, h))
    return bands


def _uniform_bands(gray: np.ndarray) -> list[tuple[int, int]]:
    """Near-uniform horizontal bars: letterboxing, banners, lower-thirds."""
    h, w = gray.shape
    std = gray.std(axis=1)
    rows = std < 6.0
    bands, start = [], None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= int(h * 0.07):
                bands.append((start, y))
            start = None
    if start is not None and h - start >= int(h * 0.07):
        bands.append((start, h))
    return bands


def text_suspicion(path) -> dict:
    """Does this clip carry its own captions/banner? True/False/None (unknown)."""
    with tempfile.TemporaryDirectory(prefix="beast-text-") as td:
        frames = _grab(path, 3, 360, Path(td))
    if len(frames) < 2:
        return {"text_suspected": None, "reason": "frames undecodable",
                "bands": [], "uniform": []}
    per_frame = []
    for f in frames:
        g = _gray(f)
        per_frame.append((_text_bands(g), _uniform_bands(g)))
    # persistent = same band present in at least two of three samples
    counts: dict[tuple[int, int], int] = {}
    for bands, _ in per_frame:
        for b in bands:
            key = (round(b[0] / 8) * 8, round(b[1] / 8) * 8)
            counts[key] = counts.get(key, 0) + 1
    persistent = [b for b, c in counts.items() if c >= 2]
    uniform = [b for _, u in per_frame for b in u]
    h = frames[0].shape[0]
    big_uniform = [b for b in uniform if (b[1] - b[0]) / h >= 0.08]
    # Uniform bars are framing (letterbox, banner, dark ceiling), not captions.
    framing = [{"rows": list(b), "fraction_of_height": round((b[1] - b[0]) / h, 3)}
               for b in big_uniform]
    if persistent:
        return {"text_suspected": True,
                "reason": f"persistent glyph-like band {persistent[0]}",
                "bands": persistent, "uniform": big_uniform, "framing": framing}
    return {"text_suspected": False,
            "reason": ("no persistent glyph-like band"
                       + (f"; uniform framing bands {big_uniform[:2]}" if big_uniform else "")),
            "bands": [], "uniform": big_uniform, "framing": framing}


def band_busyness(path, format_id: str) -> float | None:
    """Gradient energy inside the template's text band: lower is more legible."""
    lo, hi = TEXT_BAND.get(format_id, (0.35, 0.65))
    with tempfile.TemporaryDirectory(prefix="beast-band-") as td:
        frames = _grab(path, 3, 270, Path(td))
    if not frames:
        return None
    vals = []
    for f in frames:
        g = _gray(f)
        a, b = int(g.shape[0] * lo), int(g.shape[0] * hi)
        band = g[a:b]
        if band.size == 0:
            continue
        vals.append(float((_edge_rows(band) > 45).mean()))
    return round(float(np.mean(vals)), 4) if vals else None


def contact_sheet(path, out: Path, *, columns: int = 5) -> bool:
    """start/25/50/75/end strip for human review. Never used to 'prove' quality."""
    with tempfile.TemporaryDirectory(prefix="beast-sheet-") as td:
        frames = _grab(path, columns, 180, Path(td))
    if not frames:
        return False
    h, w = frames[0].shape[:2]
    sheet = Image.new("RGB", (w * len(frames), h), (12, 12, 14))
    for i, f in enumerate(frames):
        sheet.paste(Image.fromarray(f), (i * w, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return True


def analyze(path, format_id: str) -> dict:
    """One call for everything the scorer needs. Missing pieces stay None."""
    info = probe(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    from . import template_renderer
    try:
        metrics = template_renderer.measure_luma_sat(path)
    except Exception:  # noqa: BLE001
        metrics = {"yavg": None, "sat": None}
    return {
        "width": int(v.get("width") or 0), "height": int(v.get("height") or 0),
        "duration_s": round(float(info.get("format", {}).get("duration") or 0), 2),
        "fps": v.get("r_frame_rate"), "codec": v.get("codec_name"),
        "has_audio": any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
        "metrics": metrics,
        "motion": motion_profile(path),
        "text": text_suspicion(path),
        "framing": (text_suspicion(path).get("framing") or []),
        "band_busy": band_busyness(path, format_id),
    }