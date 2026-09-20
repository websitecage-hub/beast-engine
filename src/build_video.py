"""build_video.py — beat-synced vertical reel assembly.

1. Beat analysis via librosa on outputs/track.wav (fallback: mood BPM, evenly spaced).
2. Timing map: hook card at t=0 holding 1.4s, body lines on subsequent beats,
   closer in the final 20%.
3. Text cards rendered with Pillow (Anton, ALL CAPS, 4px stroke + soft shadow).
4. ffmpeg assembly with per-card alpha fades and the audio bed (loudnorm -16 LUFS).
5. Export + hard QA gate (1080x1920, 8-14s, both streams, <60MB, faststart).
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

from . import config

MAX_CHARS_PER_LINE = 22
MAX_LINES = 3
SIDE_MARGIN = 90
TOP_FRACTION = 0.34
FONT_MIN, FONT_MAX = 88, 120
CARD_FADE = 0.35
HOOK_HOLD = 1.4


# ------------------------------------------------------------------ timing

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
    bpm = float((cfg.get("mood_fallback_bpm") or {}).get(mood, 90))
    interval = 60.0 / max(bpm, 1)
    out, t = [], interval * 0.5
    while t < duration_s:
        out.append(t)
        t += interval
    return out or [0.5, 1.0, 2.0, 3.0, 4.0]


def timing_map(content: dict, beats: list, duration_s: float) -> list:
    """Returns [{'text','start','end','kind'}].

    Hook holds ~1.4s, body lines are spread evenly across the middle of the reel and
    snapped to the nearest beat, closer sits in the final 20%.
    """
    cards = [{"text": content.get("hook", ""), "kind": "hook"}]
    for line in content.get("body_lines") or []:
        cards.append({"text": line, "kind": "body"})
    cards.append({"text": content.get("closer") or "", "kind": "closer"})
    cards = [c for c in cards if c["text"].strip()]
    n = len(cards)
    if n == 0:
        return []
    if n == 1:
        return [{"text": cards[0]["text"], "start": 0.0, "end": duration_s,
                 "kind": cards[0]["kind"]}]

    MIN_HOLD = 0.9
    hook_end = min(HOOK_HOLD, max(duration_s * 0.18, MIN_HOLD))
    region_start, region_end = hook_end, max(duration_s * 0.8, hook_end + MIN_HOLD)
    middles = n - 2                      # body cards between hook and closer
    starts = [0.0]
    if middles > 0:
        span = max(region_end - region_start, MIN_HOLD)
        for i in range(middles):
            target = region_start + span * (i + 1) / (middles + 1)
            starts.append(_snap(target, beats))
    closer_start = max(starts[-1] + MIN_HOLD, duration_s * 0.8) if middles > 0 \
        else max(hook_end, duration_s * 0.8)
    starts.append(min(closer_start, duration_s - MIN_HOLD))

    # enforce monotonic, readable spacing
    for i in range(1, len(starts)):
        starts[i] = max(starts[i], starts[i - 1] + MIN_HOLD)
    overflow = starts[-1] - (duration_s - MIN_HOLD)
    if overflow > 0:
        starts = [max(s - overflow, 0.0) for s in starts]

    out = []
    for i, c in enumerate(cards):
        end = starts[i + 1] if i + 1 < n else duration_s
        if i == 0:
            end = min(end, hook_end)
        out.append({"text": c["text"], "start": round(starts[i], 3),
                    "end": round(max(end, starts[i] + 0.4), 3), "kind": c["kind"]})
    for i in range(len(out) - 1):
        if out[i]["end"] > out[i + 1]["start"]:
            out[i]["end"] = max(out[i + 1]["start"], out[i]["start"] + 0.4)
    out[-1]["end"] = duration_s
    return out


def _snap(target: float, beats: list, tolerance: float = 0.25) -> float:
    """Snap a target time to the nearest beat when one is close enough."""
    if not beats:
        return target
    nearest = min(beats, key=lambda b: abs(b - target))
    return nearest if abs(nearest - target) <= tolerance else target


# --------------------------------------------------------------- rendering

def _wrap(text: str, max_chars: int = MAX_CHARS_PER_LINE, max_lines: int = MAX_LINES) -> list:
    words = text.upper().split()
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) <= max_chars:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        merged = []
        per = max(1, len(lines) // max_lines + (1 if len(lines) % max_lines else 0))
        for i in range(0, len(lines), per):
            merged.append(" ".join(lines[i:i + per]))
        lines = merged[:max_lines]
    return lines


def render_card(text: str, out_png: Path, cfg) -> Path:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    w = int(cfg["reel"]["w"])
    font_path = config.ROOT / cfg["font_path"]
    lines = _wrap(text)

    def load(size):
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:  # noqa: BLE001
            return ImageFont.load_default()

    size = FONT_MAX
    font = load(size)
    max_w = w - 2 * SIDE_MARGIN
    while size > FONT_MIN:
        font = load(size)
        widths = []
        for ln in lines:
            try:
                widths.append(font.getbbox(ln)[2] - font.getbbox(ln)[0])
            except Exception:  # noqa: BLE001
                widths.append(int(len(ln) * size * 0.55))
        if max(widths or [0]) <= max_w:
            break
        size -= 4
    font = load(size)

    line_h = int(size * 1.22)
    img = Image.new("RGBA", (w, int(cfg["reel"]["h"])), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    d = ImageDraw.Draw(img)

    top = int(cfg["reel"]["h"] * TOP_FRACTION)
    for i, ln in enumerate(lines):
        try:
            bb = font.getbbox(ln)
            lw = bb[2] - bb[0]
        except Exception:  # noqa: BLE001
            lw = int(len(ln) * size * 0.55)
        x = (w - lw) // 2
        y = top + i * line_h
        sd.text((x + 3, y + 5), ln, font=font, fill=(0, 0, 0, 190))
        d.text((x, y), ln, font=font, fill=(255, 255, 255, 255),
               stroke_width=4, stroke_fill=(0, 0, 0, 235))

    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    img = Image.alpha_composite(shadow, img)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, "PNG")
    return out_png


def cover_from_frame0(reel: Path, out_jpg: Path) -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(reel),
                        "-frames:v", "1", "-q:v", "2", str(out_jpg)],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and out_jpg.exists()


# ---------------------------------------------------------------- assembly

def _probe_stream_codec(path) -> tuple:
    """Returns (first_stream_codec_type, nb_frames_or_None)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_streams", "-select_streams", "v", str(path)],
                           capture_output=True, text=True, timeout=60)
        s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        nf = s.get("nb_frames")
        return s.get("codec_name"), (int(nf) if nf and str(nf).isdigit() else None)
    except Exception:  # noqa: BLE001
        return None, None


