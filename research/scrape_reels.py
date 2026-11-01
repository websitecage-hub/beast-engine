"""Scrape reels from the niche creators with Apify and rank true outliers.

The hashtag scraper returns images/carousels only (verified), so discovery goes
through the creators who actually publish this format. Outlier = views / that
creator's median views, which is the only fair cross-creator comparison.

Writes research/reels.json (committed; small). Run:
    set -a; . ~/.beast-secrets/apify.env; set +a
    python3 research/scrape_reels.py
"""
from __future__ import annotations

import json
import os
import statistics as st
import time
from pathlib import Path

import requests

TOKEN = os.environ.get("APIFY_TOKEN", "")
BASE = "https://api.apify.com/v2"
OUT = Path(__file__).resolve().parent / "reels.json"
LIMIT = 12

HANDLES = ["lifeliftman", "tamannah_a_wish", "rock.s.w", "grishu01", "dalmatean",
           "colleen.at.home", "jeneral.reels", "ratulxyx", "darewithdrea"]


def views(p: dict) -> int:
    for k in ("videoViewCount", "videoPlayCount", "playsCount", "viewCount"):
        v = p.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def run(handle: str) -> list:
    url = f"{BASE}/acts/apify~instagram-reel-scraper/run-sync-get-dataset-items"
    body = {"username": [handle], "resultsLimit": LIMIT,
            "includeTranscript": False, "includeDownloadedVideo": False}
    r = requests.post(url, params={"token": TOKEN, "timeout": 240}, json=body, timeout=300)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected payload: {str(data)[:160]}")
    return data


def main() -> int:
    if not TOKEN:
        raise SystemExit("APIFY_TOKEN missing (source ~/.beast-secrets/apify.env)")
    rows, errors = [], []
    for h in HANDLES:
        t0 = time.time()
        try:
            posts = run(h)
        except Exception as exc:  # noqa: BLE001
            errors.append({"handle": h, "error": str(exc)[:160]})
            continue
        for p in posts:
            rows.append({
                "handle": h, "shortCode": p.get("shortCode"), "url": p.get("url"),
                "views": views(p), "likes": p.get("likesCount"),
                "comments": p.get("commentsCount"),
                "caption": (p.get("caption") or "")[:300],
                "video_url": p.get("videoUrl"), "timestamp": p.get("timestamp"),
                "music": (p.get("musicInfo") or {}).get("song_name")
                if isinstance(p.get("musicInfo"), dict) else None,
                "duration": p.get("videoDuration"),
            })
        print(f"[scrape] {h}: {len(posts)} reels ({time.time()-t0:.0f}s)", flush=True)

    medians = {}
    for h in {r["handle"] for r in rows}:
        vs = [r["views"] for r in rows if r["handle"] == h and r["views"] > 0]
        medians[h] = int(st.median(vs)) if vs else 0
    for r in rows:
        med = medians.get(r["handle"]) or 0
        r["median_views"] = med
        r["outlier"] = round(r["views"] / med, 1) if med else None

    rows.sort(key=lambda r: r["views"], reverse=True)
    OUT.write_text(json.dumps({"handles": HANDLES, "rows": rows, "errors": errors,
                               "medians": medians}, indent=1))
    print(f"[scrape] {len(rows)} reels -> {OUT} (errors: {len(errors)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())