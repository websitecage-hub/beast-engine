"""run_manifest.py — durable proof of what a preview run actually used.

Every rendered variant appends one entry. The entry is the artefact of record:
which template (and its hash), which payload, which background and track (stable
id + content hash), what the grader did, what the output measured, and whether
QA passed.

Freshness is derived from this file rather than tracked separately, which is the
fix for the factory's worst ledger bug: a run that was killed (or whose output
was thrown away) had still burned a background, a track and a copy of text it
never delivered. Deriving from *successful* entries means a crashed run consumes
nothing, and re-rendering a run releases its own previous reservation first.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "template_manifest.json"

# Only these states count as consumed.
CONSUMED = ("rendered", "delivered")


def new_run_id(format_id: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{stamp}-{format_id}-{uuid.uuid4().hex[:6]}"


def load() -> list:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        # A corrupt manifest must not be silently treated as empty: that would
        # reset the ledger and re-use assets. Move it aside and report loudly.
        broke = MANIFEST.with_suffix(f".corrupt.{int(time.time())}.json")
        MANIFEST.replace(broke)
        raise RuntimeError(f"manifest was corrupt; moved to {broke.name} for inspection")


def save(entries: list) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, MANIFEST)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def asset_ref(path, *, source: str, stable_id: str | None = None,
              creator: str | None = None, pin_url: str | None = None,
              usage_clearance: str = "unknown") -> dict:
    p = Path(path)
    return {
        "path": str(p),
        "sha256": sha256_file(p) if p.exists() else None,
        "bytes": p.stat().st_size if p.exists() else None,
        "source": source,
        "stable_id": stable_id,
        "creator": creator,
        "pin_url": pin_url,
        # Unknown stays unknown: a download is not a license, and pixels are not
        # a rights clearance. Only an explicit human decision can upgrade this.
        "usage_clearance": usage_clearance,
    }


def record(entry: dict) -> dict:
    """Insert or replace one run entry (idempotent by run_id)."""
    entries = [e for e in load() if e.get("run_id") != entry.get("run_id")]
    entries.append(entry)
    save(sorted(entries, key=lambda e: e.get("run_id", "")))
    return entry


def release(run_id: str) -> bool:
    """Drop a reservation (failed or discarded run). Returns True if it existed."""
    entries = load()
    keep = [e for e in entries if e.get("run_id") != run_id]
    if len(keep) == len(entries):
        return False
    save(keep)
    return True


def consumed(format_id: str | None = None) -> dict:
    """Assets/text already delivered, derived from successful entries only."""
    out = {"backgrounds": set(), "audio": set(), "topics": set(), "run_ids": set()}
    for e in load():
        if e.get("status") not in CONSUMED or not e.get("qa_ok"):
            continue
        if format_id and e.get("format_id") != format_id:
            continue
        bg = (e.get("background") or {}).get("sha256") or (e.get("background") or {}).get("stable_id")
        au = (e.get("audio") or {}).get("sha256") or (e.get("audio") or {}).get("stable_id")
        if bg:
            out["backgrounds"].add(bg)
        if au:
            out["audio"].add(au)
        if e.get("topic_id"):
            out["topics"].add(e["topic_id"])
        if e.get("run_id"):
            out["run_ids"].add(e["run_id"])
    return out


def make_entry(*, run_id: str, format_id: str, template_dir: str,
               template_sha256: str, payload: dict, background: dict,
               audio: dict, render: dict, qa: dict, output) -> dict:
    p = Path(output)
    return {
        "run_id": run_id,
        "format_id": format_id,
        "schema_version": payload.get("_schema_version", "1.0"),
        "template": {"dir": template_dir, "sha256": template_sha256},
        "topic_id": payload.get("topic_id"),
        "payload": payload,
        "background": background,
        "audio": audio,
        "grade": render.get("grade"),
        "render": {"seconds": (render.get("render") or {}).get("seconds"),
                   "quality": render.get("quality", "delivery")},
        "measured": render.get("measured"),
        "drift": render.get("drift"),
        "qa_ok": bool(qa.get("ok")),
        "qa": qa,
        "output": {"path": str(p), "sha256": sha256_file(p) if p.exists() else None,
                   "bytes": p.stat().st_size if p.exists() else None,
                   "duration_s": render.get("duration_s")},
        "status": "rendered" if qa.get("ok") else "needs_review",
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "delivery": {"channel": None, "ok": None, "at": None},
    }