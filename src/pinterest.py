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

import re
import shutil
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


def health(timeout: int = 20) -> dict:
    """Liveness of the upstream service, so failures are diagnosable.

    A redeployed or cold Pinterest service returns nothing and every lane comes back
    empty; without this the run only said "0 rejected", which reads like a filtering
    problem rather than a service that never answered.
    """
    for path in ("/api/status", "/health"):
        try:
            r = requests.get(f"{BASE}{path}", timeout=timeout)
            if r.status_code == 200:
                return {"status": r.status_code, "path": path, "body": r.text[:80]}
        except requests.RequestException as exc:
            last = str(exc)[:100]
        else:
            last = f"{path} -> {r.status_code}"
    return {"error": last if 'last' in dir() else "unreachable"}


def wake(retries: int = 6, wait: float = 15.0) -> bool:
    global _woken
    if _woken:
        return True
    # The service has answered on /health (FastAPI) and /api/status (v3.1 app); wake
    # must recognise both or a healthy service looks asleep and searches get skipped.
    for attempt in range(retries):
        for path in ("/api/status", "/health"):
            try:
                if requests.get(f"{BASE}{path}", timeout=30).status_code == 200:
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


def _normalise_new_pin(p: dict) -> dict:
    """Map the v3.1 /api/search pin shape onto the row shape the pipeline expects.

    The new service returns {id,title,description,board,domain,pin_url,video_url};
    it has no `videos[]`, no `type`, and its is_video flag is unreliable (measured:
    false on pins that carry a video_url). A pin with a video_url IS a video, so the
    type is set here and media_url() picks the direct MP4 up.
    """
    vu = str(p.get("video_url") or "")
    return {
        "id": p.get("id"),
        "title": p.get("title") or "",
        "description": p.get("description") or "",
        "creator": p.get("board") or "",
        "domain": p.get("domain"),
        "pin_url": p.get("pin_url"),
        "type": "video" if vu else "image",
        "best_video": vu or None,
        "videos": [{"url": vu}] if vu else [],
        "is_promoted": False,
        "image": p.get("image"),
    }


def search(query: str, media_type: str | None = None, retries: int = 3,
           page_size: int = 25) -> list:
    """Search pins. media_type 'video' -> the video surface. Returns results[].

    Two services have answered at BASE. The current one exposes one route
    (/api/search) whose scope selects pins vs videos; the older FastAPI one had
    /search/videos. The current shape is tried first and the old route is kept as a
    fallback, so a redeploy of either keeps the pipeline working.
    """
    wake()
    attempts = []
    if media_type == "video":
        attempts = [("/api/search", {"q": query, "scope": "videos", "page_size": page_size}),
                    ("/search/videos", {"q": query, "page_size": page_size})]
    else:
        attempts = [("/api/search", {"q": query, "page_size": page_size}),
                    ("/search", {"q": query, "page_size": page_size})]
    last = None
    for attempt in range(retries):
        for path, params in attempts:
            try:
                r = requests.get(f"{BASE}{path}", params=params,
                                 headers={"User-Agent": UA}, timeout=60)
                if r.status_code == 404:
                    last = RuntimeError(f"{path} not found (404)")
                    continue
                if r.status_code in (502, 503, 429) or r.status_code >= 500:
                    raise RuntimeError(f"transient {r.status_code}")
                r.raise_for_status()
                body = r.json()
                if isinstance(body.get("pins"), list):
                    return [_normalise_new_pin(p) for p in body["pins"]]
                return body.get("results") or []
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


def download(url: str, out_path, retries: int = 3, max_bytes: int = 0) -> bool:
    """Fetch a Pinterest video to disk as a playable file.

    Routes:
      * .m3u8 -> ffmpeg remux always (requests cannot read HLS at all).
      * direct MP4 -> /download?url= proxy first (the documented route; it also
        covers opaque-id pins and is CDN-cached), raw CDN as the fallback.

    `max_bytes` is a bounded-download guard for CI runners: a clip that exceeds it
    is discarded rather than silently filling the workspace.
    """
    url = str(url or "")
    if not url:
        return False
    if ".m3u8" in url.lower():
        # HLS is most of Pinterest's video. ffmpeg's network reader segfaults here,
        # so fetch the segments ourselves and let ffmpeg mux local bytes only.
        if _download_hls(url, out_path, max_bytes=max_bytes):
            return True
        return _download_ffmpeg(url, out_path)
    # Measured: 180s x 3 retries let ONE slow file block a run for nine minutes while
    # healthy clips land in seconds. Both bounds are configurable for slow networks.
    import os as _os
    retries = retries if retries is not None else int(_os.environ.get("BEAST_DL_RETRIES", "2"))
    timeout = int(_os.environ.get("BEAST_DL_TIMEOUT", "90"))
    deadline_s = float(_os.environ.get("BEAST_DL_DEADLINE", "75"))
    deadline = time.monotonic() + deadline_s
    # The raw CDN is tried first: measured 2.1 MB in 2.1s on the v3.1 service, which
    # no longer exposes the /download proxy at all (404). The proxy stays as a
    # fallback for opaque-id pins that only the old service could resolve.
    routes = [url, f"{BASE}/download?url={url}"]
    for attempt in range(retries):
        for route in routes:
            try:
                r = requests.get(route, timeout=timeout, stream=True,
                                 headers={"User-Agent": UA})
                if r.status_code == 404 and route != url:
                    continue
                if r.status_code == 200:
                    total = 0
                    with open(out_path, "wb") as fh:
                        for chunk in r.iter_content(1 << 16):
                            if time.monotonic() > deadline:
                                # A trickle of bytes keeps requests' per-read timeout
                                # alive forever; measured: one pin held a run for nine
                                # minutes.
                                raise TimeoutError(f"download exceeded {deadline_s:.0f}s")
                            if not chunk:
                                continue
                            total += len(chunk)
                            if max_bytes and total > max_bytes:
                                fh.close()
                                Path(out_path).unlink(missing_ok=True)
                                return False
                            fh.write(chunk)
                    if Path(out_path).stat().st_size > 0:
                        return True
                elif r.status_code in (403, 404, 410):
                    continue               # gone or blocked: try the next route
            except requests.RequestException:
                pass
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    return _download_ffmpeg(url, out_path)


