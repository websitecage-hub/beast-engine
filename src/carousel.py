"""carousel.py — BEAST ENGINE v2 carousels (ENGINE_SPEC §3.1).

STRATEGY §3.3: a carousel is the SAVE/SEND format, 2 per week, 6-8 slides, 4:5,
slide 1 is the hook. Instagram accepts JPEG only for images (verified against the
Meta docs 2026-10-11), so every slide is written as JPEG, never PNG.

Publishing is three steps, and the middle one is where this normally breaks:
  1. one container per slide with is_carousel_item=true  (a child needs no caption)
  2. a carousel container with media_type=CAROUSEL and children=id1,id2,...
  3. media_publish on the carousel container

A carousel counts as ONE post against the publishing limit, but the docs disagree
on whether that limit is 100 or 50 per 24h, so the caller reads
GET /{ig-id}/content_publishing_limit at run time rather than trusting either.

Insights for media inside an album are unavailable per the docs, so these posts
are tagged format=carousel and stored with album-level metrics only — never
scored as if they were reels, which would poison the learner with zeros.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import requests

from . import config

GRAPH = "https://graph.instagram.com/v21.0"
W, H = 1080, 1350          # 4:5
JPEG_QUALITY = 92


def slide_texts(reel: dict) -> list:
    """Slide copy from a reel: hook first, then the beats, then a soft close."""
    lines = [str(reel.get("hook") or "").strip()] + \
            [str(b).strip() for b in (reel.get("beats") or []) if str(b).strip()]
    lines = [ln for ln in lines if ln]
    if len(lines) <= 8:
        return lines
    # Keep the hook and the closing line; thin the middle.
    return [lines[0]] + lines[1:7] + [lines[-1]]


def _render_slide(text: str, out_jpg: Path, cfg, bg: Path | None = None) -> bool:
    """One JPEG slide: near-white text on a dark background, in the safe band.

    Reuses build_video's text renderer for the type so the carousel matches the
    reels instead of looking like a different account.
    """
    from . import build_video
    from .still import FFMPEG

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    png = out_jpg.with_suffix(".png")
    band = (cfg.get("text_limits") or {}).get("safe_band") or [0.15, 0.72]
    lo, hi = (float(band[0]), float(band[1]))
    if hi > 1.0:
        lo, hi = lo / 100.0, hi / 100.0

    px = build_video.fit_common_px([text], cfg, start_px=64, y_band=(lo, hi))
    build_video.render_single_line(text, png, cfg, px=px,
                                   y_frac=(lo + hi) / 2.0)

    src = str(bg) if bg and Path(bg).exists() else None
    if src:
        cmd = [FFMPEG, "-y", "-v", "error", "-stream_loop", "-1", "-i", src,
               "-i", str(png), "-filter_complex",
               f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{H},setsar=1[bg];[bg][1:v]overlay=0:0[o]",
               "-map", "[o]", "-frames:v", "1", "-q:v", "2", str(out_jpg)]
    else:
        cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
               "-i", f"color=c=0x0d0d10:s={W}x{H}", "-i", str(png),
               "-filter_complex", "[0:v][1:v]overlay=0:0[o]",
               "-map", "[o]", "-frames:v", "1", "-q:v", "2", str(out_jpg)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"[carousel] slide render failed: {r.stderr[-400:]}")
        return False
    return out_jpg.exists() and out_jpg.stat().st_size > 2048


def build(reel: dict, out_dir: Path, cfg, bg: Path | None = None) -> dict:
    """Render every slide as JPEG. Returns {"ok", "slides": [paths], "problems"}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    texts = slide_texts(reel)
    problems = []
    if not 6 <= len(texts) <= 8:
        problems.append(f"{len(texts)} slides (docs allow 2-10, strategy wants 6-8)")
    slides = []
    for i, txt in enumerate(texts):
        jpg = out_dir / f"slide_{i + 1:02d}.jpg"
        if _render_slide(txt, jpg, cfg, bg):
            slides.append(jpg)
        else:
            problems.append(f"slide {i + 1} failed to render")
    return {"ok": bool(slides) and not problems, "slides": slides,
            "problems": problems}


def caption_for(reel: dict, cfg: dict) -> str:
    """The album caption. Slide 1 is the hook, so the caption adds the reframe."""
    cap = str(reel.get("caption") or "").strip()
    return cap


def publish(ig_id: str, slides: list, caption: str, publish_mod=None,
            dry_run: bool = False) -> dict:
    """Three-step carousel publish (ENGINE_SPEC §3.1).

    Returns {"ok", "media_id", "stage", "error"}. Fails CLOSED: a carousel missing
    even one child is never published, because Instagram would either error or
    publish a partial album, and a partial album is worse than a skipped slot.

    Uses upload_host.publicize (the proven host route) — the Instagram-Login token
    has no resumable upload, so every image is parked on a public URL first.
    """
    from . import publish as _pub
    from . import upload_host
    publish = publish_mod or _pub
    if not slides:
        return {"ok": False, "stage": "no_slides", "error": "no slides built"}

    host_urls = []
    for p in slides:
        try:
            url, _host = upload_host.publicize(Path(p))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "host", "error": str(exc)[:200]}
        if not url:
            return {"ok": False, "stage": "host", "error": f"no url for {p}"}
        host_urls.append(url)

    token = publish._token()
    children = []
    for url in host_urls:
        try:
            r = requests.post(f"{GRAPH}/{ig_id}/media", data={
                "image_url": url, "is_carousel_item": "true",
                "access_token": token}, timeout=120)
            if r.status_code != 200:
                return {"ok": False, "stage": "child",
                        "error": f"{r.status_code}: {r.text[:200]}"}
            children.append(str((r.json() or {}).get("id")))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "child", "error": str(exc)[:200]}

    if len(children) != len(host_urls):
        return {"ok": False, "stage": "child",
                "error": f"only {len(children)}/{len(host_urls)} children created"}

    try:
        r = requests.post(f"{GRAPH}/{ig_id}/media", data={
            "media_type": "CAROUSEL", "children": ",".join(children),
            "caption": caption, "access_token": token}, timeout=120)
        if r.status_code != 200:
            return {"ok": False, "stage": "album",
                    "error": f"{r.status_code}: {r.text[:200]}"}
        container = str((r.json() or {}).get("id"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "album", "error": str(exc)[:200]}

    if dry_run:
        return {"ok": True, "media_id": None, "container_id": container,
                "stage": "dry_run"}
    try:
        media_id = publish.media_publish(ig_id, container)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "publish", "error": str(exc)[:200]}
    return {"ok": bool(media_id), "media_id": media_id, "stage": "published",
            "error": "" if media_id else "no media id returned"}


def publish_limit(ig_id: str, publish_mod=None) -> dict:
    """GET /{ig-id}/content_publishing_limit — the docs disagree on 100 vs 50."""
    from . import publish as _pub
    publish = publish_mod or _pub
    try:
        r = publish._get(f"/{ig_id}/content_publishing_limit",
                         {"fields": "quota_usage,config"})
        if r.status_code == 200:
            data = (r.json() or {}).get("data") or []
            return data[0] if data else {}
    except Exception:  # noqa: BLE001
        pass
    return {}