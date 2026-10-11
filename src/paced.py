"""paced.py — BEAST ENGINE v2 paced-text renderer.

STRATEGY §3.2 is explicit about the format, and it is the opposite of the old
one-block render that shipped at 8.2 words/s:

  * the hook is on screen ALONE at frame 0
  * each following line appears at its own reveal time (lint.reveal_schedule),
    held for its own reading time (0.34 s/word, min 1.6 s)
  * when line i+1 appears, line i drops to 55% alpha so the eye follows the newest
    line
  * everything stays visible for the final 2 s so the loop feels intentional
  * the CTA, if any, is a small footer in the last ~2.5 s only

This module renders that. It lives beside build_video.py (which still owns the
one-block format and the QA gate) rather than replacing it, so the working
pipeline is not disturbed while v2 content flows through the same plumbing.

The 55% dim is done with two overlays per line — full alpha from its reveal to
the next line's reveal, then 55% from there on. ffmpeg cannot vary a static
filter's alpha over time, so the split is the honest way to express it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config, lint
from .build_video import FFMPEG, assert_fits, fit_common_px, render_single_line

DIM_ALPHA = 0.55
CTA_TAIL_S = 2.5


def _safe_band(cfg) -> tuple:
    """The band every line is centred in (STRATEGY §3.2: 13-72% of height)."""
    band = (cfg.get("text_limits") or {}).get("safe_band")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        lo, hi = float(band[0]), float(band[1])
        # tolerate 0-1 or 0-100 notation
        if hi <= 1.0:
            return lo, hi
        return lo / 100.0, hi / 100.0
    return 0.13, 0.72


def render_layers(lines: list, cfg, out_dir: Path, layout: str = "stack") -> tuple:
    """One transparent PNG per line, positioned in the safe band.

    Returns (png_paths, px). All lines share ONE font size: fitting each line
    independently made short lines render large and long lines shrink, and the
    block shipped visibly mixed sizes, which reads as broken rather than designed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    w = int(cfg["reel"]["w"])
    band_lo, band_hi = _safe_band(cfg)
    n = max(len(lines), 1)

    if layout == "single":
        # Only the current line shows, so it can be larger and always centred.
        px = fit_common_px(lines, cfg, start_px=72, y_band=(band_lo, band_hi))
        pngs = []
        for i, ln in enumerate(lines):
            p = out_dir / f"pace_{i}.png"
            render_single_line(ln, p, cfg, px=px, y_frac=0.42)
            pngs.append(p)
        return pngs, px

    # stack: every line has its own row, and rows accumulate
    px = fit_common_px(lines, cfg, start_px=64, y_band=(band_lo, band_hi))
    pngs = []
    for i, ln in enumerate(lines):
        slot = band_lo + (i + 0.5) * (band_hi - band_lo) / n
        p = out_dir / f"pace_{i}.png"
        render_single_line(ln, p, cfg, px=px, y_frac=slot)
        pngs.append(p)
    return pngs, px


