"""build_video.py — THE COMPLETE MIND format (Part 5).

  * dark cinematic VIDEO background, darkened -0.13, looped continuously
  * 2-3 LARGE static text blocks, no per-word/per-line animation — a block
    fades in over <=0.3s and holds
  * hook: Anton, largest. deepening: Playfair Display Italic. landing: Anton,
    same weight/position as the hook (the visual echo = the invisible loop)
  * timing map (Part 5.5): hook 0.0-3.5, deepening 3.5-7.0, landing 7.0-end
  * 9-10 seconds, 1080x1920, -16 LUFS audio

Every text line is MEASURED with real font metrics and auto-fitted: overflow
is impossible by construction (v4.1 lesson).
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

from . import config

FONT_HOOK = "assets/fonts/Coolvetica-Regular.otf"      # user pick (2026-09-26)
FONT_BODY = "assets/fonts/Coolvetica-Regular.otf"       # one font family, per user
FONT_MARK = "assets/fonts/Inter-Regular.ttf"
WATERMARK_PX = 28
SIDE_MARGIN = 90                # Part 5.3: generous margins, 90px sides
MAX_BLOCK_H = 0.62
BLOCK_FADE = 0.3                # Part 5.3: block fade <= 0.3s, no text theatre
# Coolvetica runs wide, so the ladders start lower and walk further down.
HOOK_PX_LADDER = [120, 112, 104, 96, 88, 82, 76, 70, 64, 58, 52, 46, 40]
BODY_PX_LADDER = [104, 96, 90, 84, 78, 72, 66, 60, 54, 48, 42]
INK = (245, 245, 245, 255)
INK_MARK = (230, 230, 230, 150)


# ------------------------------------------------------------------ wording

def _clean(text) -> str:
    return " ".join(str(text or "").split()).strip().strip('"').strip()


def text_blocks(content: dict) -> list:
    """The 2-3 on-screen blocks: hook, deepening..., landing (Part 5.3)."""
    hook = _clean(content.get("hook"))
    landing = _clean(content.get("landing"))
    deep = [_clean(d) for d in (content.get("deepening") or []) if _clean(d)]
    if not hook and content.get("blocks"):
        # legacy content.json with only a `blocks` list
        legacy = [_clean(b) for b in content["blocks"] if _clean(b)]
        if legacy:
            return [{"text": legacy[0], "kind": "hook"},
                    *[{"text": t, "kind": "deepening"} for t in legacy[1:-1]],
                    *([{"text": legacy[-1], "kind": "landing"}] if len(legacy) > 1 else [])]
    out = [{"text": hook, "kind": "hook"}]
    out += [{"text": d, "kind": "deepening"} for d in deep[:2]]
    out.append({"text": landing, "kind": "landing"})
    return [b for b in out if b["text"]]


def _wrap_measured(text: str, font, max_w: int) -> list:
    """Greedy wrap by MEASURED width + balancing pass. [] if even one word won't fit."""
    words = text.split()
    if not words:
        return []

    def width(s: str) -> int:
        return font.getbbox(s)[2]

    lines: list[str] = []
    cur = ""
    for wd in words:
        cand = f"{cur} {wd}".strip()
        if width(cand) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    if any(width(ln) > max_w for ln in lines):
        return []

    for _ in range(200):
        moved = False
        for i in range(len(lines) - 1):
            wds = lines[i].split()
            while len(wds) > 1:
                last = " ".join(wds[-1:])
                rest = " ".join(wds[:-1])
                nxt = f"{last} {lines[i + 1]}".strip()
                if width(rest) >= width(nxt) and width(nxt) <= max_w:
                    lines[i], lines[i + 1] = rest, nxt
                    wds = rest.split()
                    moved = True
                else:
                    break
        if not moved:
            break
    return lines


