"""build_video.py — v4.0 text-show reels: hard-cut paragraph blocks, font switching.

Format laws (Brain §7):
  * Paragraph blocks, NO cumulative reveal — one complete thought per state,
    hard cut on the beat. The block change IS the transition.
  * Font switch mandatory, never same font twice in a row:
      anton (hooks + landings), playfair_italic (confessions),
      cormorant_italic (fragile intimacy), caveat (inner voice/2am), bebas (cold facts)
  * 2-second hold law: hook block static and alone, minimum 1.8s.
  * Center-third, white, soft shadow, readable at thumbnail size.
  * 8-11 seconds, 3-5 blocks.
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

from . import config

SIDE_MARGIN = 120
FONT_FILES = {
    "anton": "assets/fonts/Anton-Regular.ttf",
    "playfair_italic": "assets/fonts/PlayfairDisplay-Italic.ttf",
    "cormorant_italic": "assets/fonts/CormorantGaramond-Italic.ttf",
    "caveat": "assets/fonts/Caveat.ttf",
    "bebas": "assets/fonts/BebasNeue-Regular.ttf",
}
FALLBACK_FONT = "assets/fonts/Tinos-Regular.ttf"
# Slightly different px per font so optically similar (Anton is dense, Caveat light).
FONT_PX = {"anton": 74, "playfair_italic": 64, "cormorant_italic": 66,
           "caveat": 84, "bebas": 72}
WATERMARK_PX = 28
CARD_FADE = 0.22
MIN_HOLD = 1.0
HOOK_MIN_HOLD = 1.8
INK = (245, 245, 245, 255)
INK_DIM = (235, 235, 235, 115)
INK_MARK = (230, 230, 230, 150)


# ------------------------------------------------------------------ wording

def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip().strip('"').strip()


def _wrap_block(text: str, font_key: str, max_chars: int = 30) -> list:
    """Wrap one block into balanced display lines (short lines, centered)."""
    words = text.split()
    if not words:
        return []
    max_lines = 4 if len(words) > 24 else (3 if len(words) > 14 else 2)
    best = {"cost": float("inf"), "lines": None}

    def cost_of(lines):
        lens = [len(l) for l in lines]
        target = sum(lens) / len(lens)
        c = sum((l - target) ** 2 for l in lens)
        c += sum(max(0, l - max_chars) ** 2 * 40 for l in lens)
        c += sum(900 for l in lines if len(l.split()) == 1)
        return c

    def recurse(start, acc):
        if len(acc) == max_lines or start >= len(words):
            if start >= len(words) and acc:
                c = cost_of(acc)
                if c < best["cost"]:
                    best.update(cost=c, lines=list(acc))
            return
        for end in range(start + 1, min(len(words), start + 10) + 1):
            chunk = " ".join(words[start:end])
            if len(chunk) > max_chars and end > start + 1:
                break
            recurse(end, acc + [chunk])

    recurse(0, [])
    return best["lines"] or [" ".join(words)]


def script_blocks(content: dict, cfg: dict | None = None) -> list:
    """Return [{text, font, role}] — the reel's block sequence (v4.0)."""
    blocks = [ _clean(b) for b in (content.get("blocks") or []) ]
    blocks = [b for b in blocks if b]
    if not blocks:
        # legacy single-quote content: one block
        q = _clean(content.get("quote") or content.get("hook") or "")
        blocks = [q] if q else []
    fonts = [str(f).strip() for f in (content.get("fonts") or [])]
    out = []
    for i, b in enumerate(blocks):
        fk = fonts[i] if i < len(fonts) and fonts[i] in FONT_FILES else (
            "anton" if i in (0, len(blocks) - 1) else "playfair_italic")
        role = "hook" if i == 0 else ("landing" if i == len(blocks) - 1 else "middle")
        out.append({"text": b, "font": fk, "role": role})
    # Law: never same font twice in a row
    for i in range(1, len(out)):
        if out[i]["font"] == out[i - 1]["font"]:
            out[i]["font"] = "cormorant_italic" if out[i]["role"] == "middle" else "anton"
    # Law: hook + landing are anton
    if out:
        out[0]["font"] = "anton"
        out[-1]["font"] = "anton"
    return out


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
    return out or [0.5, 1.0, 2.0, 3.0, 4.0]


