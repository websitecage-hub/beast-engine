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

from . import config, critic_model, language, llm

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

# ABSTRACTIONS THE LAST LINE MAY NOT HIDE BEHIND. The user's rule: rename a
# verdict the viewer already uses ("rude, cold, fake, flaky, weird") in PLAIN
# words. "You're trying not to get caught off guard" fails — it names a state,
# not the word the viewer calls themselves. The spec's own gold set always lands
# on a plain label or a concrete image ("You're not slow", "You're not ungrateful").
# This list is the vocabulary of evasion.
ABSTRACT_TAILS = (
    "caught off guard", "off guard", "guarded", "protective", "protecting yourself",
    "keeping yourself safe", "keeping safe", "staying safe", "self-protection",
    "nervous system", "overwhelmed", "dysregulated", "dysregulation", "triggered",
    "on high alert", "on edge", "in survival mode", "survival", "defence",
    "defense", "defensive", "self sabotage", "self-sabotage", "protecting your peace",
    "your peace", "inner peace", "holding space", "showing up for yourself",
    "doing your best", "being human", "human", "perfectly normal", "totally normal",
    "valid", "validating", "understandable", "not your fault", "not weakness",
    "not a flaw", "not a defect", "not broken", "not crazy",
)

# The plain verdict words. A rename must use the viewer's own label for
# themselves, and these are the ones the bank already rotates.
PLAIN_VERDICTS = VERDICTS


def abstract_last_line(last_line: str) -> str | None:
    """The abstraction the rename hides behind, or None. Mechanical, on purpose."""
    low = _clean(last_line).lower()
    for a in ABSTRACT_TAILS:
        if a in low:
            return a
    return None


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


# ------------------------------------------------- the named-referent gate
#
# The first live sample shipped "Aisle ends and you see them." — a pronoun with
# nothing behind it. The viewer cannot picture "them", so the line does no work.
# This gate is the fix, and it is mechanical rather than a prompt request because
# a prompt cannot guarantee a negative.
#
# The rule has two halves, and BOTH are needed:
#   1. the draft must NAME a person or thing (the row's subject head, or any of
#      the concrete nouns), and
#   2. it must not use a bare person-pronoun BEFORE that naming has happened —
#      "you see them" in line one with "them" appearing nowhere else is exactly
#      the failure. After the referent is named, pronouns are normal English and
#      are allowed.
BARE_PERSON_PRONOUNS = ("them", "they", "someone", "somebody", "him", "her",
                        "his", "hers", "their", "theirs")

# "you" and "your" are the format's own voice (second person throughout), so they
# are never treated as an unbound pronoun.
_SELF = ("you", "your", "yours", "yourself", "yourselves")


def _names_a_person_or_thing(lines: list, row: dict | None = None) -> tuple:
    """(named, what). Has the draft put a nameable person/thing on screen?"""
    body = " ".join(lines).lower()
    if row:
        head = str(row.get("subject_head") or "").strip().lower()
        if head and re.search(rf"\b{re.escape(head)}s?\b", body):
            return True, head
        subj = str(row.get("subject") or "").strip().lower()
        for word in re.findall(r"[a-z']{4,}", subj):
            if word in _SELF:
                continue
            if re.search(rf"\b{re.escape(word)}s?\b", body):
                return True, word
        obj = str(row.get("object") or "").strip().lower()
        if obj and obj in body:
            return True, obj
    for noun in CONCRETE_NOUNS:
        if re.search(rf"\b{re.escape(noun)}s?\b", body):
            return True, noun
    return False, ""


