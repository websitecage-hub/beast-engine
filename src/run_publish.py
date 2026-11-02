"""run_publish.py — publish the newest rendered TWO-FORMAT reel to Instagram.

The create pipeline (`run_create`) is the old v3 renderer: it builds a different
video and knows nothing about caption_list_title_v1 / social_moment_loop_v1. This
module is the bridge for the new system: it takes an artifact that `run_preview`
already rendered and QA'd, and publishes exactly that file through the repo's
existing Instagram path (`publish.publish_reel`).

It never renders, never rewrites copy, and refuses anything whose manifest status
is not `rendered` or whose file is missing.

    python -m src.run_publish --format auto            # newest rendered, any format
    python -m src.run_publish --format B --test        # container only, no publish
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import config, publish
from .formats import run_manifest

ALIAS = {"A": "caption_list_title_v1", "B": "social_moment_loop_v1"}


def _fmt(value: str) -> str | None:
    if value in ("auto", "", None):
        return None
    return ALIAS.get(value, value)


def pick_entry(format_id: str | None, run_id: str | None = None):
    """Newest rendered entry with an existing artifact, or a clear failure."""
    for e in reversed(run_manifest.load()):
        if run_id and e.get("run_id") != run_id:
            continue
        if format_id and e.get("format_id") != format_id:
            continue
        if e.get("status") != "rendered":
            continue
        path = Path((e.get("output") or {}).get("path") or "")
        if path.is_file() and path.stat().st_size > 100_000:
            return e, path
    raise SystemExit(f"no rendered two-format artifact found "
                     f"(format={format_id or 'any'}, run_id={run_id or 'any'})")


def content_for(payload: dict) -> dict:
    """Build the publish content dict from the approved payload, nothing invented."""
    caption = str(payload.get("caption") or "").strip()
    closing = str(payload.get("closing") or "").strip()
    if closing and closing not in caption:
        caption = f"{caption}\n\n{closing}" if caption else closing
    lines = (payload.get("titleLines") or
             (payload.get("hookLines") or []) + (payload.get("payoffLines") or []))
    return {
        "caption": caption,
        "hook": " ".join(str(x) for x in lines[:1]),
        "topic": payload.get("topic_id") or "",
        "scene": payload.get("background_query") or "",
        "hashtags": payload.get("hashtags") or [],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="publish a rendered two-format reel")
    ap.add_argument("--format", default="auto", help="A, B, auto or a format id")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--test", action="store_true",
                    help="create the container but do not call media_publish")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    fmt = _fmt(args.format)
    entry, video = pick_entry(fmt, args.run_id)
    cfg = config.load_config()
    content = content_for(entry.get("payload") or {})

    print(f"[publish-new] run={entry['run_id']} format={entry['format_id']} "
          f"file={video.name} ({video.stat().st_size // 1024} KB)")

    result = publish.publish_reel(video, content, cfg, test=args.test,
                                  dry_run=args.dry_run)

    entries = run_manifest.load()
    for e in entries:
        if e.get("run_id") == entry["run_id"]:
            e["publish"] = {**result,
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "channel": "instagram", "test": bool(args.test)}
            e["status"] = "published" if result.get("media_id") else e.get("status")
    run_manifest.save(entries)

    report = {"ok": bool(result.get("media_id")) or args.test or args.dry_run,
              "run_id": entry["run_id"], "format_id": entry["format_id"],
              "video": str(video), "result": result}
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())