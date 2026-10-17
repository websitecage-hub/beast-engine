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

def _wrap(text: str, max_chars: int) -> list:
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
    return lines[:2]  # §4: max 2 lines per beat


def _drawtext_filter(text: str, fontsize: int, y_expr: str, start: float, end: float,
                     font_path: str) -> str:
    """White text, black stroke, fade-in 0.2s at its reveal time."""
    esc = (text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
           .replace("%", "\\%"))
    return (
        f"drawtext=fontfile={font_path}:text='{esc}':"
        f"fontcolor=white:fontsize={fontsize}:borderw={max(3, fontsize // 12)}:"
        f"bordercolor=black:x=(w-text_w)/2:{y_expr}:"
        f"enable='between(t,{start:.2f},{end:.2f})'"
    )


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
    # deep navy-black with a faint centre glow; static, so a still png suffices
    cmd = [ffmpeg, "-y",
           "-f", "lavfi", "-i",
           f"color=c=0x0e1220:s=1080x1920:d={duration_s:.2f}:r={FPS}",
           "-f", "lavfi", "-i",
           "color=c=0x1a2340:s=1080x1080",
           "-filter_complex",
           "[1:v]geq=r='40+20*sin(2*PI*X/1080)':g='35+18*sin(2*PI*X/1080)':b='64+30*sin(2*PI*X/1080)'[glow];"
           "[0:v][glow]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p[v]",
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
    is_still = bg.get("is_video") is False or bg.get("source") == "textpost"

    # 2. the text treatment
    ffmpeg = config.resolve_ffmpeg()
    font = str((config.ASSETS / "fonts" / "DejaVuSans-Bold.ttf"))
    if not Path(font).exists():
        # the repo ships Coolvetica; DejaVu is the guaranteed bold sans fallback
        import shutil
        cand = shutil.which("fc-match")
        font = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if not Path(font).exists():
            font = str(config.DEFAULT_CONFIG.get("font_path") or "")
            font = str(config.ROOT / font) if not Path(font).is_absolute() else font
    hook_lines = _wrap(hook, 22)
    payoff_lines = _wrap(payoff, 24)
    hook_at, payoff_at, total = beat_times(duration_s)

    # hook larger than payoff (§4)
    hook_size = 74
    payoff_size = 58
    n_h = max(1, len(hook_lines))
    n_p = max(1, len(payoff_lines))
    hook_y = f"(h-text_h)/2-{int(hook_size * 0.75 * n_h / 2)}"
    payoff_y = f"(h-text_h)/2+{int(payoff_size * 0.9 * n_p / 2)}"

    filters = [_scrim_filter(0.45)]
    # hook: on screen from 0 to payoff reveal (fade handled by enable + one beat at a time)
    for i, ln in enumerate(hook_lines):
        filters.append(_drawtext_filter(
            ln, hook_size if i == 0 else int(hook_size * 0.92),
            hook_y if i == 0 else f"(h-text_h)/2-{int(hook_size*0.75*(n_h - i - 1)/2)}",
            hook_at, payoff_at + 0.001, font))
    # payoff: from its reveal to the end; hook disappears the moment it lands
    for i, ln in enumerate(payoff_lines):
        filters.append(_drawtext_filter(
            ln, payoff_size if i == 0 else int(payoff_size * 0.92),
            payoff_y if i == 0 else f"(h-text_h)/2+{int(payoff_size*0.9*(n_p - i - 1)/2)}",
            payoff_at, total, font))

    vf = ",".join(filters)
    if is_still:
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
               f"[0:v]{vf}[v];[2:a]{afilter}[a]",
               "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
               "-t", f"{duration_s:.2f}",
               "-movflags", "+faststart", str(out_path)]
    else:
        # §5: silence is not an option — synthesize a quiet room tone so the reel
        # never ships truly silent and never trips a "no audio" gate.
        afilter = (f"anullsrc=r=48000:cl=stereo,atrim=0:{duration_s:.2f}{fade_out}")
        cmd = [ffmpeg, "-y", *inputs,
               "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
               "-filter_complex",
               f"[0:v]{vf}[v];[2:a]{afilter}[a]",
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
