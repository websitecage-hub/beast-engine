"""provenance.py — WHO OWNS EVERY IMAGE, RECORDED (requirement 5).

THE PROBLEM. A Pinterest pin is somebody else's photograph. The engine was
downloading one per reel and recording nothing but the pin id and a query string.
That is fine for a personal account and not fine for a business selling a product
off the back of the images: there is no record of where a picture came from, no
licence, and no way to answer a takedown or to replace one image with another.

WHAT THIS DOES. Every image that reaches a reel gets a provenance record:

    {source, source_url, licence, licence_url, author, pin_id, query,
     retrieved_at, checksum_sha256}

The record is written next to the artefact and appended to
data/provenance/images.jsonl, so the account always has an answer to "where did
this come from".

IMAGE_SOURCE is a ONE-LINE SWITCH in config.json:

    "image_source": "pinterest"   # the operator's instruction: use Pinterest only

The other modes exist and are one edit away, because the requirement asked for a
license-free default:
    "pexels" / "unsplash"  -> a licensed API (needs a key; not configured)
    "generated"            -> this stack's own image endpoint
    "pinterest"            -> the current, chosen mode

Pinterest mode is honest about what it is: NO LICENCE IS GRANTED for a pin. The
record therefore stores licence="unknown-pinterest-pin" and the source URL, so
the exposure is visible rather than assumed away. Pins are used to set the MOOD,
per the operator's instruction.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("data") / "provenance" / "images.jsonl"

ALLOWED_SOURCES = ("pinterest", "pexels", "unsplash", "generated", "fallback")

# What each source's licence actually is, stated rather than implied.
LICENCE = {
    "pinterest": {
        "licence": "unknown-pinterest-pin",
        "licence_url": "https://policy.pinterest.com/en/terms-of-service",
        "note": ("A Pinterest pin is user-uploaded content. No licence to reuse is "
                 "granted by the act of pinning. Recorded so the exposure is "
                 "visible; the operator has accepted this for mood-setting."),
    },
    "pexels": {
        "licence": "Pexels License (free to use, no attribution required)",
        "licence_url": "https://www.pexels.com/license/",
        "note": "Licensed for commercial use without attribution.",
    },
    "unsplash": {
        "licence": "Unsplash License (free to use)",
        "licence_url": "https://unsplash.com/license",
        "note": "Licensed for commercial use; attribution appreciated, not required.",
    },
    "generated": {
        "licence": "generated-by-this-stack",
        "licence_url": "",
        "note": "Produced by the pipeline's own image endpoint; no third-party rights.",
    },
    "fallback": {
        "licence": "bundled-with-repo",
        "licence_url": "",
        "note": "A local asset shipped in assets/fallback; owned by the operator.",
    },
}


def source_mode(cfg: dict | None = None) -> str:
    """The one-line switch, read from config. Defaults to pinterest."""
    if cfg is None:
        from . import config
        cfg = config.load_config()
    mode = str((cfg.get("image_source") or "pinterest")).strip().lower()
    return mode if mode in ALLOWED_SOURCES else "pinterest"


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(path, *, source: str, source_url: str = "", pin_id: str = "",
           query: str = "", author: str = "", extra: dict | None = None,
           cfg: dict | None = None) -> dict:
    """Build, write and return the provenance record for one image."""
    source = source if source in ALLOWED_SOURCES else "pinterest"
    meta = LICENCE[source]
    rec = {
        "file": str(path),
        "source": source,
        "source_url": source_url,
        "pin_id": pin_id,
        "author": author,
        "query": query,
        "licence": meta["licence"],
        "licence_url": meta["licence_url"],
        "licence_note": meta["note"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "checksum_sha256": "",
    }
    try:
        rec["checksum_sha256"] = sha256_of(path)
    except OSError:
        rec["checksum_sha256"] = "unreadable"
    if extra:
        rec.update(extra)

    p = Path(path)
    if p.exists():
        sidecar = p.with_suffix(p.suffix + ".provenance.json")
        try:
            sidecar.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        except OSError:
            pass

    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return rec


def history(limit: int = 0) -> list:
    """Every record ever written, newest last."""
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:] if limit else out


def summary() -> dict:
    """Counts by source and licence — the audit view."""
    out: dict = {}
    for r in history():
        key = f"{r.get('source')}|{r.get('licence')}"
        out[key] = out.get(key, 0) + 1
    return out