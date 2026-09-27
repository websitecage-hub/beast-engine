"""harvest.py — pull insights for 24h-7d old posts, score them, prune memory.

v5.0 metric hierarchy (Part 4.1 / 7.3) — sends-per-reach is the king signal,
then watch-through, then saves, then comments; likes are near-worthless.

score = (6*sends + 3*saved + 2*watch + 1*comments + 0.25*likes) / max(reach, 1)
        where watch = completion/rewatch proxy when available, else plays/reach.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config, publish

MIN_AGE_H = 24
MAX_AGE_D = 7
REPULL_H = 6
PRUNE_PINS_D = 30
PRUNE_TRACK_DAYS = 60      # audio fingerprints outlive the URLs (repeat guard)
PRUNE_HOOKS_D = 90
TARGET_SENDS_PER_REACH = 0.02        # Part 4.1: >2% triggers distribution


def _parse(ts: str):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def sends_per_reach(m: dict) -> float:
    reach = max(float(m.get("reach") or 0), 1.0)
    return float(m.get("shares") or 0) / reach


def saves_per_reach(m: dict) -> float:
    reach = max(float(m.get("reach") or 0), 1.0)
    return float(m.get("saved") or 0) / reach


def compute_score(m: dict) -> float:
    """Weighted per-reach engagement, sends-first (Part 4.1 hierarchy)."""
    reach = max(float(m.get("reach") or 0), 1.0)
    sends = float(m.get("shares") or 0)
    saved = float(m.get("saved") or 0)
    comments = float(m.get("comments") or 0)
    likes = float(m.get("likes") or 0)
    plays = float(m.get("plays") or 0)
    watch = plays / reach if plays else 0.0        # completion/rewatch proxy
    return (6 * sends + 3 * saved + 2 * watch + 1 * comments + 0.25 * likes) / reach


def harvest(dry_run: bool = False) -> dict:
    memory = config.load_memory()
    now = datetime.now(timezone.utc)
    updated, skipped, errors = 0, 0, []
    for post in memory.get("posts", []):
        created = _parse(post.get("created_at"))
        if not created:
            continue
        age = now - created
        if age < timedelta(hours=MIN_AGE_H) or age > timedelta(days=MAX_AGE_D):
            if age > timedelta(days=MAX_AGE_D) and not post.get("harvested"):
                if not dry_run:
                    post["harvested"] = True
            continue
        last = _parse(post.get("last_harvested"))
        if post.get("harvested") and last and (now - last) < timedelta(hours=REPULL_H):
            continue
        try:
            m = publish.insights(post["media_id"])
        except Exception as exc:  # noqa: BLE001 — young-post 4xx is silently skipped
            errors.append({"media_id": post.get("media_id"), "error": str(exc)[:200]})
            skipped += 1
            continue
        if not m:
            skipped += 1
            continue
        post.setdefault("metrics", {}).update(m)
        post["score"] = compute_score(post.get("metrics") or {})
        post["last_harvested"] = now.isoformat()
        if age >= timedelta(days=MAX_AGE_D):
            post["harvested"] = True
        updated += 1
        if not dry_run:
            config.save_memory(memory)
    if not dry_run:
        prune(memory)
        config.save_memory(memory)
    return {"updated": updated, "skipped": skipped, "errors": errors,
            "total_posts": len(memory.get("posts", []))}


def prune(memory: dict) -> dict:
    today = config.today_utc()

    def _keep(entry, days):
        if not isinstance(entry, dict):
            try:
                d = config.date.fromisoformat(str(entry)[:10])
                return d >= today - timedelta(days=days)
            except Exception:  # noqa: BLE001
                return True
        raw = entry.get("date") or entry.get("last_harvested") or ""
        try:
            return config.date.fromisoformat(str(raw)[:10]) >= today - timedelta(days=days)
        except Exception:  # noqa: BLE001
            return True

    before = {k: len(memory.get(k, [])) for k in
              ("used_pins", "used_tracks", "used_track_urls", "used_track_profiles",
               "used_hooks", "candidate_log")}
    memory["used_pins"] = [e for e in memory.get("used_pins", []) if _keep(e, PRUNE_PINS_D)]
    memory["used_tracks"] = [e for e in memory.get("used_tracks", []) if _keep(e, 14)]
    memory["used_track_urls"] = [e for e in memory.get("used_track_urls", []) if _keep(e, PRUNE_PINS_D)]
    # keep the audio fingerprints longer than the URLs: a repeat noticed a month later
    # is still a repeat, and the hash list is tiny.
    memory["used_track_profiles"] = [e for e in memory.get("used_track_profiles", [])
                                     if _keep(e, PRUNE_TRACK_DAYS)]
    memory["used_hooks"] = [e for e in memory.get("used_hooks", []) if _keep(e, PRUNE_HOOKS_D)]
    memory["candidate_log"] = [e for e in memory.get("candidate_log", []) if _keep(e, PRUNE_HOOKS_D)]
    return {"before": before,
            "after": {k: len(memory.get(k, [])) for k in before}}


def main(dry_run: bool = False):
    res = harvest(dry_run=dry_run)
    print(f"harvest: {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