def assemble(bg_mp4: Path, cards: list, track_mp3: Path, out_mp4: Path, cfg,
             duration_s: float, track_wav: Path | None = None) -> bool:
    """Overlay each card over the background.

    Loop inputs are mandatory: image2 demuxers stop after one frame, which silently
    kills every `enable='between(t,..)'` overlay past t=0.
    """
    fps = int(cfg["reel"]["fps"])
    n_cards = max(len(cards), 1)
    card_paths = [config.OUTPUTS / f"card_{i}.png" for i in range(n_cards)]

    inputs = []
    if _probe_stream_codec(bg_mp4)[1] == 1:
        inputs += ["-loop", "1"]
    inputs += ["-i", str(bg_mp4)]
    for p in card_paths:
        inputs += ["-loop", "1", "-i", str(p)]
    inputs += ["-i", str(track_mp3)]

    filters = [f"[0:v]fps={fps},format=yuv420p[base]"]
    last = "base"
    for i, c in enumerate(cards):
        st, en = c["start"], c["end"]
        lbl_in = f"c{i}"
        lbl_out = f"v{i}"
        filters.append(
            f"[{i + 1}:v]fade=t=in:st={st:.3f}:d={CARD_FADE}:alpha=1,format=rgba[{lbl_in}]")
        filters.append(
            f"[{last}][{lbl_in}]overlay=enable='between(t,{st:.3f},{en:.3f})':"
            f"x=0:y=0:eof_action=pass[{lbl_out}]")
        last = lbl_out
    fade_out = 0.8
    audio_idx = n_cards + 1
    filters.append(
        f"[{audio_idx}:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
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
        print(f"[video] assemble failed: {r.stderr[-1500:]}")
        return False
    return out_mp4.exists()


# ----------------------------------------------------------------- QA gate

def extract_stamps(reel: Path, cards: list, out_dir=None) -> list:
    """Pull one frame per card to prove every overlay actually rendered."""
    out_dir = Path(out_dir or config.OUTPUTS)
    stamps = []
    for i, c in enumerate(cards):
        t = c["start"] + 0.5
        if t >= c["end"]:
            t = (c["start"] + c["end"]) / 2
        p = out_dir / f"check_{i}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}",
                            "-i", str(reel), "-frames:v", "1", "-q:v", "3", str(p)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and p.exists():
            stamps.append({"index": i, "t": round(t, 2), "kind": c.get("kind"),
                           "text": c["text"], "frame": str(p)})
    return stamps


def card_visible(frame_path: Path) -> bool:
    """True when the rendered card text is actually present in the frame.

    The text is pure white (255,255,255) so ~50 near-white pixels is a comfortable,
    robust threshold, and a frame with no overlay scores exactly zero.
    """
    from PIL import Image
    try:
        with Image.open(frame_path) as im:
            g = im.convert("L")
            hist = g.histogram()
        return sum(hist[245:256]) >= 50
    except Exception:  # noqa: BLE001
        return False


def qa_gate(reel: Path, cfg) -> tuple:
    """Returns (ok, info dict). Hard fail on any violation."""
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
                 "has_video": bool(v), "has_audio": bool(a),
                 "pix_fmt": (v or {}).get("pix_fmt"),
                 "codec": (v or {}).get("codec_name")})
    head = reel.open("rb").read(64)
    info["faststart"] = b"moov" in head

    problems = []
    if not v or not a:
        problems.append("missing video or audio stream")
    if (v or {}).get("width") != int(cfg["reel"]["w"]) or (v or {}).get("height") != int(cfg["reel"]["h"]):
        problems.append(f"resolution {(v or {}).get('width')}x{(v or {}).get('height')}")
    if not (min_s <= dur <= max_s):
        problems.append(f"duration {dur:.2f}s outside {min_s}-{max_s}s")
    if size >= 60 * 1024 * 1024:
        problems.append(f"size {size / 1048576:.1f}MB >= 60MB")
    if not info["faststart"]:
        problems.append("moov atom not at front (faststart missing)")
    info["problems"] = problems
    return (not problems), info


