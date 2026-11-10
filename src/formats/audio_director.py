"""audio_director.py — pick the sound by sonic metaphor, not by literal emotion.

The same principle as the visual director, applied to music: human editors do not
score the sentence, they score the tension. "I am panicking" does not become
"sad"; it becomes "phonk", "dark", "glitch". The contrast is what makes the post
feel alive. The TikTok trending API is niche-priority, so a broad sonic tag
outperforms a descriptive phrase.

    python -m src.formats.audio_director --run-id <id>      # copy from the manifest
    python -m src.formats.audio_director --text "..."       # copy passed directly
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests

from .. import config, music
from . import content_factory, run_manifest

OUT = Path("/var/tmp/audio-director")
PUBLIC = Path("/home/testflowthrowout/beast-engine/outputs/audio-director")

PROMPT = """You are an elite Sound Designer for viral short-form content. You understand that literal music ruins the edit, but random sounds destroy the pacing.

Here is the generated script content:
"{text}"

Here is the exact API documentation for the TikTok Audio search tool you are using:
{docs}

YOUR TASK:
Suppose you need a trending audio track from TikTok based on this script.
1. Analyze the sonic pacing, tension, and underlying emotional vibe of the text.
2. Generate the exact search term to find this on the TikTok API.
3. CRITICAL RULE: Make sure the search term is SEMANTICALLY RELATED and BROAD enough to get highly creative, high-energy, or highly emotional trending sounds. It MUST be exactly ONE WORD. Do NOT use literal emotions like "sad" or "anxiety". Use broad sonic genres or moods.

Output ONLY valid JSON:
{{"sonic_analysis": "1 sentence on the audio vibe.",
  "tiktok_search_query": "1 broad word only"}}"""

# The real API surface, used when the docs file is not on this machine.
FALLBACK_API_DOCS = """Trending Audio Scraper API (no auth)
Base URL: https://audi0-scraper.onrender.com

GET /v1/trending?keyword=<keyword>&limit=<n>
  - returns ranked, real trending audio for that keyword
  - NICHE PRIORITY: broad sonic tags (a genre or mood) return far better rows
  - a fresh keyword may answer 202 and must be polled until the job is ready
  - each row carries: title, artist, video_count (how widely the sound is used),
    duration, and a signed play_link you can download
GET /v1/trending/<niche>          same, niche addressed in the path
POST /v1/download                  save one track's audio by play_link
GET /health                        liveness

