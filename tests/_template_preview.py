"""Render one preview of each cloned format through the engine renderer.

Fixture media = the media bundled inside each template, so this proves the
renderer, injection, grading, QA and manifest end-to-end without touching the
network. Outputs land in scratch first; the caller copies the final artefacts.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/home/testflowthrowout/beast-engine")

from src.formats import run_manifest, template_payloads, template_renderer  # noqa: E402

OUT = Path(os.environ.get("BEAST_SCRATCH", "/tmp/beast-preview")) / "out"
OUT.mkdir(parents=True, exist_ok=True)

A_ITEMS = [
    "replay the exact sentence you said",
    "decide they secretly hate you",
    "rehearse a better version in the shower",
    "check if they posted since",
    "draft an apology you never send",
    "ask a friend if it was weird",
    "promise to be quieter next time",
    "replay it again at 2am",
]

payloads = {
    "caption_list_title_v1": {
        "topic_id": "post-conversation-replay",
        "headline": "8 things you do after a conversation when you're worried you sounded weird",
        "titleLines": ["8 things you do after", "a conversation when",
                       "you're worried you", "sounded weird"],
        "list_items": A_ITEMS,
        "caption": "8 things you do after a conversation when you're worried you "
                   "sounded weird:\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(A_ITEMS)),
        "background_query": "person walking forest trail morning light",
        "audio": {"source": "audi0-scraper", "rights": "preview_only",
                  "title": "Simplicity", "stable_id": "6777323236687349761"},
        "provenance": {"source": "pinterest", "pin_id": "pinvid_5cced687408a5a9d",
                       "creator": "emmawatsonbarbe", "usage_clearance": "unknown"},
        "_schema_version": template_payloads.SCHEMA_VERSION,
    },
    "social_moment_loop_v1": {
        "topic_id": "hey-then-silence",
        "hook_lines": ["you sent \u201chey.\u201d", "two hours ago."],
        "payoff_lines": ["they're online.", "you're not."],
        "loop_hypothesis": "The last line ('you're not.') re-reads as the first "
                           "('you sent hey') on a loop: waiting again.",
        "caption": "the two hours after \u201chey.\u201d",
        "background_query": "quiet night window rain bokeh pov",
        "audio": {"source": "audi0-scraper", "rights": "preview_only",
                  "title": "hate myself", "stable_id": "6696371042123778818"},
        "provenance": {"source": "pinterest", "pin_id": "pinvid_44d38b7087ea5936",
                       "creator": "unknown", "usage_clearance": "unknown"},
        "_schema_version": template_payloads.SCHEMA_VERSION,
    },
}

only = sys.argv[1] if len(sys.argv) > 1 else None
results = {}
for fmt, payload in payloads.items():
    if only and only not in fmt:
        continue
    out = OUT / f"{fmt}-preview.mp4"
    print(f"\n=== {fmt} ===")
    rec = template_renderer.render_preview(fmt, payload, out, quality="delivery")
    entry = run_manifest.make_entry(
        run_id=rec["run_id"], format_id=fmt,
        template_dir=f"templates/{fmt}", template_sha256=rec["template_sha256"],
        payload=payload, background=run_manifest.asset_ref(
            Path("/home/testflowthrowout/beast-engine/templates") / fmt / "assets/media/bg.mp4",
            source="template-fixture", stable_id=payload["provenance"]["pin_id"],
            usage_clearance="unknown"),
        audio=run_manifest.asset_ref(
            Path("/home/testflowthrowout/beast-engine/templates") / fmt / "assets/audio/bgm.m4a",
            source="template-fixture", stable_id=payload["audio"]["stable_id"],
            usage_clearance="unknown"),
        render=rec, qa=template_renderer.TemplateRenderer(fmt).qa(rec, payload), output=out)
    run_manifest.record(entry)
    results[fmt] = {k: rec[k] for k in
                    ("run_id", "output", "sha256", "bytes", "duration_s", "measured",
                     "drift", "gate_ok", "grade")}
    results[fmt]["qa_ok"] = entry["qa_ok"]
    print(json.dumps(results[fmt], indent=1))

print("\n=== summary ===")
print(json.dumps(results, indent=1))