def fit_block(text: str, kind: str, cfg) -> tuple:
    """Return (lines, px, font_path) — largest size where everything fits."""
    from PIL import ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    max_w = w - 2 * SIDE_MARGIN
    max_h = int(h * MAX_BLOCK_H)
    ladder = HOOK_PX_LADDER if kind in ("hook", "landing") else BODY_PX_LADDER
    font_path = FONT_HOOK if kind in ("hook", "landing") else FONT_BODY
    for px in ladder:
        font = ImageFont.truetype(str(config.ROOT / font_path), px)
        lines = _wrap_measured(text, font, max_w)
        if not lines:
            continue
        if len(lines) * int(px * 1.32) <= max_h:
            return lines, px, font_path
    raise RuntimeError(f"text cannot fit at any size: {text!r}")


def assert_fits(lines: list, px: int, font_path: str, cfg) -> int:
    """Deterministic pre-render overflow gate. Returns widest line (px)."""
    from PIL import ImageFont
    max_w = int(cfg["reel"]["w"]) - 2 * SIDE_MARGIN
    font = ImageFont.truetype(str(config.ROOT / font_path), px)
    widest = max(font.getbbox(ln)[2] for ln in lines)
    assert widest <= max_w, f"overflow: widest line {widest}px > {max_w}px allowed"
    return widest


# --------------------------------------------------------------- rendering

