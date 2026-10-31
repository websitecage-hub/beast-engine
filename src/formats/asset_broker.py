"""asset_broker.py — the asset intelligence layer (Asset Intelligence v1 §4-10).

Flow, per reel:

    AssetIntent -> three bounded query lanes (literal/adjacent/emotional)
    -> /search/videos, deduplicated across lanes
    -> metadata prefilters (ads, promoted pins, branded domains, wrong shape)
    -> bounded downloads (never the whole search page)
    -> local measurement: probe, luma/sat, motion, text-safe band, burned-in text
    -> 100-point candidate score, best candidate >= 75 wins
    -> reserve in the freshness ledger (commit only after delivery)

Two live contracts, both owned by the owner's services:

Pinterest (v2.5)  `/search/videos` -> best_video (MP4) or videos[].m3u8; media goes
through the allow-listed `/download?url=` proxy. Opaque `e1_...` ids never reach
`/pin/{id}/...`.

Audio (audi0-scraper v2.3.0)  `/v1/trending` is async (202 + poll_with until
status=ready, rows under `data`). `play_link` is signed and expiring, so the file
is fetched immediately, normalised to about -20 LUFS, decoded and fingerprinted.
Selection is by measured emotional/feature score; an unmeasurable track fails
closed instead of becoming "the first unused one".

Rights are never inferred: a downloaded file is `usage_clearance=unknown`, which
means preview-only.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import requests

from .. import audio_features, config, music, pinterest, render_gate
from . import (asset_intent, asset_ledger, asset_probe, asset_scoring,
               run_manifest, template_renderer)

UA = {"User-Agent": "Mozilla/5.0 (beast-engine asset broker)"}

MAX_DOWNLOADS = 6           # bounded: a search page is not a shopping list
MAX_BYTES = 25 * 1024 * 1024   # bounded per clip, for CI runners
LANE_EXPANSION = 1          # one extra search per lane before we give up


class AssetError(RuntimeError):
    """No acceptable asset. Never silently replaced with something unrelated."""


def _ffmpeg() -> str:
    return config.resolve_ffmpeg()


def _probe(path) -> dict:
    return render_gate.probe(path)


# --------------------------------------------------------------------- Pinterest
def _clip(text: str, n: int = asset_intent.QUERY_MAX) -> str:
    return asset_intent.clip(text, n)


def _generalise(query: str) -> str:
    """Less specific wording of the SAME scene (§4 retry policy), never a new topic."""
    parts = [p.strip() for p in re.split(r",|\band\b", query) if p.strip()]
    cands = [_clip(" ".join(query.split()[:5]), 34)]
    if len(parts) > 1:
        cands.insert(0, _clip(", ".join(parts[:2])))
    return next((c for c in cands if c and c.lower() != query.lower()), "")


def _key(entry: dict) -> str:
    return str(entry.get("id") or entry.get("pin_url") or entry.get("best_video") or "")


def _pre_rank(entries: list, intent: dict) -> list:
    """Order candidates by cheap metadata relevance before downloading any.

    The API returns results in its own order, so taking the first six spent the
    whole download budget on whatever Pinterest ranked first. This orders by scene
    token overlap (the only semantic signal available before download), then by
    direct MP4 over HLS for speed.
    """
    want = {w for w in re.findall(r"[a-z]{3,}", " ".join(
        [str(intent.get("literal_scene") or ""), str(intent.get("setting") or ""),
         str(intent.get("human_action") or "")] +
        [str(e) for e in (intent.get("scene_entities") or [])]).lower())}
    lane_rank = {"literal": 0, "adjacent": 1, "emotional": 2}
    def key(e):
        text = " ".join(str(e.get(k) or "") for k in ("title", "description"))
        toks = {w for w in re.findall(r"[a-z]{3,}", text.lower())}
        overlap = len(want & toks)
        direct = 0 if e.get("best_video") else 1
        return (-overlap, lane_rank.get(str(e.get("_lane") or "literal"), 3), direct)
    return sorted(entries, key=key)


def _search_lanes(plan: list[dict], *, page_size: int = 25) -> tuple[list, list]:
    """Run the lanes, dedupe by pin id and media url, keep the lane that found it."""
    seen, results, log = set(), [], []
    for item in plan:
        q = item["query"]
        if not q or asset_intent.banned_query(q):
            log.append({"lane": item["lane"], "query": q, "why": "banned mood slogan"})
            continue
        try:
            found = pinterest.search_videos(q, page_size=page_size)
        except Exception as exc:  # noqa: BLE001
            log.append({"lane": item["lane"], "query": q, "why": f"search failed: {exc}"})
            continue
        kept = 0
        for e in found:
            k = _key(e)
            if not k or k in seen:
                continue
            seen.add(k)
            e = {**e, "_query": q, "_lane": item["lane"]}
            results.append(e)
            kept += 1
        log.append({"lane": item["lane"], "query": q, "kept": kept,
                    "returned": len(found)})
    return results, log


def acquire(intent: dict, format_id: str, scratch: Path, *,
            run_id: str = "adhoc", min_width: int = 720,
            max_downloads: int = MAX_DOWNLOADS, verbose: bool = True) -> dict:
    """Full staged acquisition. Returns the chosen clip plus every scored candidate."""
    scratch.mkdir(parents=True, exist_ok=True)
    prof = template_renderer.PROFILES[format_id]
    needed = float(prof["duration_s"])
    ledger = asset_ledger.sync_from_manifest()
    # Only assets used INSIDE the no-repeat window are excluded; older use is
    # allowed again (the window is configurable, not permanent).
    cooled = {k: r for k, r in ledger["visual"].items()
              if not asset_ledger.is_fresh(k, kind="visual", data=ledger)}
    used_pins = {r.get("pin_id") for r in cooled.values() if r.get("pin_id")}
    used_shas = {r.get("sha256") for r in cooled.values() if r.get("sha256")}

    plan = asset_intent.lanes(intent)
    if not plan:
        raise AssetError("asset intent produced no usable scene query")

    rejected: list[dict] = []
    scored: list[dict] = []
    query_log: list[dict] = []
    expansion_used = 0
    rounds = 0
    while rounds <= LANE_EXPANSION:
        play = plan if rounds == 0 else [
            {"lane": p["lane"], "query": _generalise(p["query"])} for p in plan]
        play = [p for p in play if p["query"]]
        if play:
            found, log = _search_lanes(play)
            query_log += log
            for e in _pre_rank(found, intent):
                if len(scored) >= max_downloads:
                    break
                why = asset_scoring.hard_reject(e, needed_s=needed, min_width=min_width)
                if why:
                    rejected.append({"id": _key(e), "query": e.get("_query"),
                                     "lane": e.get("_lane"), "why": why})
                    continue
                pid = str(e.get("id") or "")
                if pid and pid in used_pins:
                    rejected.append({"id": pid, "why": "used inside the no-repeat window"})
                    continue
                url = pinterest.media_url(e)
                if not url:
                    rejected.append({"id": pid, "why": "no mp4 and no hls variant"})
                    continue
                idx = len(scored)
                dest = scratch / f"cand_{rounds}_{idx}.mp4"
                try:
                    # Bounded: a CI runner must not be filled by one clip.
                    ok = pinterest.download(url, dest, max_bytes=MAX_BYTES)
                except Exception as exc:  # noqa: BLE001
                    rejected.append({"id": pid, "why": f"download error: {str(exc)[:60]}"})
                    continue
                if not ok:
                    rejected.append({"id": pid,
                                     "why": f"download failed or exceeded the "
                                            f"{MAX_BYTES // (1 << 20)} MB cap"})
                    continue
                sha = run_manifest.sha256_file(dest)
                if sha in used_shas:
                    rejected.append({"id": pid, "why": "identical file already used"})
                    dest.unlink(missing_ok=True)
                    continue
                measured = asset_probe.analyze(dest, format_id)
                if int(measured.get("width") or 0) < min_width or \
                        int(measured.get("height") or 0) <= int(measured.get("width") or 0):
                    rejected.append({"id": pid,
                                     "why": f"unusable crop {measured.get('width')}"
                                            f"x{measured.get('height')}"})
                    dest.unlink(missing_ok=True)
                    continue
                if float(measured.get("duration_s") or 0) < needed:
                    rejected.append({"id": pid,
                                     "why": f"too short {measured.get('duration_s')}s "
                                            f"for {needed:.1f}s"})
                    dest.unlink(missing_ok=True)
                    continue
                _nat, grade = template_renderer.grade_for(
                    format_id, measured.get("metrics") or {})
                flags = asset_scoring.soft_flags(e)
                score = asset_scoring.score_candidate(
                    e, intent=intent, lane=str(e.get("_lane") or "literal"),
                    measured=measured, prof=prof, grade=grade, flags=flags)
                scored.append({
                    "path": str(dest), "sha256": sha, "pin_id": pid,
                    "pin_url": e.get("pin_url"), "creator": e.get("creator"),
                    "title": (e.get("title") or "")[:120],
                    "query": e.get("_query"), "query_lane": e.get("_lane"),
                    "width": measured.get("width"), "height": measured.get("height"),
                    "duration_s": measured.get("duration_s"),
                    "hls": ".m3u8" in url, "metrics": measured.get("metrics"),
                    "motion": measured.get("motion"), "text": measured.get("text"),
                    "band_busy": measured.get("band_busy"), "grade": grade,
                    "stretch_pick": not grade.get("natural_fit"),
                    "flags": flags, "score": score,
                    "review_status": score["review_status"],
                    "fresh": True, "usage_clearance": "unknown",
                    "source": "pinterest", "is_promoted": bool(e.get("is_promoted")),
                    "domain": e.get("domain"),
                    "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
        rounds += 1
        if any(c["score"]["passes"] for c in scored):
            break
        expansion_used = rounds

    if not scored:
        from collections import Counter
        causes = Counter(re.sub(r"[\d.,]+", "N", r["why"]).split(":")[0]
                         for r in rejected)
        raise AssetError(f"no candidate survived the prefilters: {len(rejected)} "
                         f"rejected {causes.most_common(6)}; "
                         f"first={json.dumps(rejected[:3])[:220]}")
    passing = [c for c in scored if c["score"]["passes"]]
    pool = passing or scored
    best = max(pool, key=lambda c: (c["score"]["total"],
                                    0 if not c["stretch_pick"] else -1))
    for c in scored:
        if c["path"] != best["path"]:
            Path(c["path"]).unlink(missing_ok=True)

    key = asset_ledger.key_for(best["pin_id"], best["sha256"])
    asset_ledger.reserve(run_id, key=key, kind="visual", pin_id=best["pin_id"],
                         sha256=best["sha256"], query=best["query"],
                         lane=best["query_lane"], review_status=best["review_status"])
    best["reserve_key"] = key
    best["candidates_evaluated"] = len(scored)
    best["candidates"] = [{k: c[k] for k in
                           ("pin_id", "query", "query_lane", "score", "review_status",
                            "text", "band_busy", "stretch_pick", "width", "height")}
                          for c in scored]
    best["rejected"] = rejected[:40]
    best["query_log"] = query_log
    best["lane_expansions"] = expansion_used
    best["passes_minimum"] = bool(passing)
    if not passing:
        # Never lower the bar to force a pass: say so and let the caller decide.
        best["review_status"] = "needs_human_review"
    if verbose:
        print(f"[assets] {len(scored)} candidate(s) scored, best={best['score']['total']}"
              f"/100 lane={best['query_lane']} query={best['query']!r} "
              f"review={best['review_status']}", flush=True)
    return best


def pinterest_background(query: str, format_id: str, scratch: Path, *,
                         limit: int = 8, min_width: int = 720) -> dict:
    """Compatibility wrapper: a bare scene phrase still goes through the pipeline."""
    intent = asset_intent.fallback({"background_query": query, "topic_id": "adhoc"},
                                   format_id, reason="(compat call: bare query)")
    return acquire(intent, format_id, scratch, min_width=min_width,
                   max_downloads=min(limit, MAX_DOWNLOADS))


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


def trending_audio(format_id: str, scratch: Path, *, keyword: str | None = None,
                   intent: dict | None = None, limit: int = 5,
                   run_id: str = "adhoc") -> dict:
    """Intent-driven trending discovery, then a MEASURED feature/emotional score."""
    cfg = config.load_config()
    api = (cfg.get("music") or {}).get("trending_api")
    if not api:
        raise AssetError("music.trending_api is not configured")
    scratch.mkdir(parents=True, exist_ok=True)
    prof = template_renderer.PROFILES[format_id]
    ref = prof["music"]
    need_s = float(prof["duration_s"])

    keywords = ([keyword] if keyword else []) + asset_intent.audio_keywords(
        intent or {}, default=keyword or "overthinking")
    ledger = asset_ledger.sync_from_manifest()
    rows, used_keywords = [], []
    for kw in keywords[:3]:
        try:
            got = music.trending_rows(api, kw, limit=limit)
        except Exception as exc:  # noqa: BLE001
            used_keywords.append({"keyword": kw, "why": f"trending failed: {exc}"})
            continue
        used_keywords.append({"keyword": kw, "rows": len(got or [])})
        rows += [r for r in (got or []) if r not in rows]
        if len(rows) >= limit:
            break
    if not rows:
        raise AssetError(f"trending API returned no rows for {keywords!r}")

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
        sid = str(row.get("id") or "x")
        dest = scratch / f"track_{sid}.m4a"
        if not _download_to_m4a(str(row["play_link"]), dest):
            rejected.append({"id": sid, "why": "download/decode failed"})
            continue
        try:
            feats = audio_features.analyze(dest)
        except audio_features.AudioFeatureError as exc:
            rejected.append({"id": sid, "why": f"unmeasured: {exc}"})
            continue
        fresh = asset_ledger.is_fresh(sid, kind="audio", data=ledger)
        score = asset_scoring.score_audio(intent or {}, row, feats, prof, fresh=fresh)
        scored.append({"score": score, "features": feats, "row": row, "path": dest,
                       "distance": audio_features.distance(ref, feats), "fresh": fresh})

    if not scored:
        raise AssetError("no trending track could be measured; refusing to pick "
                         f"the first unused one: {rejected[:5]}")
    best = max(scored, key=lambda c: (c["score"]["total"], -c["distance"]))
    row = best["row"]
    track_id = str(row.get("id") or row.get("title") or "")
    return {
        "path": str(best["path"]), "keywords_tried": used_keywords,
        "keyword": used_keywords[0]["keyword"] if used_keywords else keyword,
        "stable_id": track_id, "title": row.get("title"), "artist": row.get("artist"),
        "tiktok_url": row.get("tiktok_url"), "video_count": row.get("video_count"),
        "is_niche": row.get("is_niche"), "is_copyrighted": row.get("is_copyrighted"),
        "is_commercial_use_allowed": row.get("is_commercial_use_allowed"),
        "features": best["features"], "distance": best["distance"],
        "reference": ref, "score": best["score"], "fresh": best["fresh"],
        "candidates": [{k: c["row"].get(k) for k in ("id", "title", "artist",
                                                     "video_count")} |
                       {"score": c["score"]["total"], "distance": c["distance"],
                        "fresh": c["fresh"]} for c in scored],
        "rejected": rejected, "source": "audi0-scraper:/v1/trending",
        # A positive TikTok flag is NOT Instagram clearance.
        "usage_clearance": "unknown", "rights": "preview_only",
        "sha256": run_manifest.sha256_file(best["path"]),
        "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }