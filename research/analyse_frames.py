"""Read the outliers' frames with Gemini, and cache every answer in the repo.

Downloads go to /var/tmp (ephemeral by design - /home is 4.8 GB). Only the JSON
measurements are written to research/frames.json, so a restart costs nothing and
Gemini's 20/day free tier is never spent twice on the same reel.

Run:
    set -a; . ~/.beast-secrets/vision.env; set +a
    python3 research/analyse_frames.py            # analyses up to 8 new reels
    python3 research/analyse_frames.py --limit 3  # stay inside today's quota
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path

import requests
from PIL import Image

LAB = Path(os.environ.get("BEAST_RESEARCH_DIR", "/var/tmp/beast-lab"))
REFS = LAB / "refs"
OUT = Path(__file__).resolve().parent / "frames.json"
GEM = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
MAX_BYTES = 100 * 1024 * 1024
FFMPEG = os.environ.get("FFMPEG", "/var/tmp/bin/ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "/var/tmp/bin/ffprobe")

PROMPT = """You are analysing a viral Instagram Reel frame for a template rebuild.
Return ONLY JSON:
{"on_screen_text": ["line 1"],
 "has_numbered_promise": true/false, "number": null,
 "text_colour_hex": "#RRGGBB", "text_is_lowercase": true/false,
 "font_weight": "regular|medium|bold|black", "outline": "none|thin|thick",
 "text_position": "upper|centre|lower", "lines_on_screen": 0,
 "background": "what is actually visible, one sentence",
 "background_style": "lifestyle|aesthetic|night-window|street|interior|other",
 "busy_behind_text": true/false, "looks_like_ad_or_stock": true/false,
 "format_guess": "numbered_list_title|lowercase_moment|other"}
Judge only what is visible. If there is no text, return an empty list and nulls."""


def pick(rows: list) -> list:
    by = {}
    for r in rows:
        by.setdefault(r["handle"], []).append(r)
    out = []
    for h, rs in by.items():
        with_views = [r for r in rs if r.get("views", 0) > 0 and r.get("video_url")]
        if with_views:
            out.append(max(with_views, key=lambda r: r["views"]))
    out.sort(key=lambda r: r["views"], reverse=True)
    return out


def download(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=120,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                return False
            total = 0
            with dest.open("wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        fh.close(); dest.unlink(missing_ok=True); return False
                    fh.write(chunk)
        return dest.exists() and dest.stat().st_size > 50_000
    except Exception:  # noqa: BLE001
        return False


def frame(video: Path, t: float, out: Path) -> bool:
    if out.exists() and out.stat().st_size > 1000:
        return True
    r = subprocess.run([FFMPEG, "-nostdin", "-y", "-v", "error", "-ss", f"{t:.2f}",
                        "-i", str(video), "-frames:v", "1", "-vf", "scale=720:-2",
                        str(out)], capture_output=True, timeout=90)
    return r.returncode == 0 and out.exists()


def gemini(img: Path) -> dict:
    b64 = base64.b64encode(img.read_bytes()).decode()
    body = {"contents": [{"parts": [{"text": PROMPT},
                                    {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 900,
                                 "responseMimeType": "application/json",
                                 # 2.5-flash otherwise spends the whole budget thinking
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                      params={"key": GEM}, json=body, timeout=120)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:160]}"}
    try:
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else {"error": "no JSON in reply"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()
    if not GEM:
        raise SystemExit("GEMINI_API_KEY missing (source ~/.beast-secrets/vision.env)")

    src = Path(__file__).resolve().parent / "reels.json"
    rows = json.loads(src.read_text())["rows"]
    cache = json.loads(OUT.read_text()) if OUT.exists() else {}
    REFS.mkdir(parents=True, exist_ok=True)

    todo = [r for r in pick(rows) if r["shortCode"] not in cache][:args.limit]
    for r in todo:
        name = f"{r['handle']}_{r['shortCode']}"
        vid = REFS / f"{name}.mp4"
        if not vid.exists() and not download(r["video_url"], vid):
            cache[r["shortCode"]] = {"handle": r["handle"], "error": "download failed"}
            continue
        probe = json.loads(subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                                           "-show_format", str(vid)],
                                          capture_output=True, text=True).stdout or "{}")
        secs = float(probe.get("format", {}).get("duration") or 0)
        rec = {"handle": r["handle"], "views": r["views"], "outlier": r.get("outlier"),
               "seconds": round(secs, 1), "caption": r.get("caption")}
        f0, fm = REFS / f"{name}_f0.jpg", REFS / f"{name}_mid.jpg"
        if frame(vid, 0.5, f0):
            rec["frame0"] = gemini(f0)
        if frame(vid, max(0.6, secs / 2), fm):
            rec["mid"] = gemini(fm)
        cache[r["shortCode"]] = rec
        t = rec.get("frame0") or {}
        print(f"[frames] {name}: {secs:.1f}s text={str(t.get('on_screen_text'))[:60]!r} "
              f"colour={t.get('text_colour_hex')}", flush=True)
        OUT.write_text(json.dumps(cache, indent=1))   # persist after every reel

    OUT.write_text(json.dumps(cache, indent=1))
    print(f"[frames] {len(cache)} reels cached -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())