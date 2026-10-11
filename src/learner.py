"""learner.py — BEAST ENGINE v2 arms, reward and selection (ENGINE_SPEC §2).

This is the whole learning layer: the reward (how good was that post), the arm
statistics (which choices earned it), and the selection policy (what to try next).

DESIGN NOTES THAT MATTER

  * The reward is computed on the 24h snapshot only. 2h is too early to mean
    anything and 72h is too late to steer the next post.
  * Retention dominates while volume is tiny; shares and saves take over as reach
    grows. That is why watch carries 1.0 against 20*(shares/reach) — at reach 100
    a share is already worth 0.2, twenty times a full watch ratio.
  * Posts under the reach floor are a DELIVERY failure, not a content signal, so
    they are logged and excluded from learning. Including them teaches the
    learner that a topic nobody was shown is a bad topic.
  * Arcs are picked ONE ATTRIBUTE AT A TIME (additive model). With this few posts,
    interactions are 1-2 samples per cell — estimating them would be fitting noise.
  * Hard constraints ALWAYS beat the draw. Thompson sampling maximises expected
    reward; it does not know about cooldowns, caps or the account's safety rules.

`shrunk` and `pick` are the spec's own functions, kept verbatim in behaviour so the
learner can be reasoned about against the spec rather than against a refinement.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- the arms
#
# Topic is deliberately NOT an arm (ENGINE_SPEC §2.2): a topic cooldown already
# stops repeat topics, and making it an arm would let the learner keep re-running
# whichever topic got lucky once.

ATTRIBUTES = ["series", "hook_type", "scene_family", "layout", "voice",
              "length_bucket", "cta_type", "slot", "bg_source", "music_mood"]

LENGTH_BUCKETS = ["14-18", "18-24", "24-34"]

# STRATEGY §5.2. The learner may move these by at most 10 points, and they must
# still sum to 1.0.
CTA_FLOOR_CEIL = 0.10


def default_arms() -> dict:
    """Every arm starts unobserved; the caller seeds from the schema it knows."""
    return {a: {} for a in ATTRIBUTES}


# ---------------------------------------------------------------- the reward

def reward(m: dict, duration_s: float, weights: dict | None = None) -> float:
    """ENGINE_SPEC §2.1, with the weights read from config so they are tunable.

        watch = min(avg_watch_s / duration_s, 2.0)      # >1 means replays
        score = 1.0*watch + 20*(shares/reach) + 8*(saves/reach)
              + 3*(comments/reach) + 1*(likes/reach)
              - 0.5*max(0, skip_rate - 0.5)             # only when available
    """
    w = dict(weights or {})
    reach = max(float(m.get("reach") or 0), 1.0)
    dur = max(float(duration_s or 0), 0.1)

    aw = m.get("ig_reels_avg_watch_time")
    try:
        aw = float(aw)
    except (TypeError, ValueError):
        aw = 0.0
    watch = min(aw / dur, float(w.get("watch_cap", 2.0))) if aw else 0.0

    score = (float(w.get("watch", 1.0)) * watch
             + float(w.get("shares_over_reach", 20.0)) * (float(m.get("shares") or 0) / reach)
             + float(w.get("saves_over_reach", 8.0)) * (float(m.get("saved") or 0) / reach)
             + float(w.get("comments_over_reach", 3.0)) * (float(m.get("comments") or 0) / reach)
             + float(w.get("likes_over_reach", 1.0)) * (float(m.get("likes") or 0) / reach))

    skip = m.get("reels_skip_rate")
    if skip is not None:
        try:
            skip = float(skip)
            thr = float(w.get("skip_threshold", 0.5))
            score -= float(w.get("skip_penalty", 0.5)) * max(0.0, skip - thr)
        except (TypeError, ValueError):
            pass
    return round(score, 6)


def is_learnable(m: dict, weights: dict | None = None) -> bool:
    """False for delivery failures — logged, never learned from."""
    floor = float((weights or {}).get("reach_floor", 25))
    try:
        return float(m.get("reach") or 0) >= floor
    except (TypeError, ValueError):
        return False


def median(vals: list) -> float:
    xs = sorted(float(v) for v in vals if v is not None)
    if not xs:
        return 0.0
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def breakout(metrics: list, shares: list) -> bool:
    """ENGINE_SPEC §2.4: reach >= max(3 x median(last 20), 400) OR shares >= 3."""
    recent = [float(x or 0) for x in metrics[-20:]] or [0.0]
    med = median(recent)
    last_reach = float((metrics[-1] if metrics else 0) or 0)
    last_shares = float((shares[-1] if shares else 0) or 0)
    return last_reach >= max(3 * med, 400) or last_shares >= 3


# ---------------------------------------------------------------- selection

def shrunk(xs: list, w: list, G: float, k: int = 5) -> float:
    """Weighted mean pulled toward the global mean G by k pseudo-observations.

    Without this, an arm that won once sits at the top forever: with one
    observation the mean IS that observation.
    """
    num = sum(x * wi for x, wi in zip(xs, w)) + k * G
    den = sum(w) + k
    return num / den if den else G


def pick(arm_stats: dict, G: float, sigma: float) -> str:
    """One Thompson draw per arm; the highest draw wins. (ENGINE_SPEC §2.3.)"""
    draws = {}
    for name, (xs, w) in arm_stats.items():
        mu = shrunk(xs, w, G)
        se = sigma / (sum(w) + 5) ** 0.5
        draws[name] = random.gauss(mu, se)
    return max(draws, key=draws.get)


def recency_weights(ages_days: list) -> list:
    """0.5 ** (age_days / 28) — a half-life of four weeks."""
    return [0.5 ** (max(float(a), 0.0) / 28.0) for a in ages_days]


# ---------------------------------------------------------------- constraints

def series_ok(series: str, recent_series: list) -> bool:
    """Never three of the same series in a row (STRATEGY §3.1)."""
    if len(recent_series) < 2:
        return True
    return not (recent_series[-1] == series and recent_series[-2] == series)


def topic_ok(topic: str, recent: list) -> bool:
    """Same topic at most once per 7 days (STRATEGY §3.1)."""
    return str(topic or "").strip().lower() not in {
        str(t).strip().lower() for t in recent if t}


def pov_ok(hook: str, recent_hooks: list) -> bool:
    """STRATEGY §3.4: POV at most 20% of recent hooks."""
    window = list(recent_hooks)[-60:]
    if not window:
        return True
    if not str(hook).upper().startswith("POV"):
        return True
    pov = sum(1 for h in window if str(h).upper().startswith("POV"))
    return (pov + 1) / (len(window) + 1) <= 0.20


def keyword_cta_ok(cta_type: str, last_cta_type: str) -> bool:
    """Never two keyword CTAs (guide/book) in a row (STRATEGY §5.2)."""
    keywords = {"guide", "book"}
    return not (cta_type in keywords and last_cta_type in keywords)


def cta_shares_sane(shares: dict, base: dict | None = None) -> bool:
    """Shares must sum to 1.0 and stay within 10 points of the policy."""
    base = base or {"none": 0.50, "send": 0.25, "guide": 0.20, "book": 0.05}
    if abs(sum(float(v) for v in shares.values()) - 1.0) > 1e-6:
        return False
    for k, v in shares.items():
        if abs(float(v) - float(base.get(k, 0))) > CTA_FLOOR_CEIL + 1e-9:
            return False
    return True


# ---------------------------------------------------------------- the state

def _age_days(ts) -> float:
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return 0.0
    return max((datetime.now(timezone.utc) - t).total_seconds() / 86400.0, 0.0)


def arm_stats(memory: dict) -> tuple:
    """Rebuild {attribute: {arm: (scores, weights)}} from the post history.

    Returns (stats, global_mean). The global mean G is the shrinkage target, so
    an arm with no data is drawn at the account average rather than at zero.
    """
    posts = [p for p in (memory.get("posts") or [])
             if isinstance(p.get("score"), (int, float))]
    if not posts:
        return default_arms(), 0.0
    G = sum(float(p["score"]) for p in posts) / len(posts)

    stats: dict = {a: {} for a in ATTRIBUTES}
    for p in posts:
        age = _age_days(p.get("published_at_utc") or p.get("created_at"))
        wt = 0.5 ** (age / 28.0)
        for attr in ATTRIBUTES:
            arm = p.get(attr)
            if attr == "length_bucket":
                arm = length_bucket_of(p.get("duration_s"))
            if attr == "slot":
                arm = p.get("slot") or p.get("hour_slot")
            if not arm:
                continue
            xs, ws = stats[attr].setdefault(str(arm), ([], []))
            xs.append(float(p["score"]))
            ws.append(wt)
    return stats, G


def length_bucket_of(duration_s) -> str:
    """14-18 | 18-24 | 24-34 (ENGINE_SPEC §2.2)."""
    try:
        d = float(duration_s)
    except (TypeError, ValueError):
        return "18-24"
    if d < 18:
        return "14-18"
    if d < 24:
        return "18-24"
    return "24-34"


def choose(arms: dict, G: float, allowed: dict, explore_rate: float,
           sigma: float = 1.0) -> dict:
    """One value per attribute: Thompson draw among the ALLOWED arms.

    `allowed[attr]` is the list the hard constraints have already filtered. An
    attribute with no allowed values keeps nothing — the caller's constraints are
    authoritative, and inventing a value here would quietly bypass them.
    """
    out = {}
    for attr, options in allowed.items():
        if not options:
            continue
        stats = arms.get(attr) or {}
        # arms never seen (n<3) are where exploration lives
        fresh = [o for o in options if len(stats.get(o, ([], []))[0]) < 3]
        if fresh and random.random() < explore_rate:
            out[attr] = random.choice(fresh)
            continue
        draws = {}
        for o in options:
            xs, ws = stats.get(o, ([], []))
            mu = shrunk(xs, ws, G) if xs else G
            se = sigma / (sum(ws) + 5) ** 0.5
            draws[o] = random.gauss(mu, se) if xs else random.gauss(G, sigma)
        out[attr] = max(draws, key=draws.get)
    return out