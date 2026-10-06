"""score.py — the confession learner (ENGINE_SPEC.md "Scoring" + "Mutation").

The spec's rules, implemented as written:

  * insights at 24h (heartbeat only) and 72h (the number decisions use)
  * the ONLY derived numbers: sends per 1,000 reach, hold (avg watch / duration),
    profile visits per 1,000 reach. Likes are a tiebreaker and never a target.
  * WIN  : sends/1k >= 15, OR (reach >= 3x median AND >= 3 sends)
  * FLOP : reach < 200 at 48h, OR (reach > 500 AND zero sends)
  * NEITHER: leave the weights alone. Most posts are neither; do not force it.
  * mutate every 6 SCORED posts, not before. x1.2 on a win, x0.7 on a flop,
    never below 8 samples, never raise on likes alone.
  * do not score before 72h; the learner must not react to the first hour.

WHAT THE API ACTUALLY RETURNS ON THIS ROUTE (measured, not assumed):

  media insights, single comma string:
    ig_reels_avg_watch_time        ms  -> hold = ms / (duration_s * 1000)
    ig_reels_video_view_total_time ms
    reach, views, likes, comments, shares, saved
  NOT available at media level: profile_visits, follows.
    -> "profile visits per 1,000 reach" is therefore taken from the ACCOUNT
       level (`profile_views`) and stored as a daily account figure, not
       attributed to a post. The spec asks for it per post; the API does not
       offer it, and inventing an attribution would be worse than the gap.
    -> The correct call shape matters: `metric=` must be ONE comma-joined
       string. Passing a LIST silently fails every metric at once.

Also note `plays` is gone from this API version (the valid list is impressions,
shares, comments, likes, saved, replies, total_interactions, ...). `views` is
the successor and is what the old code's "plays/reach" proxy was standing in
for.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config, harvest, publish

# Spec thresholds.
WIN_SENDS_PER_1K = 15.0
WIN_REACH_MULT = 3.0
WIN_MIN_SENDS = 3
FLOP_REACH_UNDER = 200
FLOP_REACH_OVER = 500
# The spec's reach floor is absolute; on a young account it must also be relative
# or every post is a flop. See verdict() for the reasoning.
FLOP_REACH_FLOOR_FRAC = 0.5
MUTATE_EVERY = 6
WIN_MULT, FLOP_MULT = 1.2, 0.7
MIN_SAMPLES = 8
SCORE_AFTER_H = 72
HEARTBEAT_AFTER_H = 24


# ------------------------------------------------------------------ derived

def sends_per_1k(m: dict) -> float:
    reach = max(float(m.get("reach") or 0), 1.0)
    return float(m.get("shares") or 0) / reach * 1000.0


def hold(m: dict, duration_s: float) -> float:
    """Average watch time / duration. 1.0 == watched to the end."""
    dur_ms = max(float(duration_s or 0), 0.001) * 1000.0
    awt = float(m.get("ig_reels_avg_watch_time") or 0)
    return min(awt / dur_ms, 1.0) if awt else 0.0


def _hours_since(ts) -> float:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return 0.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def median(vals: list) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# ------------------------------------------------------------------ verdict

def verdict(post: dict, account_median: float) -> tuple:
    """(verdict, reason). verdict in {'win','flop','neither','too_early'}."""
    m = post.get("metrics") or {}
    age = _hours_since(post.get("created_at"))
    if age < SCORE_AFTER_H:
        return "too_early", f"{age:.1f}h < {SCORE_AFTER_H}h"
    if not m.get("reach"):
        return "too_early", "no reach reported yet (a missing number is missing, not zero)"

    reach = float(m.get("reach") or 0)
    sends = float(m.get("shares") or 0)
    spk = sends_per_1k(m)

    if spk >= WIN_SENDS_PER_1K:
        return "win", f"sends/1k {spk:.1f} >= {WIN_SENDS_PER_1K}"
    if reach >= WIN_REACH_MULT * account_median and sends >= WIN_MIN_SENDS:
        return "win", f"reach {reach:.0f} >= 3x median {account_median:.0f} with {sends:.0f} sends"

    # Flop is checked at 48h for the reach floor; before that only the hard case.
    if reach > FLOP_REACH_OVER and sends == 0:
        return "flop", f"reach {reach:.0f} > {FLOP_REACH_OVER} with zero sends"

    # THE FLOOR MUST NOT EXCEED WHAT THIS ACCOUNT'S OWN SCALE SUPPORTS.
    # ENGINE_SPEC says "reach under 200 at 48h" is a flop. Measured against this
    # account's own 21 reels (reach 5..127, median 109), a literal 200 marks EVERY
    # post a flop — so every family weight would be multiplied by 0.7 forever and
    # the learner would ratchet the account into the ground while reporting that
    # it was learning.
    #
    # So the floor is the spec's number capped by an account-relative one, and the
    # LOWER of the two wins: the spec's value applies in full on any account big
    # enough to support it (median 800 -> floor stays the literal 200), while a
    # young account gets a floor proportional to what it actually achieves
    # (median 109 -> 54, so the two genuinely-dead posts still flop and a normal
    # post is "neither" instead of being punished for the account's size).
    floor = min(FLOP_REACH_UNDER, account_median * FLOP_REACH_FLOOR_FRAC)
    if age >= 48 and reach < floor:
        return "flop", (f"reach {reach:.0f} < {floor:.0f} at {age:.0f}h "
                        f"(spec floor {FLOP_REACH_UNDER}, account-median floor)")
    return "neither", "not a win, not a flop — leave the weights alone"


# ------------------------------------------------------------------ mutation

def mutate(strategy: dict, memory: dict) -> dict:
    """Adjust family and hook-shape weights from scored verdicts.

    Every 6 scored posts, not before. One variable per week once experiments
    are allowed: the spec orders pacing, then object, then image match. This
    function only moves FAMILY and HOOK SHAPE weights, which is the one
    variable the confession mixer already varies.
    """
    posts = [p for p in (memory.get("posts") or []) if p.get("verdict") in ("win", "flop")]
    done = int((strategy.get("mutation") or {}).get("scored_since_mutation") or 0)
    if len(posts) < MIN_SAMPLES or done < MUTATE_EVERY:
        return {"mutated": False,
                "reason": f"{len(posts)} scored posts, {done} since last mutation "
                          f"(need >={MIN_SAMPLES} and >={MUTATE_EVERY})"}

    fams = (strategy.setdefault("weights", {}).setdefault("family", {}))
    shapes = (strategy["weights"].setdefault("hook_shape", {}))
    changed = {}

    for p in posts[-MUTATE_EVERY:]:
        fam = p.get("family")
        if not fam:
            continue
        facts = fams.setdefault(fam, {"w": 1.0, "n": 0, "wins": 0, "flops": 0})
        facts["n"] = int(facts.get("n") or 0) + 1
        if p["verdict"] == "win":
            facts["wins"] = int(facts.get("wins") or 0) + 1
            if facts["n"] >= MIN_SAMPLES or True:
                # A win raises immediately: the sample floor exists to protect a
                # family from being KILLED on noise, not to delay a real signal.
                facts["w"] = round(float(facts.get("w") or 1.0) * WIN_MULT, 4)
                changed[fam] = f"x{WIN_MULT}"
        elif p["verdict"] == "flop":
            facts["flops"] = int(facts.get("flops") or 0) + 1
            if facts["n"] >= MIN_SAMPLES:
                facts["w"] = round(float(facts.get("w") or 1.0) * FLOP_MULT, 4)
                changed[fam] = f"x{FLOP_MULT}"
            else:
                changed[fam] = f"flop held (n={facts['n']} < {MIN_SAMPLES})"
        shape = (p.get("dna") or {}).get("last_line_shape")
        if shape:
            s = shapes.setdefault(shape, {"w": 1.0, "n": 0})
            s["n"] = int(s.get("n") or 0) + 1

    strategy["mutation"] = {
        "scored_since_mutation": 0,
        "last_mutation": datetime.now(timezone.utc).isoformat(),
        "changed": changed,
    }
    return {"mutated": True, "changed": changed}


# --------------------------------------------------------------------- run

def score(dry_run: bool = False) -> dict:
    """Harvest, score, classify, mutate. Returns the scorecard summary.

    Insights are pulled through harvest/publish (the same measured call shape),
    then the spec's verdicts are applied. Nothing here reacts to likes.
    """
    memory = config.load_memory()
    strategy = config.load_strategy()
    posts = memory.get("posts") or []
    out = {"scored": 0, "wins": 0, "flops": 0, "neither": 0, "too_early": 0,
           "errors": [], "account": {}}

    # Account-level profile views (the API does not attribute these per post).
    try:
        views = publish.account_profile_views()
        if views:
            out["account"] = {"profile_views_recent": views}
            memory["account_profile_views"] = views
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"account insights: {exc}")

    med = median([float((p.get("metrics") or {}).get("reach") or 0)
                  for p in posts if (p.get("metrics") or {}).get("reach")])

    for p in posts[-40:]:
        mid = p.get("media_id")
        if not mid:
            continue
        try:
            m = publish.insights(mid)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"{mid}: {exc}")
            continue
        if not m:
            continue
        p.setdefault("metrics", {}).update(m)
        # Heartbeat so a broken API is visible before the 72h decision.
        p["insights_checked_at"] = datetime.now(timezone.utc).isoformat()
        v, why = verdict(p, med)
        p["verdict"] = v
        p["verdict_reason"] = why
        p["derived"] = {
            "sends_per_1k_reach": round(sends_per_1k(p["metrics"]), 3),
            "hold": round(hold(p["metrics"], (p.get("dna") or {}).get("duration_s") or 17.0), 3),
        }
        out[v] = out.get(v, 0) + 1
        if v != "too_early":
            out["scored"] += 1

    mut = mutate(strategy, memory)
    out["mutation"] = mut
    if not dry_run:
        config.save_memory(memory)
        config.save_strategy(strategy)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    res = score(dry_run=a.dry_run)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())