def assemble_paced(bg_mp4: Path, pngs: list, starts: list, duration_s: float,
                   out_mp4: Path, cfg, audio: Path | None = None,
                   cta_png: Path | None = None, cta_at: float | None = None,
                   bg_is_video: bool = True) -> bool:
    """Composite the paced reveal. Returns True on a usable file."""
    fps = int(cfg["reel"]["fps"])
    w, h = int(cfg["reel"]["w"]), int(cfg["reel"]["h"])

    inputs = []
    if not bg_is_video:
        inputs += ["-loop", "1", "-t", f"{duration_s:.3f}"]
    inputs += ["-stream_loop", "-1", "-t", f"{duration_s:.3f}", "-i", str(bg_mp4)]
    for p in pngs:
        inputs += ["-loop", "1", "-t", f"{duration_s:.3f}", "-i", str(p)]
    cta_idx = None
    if cta_png is not None:
        cta_idx = 2 + len(pngs)
        inputs += ["-loop", "1", "-t", f"{duration_s:.3f}", "-i", str(cta_png)]
    audio_idx = None
    if audio and str(audio).strip() and Path(audio).is_file():
        audio_idx = (cta_idx + 1) if cta_idx is not None else (2 + len(pngs))
        inputs += ["-stream_loop", "-1", "-t", f"{duration_s:.3f}", "-i", str(audio)]

    # The background ships as shot (same rule as the one-block render): scale and
    # crop to cover 9:16, no grade, and square pixels.
    filters = [
        f"[0:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,format=yuv420p[base]"
    ]

    # PACE: each line at full alpha from its reveal until the next reveal, then
    # dimmed to 55% so the newest line is the one the eye lands on.
    #
    # EACH LAYER IS CONVERTED TO RGBA ONCE and fanned out to its two overlays.
    # The first version wrote `[N:v]format=rgba,...` twice per line (once for the
    # full-alpha copy, once for the dimmed copy), so a 7-line reel built 13 rgba
    # conversions of the same PNG and the encode timed out at 900s. Splitting with
    # `split=2` after a single conversion is what makes this tractable.
    #
    # The dim is applied by compositing with opacity rather than colorchannelmixer,
    # because opacity is a single cheap operation on the overlay itself.
    last = "base"
    for i, _p in enumerate(pngs):
        src = i + 1
        full_from = starts[i]
        dim_from = starts[i + 1] if i + 1 < len(starts) else duration_s
        if dim_from < duration_s:
            # ONE rgba conversion fanned out to both overlays. Writing the
            # conversion twice per line built 13 of them for a 7-line reel and the
            # encode timed out; split=2 is what makes it tractable.
            filters.append(f"[{src}:v]format=rgba,split=2[fa{i}][dax{i}]")
            # colorchannelmixer=aa=N multiplies the alpha channel. `overlay`'s own
            # alpha= option does NOT do this: it was measured at 98% retained
            # brightness, i.e. silently ignored, so the older line stayed at full
            # alpha and the pacing had no visible effect.
            filters.append(f"[dax{i}]colorchannelmixer=aa={DIM_ALPHA}[da{i}]")
            filters.append(
                f"[{last}][fa{i}]overlay=0:0:eof_action=pass:"
                f"enable='between(t,{full_from:.3f},{dim_from:.3f})'[f{i}]")
            filters.append(
                f"[f{i}][da{i}]overlay=0:0:eof_action=pass:"
                f"enable='gte(t,{dim_from:.3f})'[d{i}]")
            last = f"d{i}"
        else:
            # The LAST line never dims, so it must NOT be split: an unused split
            # output is a hard ffmpeg error ("Filter split has an unconnected
            # output"), which is exactly how this render failed the first time.
            filters.append(f"[{src}:v]format=rgba[fa{i}]")
            filters.append(
                f"[{last}][fa{i}]overlay=0:0:eof_action=pass:"
                f"enable='gte(t,{full_from:.3f})'[f{i}]")
            last = f"f{i}"

    # CTA footer: the last ~2.5 s only, small, below the block.
    if cta_idx is not None and cta_at is not None:
        filters.append(f"[{cta_idx}:v]format=rgba[cta]")
        filters.append(
            f"[{last}][cta]overlay=0:0:eof_action=pass:"
            f"enable='gte(t,{cta_at:.3f})'[withcta]")
        last = "withcta"

    audio_map = []
    if audio_idx is not None:
        fade = 0.5
        filters.append(
            f"[{audio_idx}:a]loudnorm=I=-22:TP=-1.5:LRA=11,"
            f"afade=t=in:st=0:d=0.3,"
            f"afade=t=out:st={max(duration_s - fade, 0):.3f}:d={fade},"
            f"atrim=0:{duration_s:.3f},asetpts=N/SR/TB[aout]")
        audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]

    cmd = [FFMPEG, "-y", "-v", "error"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", f"[{last}]", *audio_map,
        "-t", f"{duration_s:.3f}",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-profile:v", "high", "-level", "4.1",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-movflags", "+faststart", str(out_mp4)]
    # The paced graph stacks up to 14 overlay passes, so the encode is far heavier
    # than the one-block render. `veryslow` at 20s/30fps exceeded a 900s budget on
    # this machine; `medium` at CRF 18 renders in well under a minute and the
    # difference is invisible after Instagram re-encodes. Measured, not guessed:
    # veryslow timed out, medium completes.
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        print(f"[paced] assemble failed: {r.stderr[-1200:]}")
        return False
    if not out_mp4.exists() or out_mp4.stat().st_size < 4096:
        print(f"[paced] produced no usable file")
        return False
    return True


def qa_paced(lines: list, starts: list, duration_s: float, cfg,
             out_mp4: Path) -> tuple:
    """The v2 gate (ENGINE_SPEC §1.2). Returns (ok, info).

    Checks, all measured on the numbers that produced the build:
      * every line starts >= 1.0 s before the next one
      * words/s <= 3.2 for each line, so no line is unreadable
      * first frame is the hook alone (the reveal schedule guarantees it, asserted)
      * duration within 14-34 s
    """
    problems = []
    for i, ln in enumerate(lines):
        words = len(lint.words(ln))
        gap = (starts[i + 1] - starts[i]) if i + 1 < len(starts) else (
            duration_s - starts[i])
        if gap < 1.0:
            problems.append(f"line {i + 1} held {gap:.2f}s (need >= 1.0s)")
        rate = words / gap if gap > 0 else 99
        if rate > 3.2:
            problems.append(f"line {i + 1} is {rate:.1f} words/s (max 3.2)")
    if not (lint.MIN_DUR <= duration_s <= lint.MAX_DUR):
        problems.append(f"duration {duration_s}s outside {lint.MIN_DUR}-{lint.MAX_DUR}s")
    if starts and starts[0] != 0.0:
        problems.append(f"first line starts at {starts[0]}s, not 0.0 (hook must open the reel)")
    return (not problems), {"problems": problems, "duration_s": duration_s,
                            "lines": len(lines), "starts": starts}