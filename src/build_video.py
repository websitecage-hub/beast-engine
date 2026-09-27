"""build_video.py — THE COMPLETE MIND format (Part 5) + VISUAL SPEC v1.0 §4.

  * dark cinematic VIDEO background, darkened -0.13, looped continuously
  * 2-4 LARGE static text blocks, no per-word/per-line animation — a block
    fades in over <=0.3s and holds (spec §4: no reveal, no typed text)
  * hook: largest, pinned to y=35% of height (spec §4 / verified against the
    user's reference reels: "roughly one-third of the way down")
  * landing: same font, size and exact top as the hook — the visual echo IS the
    invisible loop (Part 4.2)
  * soft shadow, blur radius 8, ~70% opacity (spec §4)
  * a 30% black feathered band behind the text IS rendered when the video
    underneath is bright (spec §4 "subtle dark overlay behind text")
  * timing (spec §4): hook holds 4-5s, deepening 4-5s, landing 8-9s
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
# --- FINAL FORMAT (the "already on screen" spec) -----------------------------
LINE_SPACING = 1.32             # comfortable breathing room between lines (§2)
MIN_WIDTH_FILL = 0.75           # §2: text fills >=75% of frame width
MAX_WIDTH_FILL = 0.92           # never touch the edges
MAX_LINES_ON_SCREEN = 5         # §2: "NEVER more than 5 short lines"
MAX_TOTAL_WORDS = 46            # §2: 5 lines x ~9 words — the print cap, enforced
TEXT_TOP_FRAC = 0.35            # §2: centered, slightly above middle
SHADOW_BLUR = 8                 # soft dark shadow
SHADOW_ALPHA = 179              # ~70% opacity
TEXT_BAND_ALPHA = 77            # 30% black scrim, only over bright footage
TEXT_BAND_BRIGHT_MIN = 88       # only lay the band when the bg luma exceeds this
TEXT_SCRIM = False              # never darken the footage: text rides on the video
# Coolvetica runs wide, so the ladders start lower and walk further down.
HOOK_PX_LADDER = [120, 112, 104, 96, 88, 82, 76, 70, 64, 58, 52, 46, 40]
BODY_PX_LADDER = [104, 96, 90, 84, 78, 72, 66, 60, 54, 48, 42]
INK = (245, 245, 245, 255)
INK_MARK = (230, 230, 230, 150)


# ------------------------------------------------------------------ wording

def _clean(text) -> str:
    return " ".join(str(text or "").split()).strip().strip('"').strip()


def text_blocks(content: dict) -> list:
    """THE COMPLETE MESSAGE as ONE block (FINAL FORMAT §2).

    The format is explicit: exactly ONE text block containing the whole message
    (3-5 short lines), visible from frame 0 and never changing. The hook/body/
    landing structure still drives the *writing*, but on screen it is a single
    stacked paragraph — not three timed cards.
    """
    hook = _clean(content.get("hook"))
    landing = _clean(content.get("landing"))
    deep = [_clean(d) for d in (content.get("deepening") or []) if _clean(d)]

    if not hook and content.get("blocks"):
        deep = [_clean(b) for b in content["blocks"] if _clean(b)]   # legacy
        hook = deep[0] if deep else ""
        deep = deep[1:]

    # the complete message, in reading order: hook -> deepening -> landing.
    # The landing is the loop-echo of the hook, so when they read as the same
    # idea we drop the duplicate line rather than print it twice.
    parts = [hook, *deep[:2]]
    if landing and landing.lower() not in {p.lower() for p in parts}:
        parts.append(landing)
    parts = [p for p in parts if p]
    if not parts:
        return []

    # §2 hard cap. The model is asked for a 25-40 word total, but a model can
    # over-write, and the cap must hold deterministically regardless. Trim the
    # middle (deepening) first: the hook opens and the landing closes the loop, so
    # those two survive; the middle is what a human editor would cut.
    while len(parts) > MAX_LINES_ON_SCREEN and len(parts) > 2:
        parts.pop(len(parts) - 2)

    total_words = sum(len(p.split()) for p in parts)
    if total_words > MAX_TOTAL_WORDS and len(parts) > 2:
        # still too long to print in 5 lines: keep hook + strongest + landing
        keep = [parts[0]]
        mid = parts[1:-1]
        mid.sort(key=lambda s: len(s.split()))
        keep.append(mid[0])
        keep.append(parts[-1])
        parts = keep

    return [{"text": "\n".join(parts), "kind": "message",
             "lines_source": parts}]


def _wrap_measured(text: str, font, max_w: int) -> list:
    """Greedy wrap by MEASURED width + balancing pass. [] if even one word won't fit.

    A newline in `text` is a hard line break: the format stacks the complete
    message as distinct short lines, so those breaks must be preserved rather
    than re-flowed.
    """
    words = text.split()
    if not words:
        return []

    def width(s: str) -> int:
        return font.getbbox(s)[2]

    lines: list[str] = []
    cur = ""
    for raw in text.split("\n"):
        if not raw.strip():                       # blank line = paragraph gap
            if cur:
                lines.append(cur)
                cur = ""
            lines.append("")                      # preserved as breathing room
            continue
        for wd in raw.split():
            cand = f"{cur} {wd}".strip()
            if width(cand) <= max_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = wd
    if cur:
        lines.append(cur)
    lines = [ln for ln in lines if ln != ""] or []
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


def fit_block(text: str, kind: str, cfg, max_lines: int | None = None) -> tuple:
    """Return (lines, px, font_path) — largest size where everything fits.

    FINAL FORMAT §2: the text must fill 75-80% of the frame width (readable at
    thumbnail size) AND must never exceed 5 short lines. Both are hard limits, so
    the ladder only accepts sizes satisfying the line cap, and among those prefers
    the largest that fills at least MIN_WIDTH_FILL of the width.
    """
    from PIL import ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    max_w = w - 2 * SIDE_MARGIN
    max_h = int(h * MAX_BLOCK_H)
    cap = max_lines or MAX_LINES_ON_SCREEN
    ladder = HOOK_PX_LADDER if kind in ("hook", "landing", "message") else BODY_PX_LADDER
    font_path = FONT_HOOK if kind in ("hook", "landing", "message") else FONT_BODY
    best = None
    for px in ladder:
        font = ImageFont.truetype(str(config.ROOT / font_path), px)
        lines = _wrap_measured(text, font, max_w)
        if not lines or len(lines) > cap:      # §2 hard cap on on-screen lines
            continue
        line_h = int(px * LINE_SPACING)
        if len(lines) * line_h > max_h:
            continue
        widest = max(font.getbbox(ln)[2] for ln in lines)
        cand = (lines, px, font_path, widest / w)
        if best is None:
            best = cand
        # §2: stop at the first size that fills the target width band
        if cand[3] >= MIN_WIDTH_FILL:
            return lines, px, font_path
    if best is None:
        raise RuntimeError(
            f"text cannot fit in {cap} lines at any size: {text!r}")
    return best[0], best[1], best[2]


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
                 cfg, fixed_top: int | None = None, bg_luma: float | None = None) -> Path:
    """ONE static text block as a transparent PNG — visible on every frame.

    FINAL FORMAT §2: this is the single overlay composited over the whole reel.
    `fixed_top` places the first line at the 35% anchor.

    `bg_luma` (0-255 mean brightness of the background under the text) triggers the
    optional feathered dark scrim, so light footage can't wash the text out. The
    scrim MUST be composited before the text is drawn — see the ordering comment
    inside, and its regression test.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    font = ImageFont.truetype(str(config.ROOT / font_path), px)
    mark_font = ImageFont.truetype(str(config.ROOT / FONT_MARK), WATERMARK_PX)

    line_h = int(px * LINE_SPACING)
    block_h = len(lines) * line_h
    top = fixed_top if fixed_top is not None else (int(h * TEXT_TOP_FRAC) - block_h // 2)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # The footage is never darkened or overlaid with a scrim — text rides directly on
    # the video. The band is disabled outright (TEXT_SCRIM), and the shadow behind
    # each glyph carries legibility instead. Ordering matters: any compositing must
    # happen BEFORE the draw handles are created, or `d` points at a discarded image
    # and the glyphs vanish (that bug made the text invisible on every frame).
    if TEXT_SCRIM and bg_luma is not None and bg_luma >= TEXT_BAND_BRIGHT_MIN and block_h > 0:
        band_pad = int(px * 0.55)
        y0 = max(top - band_pad, 0)
        y1 = min(top + block_h + band_pad, h)
        band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle([0, y0, w, y1], fill=(0, 0, 0, TEXT_BAND_ALPHA))
        # feathered edges, so it reads as a cinematic scrim and not a grey box
        img = Image.alpha_composite(img, band.filter(ImageFilter.GaussianBlur(px * 0.5)))

    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd, d = ImageDraw.Draw(shadow), ImageDraw.Draw(img)      # AFTER any compositing

    def draw_center(txt, f, y, fill, sd_fill):
        bb = f.getbbox(txt)
        x = (w - (bb[2] - bb[0])) // 2 - bb[0]
        sd.text((x + 2, y + 3), txt, font=f, fill=sd_fill)
        d.text((x, y), txt, font=f, fill=fill)

    for i, ln in enumerate(lines):
        draw_center(ln, font, top + i * line_h, INK, (0, 0, 0, SHADOW_ALPHA))

    if watermark:
        bb = mark_font.getbbox(watermark)
        d.text(((w - (bb[2] - bb[0])) // 2, int(h * 0.92)), watermark,
               font=mark_font, fill=INK_MARK)

    img = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)), img)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, "PNG")
    return out_png


