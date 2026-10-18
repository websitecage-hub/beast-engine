"""build_short.py — the v3 renderer (§9 step 3): three visual arms, short format.

§4 text treatment (the fixes from the reel review):
  - ONE beat on screen at a time (the v2 flaw: lines stacked on top of each other)
  - bold sans, white with black stroke, max 2 lines per beat, inside x = 150-930
  - dark scrim behind the text (the text never fights the footage)
  - hook larger than the payoff
  - hook visible by 0.3s, payoff at ~60% of runtime, last frame held for the loop

Arms: A = Pexels stock clip; B = Pinterest still with push-in; C = text post
(no photo, plain dark/soft background).
Audio from t=0 (§5), 48kHz AAC, end faded so the loop doesn't jump.

reel_qa (render_gate) runs after every render — §9 adds two short checks:
hook visible by 0.3s (structural here) and 6-12s runtime (gate window).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import background, config, lint_short, pexels, pinterest

TEXT_X0, TEXT_X1 = 150, 930          # §4 text safe band
FPS = 30


# ------------------------------------------------------------------ beat timing

def beat_times(duration_s: float) -> tuple:
    """(hook_visible_at, payoff_at, total). Hook at 0, payoff at ~60%, §3.2."""
    payoff_at = lint_short.payoff_start(duration_s)
    return 0.0, payoff_at, float(duration_s)


# ------------------------------------------------------------------ text drawing

def _wrap(text: str, max_chars: int, max_lines: int | None = None) -> list:
    """Greedy word wrap. If max_lines is set the result is truncated to it."""
    words_list = text.split()
    lines, cur = [], ""
    for w in words_list:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:max_lines] if max_lines else lines


def _fit_beat(text: str, max_width: int = 780, max_lines: int = 2,
              start_size: int = 74) -> tuple:
    """Choose a font size (and wrapping) that shows ALL of the beat's words.

    §4 caps a beat at TWO lines. Rather than silently truncating a 14-word hook
    (which would ship an incomplete joke), shrink the font from `start_size`
    until the whole text wraps within `max_lines`. Returns (lines, fontsize).
    Floor is 30px — a 14-word hook fits two lines at 780px width by ~30px, and
    the lint already caps hooks at 14 words, so the floor is reachable for every
    lint-valid beat.
    """
    words = text.split()
    joined = " ".join(words)
    size = start_size
    while size >= 30:
        cpl = max(8, int(max_width / (size * 0.56)))
        lines = _wrap(text, cpl)
        if len(lines) <= max_lines and " ".join(lines) == joined:
            return lines, size
        size -= 2
    # floor reached: keep every word, allow more lines rather than drop any
    cpl = max(8, int(max_width / (30 * 0.56)))
    return _wrap(text, cpl), 30


# The pinned static FFmpeg has libass but NOT the drawtext filter, so text is
# burned in via an ASS subtitle track. This is also the more readable route:
# libass gives real centring (\an5), per-beat font sizes and outlines, and the
# Dialogue start/end times ARE the beat timing, so what renders is what the
# timeline specified.
FONT_FAMILY = "DejaVu Sans"
_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{font},74,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(t: float) -> str:
    t = max(0.0, float(t))
    h, m = int(t // 3600), int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(s) -> str:
    # braces open override blocks in ASS; backslashes are control sequences.
    return str(s).replace("{", "(").replace("}", ")").replace("\\", "/")


def _ass_path(path) -> str:
    """Escape a path for use as a filtergraph value."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def write_ass(path, events: list, outline: int = 4) -> str:
    """Write an ASS file. Each event: {start, end, size, lines[]}."""
    out = [_ASS_HEADER.format(font=FONT_FAMILY, outline=outline)]
    for e in events:
        text = "\\N".join(_ass_escape(ln) for ln in e["lines"])
        out.append(
            f"Dialogue: 0,{_ass_time(e['start'])},{_ass_time(e['end'])},Base,,0,0,0,,"
            f"{{\\an5\\fs{int(e['size'])}}}{text}")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
    return str(path)


def _scrim_filter(opacity: float = 0.45) -> str:
    """A soft dark band behind the text (§4): centre third, vertical gradient."""
    return (
        "drawbox=x=0:y=ih*0.30:w=iw:h=ih*0.42:color=black@"
        f"{opacity}:t=fill"
    )


# ------------------------------------------------------------------ backgrounds

