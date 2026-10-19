"""learn_short.py — the v3 learning layer (§7).

Reuses the v2 Thompson machinery (shrunk/median/choose) unchanged; what differs
is the reward (shares dominate — that is what separated winners in the research),
the arms (template/visual_arm/topic/length_bucket/audio_type/slot) and the
breakout rule (reach >= max(5x median of last 20, 500) or 5+ shares).

Reward (§7, 24h snapshot):
    1.0*min(avg_watch/duration, 2) + 25*shares/reach + 8*saves/reach
    + 3*comments/reach + 1*likes/reach

Kill rules (§7):
  - retire an arm after 8 posts if its mean reward is in the bottom 25% AND its
    confidence interval does not overlap the account median
  - exploration resets to 50% after 14 consecutive reels with median reach < 60
"""
from __future__ import annotations

import random

from . import learner

ATTRIBUTES_V3 = ["template", "visual_arm", "topic", "length_bucket",
                 "audio_type", "slot"]

LENGTH_BUCKETS_V3 = ["6-8", "8-10", "10-12"]
VISUAL_ARMS_V3 = ["pin_video", "pin_still"]
AUDIO_TYPES_V3 = ["soft_instrumental", "trending_spedup", "quiet_ambient"]

REWARD_WEIGHTS = {"shares_over_reach": 25.0, "saves_over_reach": 8.0,
                  "comments_over_reach": 3.0, "likes_over_reach": 1.0,
                  "watch": 1.0, "watch_cap": 2.0}


def reward_v3(m: dict, duration_s: float) -> float:
    """§7 reward on the 24h snapshot. Shares dominate."""
    return learner.reward(m, duration_s, REWARD_WEIGHTS)


def is_learnable(m: dict) -> bool:
    return learner.is_learnable(m, REWARD_WEIGHTS)


def breakout_short(metrics: dict, recent_reaches: list) -> bool:
    """§7 breakout: reach >= max(5x median(last 20), 500) OR 5+ shares."""
    reach = float(metrics.get("reach") or 0)
    shares = float(metrics.get("shares") or 0)
    med = learner.median((recent_reaches or [])[-20:])
    return reach >= max(5.0 * med, 500.0) or shares >= 5


def arm_stats_v3(memory: dict) -> tuple:
    """{attr: {arm: (scores, weights)}} for v3 posts only (format == short2)."""
    posts = [p for p in (memory.get("posts") or [])
             if p.get("format") == "short2" and isinstance(p.get("score"), (int, float))]
    if not posts:
        return {a: {} for a in ATTRIBUTES_V3}, 0.0
    G = sum(float(p["score"]) for p in posts) / len(posts)
    stats: dict = {a: {} for a in ATTRIBUTES_V3}
    for p in posts:
        age = learner._age_days(p.get("published_at_utc") or p.get("created_at"))
        wt = 0.5 ** (age / 28.0)
        for attr in ATTRIBUTES_V3:
            arm = p.get(attr)
            if attr == "length_bucket":
                arm = length_bucket_v3(p.get("duration_s"))
            if attr == "slot":
                arm = p.get("slot") or p.get("hour_slot")
            if not arm:
                continue
            xs, ws = stats[attr].setdefault(str(arm), ([], []))
            xs.append(float(p["score"]))
            ws.append(wt)
    return stats, G


def length_bucket_v3(duration_s) -> str:
    try:
        d = float(duration_s)
    except (TypeError, ValueError):
        return "8-10"
    if d < 8:
        return "6-8"
    if d < 10:
        return "8-10"
    return "10-12"


def choose_v3(memory: dict, recent_topics: list, recent_templates: list,
              explore_rate: float = 0.3, recent_pov_count: int = 0) -> dict:
    """One draw per attribute under the v3 hard constraints.

    Constraints (constraints ALWAYS beat the draw):
      - topic: no repeat within the last 3 reels (§7 topic cooldown)
      - template: no repeat within the last 4 reels
      - POV (T10): capped 1-in-5. The cap is enforced HERE, not in the writer:
        offering T10 when a POV is already in the last 4 sends the writer into
        max_rewrites unwinnable lint failures and kills the whole slot
        (observed live: 3 drafts, 3 lint rejections, slot dead).
    """
    stats, G = arm_stats_v3(memory)
    recent_topics = [str(t) for t in (recent_topics or [])[-3:]]
    recent_templates = [str(t) for t in (recent_templates or [])[-4:]]
    template_pool = [t for t in [f"T{i}" for i in range(1, 11)]
                     if t not in recent_templates] or [f"T{i}" for i in range(1, 11)]
    if recent_pov_count >= 1:
        template_pool = [t for t in template_pool if t != "T10"] or ["T1"]
    allowed = {
        "template": template_pool,
        "visual_arm": VISUAL_ARMS_V3[:],
        "topic": [t for t in _topics() if t not in recent_topics] or _topics(),
        "length_bucket": LENGTH_BUCKETS_V3[:],
        "audio_type": AUDIO_TYPES_V3[:],
        "slot": ["12:30", "17:30", "01:00"],
    }
    return learner.choose(stats, G, allowed, explore_rate, sigma=1.0)


def exploration_rate(memory: dict) -> float:
    """§7: 50% after 14 consecutive reels with median reach under 60."""
    posts = [p for p in (memory.get("posts") or []) if p.get("format") == "short2"]
    last = posts[-14:]
    if len(last) >= 14:
        reaches = [float(p.get("reach") or 0) for p in last]
        if learner.median(reaches) < 60.0:
            return 0.5
    return 0.3


def retired_arms(memory: dict) -> dict:
    """§7 kill rule: 8+ posts, bottom-25% mean, CI clear of the median."""
    stats, G = arm_stats_v3(memory)
    all_scores = [s for attr in stats.values() for (xs, _ws) in attr.values()
                  for s in xs]
    if len(all_scores) < 8:
        return {}
    med = learner.median(all_scores)
    floor = sorted(all_scores)[max(0, int(len(all_scores) * 0.25))]
    out = {}
    for attr, arms in stats.items():
        for arm, (xs, ws) in arms.items():
            if len(xs) < 8:
                continue
            mu = learner.shrunk(xs, ws, G)
            se = 1.0 / (sum(ws) + 5) ** 0.5
            if mu < floor and (mu + 1.96 * se) < med:
                out.setdefault(attr, []).append(arm)
    return out


def _topics() -> list:
    from . import generate_short
    return generate_short.templates()["topics"]


def breakout_clones(reel: dict, n: int = 5) -> list:
    """§7: same template + visual arm, new situation and wording, never the text."""
    clones = []
    for _ in range(n):
        clones.append({"template": reel.get("template"),
                       "visual_arm": reel.get("visual_arm"),
                       "clone_of": reel.get("media_id") or "",
                       "note": "new situation, new wording, same structure"})
    return clones
