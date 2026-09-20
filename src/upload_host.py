"""upload_host.py — temporary public hosting for the rendered MP4.

Why this exists: `graph.instagram.com` (Instagram-Login tokens) does NOT support the
resumable upload flow — `POST /{ig-id}/media` rejects everything except `video_url`.
The reel is built locally on the runner, so it must be parked on an anonymous public
host just long enough for Instagram's fetcher to pull it. Containers last 24h; hosts
here hold the file 72h.
"""
from __future__ import annotations

from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")
TIMEOUT = 300


def _litterbox(path: Path) -> str | None:
    """catbox's temporary tier: 72h, 1GB, direct video/mp4, no key."""
    try:
        with path.open("rb") as fh:
            r = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": (path.name, fh, "video/mp4")},
                headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code == 200 and r.text.strip().startswith("http"):
            return r.text.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _uguu(path: Path) -> str | None:
    """uguu.se: 3h hold, direct video/mp4."""
    try:
        with path.open("rb") as fh:
            r = requests.post("https://uguu.se/upload",
                              params={"output": "text"},
                              files={"files[]": (path.name, fh, "video/mp4")},
                              headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code == 200 and r.text.strip().startswith("http"):
            return r.text.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


HOSTS = (("litterbox", _litterbox), ("uguu", _uguu))


def publicize(path) -> tuple:
    """Upload the reel and return (url, host_name). (None, None) if every host fails."""
    path = Path(path)
    if not path.exists():
        return None, None
    for name, fn in HOSTS:
        url = fn(path)
        if url:
            return url, name
    return None, None


def is_fetchable(url: str) -> bool:
    """Sanity check: the URL must serve video bytes to a plain GET."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=120, stream=True)
        ct = (r.headers.get("content-type") or "").lower()
        ok = r.status_code == 200 and ("video" in ct or "octet-stream" in ct)
        r.close()
        return ok
    except Exception:  # noqa: BLE001
        return False