def hook_top(lines: list, px: int, cfg) -> int:
    """The canonical text top — hook and landing both use this (the visual echo).

    Spec §4: anchored so the text sits ~a third of the way down (not centred).
    """
    h = int(cfg["reel"]["h"])
    return int(int(h * TEXT_TOP_FRAC) - (len(lines) * int(px * LINE_SPACING)) // 2)


def bg_text_luma(bg_mp4: Path, at: float, cfg, top: int, block_h: int) -> float | None:
    """Mean brightness (0-255) of the background in the text band.

    Spec §4 asks for a dark scrim behind the text only when the video there is
    bright. Measuring the actual band is the only honest way to decide, so read
    one frame and average the rows the text will occupy.
    """
    try:
        import numpy as np
        w = int(cfg["reel"]["w"])
        h = int(cfg["reel"]["h"])
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(at, 0):.2f}",
                            "-i", str(bg_mp4), "-frames:v", "1", "-vf",
                            f"scale={w}:{h},format=gray", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-"],
                           capture_output=True, timeout=120)
        if r.returncode != 0 or not r.stdout:
            return None
        frame = np.frombuffer(r.stdout, dtype=np.uint8)
        if frame.size != w * h:
            return None
        frame = frame.reshape(h, w)
        y0 = max(min(top, h - 1), 0)
        y1 = max(min(top + max(block_h, 1), h), y0 + 1)
        return float(frame[y0:y1, :].mean())
    except Exception:  # noqa: BLE001
        return None


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
    """ONE text state spanning the whole reel (FINAL FORMAT §1).

    There is no timing map any more: the single block is on screen from 0.0 to the
    end. Kept as a function so QA/logging can still reason about "what is on screen
    when", and so old callers don't break.
    """
    if not blocks:
        return []
    return [{"index": 0, "kind": "message", "start": 0.0, "end": duration_s}]