def cut_map(blocks: list, beats: list, duration_s: float) -> list:
    """Hard-cut states: block i shows alone for its window. Returns [{index,start,end}].

    Hook holds >= 1.8s alone (2-second hold law). Remaining blocks snap to beats,
    spread across the rest of the reel; the landing holds to the end.
    """
    n = len(blocks)
    if n == 0:
        return []
    states = []
    if n == 1:
        return [{"index": 0, "start": 0.0, "end": duration_s}]
    hook_hold = max(HOOK_MIN_HOLD, min(2.2, duration_s * 0.22))
    starts = [0.0]
    usable = [b for b in beats if b > hook_hold + 0.4]
    last_start = duration_s - max(MIN_HOLD, 1.2)
    if usable:
        span = max(last_start - starts[0], MIN_HOLD * (n - 1))
        for i in range(1, n):
            target = starts[0] + hook_hold + (span - hook_hold) * (i - 1) / max(n - 1, 1)
            nearest = min(usable, key=lambda b: abs(b - target))
            starts.append(nearest if abs(nearest - target) <= 0.4 else target)
    else:
        for i in range(1, n):
            starts.append(hook_hold + (last_start - hook_hold) * i / max(n - 1, 1))
    for i in range(1, n):
        starts[i] = max(starts[i], starts[i - 1] + MIN_HOLD)
    starts = sorted(starts[:n])
    for i in range(n):
        end = starts[i + 1] if i + 1 < n else duration_s
        states.append({"index": i, "start": round(starts[i], 3),
                       "end": round(max(end, starts[i] + 0.6), 3)})
    states[-1]["end"] = duration_s
    return states


# --------------------------------------------------------------- rendering

def render_state(block: dict, out_png: Path, cfg, watermark: str = "") -> Path:
    """One block, one frame style — hard cuts mean each state is one card."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    font_key = block["font"]
    font_file = config.ROOT / FONT_FILES.get(font_key, FALLBACK_FONT)
    px = FONT_PX.get(font_key, 64)
    quote_font = ImageFont.truetype(str(font_file), px)
    mark_font = ImageFont.truetype(str(config.ROOT / FALLBACK_FONT), WATERMARK_PX)

    lines = _wrap_block(block["text"], font_key)
    line_h = int(px * 1.38)
    block_h = len(lines) * line_h
    top = int(h * 0.5 - block_h / 2)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    d = ImageDraw.Draw(img)

    def draw_center(txt, font, y, fill, sd_fill):
        bb = font.getbbox(txt)
        tw = bb[2] - bb[0]
        x = (w - tw) // 2
        sd.text((x + 2, y + 3), txt, font=font, fill=sd_fill)
        d.text((x, y), txt, font=font, fill=fill)

    for i, ln in enumerate(lines):
        draw_center(ln, quote_font, top + i * line_h, INK, (0, 0, 0, 150))

    if watermark:
        bb = mark_font.getbbox(watermark)
        tw = bb[2] - bb[0]
        d.text(((w - tw) // 2, int(h * 0.90)), watermark, font=mark_font, fill=INK_MARK)

    shadow = shadow.filter(ImageFilter.GaussianBlur(5))
    img = Image.alpha_composite(shadow, img)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, "PNG")
    return out_png


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
             cfg, duration_s: float) -> bool:
    """Hard-cut overlays: each state's card is enabled for exactly its window."""
    fps = int(cfg["reel"]["fps"])
    inputs = []
    if _probe_frames(bg_mp4) == 1:
        inputs += ["-loop", "1"]
    inputs += ["-i", str(bg_mp4)]
    for p in pngs:
        inputs += ["-loop", "1", "-i", str(p)]
    inputs += ["-i", str(track_mp3)]

    filters = [f"[0:v]fps={fps},format=yuv420p[base]"]
    last = "base"
    for i, (st, png) in enumerate(zip(states, pngs)):
        s0, s1 = st["start"], st["end"]
        filters.append(
            f"[{i + 1}:v]format=rgba[s{i}]")
        filters.append(
            f"[{last}][s{i}]overlay=enable='between(t,{s0:.3f},{s1:.3f})':"
            f"x=0:y=0:eof_action=pass[o{i}]")
        last = f"o{i}"
    fade_out = 0.8
    aidx = len(pngs) + 1
    filters.append(
        f"[{aidx}:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
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
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.9", "-i", str(reel),
                        "-frames:v", "1", "-q:v", "2", str(out_jpg)],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and out_jpg.exists()


