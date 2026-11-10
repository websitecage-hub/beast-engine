"""visual_director.py — pick the background by emotional metaphor, not by meaning.

Human editors do not illustrate the sentence. If the copy says "lonely in a crowded
room", they cut to an empty ocean at dusk. The contrast is the effect. This module
turns the finished copy into ONE broad cinematic query (2-3 words), then searches
Pinterest with exactly that query and leaves the results untouched.

    python -m src.visual_director --run-id <id>        # copy from the manifest
    python -m src.visual_director --text "..."         # copy passed directly
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from .. import pinterest
from . import content_factory, run_manifest

OUT = Path("/var/tmp/visual-director")

PROMPT = """You are an elite Visual Director for viral short-form content. You understand that literal stock footage looks cheap, but overly poetic descriptions break search APIs.

Here is the generated script content:
"{text}"

Here is the exact API documentation for the Pinterest search tool you are using:
{docs}

YOUR TASK:
Suppose you need a background video from Pinterest based on this script.
1. Analyze the underlying emotional vibe and semantic meaning (what the visual should FEEL like).
2. Generate the exact search terms to find this on Pinterest.
3. CRITICAL RULE: Make sure the search terms are SEMANTICALLY RELATED and BROAD enough to get highly creative, high-quality, aesthetic results. Do NOT use long sentences. Do NOT use literal emotions like "sad" or "anxiety". Use 2-3 word cinematic aesthetic terms.

Output ONLY valid JSON:
{{"semantic_analysis": "1 sentence on the visual vibe.",
  "pinterest_search_query": "2-3 broad, aesthetic words"}}"""

# The real API surface, used when the docs file is not on this machine.
FALLBACK_API_DOCS = """Pinterest Media API v2.5 (no key, no auth)
Base URL: https://pinterest-api-inyg.onrender.com   (local: http://127.0.0.1:8000)

GET /search/videos?q=<query>&page_size=<1-50>
  - the video-pin search endpoint: this is the one your query goes to
  - q is a plain search string; short, broad queries return far more results
  - each result carries: id, title, description, creator, pin_url, best_video
  - anonymous results are byte-identical to a private-window pinterest.com search
GET /search?q=<query>            images + videos + gifs (mixed)
GET /autocomplete?q=<prefix>     spelling/query suggestions
GET /health                      liveness"""

DOCS_PATH = Path.home() / "pinterest-api" / "USAGE.md"
DOCS_MAX = 2600

BANNED = re.compile(r"\b(sad|anxiety|anxious|person|people|depressed|depression)\b", re.I)


def _parse(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    m = re.search(r"\{.*\}", str(raw), re.S)
    if not m:
        raise ValueError("no JSON in the director reply")
    return json.loads(m.group(0))


def api_docs() -> str:
    """The real Pinterest API guide, trimmed to the search surface it must target."""
    if DOCS_PATH.exists():
        try:
            text = DOCS_PATH.read_text(encoding="utf-8", errors="ignore")
            keep, out = False, []
            for line in text.splitlines():
                if line.startswith("## "):
                    keep = any(k in line.lower() for k in
                               ("quickstart", "search", "params", "concept", "endpoint"))
                if keep or line.startswith("# Pinterest Media API"):
                    out.append(line)
            trimmed = "\n".join(out).strip()
            if len(trimmed) > 400:
                return trimmed[:DOCS_MAX]
        except OSError:
            pass
    return FALLBACK_API_DOCS


def direct(text_content: str, *, chat=None) -> dict:
    """Return {semantic_analysis, pinterest_search_query}. Raises on an invalid answer."""
    chat = chat or content_factory._writer_chat
    prompt = PROMPT.format(text=text_content[:1500], docs=api_docs())
    raw, vendor = chat(prompt)
    out = _parse(raw)
    q = str(out.get("pinterest_search_query") or out.get("pinterest_query") or "")
    q = q.strip().strip('"').lower()
    words = q.split()
    if not 1 <= len(words) <= 3:
        raise ValueError(f"query must be 2-3 words, got {q!r}")
    if BANNED.search(q):
        raise ValueError(f"query uses a literal/clinical word: {q!r}")
    out["pinterest_search_query"] = q
    out["pinterest_query"] = q          # both names stay valid for callers
    out["director_vendor"] = vendor
    return out


def search_and_save(query: str, *, limit: int = 25, download: int = 2) -> dict:
    """Search Pinterest with the director's query verbatim. No re-ranking, no edits."""
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    results = pinterest.search_videos(query, page_size=limit)
    rows = [{
        "id": r.get("id"), "title": r.get("title"), "creator": r.get("creator"),
        "domain": r.get("domain"), "is_promoted": r.get("is_promoted"),
        "pin_url": r.get("pin_url"), "media": bool(pinterest.media_url(r)),
    } for r in results]
    saved = []
    for i, r in enumerate([x for x in results if pinterest.media_url(x)][:download]):
        dest = OUT / f"{query.replace(' ', '_')}_{i}.mp4"
        if pinterest.download(pinterest.media_url(r), dest, max_bytes=100 * 1024 * 1024):
            saved.append({"path": str(dest), "mb": round(dest.stat().st_size / 1e6, 1),
                          "title": r.get("title"), "id": r.get("id")})
    payload = {"query": query, "returned": len(results), "kept": len(rows),
               "saved": saved, "results": rows, "seconds": round(time.time() - t0, 1),
               "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = OUT / f"{query.replace(' ', '_')}.json"
    path.write_text(json.dumps(payload, indent=1))
    payload["json"] = str(path)
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--text", default=None)
    ap.add_argument("--fresh", action="store_true",
                    help="generate fresh copy first instead of reading the manifest")
    ap.add_argument("--download", type=int, default=2)
    args = ap.parse_args(argv)

    if args.text:
        text = args.text
    elif args.fresh:
        gen = content_factory.generate("caption_list_title_v1", None, verbose=False)
        text = gen["payload"].get("caption") or ""
        print(f"[director] fresh copy: {text.splitlines()[0][:70]!r}")
    else:
        entries = run_manifest.load()
        if args.run_id:
            entries = [e for e in entries if e.get("run_id") == args.run_id]
        entry = entries[-1]
        text = (entry.get("payload") or {}).get("caption") or ""
        print(f"[director] copy from {entry['run_id']}")

    verdict = direct(text)
    print(f"[director] {verdict['emotional_analysis']}")
    print(f"[director] QUERY: {verdict['pinterest_query']}")
    found = search_and_save(verdict["pinterest_query"], download=args.download)
    print(f"[director] pinterest returned {found['returned']} videos; "
          f"saved {len(found['saved'])} to {OUT}")
    for s in found["saved"]:
        print(f"           {s['path']} ({s['mb']} MB) {s['title']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())