def _unbound_pronoun(lines: list, row: dict | None = None) -> str | None:
    """The first bare person-pronoun that appears before its referent is named.

    Walked line by line: if a pronoun is used and nothing has been named yet, the
    line is unreadable to a viewer who is one second into the reel.
    """
    named_before = False
    for ln in lines:
        low = ln.lower()
        # does THIS line (or an earlier one) name something?
        names_line, _ = _names_a_person_or_thing([ln], row)
        if named_before:
            return None
        for pron in BARE_PERSON_PRONOUNS:
            if re.search(rf"\b{re.escape(pron)}\b", low):
                # A pronoun is fine when the same line names its referent
                # ("your brother texts and you leave him on read").
                if names_line:
                    break
                return pron
        if names_line:
            named_before = True
    return None


# --------------------------------------------------------- the filler gate
#
# The user's review of the sample: 'cart gone quiet', 'feet move before your mind
# does', 'any generic body-sensation line'. These are lines that feel like writing
# and say nothing a viewer could picture. The spec's gold set has none of them —
# every gold line is an ACTION ("You typed it. Deleted it. Typed it again.") or an
# EXACT THOUGHT ("you're not flaky").
#
# Two mechanical tests, both from the user's wording:
#   a. a PICTURE-FREE line: no concrete noun, no verb of action, no named thing.
#   b. a BODY-SENSATION line standing in for a scene: a feeling used as the whole
#      content of the line ("Your chest is tight"). The gold set allows a body
#      fact only when it is tied to its cause ("Your chest is still tight from a
#      call that never happened") — the cause is what makes it specific.
BODY_WORDS = ("chest", "heart", "stomach", "throat", "cheeks", "hands",
              "shoulders", "knees", "jaw", "skin", "breath")
BODY_VERBS = ("tight", "racing", "pounding", "dropped", "sank", "closed",
              "burning", "hot", "cold", "shaking", "tightening", "heavy")
# Verbs that put a picture on the screen. STEMS, not past tense, and matched with
# a suffix allowance — an earlier version listed only "watched"/"moved"/"wanted"
# and so rejected the spec's own gold line "You watch it ring out." and
# "Everyone else had already moved on." The gate is meant to catch a line that
# shows NOTHING, not to run a grammar test, so the stem list is deliberately
# generous. Irregular past forms are listed separately.
ACTION_VERBS = (
    "type", "delete", "send", "open", "read", "watch", "wait", "ring", "call",
    "text", "laugh", "nod", "smile", "say", "ask", "answer", "walk", "turn",
    "leave", "stay", "stand", "sit", "drive", "park", "cancel", "record",
    "play", "replay", "practis", "practic", "rehears", "look", "hear", "see",
    "put", "hold", "pick", "order", "pay", "queue", "knock", "reach", "count",
    "screenshot", "draft", "write", "scroll", "mute", "check", "cross", "pass",
    "meet", "greet", "hug", "take", "want", "think", "move", "know", "need",
    "keep", "try", "feel", "find", "give", "make", "come", "go", "get", "show",
    "start", "stop", "finish", "begin", "end", "add", "drop", "raise", "lower",
    "click", "tap", "swipe", "close", "shut", "lock", "unlock", "walk", "run",
    # irregular past forms, which a stem match cannot reach
    "said", "left", "took", "drove", "held", "thought", "saw", "heard", "felt",
    "found", "gave", "made", "came", "went", "got", "knew", "told", "stood",
    "sat", "rang", "wrote", "read", "spoke", "wore", "threw", "caught", "built",
    "sent", "spent", "met", "paid", "kept", "slept", "meant", "lost", "won",
    "ran", "began", "brought", "bought", "taught", "cut", "hit", "quit", "set",
    "shut", "let", "dealt", "hung", "sang", "drank", "ate", "fell", "grew",
    "rose", "lent", "dug", "stuck", "struck", "swore", "tore", "slid", "led",
    # "have"/"do" as main verbs ("You had the sentence ready", "You did it
    # twice") are actions in this format, and the gold set uses them.
    "have", "had", "do", "did", "done", "does", "finish", "spend", "spent",
    "ready", "wait", "wear", "wore", "carry", "carried", "answer", "answered",
)
# A GENERAL VERB SHAPE, so the gate cannot be defeated by a verb the list forgot.
#
# The enumerated stems kept missing ordinary words the writer uses — "shoved",
# "agreed", "talked", "fought" all tripped "names nothing and shows no action" on
# drafts that were plainly action lines. A hand-written verb list cannot be
# complete, and a gate that rejects valid work is worse than one that lets a
# borderline line through, because the critic is the backstop for vagueness
# (PROMPTS.md assigns judgment calls to it explicitly).
#
# So: any past-tense or progressive verb shape counts as an action, MINUS a set of
# state-adjectives that wear the same -ed ending while showing no action at all.
# "You shoved the receipt in your pocket" passes; "You are tired" does not.
STATE_ADJECTIVES = (
    "tired", "bored", "worried", "scared", "exhausted", "overwhelmed",
    "embarrassed", "ashamed", "anxious", "depressed", "annoyed", "confused",
    "interested", "supposed", "used", "stressed", "depleted", "drained",
    "concerned", "surprised", "shocked", "amazed", "numb", "content", "relieved",
)
_VERB_SHAPE = re.compile(rf"\b(?!{'|'.join(STATE_ADJECTIVES)}\b)\w{{3,}}(?:ed|ing)\b")

GENERIC_FILLER = (
    "cart gone quiet", "gone quiet", "before your mind does", "body knows",
    "something in you", "part of you knew", "deep down", "all along",
    "the silence said", "the room felt", "everything changed", "nothing was the same",
    "you wanted to disappear", "you just couldn't", "your body knew",
    "the weight of it", "a wave of", "washed over", "settled in",
)


def filler_lines(lines: list, row: dict | None = None) -> list:
    """Lines that name nothing and show nothing. Empty == none found.

    Only the BODY lines are checked against the row; every other line must carry
    a picture (a concrete noun or an action verb) to survive.
    """
    out = []
    for i, ln in enumerate(lines[:-1]):          # the rename is judged elsewhere
        low = ln.lower()
        for g in GENERIC_FILLER:
            if g in low:
                out.append(f"line {i + 1} is filler: {g!r}")
        has_picture = bool(re.search(
            rf"\b(?:{'|'.join(ACTION_VERBS)})(?:s|es|ed|ing|d)?\b", low)) \
            or bool(_VERB_SHAPE.search(low))
        has_thing = any(re.search(rf"\b{re.escape(n)}s?\b", low) for n in CONCRETE_NOUNS)
        if row:
            head = str(row.get("subject_head") or "").lower()
            if head and re.search(rf"\b{re.escape(head)}s?\b", low):
                has_thing = True
        # A body line must carry its CAUSE, not just the sensation.
        body_hit = any(re.search(rf"\b{re.escape(b)}\b", low) for b in BODY_WORDS)
        body_feel = any(re.search(rf"\b{re.escape(v)}\b", low) for v in BODY_VERBS)
        if body_hit and body_feel and not (has_picture or has_thing):
            out.append(f"line {i + 1} is a bare body sensation with no cause: "
                       f"{ln.strip()[:60]!r}")
            continue
        if not has_picture and not has_thing:
            out.append(f"line {i + 1} names nothing and shows no action: "
                       f"{ln.strip()[:60]!r}")
    return out


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
        # A QUESTION AT THE VIEWER is banned. A question quoted as REPORTED
        # DIALOGUE is the scene itself, and banning it rejected a valid draft:
        # "You drop \"good, you?\" before she finishes scanning" is a description
        # of what the person said, not an ask directed at the reader.
        #
        # So the mark is only a failure when it is outside quotation marks AND the
        # line actually asks something. A quoted "you?" is the scene; an unquoted
        # "You ever feel like this?" is the pattern the spec bans.
        quoted = re.findall(r'[""\u201c\u201d]([^""\u201c\u201d]*)[""\u201c\u201d]',
                            body)
        stripped = body
        for q in quoted:
            stripped = stripped.replace(q, "")
        stripped = re.sub(r'[""\u201c\u201d]', "", stripped)
        if "?" in stripped and not stripped.rstrip().endswith("?"):
            out.append("contains a question mark (a question is banned)")
        elif "?" in stripped:
            out.append("ends on a question (the reel never asks)")
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
        # The rename must use the viewer's OWN word for themselves, in plain
        # language — not an abstraction.
        if not any(re.search(rf"\b{re.escape(v)}\b", tail) for v in PLAIN_VERDICTS):
            out.append(f"last line refuses the verdict but never names a plain one "
                       f"({', '.join(PLAIN_VERDICTS[:6])}, ...)")
        abstract = abstract_last_line(lines[-1])
        if abstract:
            out.append(f"last line is abstract: {abstract!r} — rename with the "
                       f"viewer's own plain word")

    # §the referent must be named, not an unbound pronoun
    named, what = _names_a_person_or_thing(lines, row)
    if not named:
        out.append("nothing nameable on screen: no person or object the viewer "
                   "can picture (a scene needs one concrete subject)")
    else:
        pron = _unbound_pronoun(lines, row)
        if pron:
            out.append(f"unnamed referent: {pron!r} is used before any person or "
                       f"thing has been named")

    # §no filler: every line must show an action or name a thing
    for f in filler_lines(lines, row):
        out.append(f)
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

def writer_prompt(row: dict, memory: dict, strategy: dict,
                  previous_failure: str = "") -> str:
    """PROMPTS.md 'Writer', plus the one scene to write.

    previous_failure is the critic's ruling from attempt 1. ENGINE_SPEC line 11:
    "If it fails the draft, the writer gets one retry with the failed rule pasted
    in." The first version of write() threw the ruling away and asked again with
    an identical prompt, so the retry re-made the same mistake. A retry that does
    not know why it failed is not a retry.
    """
    raw = _spec_prompts()["raw"]
    base = _section(raw, "Writer") or _FALLBACK_WRITER
    note = mutation_note(memory, strategy)
    retry = ""
    if previous_failure:
        retry = (
            "\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED BY THE CRITIC. The ruling was:\n"
            f"    {previous_failure}\n"
            "Rewrite the scene so that exact failure cannot happen again. Keep the "
            "same object and the same verdict; change whatever violated the rule.\n"
        )
    return (
        f"{base}\n\n{note}{retry}\n\n"
        "THE SCENE YOU MUST WRITE (keep this object, do not merge another scene):\n"
        f"- THE NAMED THING: {row.get('subject') or row.get('object')}\n"
        f"- object: {row.get('object')}\n"
        f"- what happened: {row.get('action')}\n"
        f"- body fact: {row.get('body')}\n"
        f"- time fact: {row.get('time')}\n"
        f"- the verdict they hold against themselves: {row.get('verdict')}\n\n"
        "THREE RULES THE GATE WILL REJECT YOU FOR, so satisfy them deliberately:\n"
        "1. NAME THE PERSON OR THING in the first two lines. Never open with "
        "\"them\", \"they\", \"someone\", \"it\" — a viewer one second in cannot "
        "see who you mean. Write the actual person or object (the old coworker, "
        "the invite, your brother, the neighbour at the mailbox).\n"
        "2. EVERY LINE MUST SHOW AN ACTION OR NAME A THING. No filler, no mood "
        "lines, no bare body sensations. A line about the body MUST carry its "
        "cause in the same line, or it is a rejection:\n"
        "     REJECTED: \"Your throat closed on the sentence you had.\"\n"
        "     REJECTED: \"Your chest is tight.\"\n"
        "     ACCEPTED: \"Your chest is still tight from a call that never "
        "happened.\"\n"
        "     ACCEPTED: \"You had the answer ready and your throat closed on it.\"\n"
        "   No \"cart gone quiet\", no \"feet move before your mind does\".\n"
        "3. THE LAST LINE RENAMES THE VERDICT IN PLAIN WORDS. Use their own word "
        "for themselves — rude, cold, fake, flaky, weird, quiet, slow. Do NOT "
        "hide behind an abstraction: \"caught off guard\", \"protecting yourself\", "
        "\"your nervous system\", \"not your fault\", \"human\" are all rejections.\n\n"
        "Output exactly two things and nothing else:\n"
        "1) the lines, one per line, 5 to 7 lines\n"
        "2) then a line containing only ---CAPTION---\n"
        "3) then the caption, which is the last line and nothing else."
    )


def critic_prompt(onscreen: str, row: dict, memory: dict | None = None) -> str:
    raw = _spec_prompts()["raw"]
    base = _section(raw, "Critic") or _FALLBACK_CRITIC
    gold = _section(raw, "Gold set") or ""

    # THE RECENT POSTS, SO "REPEAT" IS JUDGED AGAINST REAL HISTORY.
    #
    # ENGINE_SPEC line 160 defines repetition as "overlap with any of the last 40
    # posts above a shared object plus a shared verdict". The first version of
    # this prompt did not pass the posts, so the critic had no history to compare
    # against and used the GOLD SET as a proxy — and rejected every good draft
    # with "REPEATING A RECENT SCENE". That was not a critic failure, it was a
    # missing input: 7 of the 30 bank rows ARE the gold examples by design, so
    # writing the "phone" scene looked identical to copying the gold "Phone."
    hist = ""
    posts = []
    for p in ((memory or {}).get("posts") or [])[-40:]:
        # ONLY posts that carry the NEW scene schema. The 24 legacy posts have
        # object_key=None and their "keyword" is a DM CTA word (QUIET, MASK), not
        # a verdict — feeding those to the critic manufactured false "repetition"
        # matches against vocabulary that means something else entirely.
        if p.get("object_key") and p.get("verdict"):
            posts.append((p.get("object_key"), p.get("verdict"),
                          str(p.get("created_at") or "")[:10]))
    if posts:
        seen = [f"  - object={k} verdict={v} ({d})" for k, v, d in posts[-12:]]
        hist = ("\nRECENT POSTS ALREADY PUBLISHED (repetition means matching one of "
                "THESE — the same object AND the same verdict):\n"
                + "\n".join(seen) + "\n")
    else:
        n_legacy = len((memory or {}).get("posts") or [])
        hist = (f"\nRECENT POSTS: none in the current format"
                + (f" (the {n_legacy} older posts predate this format and are not "
                   f"comparable)" if n_legacy else "")
                + ". There is nothing to repeat — do NOT fail for repetition.\n")

    return (
        f"{base}\n\n"
        f"THE SCENE IT WAS SUPPOSED TO WRITE:\n- object: {row.get('object')}\n"
        f"- the named thing: {row.get('subject') or row.get('object')}\n"
        f"- verdict: {row.get('verdict')}\n"
        f"{hist}\n"
        "YOU MUST FAIL A DRAFT FOR ANY OF THESE, even if it reads well:\n"
        "- AN UNNAMED REFERENT: a person referred to as \"them\", \"they\", "
        "\"someone\", \"him\", \"her\" with no person or object named first. The "
        "viewer must be able to picture who or what this is.\n"
        "- A FILLER LINE: any line that names nothing and shows no action, or a "
        "bare body sensation with no cause. \"Cart gone quiet\", \"feet move "
        "before your mind does\". A body line is FINE when its cause is in the "
        "same line: \"Your chest is still tight from a call that never happened\" "
        "PASSES, because the cause is the content. Fail only the bare version "
        "(\"Your throat closed on the sentence you had\" standing alone as a "
        "sensation with no action attached).\n"
        "- AN ABSTRACT FINAL LINE. The last line must name the verdict the viewer "
        "already calls themselves, in PLAIN words (rude, cold, fake, flaky, "
        "weird, quiet, slow, boring, too much, ungrateful, dramatic, awkward, "
        "annoying, dull, useless, liar, obsessive, ridiculous). The line always "
        "opens \"You're not X. ...\", so X is the plain verdict — CHECK THE X "
        "FIRST, before judging what follows it.\n"
        "  The line then says what it actually was. That second part is ALLOWED "
        "to describe a behaviour, a job, or an effort — that is what a rename is: "
        "\"You're not flaky. Relief got there first.\" and \"You're not ungrateful. "
        "Wanting the night and surviving the night are two different jobs.\" are "
        "both PASSES from the gold set.\n"
        "  FAIL it only when the second part hides behind a STATE instead of "
        "doing the work: \"caught off guard\", \"protecting yourself\", \"your "
        "nervous system\", \"not your fault\", \"being human\", \"doing your "
        "best\", \"on high alert\", \"survival mode\".\n"
        "  Refusing to answer is the failure. Answering in plain words about real "
        "behaviour is the format.\n"
        "- REPEATING A RECENT POST: the same object AND the same verdict as one "
        "of the RECENT POSTS listed above. Repeating a SCENE IDENTITY is what "
        "this means. It does NOT mean the scene resembles a gold example.\n"
        "- A VERBATIM COPY: a gold line reused word-for-word. Paraphrase is not a "
        "copy. The bank scenes ARE the gold scenes, so writing one in fresh words "
        "is exactly what is wanted — the gold set is the BAR, not a quarantine "
        "list. Only wording lifted from it fails.\n"
        "- Too broad: a person without this problem would nod. That fails too.\n\n"
        f"THE GOLD SET (the quality BAR — do not treat it as a list of used "
        f"scenes):\n{gold[:2500]}\n\n"
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
            raw = llm.chat([{"role": "user", "content": writer_prompt(
                row, memory, strategy, previous_failure=failure if attempt == 2 else "")}],
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
            verdict = _clean(critic_model.chat(
                [{"role": "user", "content": critic_prompt(onscreen, row, memory)}],
                temperature=0.0))
        except Exception as exc:  # noqa: BLE001
            # FAIL CLOSED. A critic that disappears must never look like a pass:
            # the pipeline would ship unreviewed drafts while reporting green.
            return {"ok": False, "stage": "critic_unavailable", "error": str(exc),
                    "critic_family": critic_model.family(), "attempt": attempt}

        low = verdict.lower()
        if low.startswith("pass") or low == "pass":
            if not _caption_is_last_line(caption, onscreen):
                # The spec is explicit: the caption IS the last line, alone.
                caption = lines_of(onscreen)[-1]
            verdict_txt, mech = rename_parts(lines_of(onscreen)[-1])
            return {"ok": True, "onscreen_text": onscreen, "caption": caption,
                    "attempt": attempt, "critic": verdict,
                    "critic_family": critic_model.family(),
                    "writer_family": f"meta:{llm.MODEL}",
                    "last_line_verdict": verdict_txt, "last_line_mechanism": mech,
                    "line_count": len(lines_of(onscreen)),
                    "soft_signals": soft,
                    "last_line_shape": "You're not X. <what it actually was>"}
        failure = f"critic: {verdict[:200]}"

    return {"ok": False, "stage": "critic", "error": failure,
            "critic": failure, "onscreen_text": onscreen, "attempt": 2,
            "critic_family": critic_model.family()}


def dm_pages() -> list:
    """PROMPTS.md 'DM pages'. Used by the DM layer (see ENGINE_SPEC gaps)."""
    raw = _spec_prompts()["raw"]
    block = _section(raw, "DM pages") or ""
    pages = re.findall(r"^Page [A-F]\.\s*$(.*?)(?=^Page [A-F]\.|\Z)",
                       block, re.S | re.M)
    return [re.sub(r"\s+", " ", p).strip() for p in pages if p.strip()]