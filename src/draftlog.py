"""draftlog.py — append-only log of every v3 draft, accepted or rejected.

The owner reviews rejected drafts ("i want to see that reel too"), so the words
must survive the pipeline. One JSON object per line in outputs/v3_drafts.jsonl.
Never raises: logging must not break a run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config


def append(entry: dict) -> None:
    try:
        path = config.OUTPUTS / "v3_drafts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = dict(entry)
        entry.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
