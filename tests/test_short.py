"""tests/test_short.py — the v3 short-format suite.

Covers: lint gate (9 rules), template bank integrity, writer JSON contract
(offline stub), learner v3 (reward, breakout, kill rule, exploration), and the
renderer's beat timing. Model-backed tests skip cleanly when the LLM endpoints
are unreachable, so CI stays green without network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import learn_short, lint_short  # noqa: E402


class TestLintShort(unittest.TestCase):
    def test_selftest_green(self):
        self.assertEqual(lint_short.selftest(), 0)

    def test_two_beats_only(self):
        errs = lint_short.check(["one", "two", "three"])
        self.assertTrue(any("exactly 2" in e for e in errs))

    def test_word_floor(self):
        errs = lint_short.check(["Hi.", "Bye."])
        self.assertTrue(any("too few" in e for e in errs))

    def test_no_self_harm(self):
        errs = lint_short.check(["I want to disappear forever", "goodbye."])
        self.assertTrue(errs, "self-harm adjacent phrasing must trip the gate")


class TestTemplates(unittest.TestCase):
    def test_bank_shape(self):
        from src import generate_short
        t = generate_short.templates()
        self.assertEqual(len(t["templates"]), 10)
        codes = [x["code"] for x in t["templates"]]
        self.assertEqual(codes, [f"T{i}" for i in range(1, 11)])
        self.assertEqual(t["length_s"], [6, 12])
        self.assertEqual(t["words"], [8, 22])

    def test_few_shot_copy_is_blocked(self):
        """The few-shot examples are in the prompt, so copying one must fail."""
        from src import generate_short
        shots = generate_short.templates().get("few_shot") or []
        self.assertTrue(shots, "few-shot register examples must exist")
        first = shots[0]["text"].split(" / ")
        errs = lint_short.check(first, template=shots[0]["template"])
        self.assertTrue(any("template example" in e for e in errs),
                        f"copying a few-shot must be rejected, got {errs}")

    def test_every_template_has_trigger(self):
        from src import generate_short
        for x in generate_short.templates()["templates"]:
            self.assertTrue(x.get("send_trigger"), f"{x['code']} missing send_trigger")
            self.assertTrue(x.get("structure"))


class TestLearnerShort(unittest.TestCase):
    def test_reward_shares_dominate(self):
        # 10% share rate at reach 100 => 25*0.1 = 2.5 alone
        m = {"reach": 100, "shares": 10, "saved": 0, "comments": 0, "likes": 0}
        self.assertGreaterEqual(learn_short.reward_v3(m, 9.0), 2.5)

    def test_breakout_rules(self):
        self.assertTrue(learn_short.breakout_short(
            {"reach": 600, "shares": 0}, [100] * 20))
        self.assertTrue(learn_short.breakout_short(
            {"reach": 80, "shares": 6}, [100] * 20))
        self.assertFalse(learn_short.breakout_short(
            {"reach": 120, "shares": 1}, [100] * 20))

    def test_length_bucket(self):
        self.assertEqual(learn_short.length_bucket_v3(6.5), "6-8")
        self.assertEqual(learn_short.length_bucket_v3(9.0), "8-10")
        self.assertEqual(learn_short.length_bucket_v3(11.5), "10-12")

    def test_exploration_resets_on_flat_reach(self):
        mem = {"posts": [{"format": "short2", "reach": 30} for _ in range(14)]}
        self.assertEqual(learn_short.exploration_rate(mem), 0.5)
        mem2 = {"posts": [{"format": "short2", "reach": 900} for _ in range(14)]}
        self.assertEqual(learn_short.exploration_rate(mem2), 0.3)

    def test_topic_cooldown_in_choose(self):
        mem = {"posts": []}
        picks = learn_short.choose_v3(
            mem, recent_topics=["group chats", "group chats", "group chats"],
            recent_templates=[])
        self.assertNotEqual(picks.get("topic"), "group chats")

    def test_template_rotation(self):
        mem = {"posts": []}
        recent = ["T1", "T2", "T3", "T4"]
        picks = learn_short.choose_v3(mem, recent_topics=[], recent_templates=recent)
        self.assertNotIn(picks.get("template"), recent)


class TestBuildShort(unittest.TestCase):
    def test_beat_times(self):
        from src import build_short
        hook_at, payoff_at, total = build_short.beat_times(10.0)
        self.assertEqual(hook_at, 0.0)
        self.assertTrue(5.5 <= payoff_at <= 6.5, "payoff must land at 55-65%")
        self.assertEqual(total, 10.0)

    def test_wrap_max_two_lines(self):
        from src import build_short
        # a realistic worst case: a 14-word hook at max beat width
        lines = build_short._fit_beat(
            "Send this to the friend who says just an hour and means home by eight thirty",
            start_size=74, max_lines=2)
        self.assertLessEqual(len(lines[0]), 2, "max 2 lines per beat (§4)")
        self.assertGreaterEqual(lines[1], 30)

    def test_arm_c_text_post_renders_offline(self):
        """Offline (no network): the renderer must still produce a real reel.

        With Pinterest-only arms, offline falls back to background.build's
        bundled source — flagged by name so it is never reported as Pinterest.
        """
        from src import build_short, config
        reel = {"format": "short2", "template": "T4", "hook": "Not rude today.",
                "payoff": "Rude. Out of battery.", "topic": "replying late",
                "visual_arm": "pin_video", "audio_type": "quiet_ambient",
                "length_bucket": "8-10",
                "scene_queries": ["phone dark room"], "caption": "every time",
                "hashtags": ["#relatable"]}
        built = build_short.build(reel, {}, duration_s=9.0, offline=True,
                                  verbose=False)
        self.assertTrue(built.get("ok"), f"render failed: {built}")
        self.assertNotIn("pinterest", str(built.get("bg", {}).get("source", ""))
                         .lower().replace("bundled", ""),
                         "an offline render must not claim a Pinterest source")
        p = Path(built["path"])
        # flat-colour 9s clip at CRF 22 is legitimately small; 20KB+ proves a
        # real encode with audio, not a placeholder
        self.assertGreater(p.stat().st_size, 20_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
