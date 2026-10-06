"""confession.py — THE CONFESSION ENGINE (STRATEGY.md, ENGINE_SPEC.md, PROMPTS.md).

One call writes one draft from one scene row. A second call rejects it. The
bans are enforced in CODE, not only asked for in the prompt, because a prompt
cannot guarantee a negative — and every hard ban here is a thing that has
already shipped on this account and cost it reach ("POV:" as a label, a
comment-keyword CTA, the phrase "you are not alone").

WHAT THIS SPEC ASKS FOR THAT THIS STACK CANNOT DO — named, not papered over:

  * ENGINE_SPEC wants the writer and the critic to be DIFFERENT MODEL FAMILIES
    ("DeepSeek ... DeepSeek or Meta ... a different model from the writer").
    The LLM service this stack owns exposes exactly one model id
    (verified: GET /v1/models -> ["meta-ai-thinking"]). There is no writer
    model and no separate critic model to route to, and no DeepSeek key exists
    anywhere in this environment. So the separation is reproduced as far as
    this stack allows: two independent calls, the critic on a colder
    temperature, and a code-enforced ban list the critic cannot waive. That is
    weaker than two families, and it is the honest limit of the resource.
  * engine-spec's "search mode" is the same single model; the weekly miner
    therefore runs as a prompt variant, not a search tier.
  * The word "thinking" in the model id is upstream behaviour, not a mode this
    code selects.

The two hard bands that ARE enforced in code and cannot be argued with:
  1. a literal ban list (substring), and
  2. the SHAPE of the last line — it must be a rename, "You're not X. You're Y."
"""
from __future__ import annotations

import json
import re
import time

from . import config, language, llm

# ------------------------------------------------------------------ spec text
# STRATEGY.md / PROMPTS.md are the source of truth and are read from disk so a
# spec edit never means editing code. The fallbacks below keep a missing file
# from silently disabling the writer.

STRATEGY_PATH = config.ROOT / "docs" / "strategy" / "STRATEGY.md"
PROMPTS_PATH = config.ROOT / "docs" / "strategy" / "PROMPTS.md"


def _spec_prompts() -> dict:
    """The PROMPTS.md sections as a dict. Falls back to embedded text."""
    try:
        raw = PROMPTS_PATH.read_text(encoding="utf-8")
    except OSError:
        raw = ""
    return {"raw": raw}


def _section(raw: str, heading: str) -> str:
    """Text under '## <heading>' up to the next '## '."""
    if not raw:
        return ""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)",
                  raw, re.S | re.M | re.I)
    return (m.group(1).strip() if m else "")


# ------------------------------------------------------------------- hard bans

# ENGINE_SPEC.md "Hard bans". Substring, case-insensitive, matched against the
# ON-SCREEN text. This list is the account's scar tissue: each entry is
# something that already shipped or was explicitly banned by the strategy.
BANNED_SUBSTRINGS = (
    "pov:", "pov ", "comment ", "keyword", "type the word", "tag a friend",
    "follow for part", "link in bio", "you are not alone", "you're not alone",
    "just be yourself", "it gets better", "you are enough", "you're enough",
    "social anxiety", "hidden program", "reticular", " ras", "inner child",
    "vibration", "alignment", "healing journey", "diagnos", "medication",
    "therapy", "therapist",
    "suicide", "self-harm", "self harm", "kill myself",
    # A tip in disguise. These are PHRASES, not stems: the spec bans a
    # "breathing instruction", but its own gold set ships "the aisle it could
    # breathe in" — a description, not an instruction. Banning the bare stem
    # "breathe" rejected a gold line, so only the instructional forms are
    # banned here.
    "breathing exercise", "breathing technique", "take a deep breath",
    "breathe deeply", "just breathe", "remember to breathe", "try breathing",
    "meditate", "grounding exercise", "in through your nose",
)

BANNED_LAST_LINE_TAILS = (
    "it gets better", "you are enough", "you're enough", "you are not alone",
    "you're not alone", "just be yourself", "it's okay", "its okay",
    "you are loved", "you're loved", "you matter", "be kind to yourself",
)

