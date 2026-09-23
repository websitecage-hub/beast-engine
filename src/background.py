"""background.py — cinematic dark footage for quote-card reels.

Reference look (references/Video-*.mp4): REAL MOVING footage, crushed dark,
desaturated, strong vignette, faint grain — detail survives in the shadows.

Priority: Pinterest VIDEO (LLM scene query from content.json) -> Pinterest image
(with slow push-in) -> Meta image gen -> bundled still. All normalized to
1080x1920 with the cinematic grade chain.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from . import config, llm, pinterest

GRADE = ("eq=contrast=1.12:saturation=0.40:gamma=0.95,"
         "curves=all='0/0.035 0.30/0.33 1/1',"
         "colorbalance=rs=-0.04:gs=-0.01:bs=0.07:rm=0:rh=0.035:bh=-0.035,"
         "vignette=PI/4.6,noise=alls=6:allf=t+u")
PREFERRED_ASPECT = 9 / 16


def _pin_aspect(entry: dict) -> float:
    for key in ("orig", "474x", "236x"):
        v = (entry.get("images") or {}).get(key)
        if isinstance(v, dict) and v.get("width") and v.get("height"):
            try:
                return float(v["width"]) / float(v["height"])
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return 0.75


def _pick(results: list, used_pins: set, want: str):
    pool = []
    for e in results:
        if str(e.get("id")) in used_pins:
            continue
        typ = e.get("type") or "image"
        if want == "video":
            if typ == "video" and e.get("best_video"):
                ar = _pin_aspect(e)
                shape_bonus = 2.0 if 0.4 <= ar <= 0.75 else 0.2
                pool.append((3.0 * shape_bonus, e))
        else:
            imgs = e.get("images") or {}
            orig = imgs.get("orig")
            wide = 0
            if isinstance(orig, dict):
                try:
                    wide = int(orig.get("width") or 0)
                except (TypeError, ValueError):
                    wide = 0
            elif isinstance(orig, str):
                wide = 1000
            if typ == "image" and e.get("best_image") and wide >= 900:
                ar = _pin_aspect(e)
                shape_bonus = 1.6 if ar <= 0.85 else 0.5
                pool.append((shape_bonus, e))
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


def _grade_cmd(src: Path, out: Path, duration_s: float, fps: int, still: bool) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = config.load_config()["reel"]["w"], config.load_config()["reel"]["h"]
    crop = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
    if still:
        frames = max(int(round(duration_s * fps)), 2)
        ow, oh = int(w * 1.2), int(h * 1.2)
        vf = (f"scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
              f"zoompan=z='1.0+0.08*on/{frames - 1}':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},"
              f"{GRADE},format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src),
               "-t", f"{duration_s:.3f}", "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    else:
        vf = f"{crop},fps={fps},{GRADE},format=yuv420p"
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
    """Returns (bg_mp4_path, bg_source). Raises only if all providers fail."""
    fps = int(cfg["reel"]["fps"])
    out_norm = config.OUTPUTS / "bg.mp4"
    raw = config.OUTPUTS / "bg_raw"
    if offline:
        if _bundled(cfg, out_norm, duration_s, fps):
            return out_norm, "bundled"
        raise RuntimeError("bundled fallback background failed")

    scene = (content.get("scene") or "").strip()
    synonyms = (cfg["bg_types"].get(content.get("bg_type"))
                or ["dark cinematic night"])
    queries = [q for q in [scene, random.choice(synonyms)] if q]
    used = {str(u.get("id") if isinstance(u, dict) else u)
            for u in memory.get("used_pins", [])}

    # ---- 1. Pinterest VIDEO — the reference look is moving footage
    for q in queries:
        for attempt in range(3):
            try:
                results = pinterest.search(q, media_type="video")
                entry = _pick(results, used, "video")
                if not entry:
                    break
                url = entry.get("best_video")
                if url and pinterest.download(url, raw.with_suffix(".mp4")):
                    if _grade_cmd(raw.with_suffix(".mp4"), out_norm, duration_s, fps, False):
                        _record(memory, entry, dry_run)
                        return out_norm, "pinterest_video"
            except Exception as exc:  # noqa: BLE001
                print(f"[background] video {attempt + 1} failed: {exc}")

    # ---- 2. Pinterest IMAGE with slow push-in
    for q in queries:
        try:
            results = pinterest.search(q)
            entry = _pick(results, used, "image")
            if entry and entry.get("best_image"):
                if pinterest.download(entry["best_image"], raw) and _convert_still(raw):
                    if _grade_cmd(config.OUTPUTS / "bg_still.jpg", out_norm,
                                  duration_s, fps, True):
                        _record(memory, entry, dry_run)
                        return out_norm, "pinterest_image"
        except Exception as exc:  # noqa: BLE001
            print(f"[background] image failed: {exc}")

    # ---- 3. Meta image generation
    prompt = (f"cinematic night photograph, {scene or 'lone man silhouette'}, "
              f"extremely dark low-key, crushed blacks, single light source, "
              f"vertical composition, no text, no watermark")
    gen = config.OUTPUTS / "bg_ai.jpg"
    if llm.image(prompt, gen) and _convert_still(gen):
        if _grade_cmd(config.OUTPUTS / "bg_still.jpg", out_norm, duration_s, fps, True):
            return out_norm, "meta_image"

    # ---- 4. Bundled
    if _bundled(cfg, out_norm, duration_s, fps):
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
