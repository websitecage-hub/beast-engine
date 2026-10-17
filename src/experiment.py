"""experiment.py — predeclared arm schedule, cooldowns, maturity, replication.

Section 9 in code. This module is pure bookkeeping over `data/experiments.json`:
it decides which eligible arm goes next and records assignments, but it never
renders, never publishes and never scores a missing metric as zero.

Phases:
  A  calibration (first 15 valid posts): BALANCED, predeclared round-robin over
     the eligible formats, with a cooldown so one arm cannot run away with the
     schedule. Only formats the registry marks eligible are ever chosen — a
     disabled arm is never "tested" by pretending.
  B  every captured snapshot is kept (2h/24h/72h/7d); maturity uses 72h/7d.
  C  a first high outlier is a BREAKOUT CANDIDATE, not a win. A mechanism is only
     promoted after >= 3 independent supporting examples (replication).
  D  a conservative explore/exploit switch after >= 5 valid mature samples per
     arm, with a >= 30% exploration floor.

Maturity is a property of the DATA ("this post has a 72h snapshot"), never of the
wall clock of a later run.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

SCHEMA_VERSION = "1.0"
CALIBRATION_SIZE = 15
MIN_MATURE_PER_ARM = 5
EXPLORE_FLOOR = 0.30
MAX_CONSECUTIVE_SAME_ARM = 2
MATURE_SNAPSHOTS = ["72h", "7d"]
EARLY_SNAPSHOTS = ["2h", "24h"]
SNAPSHOTS = EARLY_SNAPSHOTS + MATURE_SNAPSHOTS
# Rate per reach is the primary resonance signal (Section 8.3).
PRIMARY_METRIC = "share_rate"


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ------------------------------------------------------------------ storage

def empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "plans": [], "assignments": []}


def load(path) -> dict:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty()
    for k in ("plans", "assignments"):
        data.setdefault(k, [])
    data.setdefault("schema_version", SCHEMA_VERSION)
    return data


def save(exp: dict, path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(exp, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ plans

def new_plan(arms: list, *, size: int = CALIBRATION_SIZE, note: str = "") -> dict:
    """A predeclared, balanced arm plan. `arms` are eligible format ids."""
    if not arms:
        raise ValueError("a plan needs at least one eligible arm")
    return {"experiment_id": f"exp-{_dt.datetime.now(_dt.timezone.utc):%Y%m}-cal",
            "phase": "calibration", "arms": list(arms), "size": int(size),
            "created_at": now_iso(), "note": note}


# ------------------------------------------------------------------ counts

def _valid(a: dict) -> bool:
    return bool(a.get("valid", True)) and not a.get("publish_error")


def mature_counts(exp: dict) -> dict:
    """Valid MATURE assignments per arm (a post with a 72h/7d snapshot)."""
    counts: dict = {}
    for a in exp.get("assignments", []):
        if not _valid(a) or not a.get("mature"):
            continue
        counts[a.get("arm")] = counts.get(a.get("arm"), 0) + 1
    return counts


def valid_counts(exp: dict) -> dict:
    counts: dict = {}
    for a in exp.get("assignments", []):
        if _valid(a):
            counts[a.get("arm")] = counts.get(a.get("arm"), 0) + 1
    return counts


def _consecutive_same(recent_arms: list) -> int:
    n = 0
    for a in reversed(recent_arms or []):
        if a is None:
            break
        if n == 0:
            last = a
        if a == last:
            n += 1
        else:
            break
    return n


def _blocked_from(recent_arms: list, arms: list) -> set:
    """Arms blocked by the consecutive-run cooldown."""
    if not recent_arms:
        return set()
    last = recent_arms[-1]
    if _consecutive_same(recent_arms) >= MAX_CONSECUTIVE_SAME_ARM:
        return {last}
    return set()


def sample_maturity(age_hours) -> dict:
    """Which snapshots a post of this age can have, and whether it is mature."""
    age = float(age_hours or 0)
    avail = [s for s, h in (("2h", 2), ("24h", 24), ("72h", 72), ("7d", 168))
             if age >= h]
    return {"snapshots_available": avail, "mature": any(
        s in avail for s in MATURE_SNAPSHOTS)}


# ------------------------------------------------------------------ selection

def recommend_arm(exp: dict, eligible: list, recent_arms=None,
                  rng=None) -> dict:
    """Pick the next arm. Returns {"arm", "phase", "reason"}.

    Only `eligible` arms are ever returned (fail closed: empty -> error).
    """
    import random
    rng = rng or random
    elig = [a for a in eligible if a]
    if not elig:
        raise ValueError("no eligible arms to schedule")
    recent_arms = list(recent_arms or [])
    blocked = _blocked_from(recent_arms, elig)
    candidates = [a for a in elig if a not in blocked] or elig

    total_valid = sum(valid_counts(exp).values())
    counts = valid_counts(exp)

    # Phase A: balanced calibration — pick the least-used eligible arm.
    if total_valid < CALIBRATION_SIZE:
        min_used = min(counts.get(a, 0) for a in candidates)
        least = [a for a in candidates if counts.get(a, 0) == min_used]
        arm = rng.choice(least)
        return {"arm": arm, "phase": "calibration",
                "reason": f"balanced (used {counts.get(arm, 0)}, "
                          f"valid total {total_valid})"}

    # Phase D: conservative explore/exploit once maturity supports it.
    mature = mature_counts(exp)
    if all(mature.get(a, 0) >= MIN_MATURE_PER_ARM for a in elig):
        if rng.random() < EXPLORE_FLOOR:
            arm = rng.choice(candidates)
            return {"arm": arm, "phase": "explore",
                    "reason": f"exploration floor {EXPLORE_FLOOR:.0%}"}
        best = max(candidates, key=lambda a: _arm_score(exp, a))
        return {"arm": best, "phase": "exploit",
                "reason": f"best mature {PRIMARY_METRIC}"}

    # Not enough mature data for the bandit: stay balanced but never repeat the
    # same arm more than the cooldown allows.
    min_used = min(counts.get(a, 0) for a in candidates)
    least = [a for a in candidates if counts.get(a, 0) == min_used]
    arm = rng.choice(least)
    return {"arm": arm, "phase": "calibration",
            "reason": "awaiting mature samples per arm"}


def _arm_score(exp: dict, arm: str):
    vals = [a.get("metrics", {}).get(PRIMARY_METRIC) for a in exp.get("assignments", [])
            if a.get("arm") == arm and _valid(a) and a.get("mature")]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else float("-inf")


# ------------------------------------------------------------------ logging

def log_assignment(exp: dict, *, arm: str, post_id=None, slot=None, topic=None,
                   metrics=None, snapshots=None, valid: bool = True) -> dict:
    """Append an assignment. Maturity is derived from which snapshots exist.

    `metrics` is the flat, mature decision table (e.g. {"share_rate": 0.04}).
    `snapshots` is the list of captured snapshot labels (e.g. ["2h","72h"]).
    A post is mature only when a mature snapshot (72h/7d) is present — never
    inferred from the fact that a later run happened to look at it.
    """
    snaps = sorted(set(snapshots or []))
    mature = any(s in snaps for s in MATURE_SNAPSHOTS)
    entry = {
        "ts": now_iso(), "experiment_id": None, "arm": arm, "post_id": post_id,
        "slot": slot, "topic": topic, "valid": bool(valid),
        "publish_error": None, "mature": bool(mature),
        "snapshots": snaps, "metrics": dict(metrics or {}),
        "breakout": False, "replicate_of": None,
    }
    exp.setdefault("assignments", []).append(entry)
    return entry


def replication_support(exp: dict, arm: str, threshold: float = 3.0) -> int:
    """Independent supporting examples for this arm's mechanism.

    A breakout candidate is any mature assignment whose PRIMARY_METRIC is at
    least `threshold` x the arm's own mature median. Promotion needs >= 3; a
    single viral post is never proof (Section 9 Phase C). Returns the count.
    """
    rows = [a for a in exp.get("assignments", [])
            if a.get("arm") == arm and _valid(a) and a.get("mature")
            and isinstance(a.get("metrics", {}).get(PRIMARY_METRIC), (int, float))]
    vals = sorted(a["metrics"][PRIMARY_METRIC] for a in rows)
    if len(vals) < 3:
        return 0
    median = vals[len(vals) // 2]
    if median <= 0:
        return 0
    return sum(1 for v in vals if v >= threshold * median)


def ready_to_promote(exp: dict, arm: str) -> bool:
    return replication_support(exp, arm) >= 3
