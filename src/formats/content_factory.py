"""content_factory.py — the writer/router path for the two pilot formats.

The two templates were first rendered from hand-authored payloads. This module
closes that gap: it asks the engine's live writer for a typed payload, validates
it with the deterministic contract, and puts it through the critic — the same
order the v3 path uses (write → lint → critic), with the same fail-closed rule.

Rules the writer cannot talk its way out of:
* Format A's headline number must equal `list_items` length, and the caption must
  contain every item. The template is never shrunk to hide a mismatch.
* Format B's block is <= 8 lines, each <= 42 chars, and must carry a loop
  hypothesis.
* Rights are never written by the model: audio/provenance are attached by the
  broker, and both default to preview-only / unknown.
* A malformed writer response gets ONE stricter-JSON retry; then it fails.
* A malformed critic response gets ONE protocol retry; then it fails closed
  (stage `critic_unparseable`) instead of burning a creative rewrite blind.
"""
from __future__ import annotations

import json
import os
import re
import time

from .. import critic_model, llm
from . import template_payloads

MAX_ATTEMPTS = 3

AUDIENCE = ("@unleashthe.b — social anxiety, 18-30, US/UK. Dry, specific, "
            "recognisable. Never clinical: no diagnosis, cure, disorder or "
            "medical claim. The test is 'would someone send this to one friend'.")


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


def _prompt(format_id: str, topic: str | None, notes: str = "", strict: bool = False) -> str:
    topic_line = f"The moment to write about: {topic}." if topic else \
        "Choose a specific everyday moment in this territory yourself."
    if format_id == "caption_list_title_v1":
        body = f"""Write ONE reel in this format.

Format A - a numbered promise on screen, the list in the caption.
{topic_line}

WRITE THE HUMAN MOMENT BEFORE THE HOOK. First decide, silently: the setting,
the action, the social stakes, the private reaction. Then compress that moment
into a title. Recognition beats explanation: the reader should think "that's me
on a Tuesday", not "that's an interesting topic".

Title grammar (all three parts, in any order):
  [specific identity or familiar inner experience]
+ [unexpected interpretation / visible pattern they had not named]
+ [finite numbered promise, 4-8]
Worked grammar (abstract on purpose, so you invent the words):
  "[a specific person or familiar experience] + [a visible pattern they have not
  named] + [N things/signs/habits they do about it]"

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
- 3-9 words per item, concrete object/action/time where possible;
- every item appears in the caption, in order, and the caption delivers exactly
  the promised number - the title is a contract, not clickbait;
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

WRITE THE MOMENT, NOT THE TOPIC. Decide silently: ordinary social context ->
one specific awkward action or thought -> the private friction or contradiction
-> a small payoff, reversal or self-aware line. Then compress it.

Text grammar:
  [ordinary context] + [one specific action/thought] + [friction] + [payoff]
A concrete object or gesture anchors it (a glass of water, an old chat, a call
button, a photo, a desk task). First person is welcome ("me...", "i hate when",
"when i'm..."). Humour comes from the disproportion between a tiny situation and
the reaction, never from calling the reader defective.

Return ONLY this JSON:
{{"topic_id": "kebab-case-id",
  "hookLines": ["line", "line"],
  "payoffLines": ["line", "line"],
  "loop_hypothesis": "why the last line re-reads as the first on loop",
  "caption": "optional one-line extension",
  "background_query": "concrete scene nouns for ONE continuous POV shot"}}

Hard rules:
- ONE thought per reel. Do not add a second unrelated issue or turn it into an
  essay; if two ideas compete, keep the one with a concrete object;
- at most 8 lines on screen, each at most 24 characters, at most 45 words total;
- readable in 7 seconds; the LAST line must answer or complete the FIRST so a
  replay lands as a deliberate turn, not a chopped sentence. Write the pair as
  one closed loop, then split it into lines;
- concrete beats broad: prefer time, place, object, message, pause or gesture
  over abstract words like anxiety, healing, confidence, trauma;
- never diagnose or label a condition, and keep the reader's dignity;
- lowercase is applied by the template, so write naturally, no emoji;
- background_query: concrete objects/actions/place, never a mood word."""
    out = f"{AUDIENCE}\n\n{body}\n\nReturn JSON only."
    if notes:
        out += f"\nThe previous draft was rejected: {notes}\nFix that specific problem."
    if strict:
        out += "\nIMPORTANT: output exactly one JSON object, no prose or fences."
    return out



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



# Copy rules the model cannot argue its way out of. These run BEFORE the critic
# so a structurally valid but creatively hollow payload never spends a critic
# call or reaches a render.
_BANNED = re.compile(
    r"\b(diagnos\w*|disorder\w*|syndrome\w*|symptom\w*|clinical\w*|cure[sd]?|"
    r"mental illness|mental health|high[- ]functioning|therapist|therapy)\b", re.I)
_STOP = set("a an the and or but to of in on at for with you your i me my it is are "
            "was were be been do does did that this so if when then than as by from".split())


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


def creative_errors(payload: dict, format_id: str) -> list[str]:
    errs: list[str] = []
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
            if errs:
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
    return errs


