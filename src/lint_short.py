"""lint_short.py — the v3 short-reel quality + safety gate.

§9 step 2 of the v3 plan. Every reel passes through here BEFORE the critic sees it,
and again (as a hard re-check) after the critic, because the critic cannot waive a
code rule.

Enforces, from the v3 plan:
  - 8-22 words total across hook + payoff (§3.2)
  - hook <= 14 words, payoff <= 12 words (one idea each)
  - hook on screen at frame 0 (structural: the renderer reads beats[0])
  - payoff at ~55-65% of runtime (the renderer computes timing; lint checks the
    schedule the renderer will use)
  - banned claims (§3.3): cure/heal/fix/permanent/guarantee/diagnose/trauma/clinical/
    suicide/self-harm/medication — humour targets behaviour, never the person
  - never "you have social anxiety" (§3.3)
  - POV cap: <= 1 in any 5 consecutive reels (§3.2 T10)
  - loop: the payoff must share a content word with the hook so the loop closes
  - no copied phrases: >=6-word overlap with a template example or a research
    caption is rejected (templates are structures, never wording)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES = json.loads(Path(_HERE, "templates.json").read_text(encoding="utf-8"))

BANNED = [
    "cure", "heal", "healing", "fix you", "fixed me", "permanent", "guarantee",
    "diagnose", "diagnosed", "diagnosis", "trauma", "clinical", "therapy will",
    "medication", "meds", "antidepressant", "suicide", "suicidal", "self-harm",
    "kill myself", "killing myself", "end it all", "100%", "link in bio",
    "proven", "effortless", "in 7 days", "in 30 days",
]
# "you have social anxiety" — the §3.3 phrasing ban (the DIAGNOSIS framing).
YOU_HAVE = re.compile(r"\byou have (social )?anxiety\b|\byou'?re (so )?socially anxious\b",
                      re.IGNORECASE)
POV_RE = re.compile(r"\bPOV\b", re.IGNORECASE)


def words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", str(text).lower())


def _ngrams(words_list: list, n: int) -> set:
    return {" ".join(words_list[i:i + n]) for i in range(len(words_list) - n + 1)}


def _content_words(text: str) -> list:
    stop = {"a", "an", "the", "to", "of", "in", "on", "at", "is", "are", "was",
            "be", "and", "but", "or", "so", "it", "its", "i", "me", "my", "you",
            "your", "just", "not", "with", "for", "when", "that", "this"}
    return [w for w in words(text) if w not in stop]


def overlap(a: str, b: str, n: int = 5) -> bool:
    """True if any n-CONSECUTIVE-CONTENT-WORD run appears in both strings.

    Function words are excluded BEFORE the n-gram scan. T3's frame "send this to
    the friend who" is itself six words, so a raw 6-gram check rejects every T3
    reel against T3's own example — the frame is the STRUCTURE the plan tells us
    to reuse, and only the wording may not be copied.
    """
    wa, wb = _content_words(a), _content_words(b)
    if len(wa) < n or len(wb) < n:
        return False
    return bool(_ngrams(wa, n) & _ngrams(wb, n))


def template_examples() -> list:
    """Every example the writer must not copy: template skeletons AND few-shots.

    The few-shot register examples are in the prompt, so they must be in the
    copy check too — otherwise the model's easiest way to satisfy the prompt is
    to parrot the examples, which is exactly the originality failure the critic
    keeps rejecting.
    """
    t = _TEMPLATES
    return ([x["example"] for x in t["templates"]]
            + [x["text"] for x in t.get("few_shot") or []])


def check(beats: list, template: str = "", topic: str = "", recent_beats=None,
          recent_pov_count: int = 0) -> list:
    """Lint one short reel. Returns a list of violations (empty = pass).

    beats: [hook, payoff] — exactly two lines.
    recent_pov_count: POV reels among the last 4 (the 5th window is this one).
    """
    errs = []
    hook = str(beats[0]) if beats else ""
    payoff = str(beats[1]) if len(beats) > 1 else ""
    full = " ".join(str(b) for b in beats)

    if len(beats) != 2:
        errs.append(f"need exactly 2 beats (hook, payoff), got {len(beats)}")
        return errs

    # §3.2 word budgets
    hw, pw = len(words(hook)), len(words(payoff))
    total = hw + pw
    if total < 8:
        errs.append(f"too few words: {total} (need 8-22)")
    if total > 22:
        errs.append(f"too many words: {total} (need 8-22)")
    if hw > 14:
        errs.append(f"hook too long: {hw} words (max 14)")
    if pw > 12:
        errs.append(f"payoff too long: {pw} words (max 12)")

    # §3.3 banned claims + phrasing
    low = full.lower()
    for term in BANNED:
        if term in low:
            errs.append(f"banned: '{term}'")
    if YOU_HAVE.search(full):
        errs.append("never 'you have (social) anxiety' - humour about behaviour, "
                    "never a diagnosis")

    # §3.3/§3.2: no 5+-content-word copy from the template examples or recent reels
    for ex in template_examples():
        if overlap(full, ex, 5):
            errs.append("copies a template example's wording - write new words")
            break
    for recent in (recent_beats or []):
        if overlap(full, str(recent), 5):
            errs.append("near-duplicate of a recent reel (5+-word overlap)")
            break

    # T10 POV cap: 1 in 5
    if template == "T10" or POV_RE.search(hook):
        if recent_pov_count >= 1:
            errs.append("POV cap: 1 in 5 reels (a POV reel is in the last 4)")

    # loop: payoff shares a content word with the hook
    stop = {"a", "an", "the", "to", "of", "in", "on", "at", "is", "are", "was",
            "be", "and", "but", "or", "so", "it", "its", "i", "me", "my", "you",
            "your", "just", "not", "with", "for", "when", "that", "this"}
    if set(words(hook)) - stop and not (set(words(hook)) & set(words(payoff))):
        errs.append("loop: payoff shares no word with the hook - the loop won't close")

    return errs


def payoff_start(dur_s: float, words_total: int = 0) -> float:
    """Payoff reveal time: 55-65% of runtime (§3.2). Hook gets the front."""
    return round(float(dur_s) * 0.60, 2)


def selftest() -> int:
    fails = 0

    def ok(name, cond):
        nonlocal fails
        if not cond:
            fails += 1
        print(("PASS " if cond else "FAIL ") + name)

    ok("clean reel passes", not check(
        ["Send this to the friend who types 'haha' and means 'I'm fine'.",
         "She typed 'haha' and sat there shaking."]))
    ok("too many words", bool(check(
        ["Send this to the friend who has been typing and deleting the same message "
         "to their group chat since seven and still has not sent anything at all",
         "The chat is silent."])))
    ok("banned claim rejected", bool(check(
        ["This cures social anxiety forever", "Guaranteed."])))
    ok("diagnosis phrasing rejected", bool(check(
        ["You have social anxiety", "That's why you're quiet."])))
    ok("template example copy rejected", bool(check(
        ["People think I'm ignoring them.", "I'm drafting a reply. It's been three hours."])))
    ok("recent near-duplicate rejected", bool(check(
        ["Not rude, just out of battery.", "Rude."],
        recent_beats=[["Not rude, just out of battery.", "Rude."]])))
    ok("POV cap fires when recent POV exists", bool(check(
        ["POV: the quiet friend plans it all", "The quiet friend cancels."],
        template="T10", recent_pov_count=1)))
    ok("POV allowed with no recent POV", not check(
        ["POV: the quiet friend plans it all", "The quiet friend cancels."],
        template="T10", recent_pov_count=0))
    ok("payoff_start inside 55-65%", 6.3 <= payoff_start(11.0) <= 7.1)
    print("SELFTEST " + ("PASSED" if not fails else "FAILED"))
    return fails


if __name__ == "__main__":
    raise SystemExit(1 if selftest() else 0)