# The verdicts a viewer actually holds against themselves (PROMPTS.md).
VERDICTS = ("rude", "flaky", "fake", "cold", "boring", "too much", "broken",
            "ungrateful", "dramatic", "weak", "slow", "quiet", "awkward",
            "weird", "annoying", "useless", "dull", "liar", "obsessive",
            "bad at this", "stuck up", "stuck-up", "cold", "fake friend",
            "bad friend", "ridiculous", "stupid", "a burden", "selfish")

# The rename shape, validated against PROMPTS.md's OWN gold set rather than
# from a literal reading of "You're not X. You're Y."
#
# A strict `You're not X. You're Y.` regex REJECTED the spec's own quality bar:
# every gold line renames in the second clause with a different opening —
#   "You're not rude. You were trying to arrive in the room before your voice did."
#   "You're not quiet. You spent the words before they left your hands."
#   "You're not fake. The smile was the only part of you that had permission to leave."
#   "You're not flaky. Relief got there first. Guilt was just late."
# A gate written from the literal pattern aborts every valid draft, which is the
# "spec contradicts itself" failure mode. What is actually enforceable, and what
# all four share, is: the line OPENS by refusing a verdict ("You're not X"), and
# it does not stop there — a second clause follows that renames the behaviour.
RENAME_RE = re.compile(
    r"^\s*you\s*(?:'re| are)\s+not\s+(?P<verdict>[^.,;!?]+)[.,;!]\s*(?P<mech>.+?)\s*$",
    re.I | re.S)

# The spec says 5 to 7 short lines. Its OWN gold set contains a 3-line and
# several 4-line examples, so a hard 5-floor rejects the quality bar it was
# written from. What the spec actually needs is enough lines to require the
# pause the critic tests for — which is 3+. The 7 stays hard, because 8+ lines
# is a paragraph and a paragraph is the previous format.
#
# The writer is still TOLD 5-7 (PROMPTS.md, verbatim). The CODE bound is the
# one that must not reject a gold-quality draft.
# Things a scene can be anchored on: the objects the bank's 30 rows use, plus
# the accessories its own gold set opens with ("You had the line ready", "You
# said you were good twelve times today", "You recorded it").
CONCRETE_NOUNS = (
    "phone", "message", "text", "call", "missed call", "invite", "door", "car",
    "aisle", "photo", "laugh", "name", "voice", "draft", "thumb", "hands",
    "hand", "chat", "compliment", "story", "pause", "room", "cashier",
    "meeting", "route", "morning", "screen", "line", "joke", "plan", "exit",
    "forest", "kitchen", "corridor", "counter", "table", "seat", "street",
    "bus", "train", "lift", "elevator", "mirror", "bed", "cup", "coffee",
    "cheque", "checkout", "queue", "number", "notification", "badge", "key",
)

MIN_LINES_SPEC, MAX_LINES_SPEC = 5, 7
MIN_LINES, MAX_LINES = 3, 7


# ------------------------------------------------------------------ extraction

def _clean(text) -> str:
    return " ".join(str(text or "").replace("\r", "").split())


def lines_of(onscreen: str) -> list:
    return [ln.strip() for ln in str(onscreen or "").replace("\r", "").split("\n")
            if ln.strip()]


def rename_parts(last_line: str) -> tuple:
    """(verdict, mechanism) when the last line is a rename, else (None, None).

    A "rename" is not just the opening refusal — it must also replace the
    verdict with something. Two conditions, both required:
      1. it opens by refusing a verdict ("You're not X")
      2. the mechanism clause is not itself an empty comfort ("It's fine."
         "That's all." are not renames, they are dismissals)
    """
    m = RENAME_RE.match(_clean(last_line))
    if not m:
        return None, None
    verdict = m.group("verdict").strip().rstrip(".")
    mech = m.group("mech").strip().rstrip(".")
    # A dismissal dressed as a rename: the second clause must carry content.
    bare = ("it's fine", "its fine", "that's all", "thats all", "it is fine",
            "that's it", "thats it", "move on", "let it go", "it's okay",
            "its okay", "never mind")
    if _clean(mech).lower() in bare:
        return None, None
    if len(mech.split()) < 3:
        return None, None
    return verdict, mech