def _attach_rights(payload: dict, format_id: str) -> dict:
    """Rights are an evidence question, never a writer choice."""
    payload.setdefault("audio", {"source": "audi0-scraper", "rights": "preview_only"})
    payload.setdefault("provenance", {"source": "pinterest", "usage_clearance": "unknown"})
    payload["_schema_version"] = template_payloads.SCHEMA_VERSION
    return payload


def critic_verdict(payload: dict, format_id: str) -> dict | None:
    """Score the copy. One protocol retry, then None (fail closed)."""
    if format_id == "caption_list_title_v1":
        hook = " ".join(payload.get("titleLines") or [])
        payoff = payload.get("closing") or (payload.get("caption") or "").splitlines()[-1]
    else:
        hook = " ".join(payload.get("hookLines") or [])
        payoff = " ".join(payload.get("payoffLines") or [])
    rubric = f"""Score this reel copy. Return ONLY JSON:
{{"specificity":1-5,"sendability":1-5,"tone_safety":1-5,"originality":1-5,
 "loop_ok":true/false,"claims_ok":true/false,
 "sendability_who":"the exact person who would send it",
 "rewrite_notes":"<=40 words, concrete"}}

5 = unmistakably specific; 3 = generic; pass needs every score >= 4.
loop_ok applies to looping formats only: for a static-title list it is not a
gate - return true and judge nothing by it. claims_ok must be true.
originality judges the WORDING only; the format/structure is fixed and is not a penalty.
claims_ok is false for any diagnosis, cure, disorder or medical claim.

Judge the mechanics, not the vibe:
- recognition: does it name a moment the reader has actually lived, with a
  concrete object, place, time, message, pause or gesture?
- one tension: exactly one social moment, not two mixed ideas?
- promise: for a list, is every item a different behaviour rather than a
  paraphrase of its neighbour, and does the number match what is delivered?
- dignity: is the joke the disproportion of the situation, never the reader
  being defective?
Generic abstraction ("social anxiety is hard", "healing takes time") is a 3 at
most, however smooth the sentence reads.

HOOK: {hook}
PAYOFF: {payoff}
CAPTION: {payload.get('caption', '')[:600]}"""
    for attempt in (0, 1):
        try:
            raw = critic_model.chat([{"role": "user", "content": rubric +
                                      ("\n\nONLY the JSON object." if attempt else "")}],
                                    temperature=0.0, max_tokens=400)
        except Exception:  # noqa: BLE001 — a dead critic must never look like a pass
            return None
        verdict = _extract_json(raw)
        if verdict and verdict.get("specificity") is not None:
            return verdict
    return None


def generate(format_id: str, topic: str | None = None, *, verbose: bool = True) -> dict:
    """Write → validate → critic, up to MAX_ATTEMPTS. Raises on failure."""
    if format_id not in template_payloads.FORMATS:
        raise ValueError(f"the pilot router only knows {sorted(template_payloads.FORMATS)}")
    notes = ""
    history = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[content] writer attempt {attempt} ...", flush=True)
        raw, vendor = _writer_chat(_prompt(format_id, topic, notes))
        payload = _extract_json(raw)
        if payload is None:
            # one stricter-JSON protocol retry, same creative attempt
            strict_raw, vendor = _writer_chat(
                _prompt(format_id, topic, notes, strict=True))
            raw = strict_raw
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
                print(f"[content] attempt {attempt} failed schema: {notes[:140]}", flush=True)
            continue

        creative = creative_errors(payload, format_id)
        if creative:
            notes = "; ".join(creative[:3])
            history.append({"attempt": attempt, "stage": "creative", "errors": creative})
            if verbose:
                print(f"[content] attempt {attempt} failed creative rules: {notes[:140]}",
                      flush=True)
            continue

        verdict = critic_verdict(payload, format_id)
        if verdict is None:
            history.append({"attempt": attempt, "stage": "critic_unparseable"})
            raise RuntimeError("critic_unparseable: no parseable verdict after one "
                               "protocol retry; failing closed")
        scores = [verdict.get(k) for k in
                  ("specificity", "sendability", "tone_safety", "originality")]
        try:
            loop_ok = (True if format_id != "social_moment_loop_v1"
                       else bool(verdict.get("loop_ok")))
            passed = (all(float(s) >= 4 for s in scores)
                      and loop_ok and bool(verdict.get("claims_ok")))
        except (TypeError, ValueError):
            history.append({"attempt": attempt, "stage": "critic_scores"})
            continue
        if passed:
            if verbose:
                print(f"[content] attempt {attempt} passed the critic", flush=True)
            return {"payload": payload, "format_id": format_id,
                    "attempts": attempt, "critic": verdict, "history": history,
                    "writer_vendor": vendor,
                    "independent_critic": vendor != "deepseek-bridge",
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        notes = str(verdict.get("rewrite_notes") or "")
        if verdict.get("sendability_who"):
            notes += f" (needed to reach: {verdict['sendability_who']})"
        history.append({"attempt": attempt, "stage": "critic", "verdict": verdict})
        if verbose:
            first = (payload.get("titleLines") or payload.get("hookLines") or [""])[0]
            print(f"[content] attempt {attempt} rejected: {json.dumps(verdict)[:120]}"
                  f" | copy={first[:90]!r}", flush=True)
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