def _hls_playlist(url: str, timeout: int = 30) -> str:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.text


def _hls_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _hls_variant(master: str, base: str) -> tuple[str, str | None]:
    """Pick the best variant playlist and its audio playlist from a master m3u8."""
    from urllib.parse import urljoin
    lines = _hls_lines(master)
    audio = None
    best, best_bw = None, -1
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line:
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                audio = urljoin(base, m.group(1))
        if line.startswith("#EXT-X-STREAM-INF:"):
            m = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(m.group(1)) if m else 0
            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                if bw > best_bw:
                    best, best_bw = urljoin(base, lines[i + 1]), bw
    return (best or base), audio


def _hls_segments(playlist: str, base: str) -> tuple[list[str], str | None]:
    """Segment URLs (including any init map) and the init-map URL if present."""
    from urllib.parse import urljoin
    init, segs = None, []
    for line in _hls_lines(playlist):
        if line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init = urljoin(base, m.group(1))
        elif not line.startswith("#"):
            segs.append(urljoin(base, line))
    return segs, init


def _fetch_all(urls: list[str], dest_dir: Path, timeout: int = 30) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, u in enumerate(urls):
        path = dest_dir / f"seg_{i:05d}.bin"
        with requests.get(u, timeout=timeout, headers={"User-Agent": UA}, stream=True) as r:
            r.raise_for_status()
            with open(path, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
        out.append(path)
    return out


def _concat_with_ffmpeg(parts: list[Path], out_path: Path, extra: list[Path]) -> bool:
    """Mux already-downloaded local parts with ffmpeg (no network involved).

    ffmpeg's HLS reader segfaults on this host (rc -11, no stderr) while its local
    file handling is fine, so the network is done here and ffmpeg only muxes bytes.
    """
    import subprocess
    listfile = out_path.with_suffix(".concat.txt")
    listfile.write_text("".join(f"file '{p}'\n" for p in parts))
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listfile)]
    for e in extra:
        cmd += ["-i", str(e)]
    cmd += ["-c", "copy", "-movflags", "+faststart", str(out_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    finally:
        listfile.unlink(missing_ok=True)
    return r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def _download_hls(url: str, out_path, max_bytes: int = 0) -> bool:
    """Fetch an HLS stream with requests, then mux the local bytes with ffmpeg."""
    from urllib.parse import urljoin
    out = Path(out_path)
    tmp = out.parent / f".{out.stem}_hls"
    try:
        master = _hls_playlist(url)
        variant, audio_pl = _hls_variant(master, url)
        if variant == url and "#EXTINF" in master:
            variant, audio_pl = url, None        # already a media playlist
        video_pl = _hls_playlist(variant)
        segs, init = _hls_segments(video_pl, variant)
        if not segs:
            return False
        urls = ([init] if init else []) + segs
        parts = _fetch_all(urls, tmp / "v")
        total = sum(p.stat().st_size for p in parts)
        if max_bytes and total > max_bytes:
            return False
        extra: list[Path] = []
        if audio_pl:
            a_segs, a_init = _hls_segments(_hls_playlist(audio_pl), audio_pl)
            if a_segs:
                a_parts = _fetch_all(([a_init] if a_init else []) + a_segs, tmp / "a")
                a_file = tmp / "audio_parts.bin"
                with open(a_file, "wb") as fh:
                    for part in a_parts:
                        fh.write(part.read_bytes())
                extra = [a_file]
        ok = _concat_with_ffmpeg(parts, out, extra)
        return ok
    except Exception:                      # noqa: BLE001
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