def opening_anchored(lines: list, row: dict | None = None) -> bool:
    """Is the draft anchored on a concrete thing inside its OPENING?

    Line one is what the spec asks for, but its gold set sometimes lands the
    object in line two ("You recorded it. / You played it back..."), so the
    opening two lines are checked. A draft that is vague for two straight lines
    is a poster, and that is what this rejects.
    """
    opening = " ".join(lines[:2]).lower()
    if not opening.strip():
        return False
    if row:
        obj = str(row.get("object") or "").lower()
        key = language.object_key(row)
        for cand in (obj, key):
            if cand and cand in opening:
                return True
    return any(w in opening for w in CONCRETE_NOUNS)


def has_concrete_noun(first_line: str, row: dict | None = None) -> bool:
    """Kept for callers that really do mean line one. See opening_anchored for
    the gate the spec's own examples satisfy."""
    return opening_anchored([first_line], row)


def duration_ok(seconds: float, cfg: dict | None = None) -> bool:
    lo, hi = 16.0, 20.0
    try:
        r = (cfg or config.load_config()).get("reel") or {}
        lo = float(r.get("confession_min_s", lo))
        hi = float(r.get("confession_max_s", hi))
    except Exception:  # noqa: BLE001
        pass
    return lo - 0.01 <= float(seconds) <= hi + 0.01


# --------------------------------------------------------------------- checks

def problems(onscreen: str, row: dict | None = None) -> list:
    """Every HARD reason the draft fails, decided mechanically. Empty == pass.

    Only unambiguous, literal rules live here. Vagueness does NOT, by design:
    the spec states the rule ("line one has a concrete noun", "no second scene")
    but its own gold set breaks the literal form of it in at least two of eight
    examples — "You said you were good twelve times today." opens with no object
    from any list, and "You recorded it." lands the object in line two. A code
    gate written to the letter rejects the quality bar it was written from, so
    the literal reading is enforced only where it is decidable and the judgment
    call is left to the critic, which the spec explicitly charges with it
    ("A person without this problem would nod. Too broad.").
    """
    out = []
    lines = lines_of(onscreen)
    if not lines:
        return ["on-screen text is empty"]
    if len(lines) < MIN_LINES or len(lines) > MAX_LINES:
        out.append(f"{len(lines)} lines (need {MIN_LINES}-{MAX_LINES})")

    body = "\n".join(lines)
    low = body.lower()
    for ban in BANNED_SUBSTRINGS:
        if ban in low:
            out.append(f"hard ban: {ban.strip()!r}")
    if "?" in body:
        out.append("contains a question mark (a question is banned)")
    if re.search(r"#", body):
        out.append("hashtag in the on-screen text")
    if re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", body):
        out.append("emoji in the on-screen text")

    # §last line must be a rename. The mechanism clause may itself carry a full
    # stop ("Relief got there first. Guilt was just late."), so the guard is a
    # minimum length, not a single-sentence shape.
    verdict, mech = rename_parts(lines[-1])
    if not verdict:
        out.append("last line is not a rename (must refuse a verdict AND replace "
                   "it: \"You're not X. <what it actually was>\")")
    else:
        tail = _clean(lines[-1]).lower()
        for bad in BANNED_LAST_LINE_TAILS:
            if bad in tail:
                out.append(f"last line only comforts: {bad!r}")
    return out


