"""insights.py — BEAST ENGINE v2 metric collection (ENGINE_SPEC §1.4).

WHAT THIS ADDS OVER publish.insights

An ALLOWLIST of the v2 metric set, and FEATURE DETECTION: the docs list metrics
that are not available on every media product type, and a single unsupported name
makes the API fail the whole request (measured on this route — sending a list
instead of one comma-joined string fails every metric at once, which looks
identical to "this post has no data").

So: request the full set, and on a 400 that names a metric, DROP THAT METRIC and
retry. Record which ones were available, so the learner can tell "skip_rate was
zero" apart from "skip_rate is not supported here". Without that distinction the
reward silently treats a missing skip rate as a good skip rate.

Retention cadence per §1.4: +2h, +24h, +72h, +7d. The 24h snapshot is the
learning snapshot.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

# ENGINE_SPEC §1.4, verified against the Meta docs on 2026-10-11.
WANTED = [
    "views", "reach", "saved", "shares", "likes", "comments",
    "total_interactions", "reposts",
    "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
    "reels_skip_rate",
]
# Requested first because the reward depends on them; if the API rejects the
# batch these are the ones worth a second, narrower attempt for.
CRITICAL = ["reach", "ig_reels_avg_watch_time"]

SNAPSHOTS = [("2h", 2), ("24h", 24), ("72h", 72), ("7d", 168)]
LEARN_SNAPSHOT = "24h"

FOLLOWERS_PATH = Path("data/followers.json")


def _age_hours(post: dict) -> float:
    raw = post.get("published_at_utc") or post.get("created_at")
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return -1.0
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def due_snapshot(post: dict) -> str | None:
    """Which snapshot this post owes us next, if any.

    A snapshot is due when the post is older than its age and we have not stored
    it yet. Snapshots are written once and never overwritten: the 24h number is
    the learning signal, so silently replacing it with a 48h pull would change
    what the learner was trained on after the fact.
    """
    age = _age_hours(post)
    if age < 0:
        return None
    snaps = post.get("metrics") or {}
    for name, hours in SNAPSHOTS:
        if age >= hours and name not in snaps:
            return name
    return None


def fetch(media_id: str) -> tuple:
    """(metrics, available_names). Feature-detects, dropping rejected metrics.

    Returns ({}, []) when nothing could be fetched — the caller must treat that as
    "no data", never as zeros, or a delivery failure reads as a bad post.
    """
    from . import publish  # imported here so this module stays import-light

    names = list(WANTED)
    unavailable: list = []
    # Bounded: each pass drops at least one metric, so this cannot spin.
    for _ in range(len(WANTED) + 2):
        if not names:
            return {}, []
        metric = ",".join(names)
        try:
            r = publish._get(f"/{media_id}/insights", {"metric": metric})
        except Exception:  # noqa: BLE001
            return {}, []
        if r.status_code == 200:
            out = {}
            for row in (r.json() or {}).get("data", []):
                nm = row.get("name")
                vals = row.get("values") or []
                if nm and vals:
                    out[nm] = vals[0].get("value")
            return out, sorted(out.keys())

        # A 400 naming a metric: drop exactly that one and retry.
        blurb = ""
        try:
            blurb = json.dumps(r.json())
        except Exception:  # noqa: BLE001
            blurb = getattr(r, "text", "") or ""
        dropped = [n for n in names if n in blurb]
        if not dropped:
            # The API did not name a culprit. Fall back to the two tiers the
            # proven path already used rather than guessing at names.
            tiers = [(", ".join(CRITICAL), "reach,likes,comments,saved,shares"), ]
            for _lbl, m in tiers:
                try:
                    rr = publish._get(f"/{media_id}/insights", {"metric": m})
                    if rr.status_code == 200:
                        out = {}
                        for row in (rr.json() or {}).get("data", []):
                            nm = row.get("name")
                            vals = row.get("values") or []
                            if nm and vals:
                                out[nm] = vals[0].get("value")
                        return out, sorted(out.keys())
                except Exception:  # noqa: BLE001
                    continue
            return {}, []
        for d in dropped:
            names.remove(d)
            unavailable.append(d)
    return {}, []


def record_snapshot(post: dict, metrics: dict, name: str) -> dict:
    """Store one snapshot on the post, and score it if it is the learning one."""
    post.setdefault("metrics", {})
    post["metrics"][name] = dict(metrics or {})
    if name == LEARN_SNAPSHOT:
        weights = (config.load_config().get("reward_weights") or {})
        from . import learner
        # A delivery failure is logged but NOT scored, so it cannot teach the
        # learner that the content was bad when nobody was shown it.
        post["score"] = (learner.reward(metrics, post.get("duration_s"), weights)
                         if learner.is_learnable(metrics, weights) else None)
        post["learnable"] = learner.is_learnable(metrics, weights)
        post["scored_at"] = datetime.now(timezone.utc).isoformat()
    return post


def parse_hashtags(caption: str) -> list:
    import re
    return re.findall(r"#[A-Za-z0-9_]+", caption or "")


def fetch_followers(publish_mod=None) -> dict:
    """Daily follower count into data/followers.json (ENGINE_SPEC §1.4).

    Trial Reels eligibility depends on it, so it is tracked over time rather than
    read once: the account may cross the threshold on any day.
    """
    from . import publish as _pub
    publish = publish_mod or _pub
    out = {}
    try:
        r = publish._get("/me", {"fields": "followers_count,username"})
        if r.status_code == 200:
            j = r.json() or {}
            out = {"followers_count": j.get("followers_count"),
                   "username": j.get("username")}
    except Exception:  # noqa: BLE001
        return {}
    if not out:
        return {}
    FOLLOWERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    hist = []
    if FOLLOWERS_PATH.exists():
        try:
            hist = json.loads(FOLLOWERS_PATH.read_text(encoding="utf-8"))
            if not isinstance(hist, list):
                hist = []
        except Exception:  # noqa: BLE001
            hist = []
    today = config.today_utc().isoformat()
    hist = [h for h in hist if h.get("date") != today]
    hist.append({"date": today, **out})
    FOLLOWERS_PATH.write_text(json.dumps(hist[-400:], indent=2) + "\n",
                              encoding="utf-8")
    return out


def latest_followers() -> int:
    if not FOLLOWERS_PATH.exists():
        return 0
    try:
        hist = json.loads(FOLLOWERS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    for h in reversed(hist if isinstance(hist, list) else []):
        try:
            return int(h.get("followers_count") or 0)
        except (TypeError, ValueError):
            continue
    return 0