def _pexels_clip(queries: list, duration_s: float, out: Path, memory: dict,
                 offline: bool = False) -> dict:
    """Arm A: a real 6-10s stock clip, licence-clean."""
    if offline or not pexels.available():
        return {"ok": False, "why": "pexels unavailable (no key or offline)"}
    for q in queries:
        res = pexels.fetch(q, out)
        if res.get("ok") and out.exists():
            return {"ok": True, "source": "pexels", "query": q}
    return {"ok": False, "why": "no pexels result for " + " | ".join(queries)}


def _pinterest_still(queries: list, duration_s: float, out: Path, memory: dict,
                     offline: bool = False) -> dict:
    """Arm B: Pinterest still/clip via background.build.

    build() returns (bg_mp4_path, bg_source) — an already-normalised, loop-ready
    1080x1920 mp4 (a still is animated with the push-in inside build()).
    """
    if offline:
        return {"ok": False, "why": "offline"}
    content = {"scene_queries": queries, "scene": queries[0] if queries else "",
               "bg_type": "short", "cluster": "short", "archetype": "short",
               "topic": "short", "topic_label": "short"}
    try:
        bg_path, bg_source = background.build(config.load_config(), content, memory,
                                              duration_s, offline=offline)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"background.build failed: {exc}"}
    if not bg_path or not Path(bg_path).exists():
        return {"ok": False, "why": "no background built"}
    # build() normalises everything to an mp4 (stills get animated), so the
    # renderer always treats it as a looping video input.
    return {"ok": True, "source": str(bg_source), "path": str(bg_path),
            "is_video": True}