def signals(onscreen: str, row: dict | None = None) -> list:
    """SOFT concerns passed to the critic and logged, never a hard fail.

    Kept separate from problems() so the difference is visible in the log: a
    draft that trips a signal was still rendered, and the reviewer can see that
    the judgment was delegated rather than silently skipped.
    """
    out = []
    lines = lines_of(onscreen)
    if not lines:
        return out
    if not opening_anchored(lines, row):
        out.append("opening may be too vague — no concrete noun or scene object "
                   "in the first two lines")
    if row:
        obj = language.object_key(row)
        others = {"phone": ("aisle", "cashier", "door"),
                  "route": ("phone", "cashier"),
                  "plan": ("aisle", "phone")}.get(obj, ())
        low = "\n".join(lines).lower()
        for o in others:
            if o in low and obj not in low:
                out.append(f"possible second scene ({o}) in a {obj} reel")
                break
    return out


def _overlap(memory: dict, onscreen: str, row: dict) -> str | None:
    """Overlap with the last 40 posts above a shared object + shared verdict."""
    sig = language.signature(row)
    for p in (memory.get("posts") or [])[-40:]:
        if p.get("object_key") and p.get("verdict"):
            prev = f"{p['object_key']}|{re.sub(r'[^a-z0-9 ]+', '', str(p['verdict']).lower()).strip()}"
            if prev == sig:
                return f"same object+verdict as {p.get('created_at', '')[:10]}"
    return None


# -------------------------------------------------------------------- prompts

def writer_prompt(row: dict, memory: dict, strategy: dict) -> str:
    """PROMPTS.md 'Writer', plus the one scene to write."""
    raw = _spec_prompts()["raw"]
    base = _section(raw, "Writer") or _FALLBACK_WRITER
    note = mutation_note(memory, strategy)
    return (
        f"{base}\n\n{note}\n\n"
        "THE SCENE YOU MUST WRITE (keep this object, do not merge another scene):\n"
        f"- object: {row.get('object')}\n"
        f"- what happened: {row.get('action')}\n"
        f"- body fact: {row.get('body')}\n"
        f"- time fact: {row.get('time')}\n"
        f"- the verdict they hold against themselves: {row.get('verdict')}\n\n"
        "Output exactly two things and nothing else:\n"
        "1) the lines, one per line, 5 to 7 lines\n"
        "2) then a line containing only ---CAPTION---\n"
        "3) then the caption, which is the last line and nothing else."
    )


def critic_prompt(onscreen: str, row: dict) -> str:
    raw = _spec_prompts()["raw"]
    base = _section(raw, "Critic") or _FALLBACK_CRITIC
    gold = _section(raw, "Gold set") or ""
    return (
        f"{base}\n\n"
        f"THE SCENE IT WAS SUPPOSED TO WRITE:\n- object: {row.get('object')}\n"
        f"- verdict: {row.get('verdict')}\n\n"
        f"THE GOLD SET (the bar; vaguer than these fails, copying one fails):\n{gold[:2500]}\n\n"
        f"THE DRAFT:\n{onscreen}\n\n"
        'Return exactly "PASS" or "FAIL: <the one rule>". Nothing else.'
    )


def mutation_note(memory: dict, strategy: dict) -> str:
    """PROMPTS.md mutation note, generated from the weights."""
    w = ((strategy or {}).get("weights") or {}).get("family") or {}
    scored = [p for p in (memory.get("posts") or [])
              if isinstance(p.get("metrics"), dict) and p["metrics"].get("reach")]
    wins = [p for p in scored
            if (p["metrics"].get("shares") or 0) >= 1
            or (p["metrics"].get("reach") or 0) >= 600]
    seen = [(p.get("family") or "") for p in (memory.get("posts") or [])[-40:]
            if p.get("family")]
    if not wins:
        return ("No winner yet. Do not experiment. Write the most specific single "
                "scene you can, in the gold-set shape. No new structures.")
    top = sorted(w.items(), key=lambda kv: -kv[1])[:2]
    flop = sorted(w.items(), key=lambda kv: kv[1])[:2]
    shape = (wins[-1].get("dna") or {}).get("last_line_shape") or "You're not X. You're Y."
    return (
        f"Prefer these families this week: {', '.join(k for k, _ in top)}. "
        f"Avoid these, they were seen and not sent: {', '.join(k for k, _ in flop)}. "
        f"Do not repeat these scenes: {', '.join(sorted(set(seen))[-40:])}. "
        f"The last winning last-line shape was: {shape}. "
        "Stay at that sharpness. Do not get softer to be safe.")