def build(cfg, content, duration_s: float, track_mp3: Path, track_wav: Path, bg_mp4: Path,
          out_mp4: Path | None = None, offline: bool = False):
    """Full build. Returns (reel_path, qa_info). Raises RuntimeError on QA failure."""
    out_mp4 = out_mp4 or (config.OUTPUTS / "reel.mp4")
    mood = content.get("mood") or "dark_ambient"
    beats = beat_times(track_wav, mood, cfg, duration_s) if track_wav.exists() \
        else beat_times_fallback(cfg, mood, duration_s)
    cards = timing_map(content, beats, duration_s)
    if not cards:
        raise RuntimeError("no text cards produced")
    for i, c in enumerate(cards):
        render_card(c["text"], config.OUTPUTS / f"card_{i}.png", cfg)
    if not assemble(bg_mp4, cards, track_mp3, out_mp4, cfg, duration_s, track_wav):
        raise RuntimeError("video assembly failed")
    cover_from_frame0(out_mp4, config.OUTPUTS / "cover.jpg")
    ok, info = qa_gate(out_mp4, cfg)
    info["cards"] = cards
    info["beats"] = beats[:24]
    if not ok:
        raise RuntimeError(f"QA gate failed: {info.get('problems')}")

    # proxy check: every card must actually be on screen at its own start + 0.5s
    stamps = extract_stamps(out_mp4, cards)
    invisible = []
    for s in stamps:
        s["visible"] = card_visible(Path(s["frame"]))
        if not s["visible"]:
            invisible.append(f"card {s['index']} ({s['kind']}) not visible at t={s['t']}")
    info["stamps"] = [{k: v for k, v in s.items() if k != "frame"} for s in stamps]
    if invisible or len(stamps) != len(cards):
        raise RuntimeError(f"QA gate failed: text cards missing from render: {invisible}")
    return out_mp4, info


def beat_times_fallback(cfg, mood: str, duration_s: float) -> list:
    bpm = float((cfg.get("mood_fallback_bpm") or {}).get(mood, 90))
    interval = 60.0 / max(bpm, 1)
    out, t = [], interval * 0.5
    while t < duration_s:
        out.append(t)
        t += interval
    return out or [0.5, 1.0, 2.0, 3.0, 4.0]


def pick_duration(cfg) -> float:
    lo = float(cfg["reel"]["min_s"])
    hi = float(cfg["reel"]["max_s"])
    return round(random.uniform(max(lo, 9.0), min(hi, 13.0)), 2)
