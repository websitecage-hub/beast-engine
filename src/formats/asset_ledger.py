"""asset_ledger.py — crash-safe freshness + reservation state (§9).

Rules encoded here
* found != used: searching or downloading never consumes an asset.
* A render attempt RESERVES an asset; the reservation commits only after the
  artifact is valid, and is released when a run fails or is killed.
* A correction re-render reuses its own reservation, so it cannot burn a second
  clip or a duplicate track.
* No-repeat window applies separately to visual assets and audio tracks.
* State is synced from the delivered manifest at the start of every run, so a
  lost workspace cannot silently recycle Wednesday's clip on Friday.
* Every write is atomic (temp file + os.replace): a crash mid-write leaves the
  previous ledger intact rather than a truncated one.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

VERSION = 1
NO_REPEAT_DAYS = 30

ROOT = Path(__file__).resolve().parents[2]


def ledger_path() -> Path:
    return Path(os.environ.get("BEAST_LEDGER") or (ROOT / "data" / "asset_ledger.json"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _empty() -> dict:
    return {"version": VERSION, "visual": {}, "audio": {}, "reservations": {},
            "updated_at": _now()}


def load() -> dict:
    p = ledger_path()
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        # A corrupt ledger must not licence reuse: start empty but say so.
        return {**_empty(), "recovered_from_corrupt": True}
    for key, default in (("visual", {}), ("audio", {}), ("reservations", {})):
        data.setdefault(key, default)
    return data


def save(data: dict) -> None:
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = VERSION
    data["updated_at"] = _now()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, p)


def key_for(pin_id: str | None, sha256: str | None) -> str:
    """Stable identity: the pin when we have one, else the file hash."""
    if pin_id:
        return f"pin:{pin_id}"
    if sha256:
        return f"sha:{sha256[:24]}"
    return "unknown"


def _age_days(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        t = time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return (time.time() - time.mktime(t) + time.timezone) / 86400.0


def is_fresh(key: str, *, kind: str = "visual", days: int = NO_REPEAT_DAYS,
             data: dict | None = None) -> bool:
    """False when the asset was used inside the no-repeat window."""
    data = data if data is not None else load()
    rec = (data.get(kind) or {}).get(key) or {}
    used = rec.get("last_used_at")
    age = _age_days(used)
    if age is None:
        return True
    return age >= days


def sync_from_manifest(data: dict | None = None) -> dict:
    """Mark anything the delivered manifest already consumed as used.

    Runs first in every acquisition: on GitHub Actions the workspace may be a
    fresh clone, so the manifest is the durable record of what has been used.
    """
    data = data if data is not None else load()
    try:
        from . import run_manifest
        entries = run_manifest.load()
    except Exception:  # noqa: BLE001
        return data
    for e in entries:
        if e.get("status") not in getattr(run_manifest, "CONSUMED", ()):
            continue
        when = (e.get("delivery") or {}).get("at") or e.get("created_at") or _now()
        bg = (e.get("background") or {})
        au = (e.get("audio") or {})
        vkey = key_for(bg.get("stable_id"), bg.get("sha256"))
        akey = str(au.get("stable_id") or au.get("sha256") or "")
        if vkey != "unknown":
            rec = data["visual"].setdefault(vkey, {})
            rec.update({"last_used_at": when, "used_by": e.get("run_id"),
                        "pin_id": bg.get("stable_id"), "sha256": bg.get("sha256"),
                        "status": "delivered"})
        if akey:
            rec = data["audio"].setdefault(akey, {})
            rec.update({"last_used_at": when, "used_by": e.get("run_id"),
                        "status": "delivered"})
    return data


def seen(data: dict, key: str, *, kind: str = "visual") -> dict:
    rec = (data.get(kind) or {}).get(key) or {}
    rec.setdefault("first_seen_at", _now())
    rec.setdefault("last_used_at", None)
    rec.setdefault("used_by", None)
    rec.setdefault("review_status", "pending")
    (data[kind])[key] = rec
    return rec


def reserve(run_id: str, *, key: str, kind: str = "visual", **meta) -> dict:
    """Hold an asset for this run. Nothing is consumed until commit()."""
    data = sync_from_manifest()
    rec = seen(data, key, kind=kind)
    rec.setdefault("first_seen_at", _now())
    rec["status"] = "reserved"
    rec.update({k: v for k, v in meta.items() if v is not None})
    data["reservations"].setdefault(run_id, {})[kind] = key
    save(data)
    return rec


def release(run_id: str, *, kind: str = "visual") -> None:
    """A failed/killed run must not burn an unused asset."""
    data = load()
    keys = data["reservations"].get(run_id) or {}
    key = keys.pop(kind, None)
    if key and not (data[kind].get(key) or {}).get("last_used_at"):
        rec = data[kind].get(key) or {}
        rec["status"] = "released"
        data[kind][key] = rec
    if not keys:
        data["reservations"].pop(run_id, None)
    save(data)


def commit(run_id: str, *, key: str, kind: str = "visual", **meta) -> None:
    """Artifact is valid and delivered: now (and only now) the asset is used."""
    data = load()
    rec = data[kind].setdefault(key, {})
    rec.update({"last_used_at": _now(), "used_by": run_id, "status": "delivered",
                **{k: v for k, v in meta.items() if v is not None}})
    (data["reservations"].get(run_id) or {}).pop(kind, None)
    if not data["reservations"].get(run_id):
        data["reservations"].pop(run_id, None)
    save(data)


def recent_queries(n: int = 6, *, data: dict | None = None) -> list[str]:
    """Recent query lanes, so the planner can avoid repeating itself."""
    data = data if data is not None else load()
    out = []
    for rec in (data.get("visual") or {}).values():
        q = rec.get("query")
        if q and q not in out:
            out.append(q)
    return out[-n:]