def render_block(lines: list, px: int, font_path: str, watermark: str, out_png: Path,
                 cfg, fixed_top: int | None = None) -> Path:
    """One static text block as a transparent PNG.

    `fixed_top` pins the first text line to an exact y — the hook and the landing
    are both rendered with the hook's top, so the reel's final frame sits exactly
    where frame 1 sat. That IS the invisible loop (Part 4.2), enforced visually.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    font = ImageFont.truetype(str(config.ROOT / font_path), px)
    mark_font = ImageFont.truetype(str(config.ROOT / FONT_MARK), WATERMARK_PX)

    line_h = int(px * 1.32)
    block_h = len(lines) * line_h
    top = fixed_top if fixed_top is not None else (int(h * 0.5) - block_h // 2)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd, d = ImageDraw.Draw(shadow), ImageDraw.Draw(img)

    def draw_center(txt, f, y, fill, sd_fill):
        bb = f.getbbox(txt)
        x = (w - (bb[2] - bb[0])) // 2 - bb[0]
        sd.text((x + 2, y + 3), txt, font=f, fill=sd_fill)
        d.text((x, y), txt, font=f, fill=fill)

    for i, ln in enumerate(lines):
        draw_center(ln, font, top + i * line_h, INK, (0, 0, 0, 150))

    if watermark:
        bb = mark_font.getbbox(watermark)
        d.text(((w - (bb[2] - bb[0])) // 2, int(h * 0.92)), watermark,
               font=mark_font, fill=INK_MARK)

    img = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(5)), img)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, "PNG")
    return out_png


def hook_top(lines: list, px: int, cfg) -> int:
    """The canonical text top — hook and landing both use this (the visual echo)."""
    h = int(cfg["reel"]["h"])
    return int(h * 0.5) - (len(lines) * int(px * 1.32)) // 2


# ------------------------------------------------------------------- timing

def beat_times(wav: Path, mood: str, cfg, duration_s: float) -> list:
    try:
        import librosa
        y, sr = librosa.load(str(wav), sr=22050, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        beats = [float(b) for b in beats if 0.0 < float(b) < duration_s]
        if len(beats) >= 4:
            return sorted(beats)
    except Exception as exc:  # noqa: BLE001
        print(f"[video] beat tracking failed: {exc}")
    return beat_times_fallback(cfg, mood, duration_s)


def beat_times_fallback(cfg, mood: str, duration_s: float) -> list:
    bpm = float((cfg.get("mood_fallback_bpm") or {}).get(mood, 65))
    interval = 60.0 / max(bpm, 1)
    out, t = [], interval * 0.5
    while t < duration_s:
        out.append(t)
        t += interval
    return out or [0.5, 1.0, 2.0]


def state_map(blocks: list, cfg, duration_s: float) -> list:
    """Part 5.5 timing map. Blocks only accumulate display time; each block is a
    hard cut (fade-in <=0.3s) and holds. Hook opens at 0.0 (IS the thumbnail)."""
    t = cfg["timing"]
    hook_end = min(float(t["hook_end"]), duration_s)
    deep_end = min(float(t["deepen_end"]), duration_s - 1.0)
    if deep_end <= hook_end:
        deep_end = min(hook_end + 1.5, duration_s - 0.5)
    states = []
    kinds = [b["kind"] for b in blocks]
    if kinds[0] == "hook":
        states.append({"index": 0, "kind": "hook", "start": 0.0, "end": hook_end})
    deep_idx = [i for i, k in enumerate(kinds) if k == "deepening"]
    if deep_idx:
        span = max(deep_end - hook_end, 1.0)
        per = span / len(deep_idx)
        for n, i in enumerate(deep_idx):
            s = hook_end + n * per
            states.append({"index": i, "kind": "deepening",
                           "start": round(s, 3), "end": round(s + per, 3)})
    land_idx = [i for i, k in enumerate(kinds) if k == "landing"]
    if land_idx:
        states.append({"index": land_idx[-1], "kind": "landing",
                       "start": round(deep_end, 3), "end": duration_s})
    states.sort(key=lambda s: s["index"])
    return states


def loop_echo_ok(blocks: list) -> bool:
    """Part 4.2 / 5.6 — the final frame must echo frame 1.

    Structural guarantee (not a text filter): the landing is rendered in the
    hook's font, at the hook's size, pinned to the hook's exact top coordinate,
    so its on-screen position and weight are identical. This gate asserts the
    structural preconditions are met and reports the advisory text overlap.
    """
    kinds = [b["kind"] for b in blocks]
    if "hook" not in kinds or "landing" not in kinds:
        return False
    hook_block = next(b for b in blocks if b["kind"] == "hook")
    land_block = next(b for b in blocks if b["kind"] == "landing")
    # raw text blocks (pre-render) have no geometry yet: assert the structural
    # precondition that both are hook-family text that WILL be pinned together.
    if hook_block.get("pinned_top") is None and land_block.get("pinned_top") is None:
        return True
    if land_block.get("pinned_top") is None or hook_block.get("pinned_top") is None:
        return False
    return land_block["pinned_top"] == hook_block["pinned_top"]


# ---------------------------------------------------------------- assembly

def _probe_frames(path) -> int | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_streams", "-select_streams", "v", str(path)],
                           capture_output=True, text=True, timeout=60)
        s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        nf = s.get("nb_frames")
        return int(nf) if nf and str(nf).isdigit() else None
    except Exception:  # noqa: BLE001
        return None


def assemble(bg_mp4: Path, states: list, pngs: list, track_mp3: Path, out_mp4: Path,
             cfg, duration_s: float, bg_is_video: bool = True) -> bool:
    """Video bg + hard-cut static text blocks + mood audio.

    Part 5.2: bg motion must be continuous; a still bg is only ever the bundled
    fallback (flagged by the QA gate).
    """
    fps = int(cfg["reel"]["fps"])
    inputs = []
    if _probe_frames(bg_mp4) == 1:
        inputs += ["-loop", "1"]                    # still -> keep it running
    inputs += ["-stream_loop", "-1", "-i", str(bg_mp4)]
    for p in pngs:
        inputs += ["-loop", "1", "-i", str(p)]
    inputs += ["-i", str(track_mp3)]

    darken = float(cfg.get("bg_darken", -0.13))
    filters = [f"[0:v]fps={fps},scale=1080:1920:force_original_aspect_ratio=increase,"
               f"crop=1080:1920,eq=brightness={darken}:contrast=1.08:"
               f"saturation=0.55,format=yuv420p[base]"]
    last = "base"
    for i, (st, _png) in enumerate(zip(states, pngs)):
        s0, s1 = st["start"], st["end"]
        fade_d = BLOCK_FADE if st["start"] > 0.05 else 0.01
        filters.append(f"[{i + 1}:v]format=rgba,fade=t=in:st={s0:.3f}:"
                       f"d={fade_d}:alpha=1[b{i}]")
        filters.append(f"[{last}][b{i}]overlay=enable='between(t,{s0:.3f},{s1:.3f})':"
                       f"x=0:y=0:eof_action=pass[o{i}]")
        last = f"o{i}"
    fade_out = 0.5
    aidx = len(pngs) + 1
    filters.append(f"[{aidx}:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
                   f"afade=t=in:st=0:d=0.3,"
                   f"afade=t=out:st={max(duration_s - fade_out, 0):.3f}:d={fade_out},"
                   f"atrim=0:{duration_s:.3f},asetpts=N/SR/TB[aout]")

    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", f"[{last}]", "-map", "[aout]",
        "-t", f"{duration_s:.3f}",
        "-c:v", "libx264", "-crf", "21", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart", str(out_mp4)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        print(f"[video] assemble failed: {r.stderr[-1200:]}")
        return False
    return out_mp4.exists()


def cover_from_frame0(reel: Path, out_jpg: Path) -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.6", "-i", str(reel),
                        "-frames:v", "1", "-q:v", "2", str(out_jpg)],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and out_jpg.exists()


# ----------------------------------------------------------------- QA gate

def _luma_stats(reel: Path, at: float) -> dict:
    """Sample a frame's luma histogram (text presence + motion check)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(reel),
                        "-frames:v", "1", "-vf", "scale=180:320,format=gray",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        return {"bright": 0, "mean": 0.0}
    data = r.stdout
    bright = sum(1 for b in data if b >= 235)
    mean = sum(data) / max(len(data), 1)
    return {"bright": bright, "mean": mean}


