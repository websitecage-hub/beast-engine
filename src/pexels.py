"""pexels.py — the licence-clean A/B arm (ENGINE_SPEC §1.3).

STRATEGY §1 keeps Pinterest as the asset source but wires in Pexels so that "if
reach stays flat despite strong retention you'll find out cheaply instead of
guessing". Pinterest pins belong to other people; Pexels is licence-clean. Having
both means the originality question is answered by an experiment rather than an
argument.

The key lives in the repo secret `PEXELS_API_KEY` (Actions) or the local
`~/.beast-secrets/pexels.env`. It is never printed and never committed.

Config drives the split: `bg_source_share` = {pinterest: 0.85, pexels: 0.15}.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

API = "https://api.pexels.com/videos/search"
SECRET_FILE = Path.home() / ".beast-secrets" / "pexels.env"


def api_key() -> str:
    """Env first (Actions), then the local secret file. Never logged."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if key:
        return key
    if SECRET_FILE.exists():
        for line in SECRET_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PEXELS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def available() -> bool:
    return bool(api_key())


def search(query: str, per_page: int = 15, orientation: str = "portrait",
           want: str = "video") -> list:
    """Search Pexels. Returns [] on any failure — the caller falls back.

    `orientation=portrait` matters: a landscape hit has to be cropped hard to 9:16,
    which throws away most of the frame.
    """
    key = api_key()
    if not key:
        return []
    if want == "photo":
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
            {"query": query, "per_page": per_page, "orientation": orientation})
    else:
        url = API + "?" + urllib.parse.urlencode(
            {"query": query, "per_page": per_page, "orientation": orientation})
    req = urllib.request.Request(url, headers={"Authorization": key})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return data.get("videos") or data.get("photos") or []


def _best_file(video: dict) -> str:
    """The smallest file that is still at least 1080 on the short side.

    Pexels returns several renditions per video. Taking the largest wastes
    bandwidth and memory for pixels the crop throws away; taking a small one
    upscales and looks soft. 1080+ on the SHORT side is the threshold because the
    reel is 1080 wide.
    """
    files = [f for f in (video.get("video_files") or []) if f.get("link")]
    if not files:
        return ""

    def short_side(f):
        return min(int(f.get("width") or 0), int(f.get("height") or 0))

    ok = [f for f in files if short_side(f) >= 1080]
    pool = ok or files
    pool.sort(key=lambda f: short_side(f) * max(int(f.get("width") or 1),
                                                int(f.get("height") or 1)))
    return str(pool[0].get("link") or "")


def download(url: str, out_mp4: Path, timeout: int = 120) -> bool:
    """Fetch a rendition to disk. ffmpeg is used so the container is normalised."""

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "beast-engine"})
    tmp = out_mp4.with_suffix(".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, \
                open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    except Exception:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return False
    # Normalise to h264/mp4 and cut to a usable length. A Pexels download can be
    # any codec; the renderer needs something ffmpeg reads reliably.
    r = subprocess.run(
        [config.resolve_ffmpeg(), "-y", "-v", "error", "-i", str(tmp), "-t", "20",
         "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_mp4)],
        capture_output=True, text=True, timeout=300)
    tmp.unlink(missing_ok=True)
    if r.returncode != 0 or not out_mp4.exists() or out_mp4.stat().st_size < 8192:
        print(f"[pexels] normalise failed: {r.stderr[-300:]}")
        return False
    return True


def fetch(query: str, out_mp4: Path) -> dict:
    """One licence-clean clip for `query`. Returns {"ok", "path", "url", "credit"}."""
    if not available():
        return {"ok": False, "reason": "no PEXELS_API_KEY"}
    for v in search(query, want="video"):
        link = _best_file(v)
        if not link:
            continue
        if download(link, out_mp4):
            return {"ok": True, "path": str(out_mp4), "url": link,
                    "credit": (v.get("user") or {}).get("name") or "",
                    "source_url": v.get("url") or "",
                    "licence": "pexels-license"}
    return {"ok": False, "reason": "no usable clip"}


def should_use_pexels(cfg: dict, run_index: int = 0, forced: bool = False) -> bool:
    """The A/B split from config, plus the auto-reset's forced override."""
    if forced:
        return True
    share = (cfg.get("bg_source_share") or {}).get("pexels", 0.0)
    try:
        share = float(share)
    except (TypeError, ValueError):
        return False
    if share <= 0:
        return False
    # Deterministic on the run index, so the split is the share asked for rather
    # than whatever a random draw happened to produce on a given week.
    period = max(int(round(1.0 / share)), 1)
    return run_index % period == 0