# ----------------------------------------------------------------- QA gate

def extract_stamps(reel: Path, states: list, out_dir=None) -> list:
    out_dir = Path(out_dir or config.OUTPUTS)
    stamps = []
    for i, s in enumerate(states):
        t = min(s["start"] + 0.7, (s["start"] + s["end"]) / 2)
        p = out_dir / f"check_{i}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}",
                            "-i", str(reel), "-frames:v", "1", "-q:v", "3", str(p)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and p.exists():
            stamps.append({"index": i, "t": round(t, 2),
                           "frame": str(p)})
    return stamps


def card_visible(frame_path: Path) -> bool:
    from PIL import Image
    try:
        with Image.open(frame_path) as im:
            hist = im.convert("L").histogram()
        return sum(hist[235:256]) >= 120
    except Exception:  # noqa: BLE001
        return False


def qa_gate(reel: Path, cfg) -> tuple:
    min_s, max_s = float(cfg["reel"]["min_s"]), float(cfg["reel"]["max_s"])
    info = {"path": str(reel), "exists": reel.exists()}
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
                 "faststart": b"moov" in reel.open("rb").read(64)})
    problems = []
    if not v or not a:
        problems.append("missing stream")
    if (v or {}).get("width") != int(cfg["reel"]["w"]) or (v or {}).get("height") != int(cfg["reel"]["h"]):
        problems.append(f"resolution {(v or {}).get('width')}x{(v or {}).get('height')}")
    if not (min_s - 0.2 <= dur <= max_s + 0.2):
        problems.append(f"duration {dur:.2f}s")
    if size >= 60 * 1024 * 1024:
        problems.append("size >= 60MB")
    if not info["faststart"]:
        problems.append("faststart missing")
    info["problems"] = problems
    return (not problems), info


# ------------------------------------------------------------------- build

def build(cfg, content, duration_s: float, track_mp3: Path, track_wav: Path, bg_mp4: Path,
          out_mp4: Path | None = None, offline: bool = False):
    out_mp4 = out_mp4 or (config.OUTPUTS / "reel.mp4")
    mood = content.get("mood") or "heavy_shadow"
    blocks = script_blocks(content, cfg)
    if not blocks:
        raise RuntimeError("no script blocks to render")
    states = cut_map(blocks, [], duration_s)  # beats unused for hard cuts timing base

    # beats still drive micro-fade timing; hook hold is a law
    beats = beat_times(track_wav, mood, cfg, duration_s) if track_wav.exists() \
        else beat_times_fallback(cfg, mood, duration_s)
    states = cut_map(blocks, beats, duration_s)

    watermark = ""
    handle = ((cfg.get("brand") or {}).get("handle") or "").strip()
    if handle:
        watermark = (handle.split(".")[0] + "." + handle.split(".")[-1] + "_").upper()
        watermark = "".join(ch for ch in watermark if ch.isalnum() or ch in "._")

    pngs = []
    for i, blk in enumerate(blocks):
        p = config.OUTPUTS / f"state_{i}.png"
        render_state(blk, p, cfg, watermark=watermark)
        pngs.append(p)

    if not assemble(bg_mp4, states, pngs, track_mp3, out_mp4, cfg, duration_s):
        raise RuntimeError("video assembly failed")
    cover_from_frame0(out_mp4, config.OUTPUTS / "cover.jpg")
    ok, info = qa_gate(out_mp4, cfg)
    info["states"] = states
    if not ok:
        raise RuntimeError(f"QA gate failed: {info.get('problems')}")

    stamps = extract_stamps(out_mp4, states)
    invisible = [f"state {s['index']} invisible at t={s['t']}"
                 for s in stamps if not card_visible(Path(s["frame"]))]
    info["stamps"] = [{k: v for k, v in s.items() if k != "frame"} for s in stamps]
    if invisible or len(stamps) != len(states):
        raise RuntimeError(f"QA gate failed: text missing from render: {invisible}")
    return out_mp4, info


def pick_duration(cfg) -> float:
    lo = float(cfg["reel"]["min_s"])
    hi = float(cfg["reel"]["max_s"])
    return round(random.uniform(max(lo, 8.0), min(hi, 11.0)), 2)
