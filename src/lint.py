#!/usr/bin/env python3
"""BEAST ENGINE v2 - content lint + reveal timing.  stdlib only, Python 3.9+.

Drop into src/ and call lint() from the QA gate BEFORE rendering, and
reveal_schedule() from build_video to time each line.

    python3 lint.py --selftest
    python3 lint.py reels.json [recent_hooks.json]

Reel schema (one object or a list of them):
{
  "series": "caught_in_the_act",
  "hook_type": "accusation_tender",
  "hook": "You didn't miss the call. You watched it ring.",
  "beats": ["...", "...", "closer that echoes the hook"],
  "cta_type": "none | send | guide | book",
  "cta_text": "",
  "caption": "...",
  "hashtags": ["#socialanxiety", ...]
}
"""
import json
import os
import re
import sys

# ---- timing -----------------------------------------------------------------
READ_SEC_PER_WORD = 0.34   # ~2.9 words/s: comfortable on-screen reading speed
MIN_GAP = 1.6              # a line stays up at least this long
END_HOLD = 2.0             # everything visible, then loop
MIN_DUR, MAX_DUR = 14.0, 34.0

# ---- rules ------------------------------------------------------------------
BANNED = [
    r"\bcure[ds]?\b", r"\bheal(s|ed|ing)?\b", r"\bpermanent(ly)?\b",
    r"\bguarantee[ds]?\b", r"\beffortless(ly)?\b", r"\bproven\b",
    r"\bin \d+ (days|weeks|months)\b", r"\bget rid of\b", r"\bclinical(ly)?\b",
    r"\bdiagnos\w*\b", r"\btrauma\w*\b", r"\bjourney\b", r"\btoxic\b",
    r"\blink in bio\b", r"\b100%\b",
]
ADVICE = re.compile(
    r"^(try|just|start|stop|remember|make sure|you should|you need to|don't forget)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
SECOND_PERSON = re.compile(r"\b(you|your|you're|you've|you'll|yours)\b", re.I)
SEO_KEYWORDS = ["social anxiety", "anxious", "overthinking", "introvert", "small talk",
                "panic", "shy", "group chat", "phone call"]
CTA_RULES = {   # type -> required token in cta_text (case-insensitive)
    "none": None, "send": "send", "guide": "GUIDE", "book": "BOOK",
}
POV_CAP = 0.20     # max share of recent hooks that may start with "POV"


def words(s):
    return re.findall(r"[A-Za-z0-9'’\-]+", s)


def reveal_schedule(lines):
    """Return (start_times, duration). Line i appears when the viewer has had time
    to read line i-1. Duration includes a short end hold, clamped to MIN_DUR."""
    t, starts = 0.0, []
    for i, line in enumerate(lines):
        starts.append(round(t, 2))
        t += max(MIN_GAP, READ_SEC_PER_WORD * len(words(line)))
    return starts, round(max(MIN_DUR, t + END_HOLD), 2)


def _grams(s, n):
    w = [x.lower() for x in words(s)]
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def too_similar(hook, recent):
    for r in recent:
        if _jaccard(_grams(hook, 3), _grams(r, 3)) >= 0.5:
            return r
        if _jaccard({w.lower() for w in words(hook)}, {w.lower() for w in words(r)}) >= 0.75:
            return r
    return None


def lint_hook(hook):
    errs = []
    n = len(words(hook))
    if not 3 <= n <= 9:
        errs.append(f"hook is {n} words (need 3-9)")
    if "!" in hook:
        errs.append("exclamation mark")
    if EMOJI.search(hook) or "#" in hook:
        errs.append("emoji/hashtag on screen")
    for p in BANNED:
        if re.search(p, hook, re.I):
            errs.append(f"banned phrase /{p}/ in hook")
    return errs


def lint(reel, recent_hooks=(), allow_advice=False):
    """Return a list of error strings. Empty list == pass."""
    errs = []
    hook = reel.get("hook", "").strip()
    beats = [b.strip() for b in reel.get("beats", []) if b.strip()]
    screen = [hook] + beats
    text = " ".join(screen)

    errs += lint_hook(hook)
    total = sum(len(words(x)) for x in screen)
    if not 30 <= total <= 60:
        errs.append(f"{total} on-screen words (need 30-60)")
    if not 5 <= len(screen) <= 8:
        errs.append(f"{len(screen)} lines (need 5-8 including hook)")
    if "!" in text:
        errs.append("exclamation mark")
    if EMOJI.search(text) or "#" in text:
        errs.append("emoji/hashtag on screen")
    for p in BANNED:
        if re.search(p, text, re.I):
            errs.append(f"banned phrase /{p}/")
    if not allow_advice:
        for b in beats:
            if ADVICE.match(b):
                errs.append(f"advice-style line in a recognition reel: '{b[:40]}'")
    if not SECOND_PERSON.search(text):
        errs.append("no second person ('you') anywhere")

    recent = list(recent_hooks)[-10:]
    if hook.upper().startswith("POV"):
        pov = sum(1 for h in recent if h.upper().startswith("POV"))
        if (pov + 1) / (len(recent) + 1) > POV_CAP:
            errs.append("POV hook cap exceeded (max 20% of recent hooks)")
    dup = too_similar(hook, list(recent_hooks)[-60:])
    if dup:
        errs.append(f"hook too similar to recent hook: '{dup[:50]}'")

    _, dur = reveal_schedule(screen)
    if dur > MAX_DUR:
        errs.append(f"duration {dur}s exceeds {MAX_DUR}s")

    cta_type = reel.get("cta_type", "none")
    cta = reel.get("cta_text", "").strip()
    if cta_type not in CTA_RULES:
        errs.append(f"unknown cta_type '{cta_type}'")
    else:
        need = CTA_RULES[cta_type]
        if need is None and cta:
            errs.append("cta_text must be empty when cta_type is none")
        if need and need.lower() not in cta.lower():
            errs.append(f"cta_text must contain '{need}'")
        if need == "GUIDE" and "GUIDE" not in cta:
            errs.append("keyword GUIDE must be uppercase")
        if need == "BOOK" and "BOOK" not in cta:
            errs.append("keyword BOOK must be uppercase")
        if len(words(cta)) > 12:
            errs.append("cta_text over 12 words")
        for p in BANNED:
            if cta and re.search(p, cta, re.I):
                errs.append(f"banned phrase /{p}/ in cta")

    cap = reel.get("caption", "")
    if not cap:
        errs.append("missing caption")
    else:
        if len(cap) > 800:
            errs.append("caption over 800 chars")
        if not any(k in cap[:125].lower() for k in SEO_KEYWORDS):
            errs.append("no search keyword in first 125 chars of caption")
        for p in BANNED:
            if re.search(p, cap, re.I):
                errs.append(f"banned phrase /{p}/ in caption")
    tags = reel.get("hashtags", [])
    if not 3 <= len(tags) <= 5:
        errs.append(f"{len(tags)} hashtags (need 3-5)")
    if any(not t.startswith("#") or t != t.lower() for t in tags):
        errs.append("hashtags must start with # and be lowercase")
    return errs


# ---- CLI / self-test ----------------------------------------------------------
def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _selftest(here):
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("  ok   " if cond else "  FAIL ") + name)
        fails += 0 if cond else 1

    reels = _load(os.path.join(here, "sample_reels.json"))
    recent = []
    for r in reels:
        e = lint(r, recent)
        check(f"sample '{r['hook'][:36]}...' passes", not e)
        if e:
            print("       ", e)
        _, dur = reveal_schedule([r["hook"]] + r["beats"])
        check(f"  duration {dur}s within {MIN_DUR}-{MAX_DUR}s", MIN_DUR <= dur <= MAX_DUR)
        recent.append(r["hook"])

    bank = _load(os.path.join(here, "hooks_seed.json"))["hooks"]
    bad = [(h["text"], lint_hook(h["text"])) for h in bank if lint_hook(h["text"])]
    check(f"all {len(bank)} seed hooks pass hook rules", not bad)
    for b in bad:
        print("       ", b)
    types = {h["type"] for h in bank}
    check(f"12 hook types covered ({len(types)})", len(types) == 12)

    good = reels[0]
    mut = lambda **kw: {**good, **kw}   # noqa: E731
    check("rejects 12-word hook", bool(lint(mut(hook="You never answer the phone because you are scared of your own voice"), [])))
    check("rejects 'heal' claim", any("banned" in x for x in lint(mut(beats=good["beats"][:-1] + ["This will heal you."]), [])))
    check("rejects advice line", any("advice" in x for x in lint(mut(beats=["Try breathing slowly."] + good["beats"][1:]), [])))
    check("rejects 'link in bio' CTA", bool(lint(mut(cta_type="book", cta_text="Link in bio for the BOOK."), [])))
    check("rejects guide CTA without keyword", bool(lint(mut(cta_type="guide", cta_text="Comment below."), [])))
    pov = mut(hook="POV: you watch it ring until it stops.")
    check("rejects POV when recent hooks are all POV", bool(lint(pov, ["POV: a", "POV: b", "POV: c"])))
    check("rejects near-duplicate hook", bool(lint(good, [good["hook"]])))
    check("rejects too few hashtags", bool(lint(mut(hashtags=["#socialanxiety"]), [])))
    s, d = reveal_schedule([good["hook"]] + good["beats"])
    check("reveal times strictly increase", all(b > a for a, b in zip(s, s[1:])))
    print("\nSELFTEST", "PASSED" if not fails else f"FAILED ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        sys.exit(_selftest(here))
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    data = _load(sys.argv[1])
    data = data if isinstance(data, list) else [data]
    recent = _load(sys.argv[2]) if len(sys.argv) > 2 else []
    bad = 0
    for r in data:
        e = lint(r, recent)
        s, d = reveal_schedule([r.get("hook", "")] + r.get("beats", []))
        print(("PASS" if not e else "FAIL"), f"{d}s", "|", r.get("hook", "")[:60])
        for x in e:
            print("   -", x)
        bad += bool(e)
        recent.append(r.get("hook", ""))
    sys.exit(1 if bad else 0)