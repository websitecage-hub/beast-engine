"""analyze.py — coverage, duplication, missing fields and (when possible) outliers.

Section 15 (Research acceptance) asks for: source URLs, data timestamps, creator
baselines, duplicated items, language and missing-field analysis, plus outliers
with same-creator controls.

This module reports exactly what the local corpus supports. When the corpus has
no performance fields (the current state: Reddit text only), it says so in
``performance_available = False`` and the outlier/control tables are empty —
rather than inventing a baseline from qualitative text.
"""
from __future__ import annotations

import collections

from . import normalize

PERF_FIELDS = ["views", "likes", "comments", "shares", "saves", "reach",
               "creator_baseline_views", "outlier_multiple"]


def _norm_text(rec: dict) -> str:
    return (str(rec.get("title") or "") + " " + str(rec.get("text") or "")).strip().lower()


def duplicates(records: list) -> list:
    """Groups of records whose cleaned text collides. Returns list of groups."""
    seen: dict = collections.defaultdict(list)
    for r in records:
        key = _norm_text(r)[:280]
        if key:
            seen[key].append(r)
    return [g for g in seen.values() if len(g) > 1]


def coverage(records: list) -> dict:
    """Per-field populated counts across records (of the reel schema fields)."""
    cov = {}
    for f in normalize.REEL_FIELDS:
        cov[f] = sum(1 for r in records if r.get(f) not in (None, ""))
    return cov


def missing_field_summary(records: list) -> dict:
    n = len(records)
    cov = coverage(records)
    return {f: {"present": c, "missing": n - c,
                "present_pct": round(100.0 * c / n, 1) if n else 0.0}
            for f, c in cov.items()}


def language_breakdown(records: list) -> dict:
    return dict(collections.Counter(r.get("language") or "unknown" for r in records))


def subreddit_breakdown(records: list) -> dict:
    return dict(collections.Counter(
        r.get("subreddit") for r in records if r.get("subreddit")))


def performance_available(records: list) -> bool:
    return any(r.get("views") not in (None, "") or
               r.get("creator_baseline_views") not in (None, "") for r in records)


def compute_outlier_multiple(views, baseline) -> float | None:
    """views / creator_baseline_views. Returns None when either is missing or the
    baseline is zero — an unknown multiple is None, never 0 or infinity."""
    try:
        v, b = float(views), float(baseline)
    except (TypeError, ValueError):
        return None
    if b <= 0:
        return None
    return round(v / b, 2)


def outliers_and_controls(records: list, mult_threshold: float = 3.0) -> dict:
    """Outliers (multiple >= threshold) plus same-creator normal controls.

    A control is a same-creator record whose multiple is < threshold, so a
    breakout is always read against its own creator's baseline rather than raw
    views. Empty when the corpus carries no performance data (current state).
    """
    out, controls = [], []
    for r in records:
        if r.get("views") in (None, "") or r.get("creator_baseline_views") in (None, ""):
            continue
        m = compute_outlier_multiple(r.get("views"), r.get("creator_baseline_views"))
        if m is None:
            continue
        row = {"record_id": r["record_id"], "creator": r.get("creator_handle"),
               "url": r.get("canonical_url"), "views": r.get("views"),
               "baseline": r.get("creator_baseline_views"), "multiple": m,
               "duration_s": r.get("duration_s"), "format": r.get("format_family")}
        (out if m >= mult_threshold else controls).append(row)
    out.sort(key=lambda x: x["multiple"], reverse=True)
    return {"outliers": out, "controls": controls,
            "threshold": mult_threshold}


def summarize(records: list) -> dict:
    return {
        "n_records": len(records),
        "by_source": dict(collections.Counter(r["source"] for r in records)),
        "by_platform": dict(collections.Counter(
            r.get("platform") or "unknown" for r in records)),
        "subreddits": subreddit_breakdown(records),
        "languages": language_breakdown(records),
        "duplicate_groups": len(duplicates(records)),
        "performance_available": performance_available(records),
        "missing": missing_field_summary(records),
    }