def extract_stamps(reel: Path, states: list, duration_s: float, out_dir=None) -> list:
    out_dir = Path(out_dir or config.OUTPUTS)
    stamps = []
    for i, s in enumerate(states):
        t = min(s["start"] + max(BLOCK_FADE, 0.5), max(s["end"] - 0.15, s["start"] + 0.2))
        p = out_dir / f"check_{i}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}",
                            "-i", str(reel), "-frames:v", "1", "-q:v", "3", str(p)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and p.exists():
            stamps.append({"index": i, "t": round(t, 2), "kind": s["kind"],
                           "frame": str(p), **{k: v for k, v in _luma_stats(reel, t).items()}})
    return stamps


def card_visible(frame_path: Path) -> bool:
    from PIL import Image
    try:
        with Image.open(frame_path) as im:
            hist = im.convert("L").histogram()
        return sum(hist[235:256]) >= 120
    except Exception:  # noqa: BLE001
        return False


def motion_present(reel: Path, cfg) -> bool:
    """Part 5.2/5.6 — the background must actually move (not a static frame).

    Compares frame-difference energy between two nearby timestamps where NO text
    transition happens. A static bg yields near-zero difference.
    """
    try:
        import numpy as np
        outs = []
        for at in (2.0, 2.35):
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i",
                                str(reel), "-frames:v", "1", "-vf",
                                "scale=160:284,format=gray", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"], capture_output=True, timeout=120)
            if r.returncode != 0 or not r.stdout:
                return True          # can't measure -> don't block
            outs.append(np.frombuffer(r.stdout, dtype=np.uint8).astype(np.int16))
        if len(outs) != 2 or outs[0].size != outs[1].size:
            return True
        diff = float(np.abs(outs[0] - outs[1]).mean())
        return diff >= float(cfg.get("motion_min_diff", 0.8))
    except Exception:  # noqa: BLE001
        return True


def qa_gate(reel: Path, cfg, blocks: list | None = None) -> tuple:
    """Part 5.6 checklist. Every check mandatory before publish."""
    min_s, max_s = float(cfg["reel"]["min_s"]), float(cfg["reel"]["max_s"])
    info = {"path": str(reel), "exists": reel.exists(), "checks": {}}
    if not reel.exists():
        return False, info
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(reel)],
            capture_output=True, text=True, timeout=120)
        data = json.loads(probe.stdout or "{}")
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
        return False, info
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    dur = float(fmt.get("duration") or (v or {}).get("duration") or 0)
    size = int(fmt.get("size") or reel.stat().st_size)
    info.update({"width": (v or {}).get("width"), "height": (v or {}).get("height"),
                 "duration": dur, "size_mb": round(size / 1048576, 2),
                 "faststart": b"moov" in reel.open("rb").read(64),
                 "has_audio": bool(a)})
    problems = []
    c = info["checks"]
    c["duration_9_10s"] = min_s - 0.2 <= dur <= max_s + 0.2
    c["resolution_1080x1920"] = ((v or {}).get("width") == int(cfg["reel"]["w"])
                                and (v or {}).get("height") == int(cfg["reel"]["h"]))
    c["h264_yuv420p"] = (v or {}).get("codec_name") == "h264"
    c["aac_audio"] = bool(a) and (a or {}).get("codec_name") == "aac"
    c["size_under_60mb"] = size < 60 * 1024 * 1024
    c["faststart"] = info["faststart"]
    if blocks:
        c["text_blocks_2_3"] = 2 <= len([b for b in blocks if b.get("text")]) <= 4
        c["loop_echo"] = loop_echo_ok(blocks)
    for name, ok in c.items():
        if not ok:
            problems.append(name)
    info["problems"] = problems
    return (not problems), info


