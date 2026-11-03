"""content_factory.py — the creative brain for the two pilot formats.

Encoded here, in code rather than hope, is the Creative Intelligence System:

Pass 1  audience-moment card   one situation, the observable behaviour, the
                               private worry, the tension - never a topic label
Pass 2  concept slate          >=3 distinct situations; the format is chosen, not invented
Pass 3  format writing         A: title + exactly N caption items. B: one loop block
Pass 4  independent critic     9 scored dimensions + hard fails, judging wording only
Pass 5  targeted rewrite       the failing dimensions are handed back, nothing else changes
Pass 6  structured output      validated payload + evidence + critic metadata

Deterministic rules the writer cannot argue its way out of (they run BEFORE the
critic, so a hollow payload never spends a critic call):

* the specificity ladder: a level-0/1 label ("social anxiety is hard") is rejected
  mechanically; level 2+ needs a concrete anchor, and level 3+ needs a private
  meaning or a contradiction
* A's number must equal the caption's item count, every item distinct and concrete
* B is one thought, <=45 words, closing the loop back to its first line
* no clinical/diagnostic vocabulary anywhere, in either format
* no repeat of a recent topic or a near-duplicate of a recent headline
* rights are never written by the model - the broker attaches them, unknown means
  preview-only

A malformed writer response gets one stricter-JSON retry, then fails. A malformed
critic response fails closed (`critic_unparseable`) rather than burning a blind
rewrite.
"""
from __future__ import annotations

import json
import os
import re
import time

from .. import critic_model, llm
from . import template_payloads

MAX_ATTEMPTS = 3

AUDIENCE = ("@unleashthe.b — people 18-30 who want connection but get preoccupied "
            "with how they are being perceived. They rehearse, hesitate, "
            "self-monitor, avoid, over-apologise, replay afterwards. Never broken, "
            "pathetic, antisocial or incapable. The situation and the overactive "
            "inner commentary may be funny; the person keeps their dignity.")

_CARD_PROMPT = """You are the Creative Director for {audience}

Work in two steps and return BOTH, as JSON only.

STEP 1 - audience moment card. Choose ONE concrete social moment (not a topic):
{{"situation": "where and when, one specific context",
 "observable_behaviour": "what a camera could actually see them doing",
 "private_worry": "what they fear it signals to someone else",
 "emotional_tension": "one sentence naming the contradiction",
 "audience_cluster": "after-conversation replay | group conversation entry | texting and group chats | asking and needing help | parties and social events | making and maintaining friends | everyday performance moments | boundaries and people-pleasing | quiet progress"}}

STEP 2 - a slate of exactly 3 concepts, each a DIFFERENT situation (not three
versions of one), each usable in ONE of the two formats:
[{{"concept_id": "kebab-case", "situation": "...", "behaviour": "...",
  "private_meaning": "...", "turn": "...", "format": "caption_list_title_v1|social_moment_loop_v1"}}]

Then pick the strongest concept and say why in one line.

RULES
- Concrete beats broad: bus, group chat, doorway, phone screen, kitchen, lift,
  counter, call button. No "social anxiety is hard", no "healing takes time".
- Never diagnose, never claim a list proves a condition.
- Do not reuse anything in the RECENTLY USED list.
{topic_line}
RECENTLY USED: {recent}

Return ONLY:
{{"audience_moment": {{...}}, "concepts": [{{...}}, {{...}}, {{...}}],
 "chosen": 0, "why": "one line", "topic_id": "kebab-case-id"}}"""


def _extract_json(text) -> dict | None:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _writer_chat(content: str) -> tuple[str, str]:
    """Ask the writer, returning (text, vendor).

    The Meta wrapper is the engine's writer, but its completions started
    returning upstream 500 / hanging (health is fine; generation is not). Rather
    than fake content or stall the pipeline, fall back to the DeepSeek bridge and
    RECORD the vendor: the caller marks the critic as non-independent when both
    sides come from the same service. Set BEAST_WRITER=meta to disable the
    fallback and fail instead.
    """
    choice = os.environ.get("BEAST_WRITER", "auto")
    if choice in ("auto", "meta"):
        try:
            raw = llm.chat([{"role": "user", "content": content}],
                           expect_json=True, retries=1,
                           timeout=int(os.environ.get("BEAST_WRITER_TIMEOUT", "150")))
            return (raw if isinstance(raw, str) else json.dumps(raw)), "meta-api"
        except Exception:
            if choice == "meta":
                raise
    raw = critic_model.chat([{"role": "user", "content": content}],
                            temperature=0.7, max_tokens=900)
    return raw, "deepseek-bridge"


# ------------------------------------------------------------------- Pass 1-2
def recent_context(limit: int = 8) -> dict:
    """What this account already made, so nothing repeats (§6 deduplication)."""
    topics, lines = [], []
    try:
        from . import run_manifest
        for e in run_manifest.load()[-limit:]:
            c = (e.get("content") or {})
            if c.get("topic_id"):
                topics.append(str(c["topic_id"]))
            p = e.get("payload") or {}
            for key in ("headline", "titleLines", "hookLines", "on_screen_text"):
                v = p.get(key)
                if isinstance(v, list):
                    lines.append(" ".join(str(x) for x in v))
                elif v:
                    lines.append(str(v))
    except Exception:  # noqa: BLE001 — no history is not a reason to stop
        pass
    return {"topics": topics[-limit:], "lines": [x for x in lines if x][-limit:]}


def audience_moment(format_id: str, topic: str | None = None, *,
                    recent: dict | None = None, chat=None,
                    verbose: bool = True) -> dict:
    """Pass 1+2. Always returns a usable card; `compiled_by` says how it was made."""
    recent = recent if recent is not None else recent_context()
    chat = chat or _writer_chat
    prompt = _CARD_PROMPT.format(
        audience=AUDIENCE,
        topic_line=(f"The moment must sit in this territory: {topic}."
                    if topic else "Choose the territory yourself."),
        recent=json.dumps({"topics": recent.get("topics", [])[-4:],
                           "lines": recent.get("lines", [])[-4:]})[:600])
    try:
        raw, vendor = chat(prompt)
        card = _extract_json(raw)
        if not card or not isinstance(card.get("concepts"), list) or len(card["concepts"]) < 3:
            raise ValueError("fewer than three concepts in the slate")
        am = card.get("audience_moment") or {}
        for key in ("situation", "observable_behaviour", "private_worry",
                    "emotional_tension"):
            if not str(am.get(key) or "").strip():
                raise ValueError(f"audience moment missing {key}")
        situations = {str(c.get("situation") or "").strip().lower()
                      for c in card["concepts"]}
        if len(situations) < 3:
            raise ValueError("the slate repeats one situation")
        chosen = int(card.get("chosen") or 0)
        card["chosen"] = chosen if 0 <= chosen < len(card["concepts"]) else 0
        card["compiled_by"] = f"llm:{vendor}"
        card["degraded"] = False
        return card
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"[content] concept slate fell back to the brief: {str(exc)[:120]}",
                  flush=True)
        sit = topic or "a small social moment the viewer has lived"
        return {
            "audience_moment": {
                "situation": sit,
                "observable_behaviour": "the visible thing they do in that moment",
                "private_worry": "what they fear it says about them",
                "emotional_tension": "they want connection and are busy managing how "
                                     "they are perceived",
                "audience_cluster": "everyday performance moments"},
            "concepts": [{"concept_id": "fallback-0", "situation": sit,
                          "behaviour": "the visible action", "private_meaning": "the worry",
                          "turn": "the small revealing contradiction",
                          "format": format_id}],
            "chosen": 0, "why": "degraded brief", "topic_id": (topic or "social-moment")
            .strip().lower().replace(" ", "-")[:40] or "social-moment",
            "compiled_by": "fallback", "degraded": True,
            "error": str(exc)[:200],
        }


# -------------------------------------------------------------- deterministic rules
_BANNED = re.compile(
    r"\b(diagnos\w*|disorder\w*|syndrome\w*|symptom\w*|clinical\w*|cure[sd]?|"
    r"mental illness|mental health|high[- ]functioning|therapist|therapy)\b", re.I)
_STOP = set("a an the and or but to of in on at for with you your i me my it is are "
            "was were be been do does did that this so if when then than as by from".split())

# Specificity ladder (§3). Anchors are things a camera or a phone screen shows;
# private markers are inner commentary; contradictions are the turn.
_ANCHORS = re.compile(
    r"\b(bus|train|tube|subway|car|taxi|phone|screen|message|text|reply|chat|"
    r"group chat|call|voicemail|door|doorway|handle|kitchen|kettle|mug|cup|glass|"
    r"bottle|plate|bowl|fork|spoon|sink|tap|fridge|freezer|microwave|toaster|oven|"
    r"hob|dishwasher|washing machine|bin|table|desk|counter|chair|sofa|couch|"
    r"cushion|blanket|towel|mirror|lamp|light|curtain|stairs|corridor|hallway|"
    r"lift|elevator|bed|pillow|window|street|pavement|queue|line|cafe|coffee|"
    r"shop|supermarket|station|platform|bus stop|park|bench|meeting|office|"
    r"coat|jacket|bag|keys|shoes|headphones|playlist|invite|plan|birthday|"
    r"apology|apologise|apologize|goodbye|see you|haha|lol|thumbs up|read receipt|"
    r"notification|draft|paragraph|sentence|voice note|story|post|comment|"
    r"holding|standing|sitting|waiting|walking|hovering|half[- ]turned|shrug)\b", re.I)
_PRIVATE = re.compile(
    r"\b(wonder|wondering|replay|replaying|re-?read|overthink|overthinking|decid|"
    r"assume|assuming|convinc|judge|second[- ]guess|worry|worried|question whether|"
    r"check(?:ing)? (?:it|the|again)|read(?:ing)? it again|analyse|analyze|"
    r"imagine|imagining|hope|hoping|pretend|pretending|tell(?:ing)? yourself)\b", re.I)
_TURN = re.compile(
    r"\b(but|though|although|even though|still|anyway|nobody|no one|no-one|already|"
    r"again|by then|too late|for the (?:third|fourth|fifth) time|twice)\b", re.I)


def ladder_level(payload: dict, format_id: str) -> tuple[int, list[str]]:
    """Estimate the specificity ladder level with reasons. Deterministic, recorded."""
    if format_id == "caption_list_title_v1":
        head = " ".join([payload.get("headline") or ""] +
                        [str(x) for x in (payload.get("titleLines") or [])])
        items = " ".join(str(x) for x in (payload.get("list_items") or []))
        text = f"{head} {items}"
        primary = head
    else:
        text = " ".join(str(x) for x in
                        ((payload.get("hookLines") or []) + (payload.get("payoffLines") or [])))
        primary = text
    anchor_matchers = ("anchor", bool(_ANCHORS.search(text)))
    private = bool(_PRIVATE.search(text))
    turn = bool(_TURN.search(text))
    reasons = [f"anchor={anchor_matchers[1]}", f"private={private}", f"turn={turn}"]
    if not anchor_matchers[1]:
        return 0, reasons + ["no concrete scene anchor"]
    level = 2
    if private or turn:
        level = 3
    if private and turn and re.search(r"\b(one|two|three|first|second|then|after)\b",
                                      text, re.I):
        level = 4
    if level >= 3 and not _ANCHORS.search(primary):
        return level - 1, reasons + ["the headline itself carries no concrete anchor"]
    return level, reasons


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in _STOP]


def _too_similar(a: str, b: str) -> bool:
    """Two items sharing most of their content words are one behaviour written
    twice, which breaks the promised count."""
    wa, wb = set(_words(a)), set(_words(b))
    if not wa or not wb:
        return True
    shared = wa & wb
    return len(shared) >= 3 and len(shared) / min(len(wa), len(wb)) >= 0.6


def _repeats_recent(payload: dict, format_id: str, recent: dict) -> str | None:
    tid = str(payload.get("topic_id") or "").strip().lower()
    if tid and tid in {t.lower() for t in recent.get("topics", [])}:
        return f"topic_id {tid!r} was already used"
    if format_id == "caption_list_title_v1":
        head = payload.get("headline") or " ".join(payload.get("titleLines") or [])
    else:
        head = " ".join((payload.get("hookLines") or []) +
                        (payload.get("payoffLines") or []))
    hw = set(_words(head))
    for prior in recent.get("lines", []):
        pw = set(_words(prior))
        if hw and pw and len(hw & pw) / max(len(hw), 1) >= 0.6:
            return f"too close to a recent post: {prior[:50]!r}"
    return None


def creative_errors(payload: dict, format_id: str, *, recent: dict | None = None) -> list[str]:
    """Everything checkable without a model. Runs before the critic."""
    errs: list[str] = []
    recent = recent if recent is not None else {"topics": [], "lines": []}
    if format_id == "caption_list_title_v1":
        items = payload.get("list_items") or []
        headline = payload.get("headline") or ""
        labels = [("headline", headline)] + [(f"item {i+1}", str(it)) for i, it in enumerate(items)]
        for label, text in labels:
            if _BANNED.search(text or ""):
                errs.append(f"{label} uses a clinical/medical label")
                break
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if _too_similar(str(items[i]), str(items[j])):
                    errs.append(f"items {i+1} and {j+1} are the same behaviour reworded")
                    break
            if any("reworded" in e for e in errs):
                break
    else:
        lines = (payload.get("hookLines") or []) + (payload.get("payoffLines") or [])
        total = sum(len(str(x).split()) for x in lines)
        if total > 45:
            errs.append(f"block is {total} words; one thought must fit in 45")
        for line in lines:
            if _BANNED.search(str(line)):
                errs.append("copy uses a clinical/medical label")
                break
    level, why = ladder_level(payload, format_id)
    if level < 2:
        errs.append("specificity ladder level " + str(level) +
                    ": needs a concrete scene a camera can see (" + ", ".join(why) + ")")
    repeat = _repeats_recent(payload, format_id, recent)
    if repeat:
        errs.append(repeat)
    return errs


def _attach_rights(payload: dict, format_id: str) -> dict:
    """Rights are an evidence question, never a writer choice."""
    payload.setdefault("audio", {"source": "audi0-scraper", "rights": "preview_only"})
    payload.setdefault("provenance", {"source": "pinterest", "usage_clearance": "unknown"})
    payload["_schema_version"] = template_payloads.SCHEMA_VERSION
    return payload


# -------------------------------------------------------------- Pass 3 prompts
def _prompt(format_id: str, topic: str | None, notes: str = "", strict: bool = False,
            card: dict | None = None) -> str:
    topic_line = f"The moment to write about: {topic}." if topic else \
        "Choose a specific everyday moment in this territory yourself."
    card_block = ""
    if card:
        am = card.get("audience_moment") or {}
        concept = (card.get("concepts") or [{}])[card.get("chosen") or 0]
        card_block = (
            "\nWORK FROM THIS CARD (already chosen - do not re-pick the situation):\n"
            f"{json.dumps({'audience_moment': am, 'chosen_concept': concept}, ensure_ascii=False)[:900]}\n")
    if format_id == "caption_list_title_v1":
        body = f"""Write ONE reel in this format.

Format A - a numbered promise on screen, the list in the caption.
{topic_line}
{card_block}
WRITE THE HUMAN MOMENT BEFORE THE HOOK. First decide, silently: the setting,
the action, the social stakes, the private reaction. Then compress that moment
into a title. Recognition beats explanation: the reader should think "that's me
on a Tuesday", not "that's an interesting topic".

Title grammar - the numbered promise is the SPINE of the sentence, not a suffix
bolted on at the end. All three parts, in any order:
  [specific identity or familiar inner experience]
+ [unexpected interpretation / visible pattern they had not named]
+ [finite numbered promise, 4-8]
Worked grammar (abstract on purpose, so you invent the words):
  "[a specific person or familiar experience] + [a visible pattern they have not
  named] + [N things/signs/habits they do about it]"

Specificity ladder - aim for 3 or 4, never 0 or 1:
  0 "social anxiety is hard"                       -> rejected mechanically
  1 "you overthink conversations"                  -> rejected mechanically
  2 "you replay the sentence you said on the bus"  -> minimum, anchored
  3 "everyone got home; you're still reviewing one sentence on the bus"
  4 "you had a normal conversation, got home, and became the prosecutor of one sentence"

Return ONLY this JSON:
{{"topic_id": "kebab-case-id",
  "headline": "8 things you do after ...",
  "titleLines": ["line 1", "line 2", "line 3", "line 4"],
  "list_items": ["item", "item", ...],
  "caption": "8 things ...:\\n1. item\\n2. item\\n... ",
  "closing": "one short closing line",
  "background_query": "concrete scene nouns for ONE calm continuous shot"}}

Hard rules:
- headline starts with a number 4-8; list_items length MUST equal it exactly;
- every list item is a DIFFERENT behaviour: no near-synonyms, no item that is a
  paraphrase of another. Eight ways of saying "rehearse the conversation" is
  one item, not eight;
- every item is an observable moment, internal reaction, or the context around
  it ("replay the last sentence in the shower"), not a broad label ("overthink");
- vary the items across the same episode: before / during / just after / later;
- 3-9 words per item, concrete object/action/time where possible;
- every item appears in the caption, in order, and the caption delivers exactly
  the promised number - the title is a contract, not clickbait;
- closing: one humane observation or reframe, never advice, never a treatment;
- titleLines: at most 4, each at most 24 characters, sentence case, no emoji;
- never label a normal behaviour as a diagnosis, disorder, symptom or clinical
  condition; no cure/medical claims; no "high-functioning X" style labels;
- do not put the full list on screen;
- background_query: concrete objects/actions/place (e.g. "person walking forest
  trail morning light"), never a mood word like "dark aesthetic"."""
    else:
        body = f"""Write ONE reel in this format.

Format B - a 7-second static lowercase thought: one social moment, one tension.
{topic_line}
{card_block}
WRITE THE MOMENT, NOT THE TOPIC. Decide silently: ordinary social context ->
one specific awkward action or thought -> the private friction or contradiction
-> a small payoff, reversal or self-aware line. Then compress it.

Text grammar:
  [ordinary context] + [one specific action/thought] + [friction] + [payoff]
A concrete object or gesture anchors it (a glass of water, an old chat, a call
button, a photo, a desk task). First person is welcome ("me...", "i hate when",
"when i'm..."). Humour comes from the disproportion between a tiny situation and
the reaction, never from calling the reader defective.

Structure families that work (pick ONE, do not blend them):
  micro-scene + delayed consequence:  "you finally think of what to say. the
                                       conversation has already moved on."
  outer action + inner process:       "you sent haha. then spent ten minutes
                                       wondering if it sounded dismissive."
  want + hesitation:                  "you want to join the group. you're waiting
                                       for a gap that never comes."
  expectation + private contradiction:"you said you were looking forward to it.
                                       now you're rehearsing how to arrive."
  dry self-aware observation / quiet reframe.

Return ONLY this JSON:
{{"topic_id": "kebab-case-id",
  "hookLines": ["line", "line"],
  "payoffLines": ["line", "line"],
  "loop_hypothesis": "why the last line re-reads as the first on loop",
  "caption": "optional one-line extension that adds context",
  "background_query": "concrete scene nouns for ONE continuous POV shot"}}

Hard rules:
- ONE thought per reel. Do not add a second unrelated issue or turn it into an
  essay; if two ideas compete, keep the one with a concrete object;
- at most 8 lines on screen, each at most 24 characters, at most 45 words total;
- readable in 7 seconds; the LAST line must answer or complete the FIRST so a
  replay lands as a deliberate turn, not a chopped sentence. Write the pair as
  one closed loop, then split it into lines;
- ordinary spoken language, not therapist vocabulary. Avoid generic labels
  ("insecure", "awkward", "overthinking") unless the line makes them concrete;
- concrete beats broad: prefer time, place, object, message, pause or gesture
  over abstract words like anxiety, healing, confidence, trauma;
- the viewer is never the punchline, and never diagnosed;
- lowercase is applied by the template, so write naturally, no emoji;
- caption adds one layer of context; it must not simply repeat the line;
- background_query: concrete objects/actions/place, never a mood word."""
    out = f"{AUDIENCE}\n\n{body}\n\nReturn JSON only."
    if notes:
        out += (f"\nThe previous draft was rejected. Fix ONLY this: {notes}\n"
                "Keep everything that already worked; change the failing part.")
    if strict:
        out += "\nIMPORTANT: output exactly one JSON object, no prose or fences."
    return out


# -------------------------------------------------------------- Pass 4 critic
CRITICAL = ("scene_specificity", "recognition_authenticity", "natural_voice",
            "emotional_truth_dignity", "format_fit", "distinctiveness")
_SUPPORTING = ("shareability", "caption_value", "product_trust")

_RUBRIC = """Score this reel copy. Return ONLY JSON:
{{"scene_specificity":0-5,"recognition_authenticity":0-5,"natural_voice":0-5,
 "emotional_truth_dignity":0-5,"format_fit":0-5,"distinctiveness":0-5,
 "shareability":0-5,"caption_value":0-5,"product_trust":0-5,
 "hard_fail":null,
 "evidence":"<=50 words quoting the actual wording you judged",
 "sendability_who":"the exact person who would send this and to whom",
 "rewrite_notes":"<=40 words, concrete, only about what failed"}}

What a 5 looks like: scene_specificity one vivid concrete social moment, no
generic setup; recognition_authenticity a real internal thought rather than a
marketer's guess; natural_voice plain believable language, no forced slang and
no clinical jargon; emotional_truth_dignity names the tension without shaming,
diagnosing or exaggerating; format_fit A fulfils its numbered promise and B reads
instantly with one turn; distinctiveness fresh wording and a non-duplicative
angle; shareability a clear reason to send it to one particular friend;
caption_value the caption adds genuine value and the list items are distinct and
complete; product_trust any book bridge is relevant, truthful and secondary.

hard_fail must be one short phrase (or null) if ANY of these is true:
diagnostic claim from a casual checklist; fake or unsupported clinical claim;
fabricated book content; copied distinctive wording; humiliating or stigmatising
portrayal; more than one unrelated scene; manipulative sales pressure.
{format_hard_fails}

Judge the WORDING. The format and template are fixed and are never a penalty.
Generic abstraction ("social anxiety is hard", "healing takes time") is a 3 at
most, however smooth the sentence reads.

HOOK: {hook}
PAYOFF: {payoff}
CAPTION: {caption}"""


def critic_verdict(payload: dict, format_id: str, *, card: dict | None = None,
                   recent: dict | None = None) -> dict | None:
    """Pass 4. One protocol retry, then None (fail closed)."""
    if format_id == "caption_list_title_v1":
        hook = " ".join(payload.get("titleLines") or []) or str(payload.get("headline") or "")
        payoff = payload.get("closing") or (payload.get("caption") or "").splitlines()[-1]
    else:
        hook = " ".join(payload.get("hookLines") or [])
        payoff = " ".join(payload.get("payoffLines") or [])
    if format_id == "social_moment_loop_v1":
        # Measured: the shared rubric made the critic hard-fail a valid 7s loop with
        # "title/list count mismatch" - a rule that only exists for Format A.
        format_hard_fails = (
            "This is the 7-second LOOP format: there is no numbered list and no title, "
            "so a count mismatch is impossible - never report one. Hard fails for this "
            "format are: more than one unrelated moment, a sentence that does not read "
            "instantly, or a block that does not close back on its first line.")
    else:
        format_hard_fails = (
            "This is the NUMBERED LIST format: hard fails are a title/number that does "
            "not match the delivered item count, repeated filler items, or items that "
            "restate each other.")
    rubric = _RUBRIC.format(hook=hook, payoff=payoff,
                            caption=str(payload.get('caption', ''))[:600],
                            format_hard_fails=format_hard_fails)
    if card:
        rubric += ("\nThe assigned audience moment (the draft must serve this, not a "
                   "different one):\n" + json.dumps(card.get("audience_moment") or {},
                                                    ensure_ascii=False)[:400])
    if recent and recent.get("lines"):
        rubric += ("\nAlready used recently (a repeat is a distinctiveness failure):\n"
                   + json.dumps(recent["lines"][-3:])[:400])
    for attempt in (0, 1):
        try:
            raw = critic_model.chat([{"role": "user", "content": rubric +
                                      ("\n\nONLY the JSON object." if attempt else "")}],
                                    temperature=0.0, max_tokens=500)
        except Exception:  # noqa: BLE001 — a dead critic must never look like a pass
            return None
        verdict = _extract_json(raw)
        if verdict and verdict.get("scene_specificity") is not None:
            verdict["critical_min"] = min(
                [float(verdict.get(k) or 0) for k in CRITICAL] or [0])
            verdict["supporting_min"] = min(
                [float(verdict.get(k) or 0) for k in _SUPPORTING] or [0])
            verdict["passed"] = bool(
                not verdict.get("hard_fail") and verdict["critical_min"] >= 4
                and verdict["supporting_min"] >= 3)
            return verdict
    return None


def _verdict_failures(verdict: dict) -> list[str]:
    out = []
    if verdict.get("hard_fail"):
        out.append(f"hard fail: {verdict['hard_fail']}")
    for key in CRITICAL:
        if float(verdict.get(key) or 0) < 4:
            out.append(f"{key} {verdict.get(key)}/5")
    for key in _SUPPORTING:
        if float(verdict.get(key) or 0) < 3:
            out.append(f"{key} {verdict.get(key)}/5")
    return out


# ------------------------------------------------------------------- Pass 3-6
def generate(format_id: str, topic: str | None = None, *, verbose: bool = True,
             card: dict | None = None, recent: dict | None = None) -> dict:
    """Card -> write -> validate -> critic -> targeted rewrite. Raises on failure."""
    if format_id not in template_payloads.FORMATS:
        raise ValueError(f"the pilot router only knows {sorted(template_payloads.FORMATS)}")
    recent = recent if recent is not None else recent_context()
    if card is None:
        card = audience_moment(format_id, topic, recent=recent, verbose=verbose)
        if verbose:
            am = card["audience_moment"]
            print(f"[content] card ({card['compiled_by']}): {am['situation'][:70]!r}",
                  flush=True)
    notes = ""
    history = []
    ladder = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[content] writer attempt {attempt} ...", flush=True)
        raw, vendor = _writer_chat(_prompt(format_id, topic, notes, card=card))
        payload = _extract_json(raw)
        if payload is None:
            raw, vendor = _writer_chat(
                _prompt(format_id, topic, notes, strict=True, card=card))
            payload = _extract_json(raw)
        if payload is None:
            notes = "your reply was not parseable JSON"
            history.append({"attempt": attempt, "stage": "unparseable"})
            continue

        payload = _attach_rights(payload, format_id)
        errors = template_payloads.validate(payload, format_id)
        if errors:
            notes = "; ".join(errors[:4])
            history.append({"attempt": attempt, "stage": "schema", "errors": errors})
            if verbose:
                print(f"[content] attempt {attempt} failed schema: {notes[:140]}",
                      flush=True)
            continue

        creative = creative_errors(payload, format_id, recent=recent)
        lvl, why = ladder_level(payload, format_id)
        ladder.append({"attempt": attempt, "level": lvl, "why": why})
        if creative:
            notes = "; ".join(creative[:3])
            history.append({"attempt": attempt, "stage": "creative", "errors": creative,
                            "ladder": lvl})
            if verbose:
                print(f"[content] attempt {attempt} failed creative rules (ladder "
                      f"{lvl}): {notes[:140]}", flush=True)
            continue

        verdict = critic_verdict(payload, format_id, card=card, recent=recent)
        if verdict is None:
            history.append({"attempt": attempt, "stage": "critic_unparseable"})
            raise RuntimeError("critic_unparseable: no parseable verdict after one "
                               "protocol retry; failing closed")
        if verdict["passed"]:
            if verbose:
                print(f"[content] attempt {attempt} passed the critic "
                      f"(critical min {verdict['critical_min']}, ladder {lvl})",
                      flush=True)
            return {"payload": payload, "format_id": format_id, "attempts": attempt,
                    "critic": verdict, "history": history, "ladder": ladder,
                    "audience_moment": card.get("audience_moment"),
                    "card_compiled_by": card.get("compiled_by"),
                    "card_degraded": bool(card.get("degraded")),
                    "writer_vendor": vendor,
                    "independent_critic": vendor != "deepseek-bridge",
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        failures = _verdict_failures(verdict)
        notes = ("; ".join(failures[:4]) + ". " +
                 str(verdict.get("rewrite_notes") or ""))[:400]
        if verdict.get("sendability_who"):
            notes += f" (needed to reach: {verdict['sendability_who']})"
        history.append({"attempt": attempt, "stage": "critic", "verdict": verdict,
                        "ladder": lvl})
        if verbose:
            first = (payload.get("titleLines") or payload.get("hookLines") or [""])[0]
            print(f"[content] attempt {attempt} rejected: {', '.join(failures[:3])}"
                  f" | copy={first[:80]!r}", flush=True)
    raise RuntimeError(f"content generation failed after {MAX_ATTEMPTS} attempts: "
                       f"{json.dumps(history[-2:])[:300]}")


def pick_format(memory: dict | None = None) -> str:
    """Pilot router: A or B only, alternating on the preview count.

    The experiment engine later owns real assignment; this keeps the pilot from
    ever inventing a third format or letting v2/v3 compete.
    """
    posts = ((memory or {}).get("template_previews") or [])
    counts = {f: sum(1 for p in posts if p.get("format_id") == f)
              for f in template_payloads.FORMATS}
    return min(counts, key=lambda f: (counts[f], f != "caption_list_title_v1"))