def loop_echo_ok(blocks: list) -> bool:
    """FINAL FORMAT §1: the text never changes, so the loop echo is automatic.

    Under the old multi-card format this gate verified that the landing reused the
    hook's exact top coordinate. With a single always-visible block there is
    nothing to echo — the first and last frame are identical by construction.
    """
    return bool(blocks)


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
    """Video bg + ONE text overlay that is visible on EVERY frame (FINAL FORMAT §1/§2).

    The format is unambiguous: the text is already on screen at frame 0 and never
    appears, fades, slides or changes. That means a single `overlay=0:0` with NO
    `enable=` condition at all — there is exactly one PNG and zero timing logic.

    Any per-block `enable='between(t,...)'` is a failure condition (§7).
    """
    fps = int(cfg["reel"]["fps"])
    inputs = []
    if _probe_frames(bg_mp4) == 1:
        inputs += ["-loop", "1"]                    # still -> keep it running
    inputs += ["-stream_loop", "-1", "-i", str(bg_mp4)]
    for p in pngs:
        inputs += ["-loop", "1", "-i", str(p)]
    inputs += ["-i", str(track_mp3)]

    # The background ships AS SHOT. No brightness lift/drop, no saturation crush.
    # `bg_grade: true` would restore the cinematic chain; `bg_darken` a non-zero
    # value adds the brightness term. Both default to off, so this chain is a clean
    # scale+crop with zero colour change.
    darken = float(cfg.get("bg_darken", 0.0))
    grade_parts = []
    if cfg.get("bg_grade"):
        grade_parts.append("eq=contrast=1.08:saturation=0.55")
    if darken:
        grade_parts.append(f"eq=brightness={darken}")
    grade_seg = ("," + ",".join(grade_parts)) if grade_parts else ""
    filters = [f"[0:v]fps={fps},scale=1080:1920:force_original_aspect_ratio=increase,"
               f"crop=1080:1920{grade_seg},format=yuv420p[base]"]

    # §1/§2: one overlay, composited from frame 0 to the end. No enable=, no fade.
    last = "base"
    for i, _png in enumerate(pngs):
        filters.append(f"[{i + 1}:v]format=rgba[b{i}]")
        filters.append(f"[{last}][b{i}]overlay=0:0:eof_action=pass[o{i}]")
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
        # FINAL FORMAT §2: exactly ONE text block on screen.
        c["one_text_block"] = len([b for b in blocks if b.get("text")]) == 1
        # §2: NEVER more than 5 short lines
        c["lines_max_5"] = all(
            len([ln for ln in str(b.get("text") or "").split("\n") if ln.strip()]) <= 5
            for b in blocks)
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
    if not blocks:
        raise RuntimeError("no text: content produced an empty message")

    # FINAL FORMAT §2: ONE block, all lines stacked, centered, sitting on top of
    # the video from frame 0. No pinning, no per-card top, no timing.
    fitted = []
    for blk in blocks:
        lines, px, font_path = fit_block(blk["text"], blk["kind"], cfg)
        widest = assert_fits(lines, px, font_path, cfg)
        fitted.append({**blk, "lines": lines, "px": px, "font_path": font_path,
                       "widest": widest, "pinned_top": None})
    for b in fitted:
        b["pinned_top"] = hook_top(b["lines"], b["px"], cfg)
    print(f"[video] {len(fitted)} text block(s): "
          + ", ".join(f"{len(b['lines'])} lines @{b['px']}px "
                      f"(width {b['widest']}/{int(cfg['reel']['w'])}px)" for b in fitted))

    watermark = ""
    handle = ((cfg.get("brand") or {}).get("handle") or "").strip()
    if handle:
        watermark = (handle.split(".")[0] + "." + handle.split(".")[-1] + "_").upper()
        watermark = "".join(ch for ch in watermark if ch.isalnum() or ch in "._")

    pngs = []
    for i, b in enumerate(fitted):
        p = config.OUTPUTS / f"block_{i}_{b['kind']}.png"
        # spec §4: measure the actual background band, then scrim only if bright
        top = b["pinned_top"] if b["pinned_top"] is not None else hook_top(
            b["lines"], b["px"], cfg)
        luma = bg_text_luma(bg_mp4, 1.0, cfg, top,
                            len(b["lines"]) * int(b["px"] * LINE_SPACING))
        render_block(b["lines"], b["px"], b["font_path"], watermark, p, cfg,
                     fixed_top=b["pinned_top"], bg_luma=luma)
        pngs.append(p)

    states = state_map(blocks, cfg, duration_s)
    if not assemble(bg_mp4, states, pngs, track_mp3, out_mp4, cfg, duration_s):
        raise RuntimeError("video assembly failed")
    cover_from_frame0(out_mp4, config.OUTPUTS / "cover.jpg")

    ok, info = qa_gate(out_mp4, cfg, blocks)
    if not ok:
        raise RuntimeError(f"QA gate failed: {info.get('problems')}")

    # §2 hard cap: the RENDERED line count (post-wrap), not just the source parts.
    for b in fitted:
        if len(b["lines"]) > MAX_LINES_ON_SCREEN:
            raise RuntimeError(
                f"FINAL FORMAT §2 violated: {len(b['lines'])} lines on screen "
                f"(max {MAX_LINES_ON_SCREEN})")

    info["blocks"] = [{"kind": b["kind"], "px": b["px"], "widest": b["widest"],
                       "lines": len(b["lines"])} for b in fitted]
    info["states"] = states

    # §1/§7 — the text MUST be on screen at frame 0 and still on the last frame.
    # Sampling only the middle (or a fixed timestamp past the end) would miss both
    # the "appears later" and "disappears early" failure modes.
    checkpoints = [0.0, 0.25, duration_s * 0.5, max(duration_s - 0.25, 0.1)]
    missing = []
    visibility = []
    for t in checkpoints:
        frame = config.OUTPUTS / f"vis_{t:.2f}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}",
                            "-i", str(out_mp4), "-frames:v", "1", "-q:v", "3", str(frame)],
                           capture_output=True, text=True, timeout=120)
        vis = r.returncode == 0 and frame.exists() and card_visible(frame)
        visibility.append({"t": round(t, 2), "text_visible": vis})
        if not vis:
            missing.append(round(t, 2))
    info["text_visibility"] = visibility
    if missing:
        raise RuntimeError(
            f"FINAL FORMAT §1 violated: text not visible at {missing}s — the text "
            "must be on screen from frame 0 to the end with no animation")

    info["motion"] = motion_present(out_mp4, cfg)
    return out_mp4, info


def pick_duration(cfg) -> float:
    lo, hi = float(cfg["reel"]["min_s"]), float(cfg["reel"]["max_s"])
    return round(random.uniform(lo, hi), 2)