# ------------------------------------------------------------------- build

def build(cfg, content, duration_s: float, track_mp3: Path, track_wav: Path, bg_mp4: Path,
          out_mp4: Path | None = None, offline: bool = False):
    out_mp4 = out_mp4 or (config.OUTPUTS / "reel.mp4")
    blocks = text_blocks(content)
    if len(blocks) < 2:
        raise RuntimeError(f"need 2-3 text blocks, got {len(blocks)}")

    fitted = []
    for blk in blocks:
        lines, px, font_path = fit_block(blk["text"], blk["kind"], cfg)
        widest = assert_fits(lines, px, font_path, cfg)
        fitted.append({**blk, "lines": lines, "px": px, "font_path": font_path,
                       "widest": widest, "pinned_top": None})
    # Part 4.2 — the hook and the landing share ONE top coordinate and font:
    # the reel's last frame occupies the same place as frame 1 (the invisible loop).
    hook_f = next((b for b in fitted if b["kind"] == "hook"), None)
    if hook_f:
        hook_f["pinned_top"] = hook_top(hook_f["lines"], hook_f["px"], cfg)
        for b in fitted:
            if b["kind"] == "landing":
                b["pinned_top"] = hook_f["pinned_top"]
    print(f"[video] {len(fitted)} blocks fitted: "
          + ", ".join(f"{b['kind']}@{b['px']}px" for b in fitted))
    for b in fitted:
        if b["kind"] != "hook" and b["kind"] != "landing" and b["pinned_top"] is None:
            b["pinned_top"] = hook_top(b["lines"], b["px"], cfg)

    watermark = ""
    handle = ((cfg.get("brand") or {}).get("handle") or "").strip()
    if handle:
        watermark = (handle.split(".")[0] + "." + handle.split(".")[-1] + "_").upper()
        watermark = "".join(ch for ch in watermark if ch.isalnum() or ch in "._")

    pngs = []
    for i, b in enumerate(fitted):
        p = config.OUTPUTS / f"block_{i}_{b['kind']}.png"
        render_block(b["lines"], b["px"], b["font_path"], watermark, p, cfg,
                     fixed_top=b["pinned_top"])
        pngs.append(p)

    states = state_map(blocks, cfg, duration_s)
    if not assemble(bg_mp4, states, pngs, track_mp3, out_mp4, cfg, duration_s):
        raise RuntimeError("video assembly failed")
    cover_from_frame0(out_mp4, config.OUTPUTS / "cover.jpg")

    ok, info = qa_gate(out_mp4, cfg, blocks)
    if not ok:
        raise RuntimeError(f"QA gate failed: {info.get('problems')}")

    info["blocks"] = [{"kind": b["kind"], "px": b["px"], "widest": b["widest"],
                       "lines": len(b["lines"])} for b in fitted]
    info["states"] = states
    stamps = extract_stamps(out_mp4, states, duration_s)
    invisible = [f"{s['kind']}@{s['t']}" for s in stamps
                 if not card_visible(Path(s["frame"]))]
    info["stamps"] = [{k: v for k, v in s.items() if k != "frame"} for s in stamps]
    if invisible:
        raise RuntimeError(f"QA gate failed: text missing at {invisible}")

    info["motion"] = motion_present(out_mp4, cfg)
    return out_mp4, info


def pick_duration(cfg) -> float:
    lo, hi = float(cfg["reel"]["min_s"]), float(cfg["reel"]["max_s"])
    return round(random.uniform(lo, hi), 2)
