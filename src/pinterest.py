"""pinterest.py — Pinterest Media API v2.5 client (owner's USAGE.md).

Base: https://pinterest-api-inyg.onrender.com — key-free.

v2.5 contract notes that this client now honours (from the owner's USAGE.md):
  * /search/videos resolves direct MP4s, but ~13 of 23 video pins are HLS-ONLY:
    `best_video` is null and the play URL is a .m3u8 inside videos[]. ffmpeg
    remuxes HLS fine, so an m3u8 is ACCEPTABLE media, not a dead result.
  * Opaque ids (e1_...) cannot be served by /pin/<id>/* — media must come from
    the search payload itself and go through the /download?url= proxy (the raw
    CDN may still serve it, but the proxy is the documented route and is cached).
  * 502 = transient upstream failure: retry with backoff, then next provider.
"""
from __future__ import annotations

import time
from . import config

# ffmpeg is resolved centrally: PATH first, then imageio-ffmpeg's bundled binary.
FFMPEG = config.resolve_ffmpeg()
from pathlib import Path

import requests

BASE = "https://pinterest-api-inyg.onrender.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")
_woken = False


def wake(retries: int = 6, wait: float = 15.0) -> bool:
    global _woken
    if _woken:
        return True
    for attempt in range(retries):
        try:
            if requests.get(f"{BASE}/health", timeout=30).status_code == 200:
                _woken = True
                return True
        except requests.RequestException:
            pass
        if attempt < retries - 1:
            time.sleep(wait)
    return False


def search_videos(query: str, retries: int = 3, page_size: int = 25) -> list:
    """Spec §2.1 — the ONLY acceptable background source.

    Hits /search/videos (MP4 resolution on by default per USAGE.md §2) and
    returns raw result dicts. A dict is USABLE if it has a direct MP4 in
    `best_video` OR any .m3u8 in videos[] (ffmpeg handles HLS).
    """
    return search(query, media_type="video", retries=retries, page_size=page_size)


def search(query: str, media_type: str | None = None, retries: int = 3,
           page_size: int = 25) -> list:
    """Search pins. media_type 'video' -> /search/videos. Returns results[] (possibly empty)."""
    wake()
    path = "/search/videos" if media_type == "video" else "/search"
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE}{path}",
                             params={"q": query, "page_size": page_size},
                             headers={"User-Agent": UA}, timeout=60)
            if r.status_code in (502, 503, 429) or r.status_code >= 500:
                raise RuntimeError(f"transient {r.status_code}")
            r.raise_for_status()
            return r.json().get("results") or []
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"pinterest search failed for {query!r}: {last}")


def media_url(e: dict) -> str:
    """The best PLAYABLE url for a pin: direct MP4, else the .m3u8 (USAGE §0.3).

    An m3u8 is not a failure — ffmpeg remuxes it (see download()). Empty string
    only when the pin carries no video at all.
    """
    url = str(e.get("best_video") or "")
    if url:
        return url
    for v in e.get("videos") or []:
        vu = str(v.get("url") or "")
        if vu.lower().endswith(".m3u8"):
            return vu
    return ""


def download(url: str, out_path, retries: int = 3) -> bool:
    """Fetch a Pinterest video to disk as a playable file.

    Routes:
      * .m3u8 -> ffmpeg remux always (requests cannot read HLS at all).
      * direct MP4 -> /download?url= proxy first (the documented route; it also
        covers opaque-id pins and is CDN-cached), raw CDN as the fallback.
    """
    url = str(url or "")
    if not url:
        return False
    if ".m3u8" in url.lower():
        return _download_ffmpeg(url, out_path)
    proxied = f"{BASE}/download?url={url}"
    for attempt in range(retries):
        try:
            r = requests.get(proxied, timeout=180, stream=True,
                             headers={"User-Agent": UA})
            if r.status_code == 200:
                with open(out_path, "wb") as fh:
                    for chunk in r.iter_content(1 << 16):
                        if chunk:
                            fh.write(chunk)
                if Path(out_path).stat().st_size > 0:
                    return True
            elif r.status_code in (403, 404, 410):
                break                      # gone or blocked: retrying will not help
        except requests.RequestException:
            pass
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    return _download_ffmpeg(url, out_path)


def _download_ffmpeg(url: str, out_path, timeout: int = 600) -> bool:
    """Last resort / HLS path: let ffmpeg pull the stream (handles m3u8 + odd hosts)."""
    import subprocess
    out = Path(out_path)
    if out.exists():
        out.unlink()
    cmd = [FFMPEG, "-y", "-v", "error", "-user_agent", UA,
           "-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return False
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0
