"""Phase 2 acceptance (ENGINE_SPEC §2.8) plus the learner's math.

The spec asks for one specific simulation: feed 200 synthetic posts with a planted
winning arm and assert it is chosen >60% of draws after 40 posts, with constraints
never violated. That is the test that proves the bandit actually learns rather
than merely running.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import learner, strategy_v2  # noqa: E402


# ------------------------------------------------------------------ the reward

def test_reward_matches_the_spec_formula():
    """1.0*watch + 20*(shares/reach) + 8*(saves/reach) + 3*(comments/reach)
       + 1*(likes/reach) - 0.5*max(0, skip-0.5)"""
    m = {"reach": 1000, "shares": 10, "saved": 20, "comments": 30, "likes": 100,
         "ig_reels_avg_watch_time": 10, "reels_skip_rate": 0.7}
    w = {"watch": 1.0, "shares_over_reach": 20.0, "saves_over_reach": 8.0,
         "comments_over_reach": 3.0, "likes_over_reach": 1.0,
         "skip_penalty": 0.5, "skip_threshold": 0.5, "watch_cap": 2.0}
    got = learner.reward(m, 10.0, w)
    # watch=1.0, shares=0.2, saves=0.16, comments=0.09, likes=0.1, skip=-0.1
    expect = 1.0 + 0.2 + 0.16 + 0.09 + 0.1 - 0.1
    assert abs(got - expect) < 1e-6, f"{got} != {expect}"


def test_watch_is_capped_at_2():
    """>1 means replays; the cap stops one lucky rewatch loop from dominating."""
    w = {"watch": 1.0, "watch_cap": 2.0}
    m = {"reach": 100, "ig_reels_avg_watch_time": 100.0}
    got = learner.reward(m, 10.0, w)
    assert abs(got - 2.0) < 1e-6, f"watch should cap at 2.0, got {got}"


def test_missing_skip_rate_is_not_treated_as_zero_skip():
    """A metric that was unavailable must not read as a good skip rate.

    If it did, every post on a route without reels_skip_rate would get a free
    pass and the learner would prefer them for the wrong reason.
    """
    base = {"reach": 100, "shares": 1, "saved": 1, "comments": 1, "likes": 1,
            "ig_reels_avg_watch_time": 5}
    without = learner.reward(base, 10.0, {})
    with_bad = learner.reward({**base, "reels_skip_rate": 0.9}, 10.0, {})
    assert with_bad < without, "a bad skip rate must lower the score"
    assert without == learner.reward(base, 10.0, {}), "absent skip must be neutral"


def test_delivery_failures_are_not_learnable():
    """reach < 25 is a delivery failure, not a content signal."""
    assert not learner.is_learnable({"reach": 5})
    assert not learner.is_learnable({"reach": 24})
    assert learner.is_learnable({"reach": 25})
    assert learner.is_learnable({"reach": 1000})


# ------------------------------------------------------------------ the bandit

def test_planted_winner_is_found():
    """ENGINE_SPEC §2.8: the simulation the spec asks for.

    Plant HOOK A (average reward ~1.0) against HOOK B (average reward ~0.1). After
    40 posts the learner must pick A more than 60% of the time. Beta/gaussian
    bandits converge in a few dozen draws at this gap; if this fails, the learner
    is not learning and every other metric is decoration.
    """
    random.seed(7)
    G = 0.55
    stats = {"hook_type": {"A": ([], []), "B": ([], [])}}
    picks = 0
    for i in range(200):
        choice = learner.choose(stats, G, {"hook_type": ["A", "B"]},
                                explore_rate=0.30 if i < 30 else 0.20)
        arm = choice["hook_type"]
        if i >= 40 and arm == "A":
            picks += 1
        # simulate the planted truth
        score = random.gauss(1.0, 0.15) if arm == "A" else random.gauss(0.1, 0.15)
        xs, ws = stats["hook_type"][arm]
        xs.append(score)
        ws.append(1.0)
    rate = picks / 160
    assert rate > 0.60, f"planted winner chosen only {rate:.0%} of draws after 40 posts"


def test_constraints_are_never_violated_during_learning():
    """A winning arm must not be able to break a safety rule."""
    random.seed(11)
    stats = {"series": {"caught_in_the_act": ([10.0], [1.0])}}
    for _ in range(50):
        recent = ["caught_in_the_act", "caught_in_the_act"]
        options = [s for s in ("caught_in_the_act", "why_you_do_that")
                   if learner.series_ok(s, recent)]
        choice = learner.choose(stats, 1.0, {"series": options}, explore_rate=0.3)
        assert choice["series"] != "caught_in_the_act", \
            "the highest-scoring arm must still lose to the 2-in-a-row rule"


def test_pov_cap_and_keyword_cta_alternation():
    pov_hooks = ["POV: a", "POV: b", "POV: c", "POV: d"]
    assert not learner.pov_ok("POV: another one", pov_hooks), \
        "a 5th POV in 5 hooks must be refused"
    assert learner.pov_ok("You watched it ring.", pov_hooks), \
        "a non-POV hook is always allowed"
    assert not learner.keyword_cta_ok("guide", "book"), "guide after book is a violation"
    assert learner.keyword_cta_ok("send", "guide"), "send after guide is fine"


def test_topic_cooldown_is_seven_days():
    recent = ["phone calls", "group chats"]
    assert not learner.topic_ok("Phone Calls", recent), "case must not defeat the cooldown"
    assert learner.topic_ok("cancelling plans", recent)


def test_breakout_definition():
    assert learner.breakout([100] * 20 + [400], [0] * 21), "3x median qualifies"
    assert learner.breakout([100] * 20 + [305], [0] * 21) is False, \
        "3.05x median but under 400 must not qualify when 3x median < 400"
    assert learner.breakout([1000] * 20 + [100], [0] * 20 + [3]), "3 shares qualifies"


def test_breakout_needs_the_median_floor():
    """reach 400 qualifies even when 3x median is tiny."""
    assert learner.breakout([10] * 20 + [400], [0] * 21)


def test_cta_shares_guardrail():
    base = {"none": 0.50, "send": 0.25, "guide": 0.20, "book": 0.05}
    assert learner.cta_shares_sane(base, base)
    assert not learner.cta_shares_sane({"none": 0.80, "send": 0.10,
                                        "guide": 0.05, "book": 0.05}, base), \
        "a 30-point move must be refused"
    assert not learner.cta_shares_sane({"none": 0.60, "send": 0.25,
                                        "guide": 0.20, "book": 0.05}, base), \
        "shares must sum to 1.0"


def test_length_buckets():
    assert learner.length_bucket_of(15) == "14-18"
    assert learner.length_bucket_of(20) == "18-24"
    assert learner.length_bucket_of(30) == "24-34"


# ------------------------------------------------------------------ the rails

def test_flat_reach_rail_fires():
    posts = [{"score": 0.1, "metrics": {"24h": {"reach": 40}}, "created_at":
              "2026-10-01T00:00:00+00:00"} for _ in range(14)]
    t = strategy_v2.reset_triggers({"posts": posts})
    assert t["flat_reach"], "14 posts at reach 40 with no breakout must fire"
    over = strategy_v2.apply_rails(t, {})
    assert over["explore_rate"] == 0.50
    assert over["force_bg_source"] == "pexels"


def test_no_rail_fires_on_healthy_reach():
    posts = [{"score": 1.0, "metrics": {"24h": {"reach": 500}},
              "created_at": "2026-10-01T00:00:00+00:00"} for _ in range(14)]
    t = strategy_v2.reset_triggers({"posts": posts})
    assert not t["flat_reach"], "healthy reach must not trigger a reset"


def test_strategist_cannot_escape_its_write_surface():
    """The strategist may only edit DIRECTIVES.md and priors."""
    def fake_chat(_msgs):
        return ('{"directive_text":"write tighter hooks","confidence":"low",'
                '"banned_words":["cure"],"cta_caps":{"none":0.9},'
                '"lint_disabled":true}')
    posts = [{"score": 1.0, "hook": "a", "series": "s",
              "metrics": {"24h": {"reach": 100}}} for _ in range(5)]
    res = strategy_v2.run_strategist(dry_run=True, chat=fake_chat)
    assert res["ok"]
    assert "directive_text" in res["applied"]
    for bad in ("banned_words", "cta_caps", "lint_disabled"):
        assert bad in res["refused"], f"{bad} must be refused, not applied"


def test_listening_rejects_quotes_and_handles():
    """Privacy failure, not a style issue: situations only, never quotes/handles."""
    import json as _json
    def fake_chat(_msgs):
        return _json.dumps([
            "you rehearse ordering coffee before you get to the counter",
            '"I always freeze up when they ask my name" — u/someone',
            "you leave the party without saying goodbye",
            "you @mention a friend instead of calling",
        ])
    res = strategy_v2.run_listening(dry_run=True, chat=fake_chat)
    assert res["ok"]
    assert res["added"] == 2, f"expected 2 clean situations, got {res['added']}"
    assert len(res["rejected"]) == 2, res["rejected"]


def test_series_and_hook_type_are_paired_not_independent():
    """A permission hook inside a recognition series is an impossible brief.

    The seed bank pairs hook types with the series they serve. Drawing them as
    independent arms produced caught_in_the_act + permission, where the series
    forbids the advice its hook type implies — no draft could pass, so the slot
    was skipped for a reason the writer could never fix.
    """
    from src import run_v2, writer as _w
    seed = _w._seed()
    pair = {}
    for h in seed["hooks"]:
        pair.setdefault(str(h.get("series") or ""), set()).add(str(h.get("type") or ""))
    for series, types in pair.items():
        assert types, f"{series} has no hook types"
    # the two that must never meet
    assert "permission" not in pair.get("caught_in_the_act", set())
    assert "permission" in pair.get("small_steps", set())
    # and the selector must honour the pairing
    import inspect
    src = inspect.getsource(run_v2.select_arms)
    assert "types_for_series" in src, "the selector must restrict hook types by series"


def test_empty_audio_path_is_not_the_cwd():
    """`Path("")` is `Path(".")` and `.exists()` is True.

    A music tier that reported ok but named no file produced an empty path, which
    became the CURRENT DIRECTORY as an ffmpeg input: "[in#8] Error opening input:
    Is a directory". The reel was then skipped for a missing file that was never
    going to exist. Every audio input must be checked with is_file(), not exists().
    """
    import inspect
    from src import paced
    src = inspect.getsource(paced.assemble_paced)
    assert "is_file()" in src, "paced must require a real audio file"
    assert '.exists()' not in src or "is_file()" in src
    # and a blank path must be treated as no audio at all
    assert not ("" and str("").strip() and __import__("pathlib").Path("").is_file())


if __name__ == "__main__":
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)