"""pinterest.py — Pinterest search API for dark aesthetic backgrounds. No key.

Base: https://pinterest-api-inyg.onrender.com
502 = transient upstream failure: retry 3x with backoff, then fall to next provider.
"""
from __future__ import annotations

import time
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


def search_videos(query: str, retries: int = 3) -> list:
    """Spec §2.1 — the ONLY acceptable background source.

    Hits /search/videos and returns raw result dicts (each carries `best_video`
    with a direct MP4 URL and `videos[]` with durationMs).
    """
    return search(query, media_type="video", retries=retries)


def search(query: str, media_type: str | None = None, retries: int = 3) -> list:
    """Search pins. media_type 'video' -> /search/videos. Returns results[] (possibly empty)."""
    wake()
    path = "/search/videos" if media_type == "video" else "/search"
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE}{path}", params={"q": query, "page_size": 25},
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


def download(url: str, out_path, retries: int = 3) -> bool:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=120, stream=True)
            if r.status_code >= 500:
                raise RuntimeError(f"transient {r.status_code}")
            r.raise_for_status()
            with out_path.open("wb") as fh:
                for chunk in r.iter_content(65536):
                    fh.write(chunk)
            if out_path.stat().st_size > 1024:
                return True
        except Exception:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
    return False