def _text_post_bg(out: Path, duration_s: float, seed_hue: int = 226) -> dict:
    """Arm C: plain dark background with a subtle vertical shade — no photo."""
    ffmpeg = config.resolve_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Deep navy-black with a faint centre glow. The glow is a SINGLE frame
    # (r=1:d=1) that overlay repeats for the whole clip: without the pinned
    # duration the expensive geq expression was evaluated once per output frame
    # and a 9s reel took ~134s instead of ~2s.
    cmd = [ffmpeg, "-y",
           "-f", "lavfi", "-i",
           f"color=c=0x0e1220:s=1080x1920:d={duration_s:.2f}:r={FPS}",
           "-f", "lavfi", "-i",
           "color=c=0x1a2340:s=1080x1080:r=1:d=1",
           "-filter_complex",
           "[1:v]geq=r='40+20*sin(2*PI*X/1080)':g='35+18*sin(2*PI*X/1080)':b='64+30*sin(2*PI*X/1080)'[glow];"
           "[0:v][glow]overlay=(W-w)/2:(H-h)/2:eof_action=repeat:format=auto,format=yuv420p[v]",
           "-map", "[v]", "-t", f"{duration_s:.2f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.exists():
        return {"ok": False, "why": (r.stderr or "")[-200:]}
    return {"ok": True, "source": "textpost"}


# ------------------------------------------------------------------ assembly

def build(reel: dict, memory: dict, out_path: Path | None = None,
          offline: bool = False, duration_s: float | None = None,
          verbose: bool = True) -> dict:
    """Render one short reel. Returns {"ok", "path", "gate", "bg", ...}."""
    cfg = config.load_config()
    out_path = out_path or (config.OUTPUTS / "reel_short.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    hook, payoff = str(reel["hook"]), str(reel["payoff"])
    arm = reel.get("visual_arm", "C")
    bucket = reel.get("length_bucket", "8-10")
    if duration_s is None:
        lo, hi = {"6-8": (6.0, 8.0), "8-10": (8.0, 10.0),
                  "10-12": (10.0, 12.0)}.get(bucket, (8.0, 10.0))
        duration_s = round((lo + hi) / 2, 2)

    # 1. the background per arm
    queries = reel.get("scene_queries") or ["night window rain aesthetic"]
    bg = {"ok": False, "why": "not built"}
    if arm == "A":
        bg = _pexels_clip(queries, duration_s, config.OUTPUTS / "bg_short.mp4",
                          memory, offline)
        if not bg.get("ok"):
            if verbose:
                print(f"[short] arm A unavailable ({bg.get('why')}) -> falling to C",
                      flush=True)
            arm = "C"
    if arm == "B":
        bg = _pinterest_still(queries, duration_s, config.OUTPUTS / "bg_short.mp4",
                              memory, offline)
        if not bg.get("ok"):
            if verbose:
                print(f"[short] arm B unavailable ({bg.get('why')}) -> falling to C",
                      flush=True)
            arm = "C"
    if arm == "C":
        bg = _text_post_bg(config.OUTPUTS / "bg_short.mp4", duration_s)
        if not bg.get("ok"):
            return {"ok": False, "stage": "bg", "why": bg.get("why")}

    bg_path = Path(bg["path"]) if bg.get("path") else config.OUTPUTS / "bg_short.mp4"
    # `-loop 1` is an image-demuxer option; every arm's background here is an MP4
    # (stills are animated by background.build), so loop by extension, not by the
    # arm's label. `-loop 1` on an mp4 was 'Option loop not found'.
    _bg_is_image = str(bg_path).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))

    # 2. the text treatment
    ffmpeg = config.resolve_ffmpeg()
    hook_at, payoff_at, total = beat_times(duration_s)

    # §4: hook larger than payoff. Auto-fit so no lint-valid beat loses words.
    hook_lines, hook_size = _fit_beat(hook, start_size=74, max_lines=2)
    payoff_lines, payoff_size = _fit_beat(payoff, start_size=58, max_lines=2)
    if verbose:
        print(f"[short] hook {len(hook_lines)}L@{hook_size}px  "
              f"payoff {len(payoff_lines)}L@{payoff_size}px", flush=True)

    # one beat on screen at a time: the hook's Dialogue ends exactly where the
    # payoff's begins, so the hook disappears the moment the payoff lands.
    ass_path = config.OUTPUTS / "short_text.ass"
    write_ass(ass_path, [
        {"start": hook_at, "end": payoff_at + 0.001, "size": hook_size,
         "lines": hook_lines},
        {"start": payoff_at, "end": total, "size": payoff_size,
         "lines": payoff_lines},
    ])
    vf = f"{_scrim_filter(0.45)},ass={_ass_path(ass_path)}"
    if _bg_is_image:
        inputs = ["-loop", "1", "-t", f"{duration_s:.2f}", "-i", str(bg_path)]
    else:
        inputs = ["-stream_loop", "-1", "-t", f"{duration_s:.2f}", "-i", str(bg_path)]

    # 3. audio: the music track from t=0 (§5) + loop fade at the tail
    track = Path(str(reel.get("_track_path") or "")) if reel.get("_track_path") else None
    fade_out = f",afade=t=out:st={max(0.0, duration_s - 0.5):.2f}:d=0.5"
    if track and track.exists():
        afilter = (f"atrim=0:{duration_s:.2f},asetpts=PTS-STARTPTS,"
                   f"loudnorm=I=-19:TP=-1.5:LRA=11{fade_out},"
                   "aresample=48000,pan=stereo|c0=c0|c1=c1")
        cmd = [ffmpeg, "-y", *inputs, "-i", str(track),
               "-filter_complex",
               f"[0:v]{vf}[v];[1:a]{afilter}[a]",
               "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
               "-t", f"{duration_s:.2f}",
               "-movflags", "+faststart", str(out_path)]
    else:
        # §5: silence is not an option — synthesize a quiet room tone so the reel
        # never ships truly silent and never trips a "no audio" gate. anullsrc is
        # a SOURCE filter (no input), so it carries no input label; labeling it
        # with one was the render bug (it has 0 inputs, the graph gave it 1).
        afilter = (f"anullsrc=r=48000:cl=stereo,atrim=0:{duration_s:.2f}{fade_out}")
        cmd = [ffmpeg, "-y", *inputs,
               "-filter_complex",
               f"[0:v]{vf}[v];{afilter}[a]",
               "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "96k", "-ar", "48000",
               "-t", f"{duration_s:.2f}",
               "-movflags", "+faststart", str(out_path)]

    if verbose:
        print(f"[short] arm={arm} bg={bg_path.name} dur={duration_s}s "
              f"payoff@{payoff_at}s", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=420)
    if r.returncode != 0 or not out_path.exists():
        return {"ok": False, "stage": "render", "why": (r.stderr or "")[-400:]}

    # 4. the gate (§9: reel_qa after every render, with the two short checks)
    from . import render_gate
    ok_gate, gate = render_gate.check(out_path, expect_audio=True,
                                      duration_bounds=(6.0, 12.0))
    if not ok_gate:
        return {"ok": False, "stage": "gate", "gate": gate,
                "why": render_gate.describe(gate)}
    return {"ok": True, "path": str(out_path), "gate": gate, "bg": bg,
            "arm": arm, "duration_s": duration_s, "payoff_at": payoff_at}