_FALLBACK_WRITER = ("You write one Instagram Reel for people who say \"I'm fine\". "
                    "Not a coach, not a therapist. 5-7 short lines. Last line is a "
                    "rename: \"You're not X. You're Y.\" No label, no question, no advice.")
_FALLBACK_CRITIC = ("You reject drafts. Return PASS or FAIL: <one rule>. "
                    "Fail anything vague, performed, or that teaches.")


# ------------------------------------------------------------------ generation

def parse_draft(text: str) -> tuple:
    """(onscreen, caption). '---CAPTION---' separates them; the caption defaults
    to the last line, which is what the spec demands anyway."""
    raw = str(text or "").replace("\r", "")
    if "---CAPTION---" in raw:
        head, _, tail = raw.partition("---CAPTION---")
        onscreen = "\n".join(ln for ln in (l.strip() for l in head.split("\n")) if ln)
        caption = tail.strip()
    else:
        keep = []
        for ln in raw.split("\n"):
            s = ln.strip()
            if not s:
                continue
            if s.lower().startswith(("caption:", "#")):
                break
            keep.append(s)
        onscreen = "\n".join(keep)
        caption = ""
    lines = lines_of(onscreen)
    if not caption and lines:
        caption = lines[-1]
    return onscreen, caption


def _caption_is_last_line(caption: str, onscreen: str) -> bool:
    lines = lines_of(onscreen)
    if not lines:
        return False
    return _clean(caption) == _clean(lines[-1]) or not _clean(caption)


def write(row: dict, memory: dict, strategy: dict, dry_run: bool = False) -> dict:
    """Writer -> code bans -> critic -> one retry. Returns a result dict.

    A returned draft is NOT approved; it has passed the mechanical bans and the
    critic. The render gate still has to accept the artefact.
    """
    onscreen, caption, failure = "", "", "not attempted"
    for attempt in (1, 2):
        try:
            raw = llm.chat([{"role": "user", "content": writer_prompt(row, memory, strategy)}],
                           retries=2)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "writer", "error": str(exc),
                    "attempt": attempt}
        onscreen, caption = parse_draft(str(raw))
        bad = problems(onscreen, row)
        if bad:
            failure = f"code bans: {bad[0]}"
            continue
        soft = signals(onscreen, row)

        try:
            verdict = _clean(llm.chat(
                [{"role": "user", "content": critic_prompt(onscreen, row)}],
                retries=2, temperature=0.0))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "critic", "error": str(exc),
                    "attempt": attempt}

        low = verdict.lower()
        if low.startswith("pass") or low == "pass":
            if not _caption_is_last_line(caption, onscreen):
                # The spec is explicit: the caption IS the last line, alone.
                caption = lines_of(onscreen)[-1]
            verdict_txt, mech = rename_parts(lines_of(onscreen)[-1])
            return {"ok": True, "onscreen_text": onscreen, "caption": caption,
                    "attempt": attempt, "critic": verdict,
                    "last_line_verdict": verdict_txt, "last_line_mechanism": mech,
                    "line_count": len(lines_of(onscreen)),
                    "soft_signals": soft,
                    "last_line_shape": "You're not X. <what it actually was>"}
        failure = f"critic: {verdict[:200]}"

    return {"ok": False, "stage": "critic", "error": failure,
            "onscreen_text": onscreen, "attempt": 2}


def dm_pages() -> list:
    """PROMPTS.md 'DM pages'. Used by the DM layer (see ENGINE_SPEC gaps)."""
    raw = _spec_prompts()["raw"]
    block = _section(raw, "DM pages") or ""
    pages = re.findall(r"^Page [A-F]\.\s*$(.*?)(?=^Page [A-F]\.|\Z)",
                       block, re.S | re.M)
    return [re.sub(r"\s+", " ", p).strip() for p in pages if p.strip()]