trend_score is 0-100 (cross-source corroboration 40, niche relevance 25, momentum 20,
rank quality 15); confidence (0-1) says how much evidence backed the row."""

DOCS_PATH = Path.home() / "trending-audio-scraper" / "USAGE.md"
DOCS_MAX = 2200

BANNED = re.compile(r"\b(sad|depress\w*|anxiet\w*|anxious|lonely|cry\w*)\b", re.I)


def _parse(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    m = re.search(r"\{.*\}", str(raw), re.S)
    if not m:
        raise ValueError("no JSON in the director reply")
    return json.loads(m.group(0))


def api_docs() -> str:
    """The real trending-audio API guide, trimmed to the search surface it targets."""
    if DOCS_PATH.exists():
        try:
            text = DOCS_PATH.read_text(encoding="utf-8", errors="ignore")
            keep, out = False, []
            for line in text.splitlines():
                if line.startswith("## "):
                    keep = any(k in line.lower() for k in
                               ("trending", "http api", "endpoint", "quick"))
                if keep or line.startswith("# Trending Audio"):
                    out.append(line)
            trimmed = "\n".join(out).strip()
            if len(trimmed) > 400:
                return trimmed[:DOCS_MAX]
        except OSError:
            pass
    return FALLBACK_API_DOCS


def direct(text_content: str, *, chat=None) -> dict:
    """Return {sonic_analysis, tiktok_search_query}. Raises on an invalid answer."""
    chat = chat or content_factory._writer_chat
    raw, vendor = chat(PROMPT.format(text=text_content[:1500], docs=api_docs()))
    out = _parse(raw)
    q = str(out.get("tiktok_search_query") or out.get("tiktok_query") or "")
    q = q.strip().strip('"').lower()
    words = q.split()
    if len(words) != 1:
        raise ValueError(f"keyword must be exactly one word, got {q!r}")
    if BANNED.search(q):
        raise ValueError(f"keyword is a literal emotion: {q!r}")
    out["tiktok_search_query"] = q
    out["tiktok_query"] = q             # both names stay valid for callers
    out["director_vendor"] = vendor
    return out


def _download(play_link: str, dest: Path) -> bool:
    try:
        with requests.get(play_link, stream=True, timeout=120,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                return False
            total = 0
            with dest.open("wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    total += len(chunk)
                    if total > 40 * 1024 * 1024:
                        fh.close(); dest.unlink(missing_ok=True); return False
                    fh.write(chunk)
        return dest.exists() and dest.stat().st_size > 20_000
    except Exception:  # noqa: BLE001
        return False


def search_and_save(keyword: str, *, limit: int = 10, download: int = 2) -> dict:
    """Query the trending API with the director's word. No re-ranking, no edits."""
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = config.load_config()
    api = (cfg.get("music") or {}).get("trending_api")
    if not api:
        raise SystemExit("music.trending_api is not configured")
    t0 = time.time()
    rows = music.trending_rows(api, keyword, limit=limit)
    clean = [{
        "title": r.get("title"), "artist": r.get("artist"),
        "video_count": r.get("video_count"), "is_niche": r.get("is_niche"),
        "is_copyrighted": r.get("is_copyrighted"),
        "commercial": r.get("is_commercial_use_allowed"),
        "duration_s": r.get("duration_seconds"), "has_play_link": bool(r.get("play_link")),
    } for r in rows]
    saved = []
    for i, r in enumerate(rows[:download]):
        if not r.get("play_link"):
            continue
        dest = OUT / f"{keyword}_{i}.m4a"
        if _download(str(r["play_link"]), dest):
            saved.append({"path": str(dest), "mb": round(dest.stat().st_size / 1e6, 2),
                          "title": r.get("title"), "artist": r.get("artist"),
                          "video_count": r.get("video_count")})
    payload = {"keyword": keyword, "returned": len(rows), "rows": clean, "saved": saved,
               "source": "audi0-scraper:/v1/trending", "seconds": round(time.time() - t0, 1),
               "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = OUT / f"{keyword}.json"
    path.write_text(json.dumps(payload, indent=1))
    payload["json"] = str(path)
    return payload


def publish_copies() -> list:
    """Copy the results where the owner can actually open them (/home)."""
    PUBLIC.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in sorted(OUT.glob("*")):
        if f.is_file():
            dest = PUBLIC / f.name
            dest.write_bytes(f.read_bytes())
            copied.append(str(dest))
    return copied


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--text", default=None)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--download", type=int, default=2)
    args = ap.parse_args(argv)

    if args.text:
        text = args.text
    elif args.fresh:
        gen = content_factory.generate("caption_list_title_v1", None, verbose=False)
        text = gen["payload"].get("caption") or ""
        print(f"[audio-director] fresh copy: {text.splitlines()[0][:70]!r}")
    else:
        entries = run_manifest.load()
        if args.run_id:
            entries = [e for e in entries if e.get("run_id") == args.run_id]
        entry = entries[-1]
        text = (entry.get("payload") or {}).get("caption") or ""
        print(f"[audio-director] copy from {entry['run_id']}")

    verdict = direct(text)
    print(f"[audio-director] {verdict['sonic_analysis']}")
    print(f"[audio-director] KEYWORD: {verdict['tiktok_query']}")
    found = search_and_save(verdict["tiktok_query"], download=args.download)
    print(f"[audio-director] trending returned {found['returned']} sounds; "
          f"saved {len(found['saved'])}")
    for s in found["saved"]:
        print(f"                 {s['path']} ({s['mb']} MB) {s['title']!r} — "
              f"{s['artist']} · {s['video_count']} videos")
    for c in publish_copies():
        print(f"                 copied -> {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())