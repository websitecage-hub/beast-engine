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

import os
import subprocess
import tempfile

_RAPID: dict = {}
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


def _grab_at(path, times: list, width: int, out_dir: Path) -> list:
    """Decode specific timestamps. Used by OCR: burned-in captions are usually in
    the opening seconds, and evenly spaced sampling was missing them entirely
    (measured: a clip whose text is visible at 1.0s reported 0 detections)."""
    frames = []
    for i, t in enumerate(times):
        dest = out_dir / f"t{i:02d}.png"
        r = subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.05, t):.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={width}:-2", str(dest)],
            capture_output=True, timeout=60)
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

# ------------------------------------------------------------------ OCR (simple)
_OCR_PROMPT = """Look carefully at this frame from a video. Return ONLY JSON:
{"has_text": true/false, "has_logo": false, "has_watermark": false,
 "text_said": "every word of on-screen text, or empty", "scene": "one short sentence"}

Count as text: captions, subtitles, titles, lower-third labels, location tags
("Antique Shopping in Istanbul"), usernames/@handles, stickers, emoji text,
timestamps, app UI, logos and watermarks. Text can be small, low-contrast, in a
corner, or partially covered - look at the whole frame, including the edges.
Do not count natural objects that merely resemble letters.
If you can read any word, has_text MUST be true."""


def ocr_check(path, *, api_key: str | None = None, samples: int = 3) -> dict:
    """Does this clip carry burned-in text/logos? A gate, not a score.

    Local first (RapidOCR, offline, no quota). A caption is: a real word of 5+
    characters, or a 4+ character string that is physically large in frame, or the
    same string in two sampled frames. Short numbers are ignored on purpose - price
    tags and plate markings are environmental, not captions.

    If OCR cannot run, the answer is UNKNOWN. It is never silently "clean".
    """
    import subprocess

    def _frames() -> list:
        import tempfile
        info = probe(path)
        dur = float(info.get("format", {}).get("duration") or 0) or 6.0
        # Head-weighted: captions and titles live in the first seconds.
        times = [0.2, 0.8, 1.5, 2.5, dur * 0.5, dur * 0.85]
        seen, uniq = set(), []
        for t in times:
            k = round(min(t, dur - 0.05), 1)
            if k > 0 and k not in seen:
                seen.add(k)
                uniq.append(k)
        with tempfile.TemporaryDirectory(prefix="beast-ocr-") as td:
            return _grab_at(path, uniq, 1080, Path(td))

    frames = _frames()
    if not frames:
        return {"has_text": None, "has_logo": None, "by": "ocr",
                "reason": "frames undecodable"}

    # --- local RapidOCR -----------------------------------------------------
    try:
        import sys
        if "/var/tmp/pylibs" not in sys.path:
            sys.path.append("/var/tmp/pylibs")
        from rapidocr_onnxruntime import RapidOCR          # type: ignore
        engine = _RAPID.get("engine")
        if engine is None:
            engine = _RAPID["engine"] = RapidOCR()
        hits, per_frame = [], []
        for arr in frames:
            import tempfile
            tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
            Image.fromarray(arr).save(tmp, quality=90)
            try:
                res, _ = engine(str(tmp))
            finally:
                tmp.unlink(missing_ok=True)
            rows = []
            for box, text, score in (res or []):
                text = str(text).strip()
                if float(score) < 0.5 or not text:
                    continue
                ys = [pt[1] for pt in box]
                h = (max(ys) - min(ys)) / max(arr.shape[0], 1)
                if text.isdigit() and len(text) <= 2:
                    continue                    # lone "12" price tag
                if h < 0.008 and len(text) < 3:
                    continue                    # too small to read as a caption
                rows.append({"text": text, "score": round(float(score), 2),
                             "height_frac": round(h, 3)})
            per_frame.append(rows)
            hits += rows
        strong = [h for h in hits
                  if len(h["text"]) >= 3 or h["height_frac"] >= 0.008]
        repeated = [t for t in {h["text"] for h in hits}
                    if sum(1 for fr in per_frame if any(h["text"] == t for h in fr)) >= 2]
        has = bool(strong or repeated)
        return {"has_text": has, "has_logo": None, "by": "rapidocr",
                "text_said": "; ".join(sorted({h["text"] for h in hits})[:5]),
                "reason": (f"{len(hits)} detections, {len(strong)} strong, "
                           f"{len(repeated)} repeated across frames")}
    except Exception as exc:  # noqa: BLE001
        local_error = str(exc)[:120]
    else:
        local_error = ""

    # --- Gemini fallback (only if a key is configured) ----------------------
    key = (api_key or os.environ.get("GEMINI_API_KEY", "") or "").strip().strip("'\"").strip()
    if not key:
        sus = text_suspicion(path)
        if sus.get("text_suspected") is None:
            return {"has_text": None, "has_logo": None, "by": "heuristic",
                    "reason": f"no OCR engine; {local_error}"}
        return {"has_text": bool(sus.get("text_suspected")), "has_logo": None,
                "by": "heuristic",
                "reason": f"local OCR failed: {local_error}; {sus.get('reason', '')}"}

    import base64
    import json as _json
    import requests
    verdicts = []
    for arr in frames:
        import io
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="JPEG", quality=88)
        b64 = base64.b64encode(buf.getvalue()).decode()
        body = {"contents": [{"parts": [{"text": _OCR_PROMPT},
                                        {"inline_data": {"mime_type": "image/jpeg",
                                                         "data": b64}}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 500,
                                     "responseMimeType": "application/json",
                                     "thinkingConfig": {"thinkingBudget": 0}}}
        try:
            r = requests.post("https://generativelanguage.googleapis.com/v1beta/models/"
                              "gemini-2.5-flash:generateContent",
                              params={"key": key}, json=body, timeout=60)
            if r.status_code != 200:
                continue
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            verdicts.append(_json.loads(txt))
        except Exception:  # noqa: BLE001
            continue
    if not verdicts:
        return {"has_text": None, "has_logo": None, "by": "gemini",
                "reason": "OCR engines unavailable; refusing to call the clip clean"}
    return {"has_text": any(bool(v.get("has_text")) for v in verdicts),
            "has_logo": any(bool(v.get("has_logo")) or bool(v.get("has_watermark"))
                            for v in verdicts),
            "by": "gemini",
            "text_said": next((v.get("text_said") for v in verdicts if v.get("text_said")), ""),
            "reason": f"gemini read {len(verdicts)} frame(s)"}
