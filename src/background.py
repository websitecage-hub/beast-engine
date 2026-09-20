"""background.py — dark aesthetic background: Pinterest -> Meta image -> bundled.

Normalizes to 1080x1920 with brightness -0.12. Static images get a slow Ken Burns
zoom (1.00 -> 1.08) with 20% pre-overscale to avoid jitter.
"""
from __future__ import annotations

import json
import random
import subprocess
from datetime import timedelta
from pathlib import Path

from . import config, llm, pinterest

PINTEREST_RETRIES = 3
BAR_Y = 1920
BAR_X = 1080


def _orig_width(entry: dict) -> int:
    images = entry.get("images") or {}
    for key in ("orig", "474x", "236x", "170x"):
        val = images.get(key)
        if isinstance(val, dict):
            try:
                return int(val.get("width") or 0)
            except (TypeError, ValueError):
                continue
        if isinstance(val, str):
            return 1000 if key == "orig" else 0
    return 0


def _pick(results: list, used_pins: set):
    """Weighted-random pick, favouring unused pins. Returns entry or None."""
    pool = []
    for e in results:
        if str(e.get("id")) in used_pins:
            continue
        typ = e.get("type") or "image"
        if typ == "image" and e.get("best_image") and _orig_width(e) >= 1000:
            pool.append((3.0, e))
        elif typ == "video" and e.get("best_video"):
            pool.append((2.0, e))
        elif e.get("best_image"):
            pool.append((1.0, e))
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


def _normalize_source(src: Path, out: Path, duration_s: float, is_video: bool, fps: int) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    if is_video:
        vf = (f"scale={BAR_X}:{BAR_Y}:force_original_aspect_ratio=increase,"
              f"crop={BAR_X}:{BAR_Y},eq=brightness=-0.12,fps={fps},format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(src),
               "-t", f"{duration_s:.3f}", "-vf", vf, "-an",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    else:
        # 20% overscale before zoompan so the zoom never reveals edges (no jitter)
        ow, oh = int(BAR_X * 1.2), int(BAR_Y * 1.2)
        frames = max(int(round(duration_s * fps)), 2)
        zexpr = f"1.0+0.08*on/{frames - 1}"
        vf = (f"scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
              f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"d={frames}:s={BAR_X}x{BAR_Y}:fps={fps},eq=brightness=-0.12,format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src),
               "-t", f"{duration_s:.3f}", "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not out.exists():
        print(f"[background] normalize failed: {r.stderr[-500:]}")
        return False
    return True


def build(cfg, content, memory, duration_s: float, offline: bool = False, dry_run: bool = False):
    """Returns (bg_mp4_path, bg_source). Raises only if even the bundled fallback fails."""
    fps = int(cfg["reel"]["fps"])
    bg_type = content.get("bg_type") or random.choice(list(cfg["bg_types"].keys()))
    out_norm = config.OUTPUTS / "bg.mp4"
    raw = config.OUTPUTS / "bg_raw"

    if offline:
        if _bundled_dark(cfg, out_norm, duration_s, fps):
            return out_norm, "bundled"
        raise RuntimeError("bundled fallback background failed")

    synonyms = cfg["bg_types"].get(bg_type) or ["dark aesthetic"]
    query = random.choice(synonyms)
    used = set()
    for u in memory.get("used_pins", []):
        used.add(str(u.get("id") if isinstance(u, dict) else u))

    # ---- 1. Pinterest (3 retries)
    for attempt in range(PINTEREST_RETRIES):
        try:
            results = pinterest.search(query)
            entry = _pick(results, used)
            if not entry:
                results = pinterest.search(query, media_type="video")
                entry = _pick(results, used)
            if entry:
                url = entry.get("best_video") if entry.get("type") == "video" else entry.get("best_image")
                if url and pinterest.download(url, raw):
                    if _looks_video(raw):
                        if _normalize_source(raw, out_norm, duration_s, True, fps):
                            _record_pin(memory, entry, dry_run)
                            return out_norm, "pinterest"
                    else:
                        if _convert_still(raw):
                            if _normalize_source(config.OUTPUTS / "bg_still.jpg", out_norm,
                                                 duration_s, False, fps):
                                _record_pin(memory, entry, dry_run)
                                return out_norm, "pinterest"
        except Exception as exc:  # noqa: BLE001
            print(f"[background] pinterest attempt {attempt + 1} failed: {exc}")

    # ---- 2. Meta image generation
    prompt = (f"dark moody cinematic background, {bg_type}, no text, no watermark, "
              f"high contrast, film grain, vertical")
    gen = config.OUTPUTS / "bg_ai.jpg"
    if llm.image(prompt, gen) and _convert_still(gen):
        if _normalize_source(config.OUTPUTS / "bg_still.jpg", out_norm, duration_s, False, fps):
            return out_norm, "meta_image"

    # ---- 3. Bundled
    if _bundled_dark(cfg, out_norm, duration_s, fps):
        return out_norm, "bundled"
    raise RuntimeError("all background providers failed")


def _record_pin(memory, entry, dry_run: bool):
    if dry_run:
        return
    memory.setdefault("used_pins", []).append({
        "id": str(entry.get("id")), "date": config.today_utc().isoformat(),
        "pin_url": entry.get("pin_url"),
    })


def _looks_video(path: Path) -> bool:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v",
                            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=60)
        head = path.open("rb").read(12)
        is_mp4 = b"ftyp" in head or head[:4] in (b"\x1aE\xdf\xa3",)
        return bool(r.stdout.strip()) and (is_mp4 or _ext_is_video(path))
    except Exception:  # noqa: BLE001
        return _ext_is_video(path)


def _ext_is_video(path: Path) -> bool:
    return path.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v")


def _convert_still(path: Path) -> bool:
    """Normalise whatever image format came down into a baseline JPEG."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            dst = config.OUTPUTS / "bg_still.jpg"
            im.save(dst, "JPEG", quality=92)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[background] still convert failed: {exc}")
        return False


def _bundled_dark(cfg, out_norm: Path, duration_s: float, fps: int) -> bool:
    """Bundled fallback: rendered jpg if present, else a dark-red radial gradient."""
    bundled = config.ASSETS / "fallback" / "bg_default.jpg"
    if not bundled.exists():
        make_default_bg(bundled)
    if not bundled.exists():
        return False
    return _normalize_source(bundled, out_norm, duration_s, False, fps)


def make_default_bg(path) -> bool:
    """1080x1920 near-black (#0a0a0c) with a subtle dark-red radial gradient."""
    from PIL import Image, ImageDraw
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 1080, 1920
    base = Image.new("RGB", (w, h), (10, 10, 12))
    grad = Image.new("RGB", (w, h), (10, 10, 12))
    px = grad.load()
    cx, cy = w / 2, h * 0.42
    max_d = (w ** 2 + h ** 2) ** 0.5 / 2
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / max_d
            t = max(0.0, 1.0 - d) ** 2.2
            r = int(10 + 62 * t)
            g = int(10 + 8 * t)
            b = int(12 + 14 * t)
            for dy in (0, 1):
                for dx in (0, 1):
                    if x + dx < w and y + dy < h:
                        px[x + dx, y + dy] = (min(r, 255), min(g, 255), min(b, 255))
    out = Image.blend(base, grad, 0.85)
    out = out.resize((w, h))
    out.save(path, "JPEG", quality=90)
    return path.exists()
