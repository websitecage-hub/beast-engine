"""asset_broker.py — bounded, provenance-recording acquisition for the pilot.

Two asset classes, two live contracts:

Pinterest (v2.5)  `/search/videos` → an entry with `best_video` (direct MP4) or
`videos[].url` (HLS only) → downloaded through the allow-listed `/download?url=`
proxy. An opaque `e1_...` id is never sent to `/pin/{id}/...`. A result is only
returned after the local file decodes and its crop/grade are usable.

Audio (audi0-scraper v2.3.0)  `/v1/trending` is ASYNC: 202 + poll_with until
status=ready, rows under `data`. `play_link` is a signed, expiring CDN URL and
the service does not store audio, so the file is fetched immediately, normalised
to about -20 LUFS, decoded, and fingerprinted here. Selection is by measured
distance to the format's reference fingerprint; if nothing can be measured the
broker FAILS rather than returning a first-unused track.

Rights are never inferred. A downloaded file is `usage_clearance=unknown` unless
explicit evidence is supplied, and unknown means preview-only.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
from pathlib import Path

import requests

from .. import audio_features, config, music, pinterest, render_gate
from . import run_manifest, template_payloads, template_renderer

UA = {"User-Agent": "Mozilla/5.0 (beast-engine asset broker)"}


class AssetError(RuntimeError):
    """No acceptable asset. Never silently replaced with something unrelated."""


def _ffmpeg() -> str:
    return config.resolve_ffmpeg()


def _probe(path) -> dict:
    return render_gate.probe(path)


# --------------------------------------------------------------------- Pinterest
def _query_variants(query: str):
    """The scene concept, then progressively less specific forms of the SAME scene.

    Pinterest returns nothing for long comma-heavy descriptions, but silently
    swapping in an unrelated clip is worse than generalising the exact concept.
    Every variant is recorded in the manifest.
    """
    # The API answers 422 for long queries, so every variant is clipped on a
    # word boundary before it is sent.
    def clip(text: str, n: int = 55) -> str:
        out: list = []
        for word in text.split():
            if len(" ".join(out + [word])) > n:
                break
            out.append(word)
        return " ".join(out)

    yield clip(query)
    parts = [p.strip() for p in re.split(r",|\band\b", query) if p.strip()]
    if len(parts) > 1:
        yield clip(", ".join(parts[:2]))
    if parts:
        yield clip(parts[0])
        yield clip(" ".join(parts[0].split()[:4]), 40)


def pinterest_background(query: str, format_id: str, scratch: Path, *,
                         limit: int = 8, min_width: int = 720) -> dict:
    """One bounded acquisition; returns the first candidate that survives the gates."""
    scratch.mkdir(parents=True, exist_ok=True)
    tried = []
    entries: list = []
    queries_tried: list = []
    for q in _query_variants(query):
        if q in queries_tried:
            continue
        queries_tried.append(q)
        try:
            entries = pinterest.search_videos(q, page_size=25)[:limit]
        except Exception as exc:  # noqa: BLE001
            tried.append({"query": q, "why": f"search failed: {exc}"})
            continue
        if entries:
            query = q
            break
    if not entries:
        raise AssetError(f"pinterest returned no video results for any of "
                         f"{queries_tried!r} (original: {query!r})")

    prof = template_renderer.PROFILES[format_id]
    for i, e in enumerate(entries):
        url = pinterest.media_url(e) if e else ""
        if not url:
            tried.append({"index": i, "why": "no mp4 and no hls variant"})
            continue
        dest = scratch / f"bg_{i}.mp4"
        if not pinterest.download(url, dest):
            tried.append({"index": i, "why": "download failed"})
            continue
        info = _probe(dest)
        v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        dur = float(info.get("format", {}).get("duration") or 0)
        if w < min_width or h <= w:
            tried.append({"index": i, "why": f"unusable crop {w}x{h}"})
            continue
        if dur < 3.0:
            tried.append({"index": i, "why": f"too short {dur:.1f}s"})
            continue
        metrics = template_renderer.measure_luma_sat(dest)
        _, grade = template_renderer.grade_for(format_id, metrics)
        if not grade["natural_fit"]:
            # Allowed, but never silently: a stretch pick is labelled as such.
            pass
        pin_id = str(e.get("id") or "")
        return {
            "path": str(dest), "query": query, "queries_tried": queries_tried, "index": i,
            "width": w, "height": h, "duration_s": round(dur, 2),
            "hls": ".m3u8" in url,
            "metrics": metrics, "grade": grade,
            "stretch_pick": not grade["natural_fit"],
            "pin_id": pin_id, "pin_url": e.get("pin_url"),
            "creator": e.get("creator"), "title": (e.get("title") or "")[:120],
            "source": "pinterest", "usage_clearance": "unknown",
            "review_state": "unvetted", "tried": tried,
            "sha256": run_manifest.sha256_file(dest),
            "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    raise AssetError(f"no acceptable pinterest background for {query!r}: {tried[:5]}")


# ------------------------------------------------------------------------ audio
def _download_to_m4a(play_link: str, dest: Path, *, timeout=300) -> bool:
    """Signed URL -> local m4a, normalised to about -20 LUFS (factory target)."""
    raw = dest.with_suffix(".src")
    try:
        with requests.get(play_link, headers=UA, stream=True, timeout=timeout) as r:
            if r.status_code != 200:
                return False
            with raw.open("wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
    except Exception:  # noqa: BLE001
        return False
    if not raw.exists() or raw.stat().st_size < 20480:
        return False
    r = subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", str(raw), "-vn",
                        "-af", "loudnorm=I=-20:TP=-1.5:LRA=9",
                        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                        str(dest)], capture_output=True, timeout=300)
    raw.unlink(missing_ok=True)
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 10240


def trending_audio(format_id: str, scratch: Path, *, keyword: str,
                   limit: int = 5) -> dict:
    """Poll the live trending API, then pick by MEASURED fingerprint distance."""
    cfg = config.load_config()
    api = (cfg.get("music") or {}).get("trending_api")
    if not api:
        raise AssetError("music.trending_api is not configured")
    scratch.mkdir(parents=True, exist_ok=True)
    rows = music.trending_rows(api, keyword, limit=limit)
    if not rows:
        raise AssetError(f"trending API returned no rows for {keyword!r}")

    ref = template_renderer.PROFILES[format_id]["music"]
    need_s = template_renderer.PROFILES[format_id]["duration_s"]
    candidates, rejected = [], []
    for row in rows:
        play = row.get("play_link")
        dur = row.get("duration_seconds")
        if not play:
            rejected.append({"id": row.get("id"), "why": "no play_link"})
            continue
        if dur and float(dur) < need_s:
            rejected.append({"id": row.get("id"), "why": f"shorter than {need_s}s"})
            continue
        if row.get("is_copyrighted") and row.get("is_commercial_use_allowed") is False:
            rejected.append({"id": row.get("id"), "why": "copyrighted, not commercial"})
            continue
        candidates.append(row)

    scored: list = []
    for row in candidates:
        dest = scratch / f"track_{row.get('id') or 'x'}.m4a"
        if not _download_to_m4a(str(row["play_link"]), dest):
            rejected.append({"id": row.get("id"), "why": "download/decode failed"})
            continue
        try:
            feats = audio_features.analyze(dest)
        except audio_features.AudioFeatureError as exc:
            rejected.append({"id": row.get("id"), "why": f"unmeasured: {exc}"})
            continue
        scored.append({"distance": audio_features.distance(ref, feats),
                       "features": feats, "row": row, "path": dest})

    if not scored:
        raise AssetError("no trending track could be measured; refusing to pick "
                         f"the first unused one: {rejected[:5]}")
    best = min(scored, key=lambda c: c["distance"])
    row = best["row"]
    return {
        "path": str(best["path"]), "keyword": keyword,
        "stable_id": str(row.get("id") or ""), "title": row.get("title"),
        "artist": row.get("artist"), "tiktok_url": row.get("tiktok_url"),
        "video_count": row.get("video_count"), "is_niche": row.get("is_niche"),
        "is_copyrighted": row.get("is_copyrighted"),
        "is_commercial_use_allowed": row.get("is_commercial_use_allowed"),
        "features": best["features"], "distance": best["distance"],
        "reference": ref, "rejected": rejected,
        "source": "audi0-scraper:/v1/trending",
        # A positive TikTok flag is NOT Instagram clearance.
        "usage_clearance": "unknown", "rights": "preview_only",
        "sha256": run_manifest.sha256_file(best["path"]),
        "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }