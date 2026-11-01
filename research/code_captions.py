"""Code every scraped caption for the writing mechanics, in batches.

This is the step the pilot skipped: it looks at all reels, not just the ones whose
frames were read, and records the structure the copy uses (numbered promise,
identity anchor, private worry, turn/payoff) so the writer can be tuned to what
actually travels rather than to one good example.

Batches 10 captions per Gemini call to stay inside the 20/day free tier; results
are cached per shortCode in research/captions.json.

Run:
    set -a; . ~/.beast-secrets/vision.env; set +a
    python3 research/code_captions.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent / "captions.json"
SRC = Path(__file__).resolve().parent / "reels.json"
GEM = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
BATCH = 10

PROMPT = """You are coding Instagram Reel captions for writing mechanics. For EACH
item return one object; return ONLY a JSON array, same order as the input.

{"shortCode": "...",
 "format": "numbered_list_title|lowercase_moment|other",
 "numbered_promise": {"present": true/false, "number": null},
 "identity_anchor": "the specific person/experience named, or empty",
 "private_worry": "the fear or interpretation implied, or empty",
 "turn_type": "contradiction|delayed_consequence|reframe|joke|none",
 "words": 0,
 "on_screen_likely": "the sentence that would sit on screen, or empty"}

Judge the caption text only. Do not invent details that are not there.

ITEMS:
{items}"""


def main() -> int:
    if not GEM:
        raise SystemExit("GEMINI_API_KEY missing")
    rows = json.loads(SRC.read_text())["rows"]
    cache = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [r for r in rows if r["shortCode"] not in cache and r.get("caption")]
    print(f"[code] {len(todo)} captions to code, {len(cache)} cached")

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        items = [{"shortCode": r["shortCode"], "caption": r["caption"]} for r in batch]
        body = {"contents": [{"parts": [{"text": PROMPT.format(
                    items=json.dumps(items, ensure_ascii=False))}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 2000,
                                     "responseMimeType": "application/json",
                                     "thinkingConfig": {"thinkingBudget": 0}}}
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                          params={"key": GEM}, json=body, timeout=120)
        if r.status_code != 200:
            print(f"[code] batch {i//BATCH}: HTTP {r.status_code} {r.text[:120]}")
            break
        try:
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(txt)
        except Exception as exc:  # noqa: BLE001
            print(f"[code] batch {i//BATCH}: parse failed {exc}")
            continue
        for obj in data if isinstance(data, list) else []:
            if obj.get("shortCode"):
                cache[obj["shortCode"]] = obj
        OUT.write_text(json.dumps(cache, indent=1))
        print(f"[code] batch {i//BATCH} -> {len(cache)} coded")

    OUT.write_text(json.dumps(cache, indent=1))
    print(f"[code] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())