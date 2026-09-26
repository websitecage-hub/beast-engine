"""background.py — dark cinematic VIDEO for the Part 5 format.

Part 5.2: the background MUST be video, not a static image — motion is the
retention device. So the chain is video-first and video-only:

  1. Pinterest VIDEO (cluster query from config bg_types, LLM scene first)
  2. bundled still loop (last resort — flagged as a QA warning, not silently ok)

Follows the grade chain: crush dark, desaturate, vignette, grain, brightness
-0.13 so white text always pops.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from . import config, pinterest

GRADE = ("eq=contrast=1.12:saturation=0.40:gamma=0.95,"
         "curves=all='0/0.035 0.30/0.33 1/1',"
         "colorbalance=rs=-0.04:gs=-0.01:bs=0.07:rm=0:rh=0.035:bh=-0.035,"
         "vignette=PI/4.6,noise=alls=6:allf=t+u")
PREFERRED_ASPECT = 9 / 16
BG_DARKEN = -0.13


def _pin_aspect(entry: dict) -> float:
    for key in ("orig", "474x", "236x"):
        v = (entry.get("images") or {}).get(key)
        if isinstance(v, dict) and v.get("width") and v.get("height"):
            try:
                return float(v["width"]) / float(v["height"])
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return 0.75


def _pick_video(results: list, used_pins: set):
    """Pick an unused VIDEO pin, preferring vertical/portrait shape."""
    pool = []
    for e in results:
        if str(e.get("id")) in used_pins:
            continue
        if (e.get("type") or "image") != "video" or not e.get("best_video"):
            continue
        ar = _pin_aspect(e)
        shape_bonus = 2.0 if 0.4 <= ar <= 0.75 else 0.2
        pool.append((3.0 * shape_bonus, e))
    if not pool:
        return None
    total = sum(w for w, _ in pool)
    r = random.uniform(0, total)
    upto = 0.0
    for w, e in pool:
        upto += w
        if r <= upto:
            return e
    return pool[-1][1]


def _grade_cmd(src: Path, out: Path, duration_s: float, fps: int, still: bool,
               darken: float = BG_DARKEN) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = config.load_config()
    w, h = cfg["reel"]["w"], cfg["reel"]["h"]
    crop = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if still:
        frames = max(int(round(duration_s * fps)), 2)
        ow, oh = int(w * 1.2), int(h * 1.2)
        vf = (f"scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
              f"zoompan=z='1.0+0.06*on/{frames - 1}':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},"
              f"{GRADE},eq=brightness={darken},format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src),
               "-t", f"{duration_s:.3f}", "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    else:
        # Part 5.2 — seamless loop: trim to duration so the end flows into the start
        vf = (f"{crop},fps={fps},{GRADE},eq=brightness={darken},format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(src),
               "-t", f"{duration_s:.3f}", "-vf", vf, "-an",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        print(f"[background] grade failed: {r.stderr[-400:]}")
        return False
    return True


def build(cfg, content, memory, duration_s: float, offline: bool = False,
          dry_run: bool = False):
    """Returns (bg_mp4_path, bg_source). Video-first; bundled only as last resort."""
    fps = int(cfg["reel"]["fps"])
    out_norm = config.OUTPUTS / "bg.mp4"
    raw = config.OUTPUTS / "bg_raw"
    if offline:
        if _bundled(cfg, out_norm, duration_s, fps):
            return out_norm, "bundled"
        raise RuntimeError("bundled fallback background failed")

    scene = (content.get("scene") or "").strip()
    bg_type = content.get("bg_type")
    synonyms = (cfg["bg_types"].get(bg_type)
                or cfg.get("bg_types", {}).get(
                    cfg.get("archetype_bg_map", {}).get(
                        content.get("cluster") or content.get("archetype"), ""), [])
                or ["dark cinematic night video"])
    queries = [q for q in [scene, *synonyms] if q]
    used = {str(u.get("id") if isinstance(u, dict) else u)
            for u in memory.get("used_pins", [])}

    # ---- 1. Pinterest VIDEO — the mandated format (Part 5.2)
    for q in queries:
        for attempt in range(3):
            try:
                results = pinterest.search(q, media_type="video")
                entry = _pick_video(results, used)
                if not entry:
                    break
                url = entry.get("best_video")
                if url and pinterest.download(url, raw.with_suffix(".mp4")):
                    if _grade_cmd(raw.with_suffix(".mp4"), out_norm, duration_s, fps, False):
                        _record(memory, entry, dry_run)
                        return out_norm, "pinterest_video"
            except Exception as exc:  # noqa: BLE001
                print(f"[background] video {attempt + 1} failed: {exc}")

    # ---- 2. Bundled still loop (NOT the format — QA flags it)
    if _bundled(cfg, out_norm, duration_s, fps):
        print("[background] WARNING: fell back to bundled still background "
              "(format requires video)")
        return out_norm, "bundled"
    raise RuntimeError("all background providers failed")


def _record(memory, entry, dry_run: bool):
    if dry_run:
        return
    memory.setdefault("used_pins", []).append({
        "id": str(entry.get("id")), "date": config.today_utc().isoformat(),
        "pin_url": entry.get("pin_url"),
    })


def _convert_still(path: Path) -> bool:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.convert("RGB").save(config.OUTPUTS / "bg_still.jpg", "JPEG", quality=92)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[background] still convert failed: {exc}")
        return False


def _bundled(cfg, out_norm: Path, duration_s: float, fps: int) -> bool:
    bundled = config.ASSETS / "fallback" / "bg_default.jpg"
    if not bundled.exists():
        make_default_bg(bundled)
    if not bundled.exists():
        return False
    return _grade_cmd(bundled, out_norm, duration_s, fps, True)


def make_default_bg(path) -> bool:
    """1080x1920 near-black with a smooth dark-red radial glow.

    Built with float maths + dither noise: stepping a Pillow pixel loop produced
    visible banding rings, which is a dead giveaway of an amateur card.
    """
    from PIL import Image
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 1080, 1920
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2.0, h * 0.42
    max_d = float((w ** 2 + h ** 2) ** 0.5 / 2)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max_d
    t = np.clip(1.0 - d, 0.0, 1.0) ** 2.2
    r = 10.0 + 62.0 * t
    g = 10.0 + 8.0 * t
    b = 12.0 + 14.0 * t
    # ordered dither to kill banding in the smooth ramp
    rng = np.random.default_rng(11)
    n = rng.uniform(-0.9, 0.9, size=(h, w)).astype(np.float32)
    arr = np.stack([r + n, g + n, b + n], axis=-1)
    Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB").save(path, "JPEG", quality=92)
    return path.exists()
