"""build_video.py — static confession-paragraph reels.

Format (user-confirmed 2026-09-24, supersedes the multi-block/font-switch
experiment): ONE paragraph on screen for the whole reel — no reveals, no
hard cuts, no font switching. Serif (Tinos, Times-class), flat near-white,
soft blurred shadow, centered — matching references/Video-*.mp4.

Text is MEASURED, never guessed: the font auto-fits so no line can overflow
(the v4.0 reel overflowed because Caveat was sized by char-count, not by
rendered width — impossible now by construction).
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

from . import config

FONT_SERIF = "assets/fonts/Tinos-Regular.ttf"
FONT_MARK = "assets/fonts/Inter-Regular.ttf"
WATERMARK_PX = 28
SIDE_MARGIN = 110          # px each side -> 860 usable of 1080
MAX_BLOCK_H = 0.60        # fraction of frame height the text block may use
FADE_IN = 0.5
PX_LADDER = [72, 68, 64, 60, 56, 52, 48, 44, 40]   # tried in order, first fit wins
INK = (245, 245, 245, 255)
INK_MARK = (230, 230, 230, 150)


# ------------------------------------------------------------------ wording

def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip().strip('"').strip()


def paragraph_text(content: dict) -> str:
    """The single confession paragraph (falls back to legacy quote field)."""
    p = _clean(content.get("paragraph") or content.get("quote") or content.get("hook") or "")
    return p


def _wrap_measured(text: str, font, max_w: int) -> list:
    """Greedy wrap by MEASURED width, then a balancing pass.

    Returns [] if even a single word doesn't fit (caller tries a smaller px).
    """
    words = text.split()
    if not words:
        return []

    def width(s: str) -> int:
        return font.getbbox(s)[2]

    # greedy fill
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
        return []                    # a single word overflows -> smaller font

    # balance: move trailing words down while it evens the line widths
    for _ in range(200):
        moved = False
        for i in range(len(lines) - 1):
            wds = lines[i].split()
            while len(wds) > 1:
                trial_last = " ".join(wds[-1:])
                rest = " ".join(wds[:-1])
                nxt = f"{trial_last} {lines[i + 1]}".strip()
                if width(rest) >= width(nxt) and width(nxt) <= max_w:
                    lines[i] = rest
                    lines[i + 1] = nxt
                    wds = rest.split()
                    moved = True
                else:
                    break
        if not moved:
            break
    return lines


def fit_paragraph(text: str, cfg) -> tuple:
    """Return (lines, px) — the largest font size where every line fits."""
    from PIL import ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    max_w = w - 2 * SIDE_MARGIN
    max_h = int(h * MAX_BLOCK_H)
    for px in PX_LADDER:
        font = ImageFont.truetype(str(config.ROOT / FONT_SERIF), px)
        lines = _wrap_measured(text, font, max_w)
        if not lines:
            continue
        line_h = int(px * 1.45)
        if len(lines) * line_h <= max_h:
            return lines, px
    raise RuntimeError("paragraph cannot fit even at the smallest font size")


def assert_fits(lines: list, px: int, cfg) -> None:
    """Deterministic pre-render overflow gate (measured, not guessed)."""
    from PIL import ImageFont
    w = int(cfg["reel"]["w"])
    max_w = w - 2 * SIDE_MARGIN
    font = ImageFont.truetype(str(config.ROOT / FONT_SERIF), px)
    worst = max(font.getbbox(ln)[2] for ln in lines)
    assert worst <= max_w, f"overflow: widest line {worst}px > {max_w}px allowed"
    assert len(lines) * int(px * 1.45) <= int(cfg["reel"]["h"] * MAX_BLOCK_H), "block too tall"


# --------------------------------------------------------------- rendering

def render_card(lines: list, px: int, watermark: str, out_png: Path, cfg) -> Path:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    font = ImageFont.truetype(str(config.ROOT / FONT_SERIF), px)
    mark_font = ImageFont.truetype(str(config.ROOT / FONT_MARK), WATERMARK_PX)

    line_h = int(px * 1.45)
    block_h = len(lines) * line_h
    top = int(h * 0.5 - block_h / 2)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    d = ImageDraw.Draw(img)

    def draw_center(txt, f, y, fill, sd_fill):
        bb = f.getbbox(txt)
        tw = bb[2] - bb[0]
        x = (w - tw) // 2 - bb[0]
        sd.text((x + 2, y + 3), txt, font=f, fill=sd_fill)
        d.text((x, y), txt, font=f, fill=fill)

    for i, ln in enumerate(lines):
        draw_center(ln, font, top + i * line_h, INK, (0, 0, 0, 150))

    if watermark:
        bb = mark_font.getbbox(watermark)
        tw = bb[2] - bb[0]
        d.text(((w - tw) // 2, int(h * 0.90)), watermark, font=mark_font, fill=INK_MARK)

    shadow = shadow.filter(ImageFilter.GaussianBlur(5))
    img = Image.alpha_composite(shadow, img)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, "PNG")
    return out_png


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


def assemble(bg_mp4: Path, card_png: Path, track_mp3: Path, out_mp4: Path,
             cfg, duration_s: float) -> bool:
    """One static card: fades in at ~0.15s, holds to the end."""
    fps = int(cfg["reel"]["fps"])
    inputs = []
    if _probe_frames(bg_mp4) == 1:
        inputs += ["-loop", "1"]
    inputs += ["-i", str(bg_mp4), "-loop", "1", "-i", str(card_png), "-i", str(track_mp3)]

    fade_out = 0.8
    filters = [
        f"[0:v]fps={fps},format=yuv420p[base]",
        f"[1:v]format=rgba,fade=t=in:st=0.15:d={FADE_IN}:alpha=1[card]",
        f"[base][card]overlay=x=0:y=0:eof_action=pass[v]",
        f"[2:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"afade=t=out:st={max(duration_s - fade_out, 0):.3f}:d={fade_out},"
        f"atrim=0:{duration_s:.3f},asetpts=N/SR/TB[aout]",
    ]
    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[aout]",
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


# ----------------------------------------------------------------- QA gates

def extract_stamps(reel: Path, duration_s: float, out_dir=None) -> list:
    """Three stamps: after fade-in, middle, near end. The card is static, so
    every stamp MUST show text — this catches 'text missing' regressions."""
    out_dir = Path(out_dir or config.OUTPUTS)
    times = [1.2, duration_s / 2.0, max(duration_s - 1.2, 1.2)]
    stamps = []
    for i, t in enumerate(sorted(set(round(t, 2) for t in times))):
        p = out_dir / f"check_{i}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}",
                            "-i", str(reel), "-frames:v", "1", "-q:v", "3", str(p)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and p.exists():
            stamps.append({"index": i, "t": t, "frame": str(p)})
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
    text = paragraph_text(content)
    if not text:
        raise RuntimeError("no paragraph to render")

    lines, px = fit_paragraph(text, cfg)          # measured fit — no overflow
    assert_fits(lines, px, cfg)                   # deterministic gate
    print(f"[video] paragraph fit: {len(lines)} lines at {px}px")

    watermark = ""
    handle = ((cfg.get("brand") or {}).get("handle") or "").strip()
    if handle:
        watermark = (handle.split(".")[0] + "." + handle.split(".")[-1] + "_").upper()
        watermark = "".join(ch for ch in watermark if ch.isalnum() or ch in "._")

    card = render_card(lines, px, watermark, config.OUTPUTS / "card.png", cfg)

    if not assemble(bg_mp4, card, track_mp3, out_mp4, cfg, duration_s):
        raise RuntimeError("video assembly failed")
    cover_from_frame0(out_mp4, config.OUTPUTS / "cover.jpg")
    ok, info = qa_gate(out_mp4, cfg)
    if not ok:
        raise RuntimeError(f"QA gate failed: {info.get('problems')}")
    from PIL import ImageFont
    widest = max(ImageFont.truetype(str(config.ROOT / FONT_SERIF), px).getbbox(ln)[2]
                 for ln in lines)
    info["text"] = {"lines": len(lines), "px": px, "widest": widest}

    stamps = extract_stamps(out_mp4, duration_s)
    invisible = [f"t={s['t']}" for s in stamps if not card_visible(Path(s["frame"]))]
    info["stamps"] = [{k: v for k, v in s.items() if k != "frame"} for s in stamps]
    if invisible or len(stamps) != 3:
        raise RuntimeError(f"QA gate failed: text missing at {invisible}")
    return out_mp4, info


def pick_duration(cfg) -> float:
    lo = float(cfg["reel"]["min_s"])
    hi = float(cfg["reel"]["max_s"])
    return round(random.uniform(max(lo, 8.5), min(hi, 11.0)), 2)
