"""writer.py — BEAST ENGINE v2 writer (PROMPTS.md §1), lint gate, and critic (§2).

This REPLACES mind.SYSTEM_PROMPT as the writer for the reel pipeline. The proven
plumbing is untouched: this module only produces the content dict, and
build_video/background/publish/music keep working exactly as before.

The contract, per ENGINE_SPEC §1.1:

    writer (Meta, search on) -> lint.lint() -> critic (DeepSeek, §2, all scores >=4)
      -> up to 3 rewrites carrying the critic's notes -> else skip the slot

Why the rewrite loop lives here and not in the caller: a rejected draft must be
retried with the CRITIC'S NOTES attached, not re-rolled blind. Re-rolling blind is
what produced the same defect repeatedly; the model needs to be told specifically
what was wrong with what it just wrote.

The writer never repeats a hook: recent_hooks (last 60 from memory.json) are passed
to the model AND enforced by lint.too_similar, so a model that ignores the prompt
still cannot ship a near-duplicate.
"""
from __future__ import annotations

import json
import re

from . import config, critic_model, lint, llm

# Filled at runtime from src/hooks_seed.json so the seed bank and the live prompt
# cannot drift apart.
SEED_PATH = "hooks_seed.json"

DEFAULT_SERIES = ["caught_in_the_act", "why_you_do_that", "show_them_this",
                  "the_quiet_cost", "small_steps"]
# STRATEGY §3.1
SERIES_SHARE = {"caught_in_the_act": 0.30, "why_you_do_that": 0.25,
                "show_them_this": 0.20, "the_quiet_cost": 0.15,
                "small_steps": 0.10}
SERIES_JOB = {
    "caught_in_the_act": "Mirror a specific behaviour so exactly the viewer feels watched.",
    "why_you_do_that": "Explain one mechanism plainly.",
    "show_them_this": "Written to be forwarded to the people who misread the viewer.",
    "the_quiet_cost": "What avoidance quietly takes.",
    "small_steps": "Permission and one tiny, non-clinical thing to notice.",
}
SERIES_SEND = {
    "caught_in_the_act": "this is literally me",
    "why_you_do_that": "this is why I am like this",
    "show_them_this": "read this about me",
    "the_quiet_cost": "I needed to hear that",
    "small_steps": "save",
}


def _seed() -> dict:
    """The seed bank: hook types, skeletons, series map."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, SEED_PATH), encoding="utf-8") as f:
        return json.load(f)


def hook_types() -> list:
    return sorted({h["type"] for h in _seed()["hooks"]})


def series_map() -> dict:
    return _seed()["series"]


def skeleton_for(hook_type: str) -> str:
    """One example hook per type, used as the skeleton the model must follow."""
    for h in _seed()["hooks"]:
        if h["type"] == hook_type:
            return h["text"]
    return ""


# ------------------------------------------------------------------ the prompt

def writer_system_prompt(allow_pov: bool = False) -> str:
    """PROMPTS.md §1, verbatim structure with the engine's placeholders resolved.

    The three worked examples from sample_reels.json are attached as few-shot
    examples, exactly as §1 instructs ("Include all three as few-shot examples").
    """
    seed = _seed()
    types = ", ".join(hook_types())
    prompt = f"""You write short on-screen text reels for @unleashthe.b, an account about social anxiety for people
aged 18-30 in the USA, UK and Canada. You are a calm observer who has clearly seen this up close.
You never claim a personal history, never invent a story, quote, testimonial, statistic or "client".
Your readers are scrolling at night. A reel succeeds when a stranger watches it to the end and sends
it to ONE specific person ("this is me" / "this is why I'm like this" / "read this about me").

STRUCTURE
hook (3-9 words) -> 3-5 beats, each ONE concrete, physical, specific image (<=12 words)
-> one turn that reframes the behaviour as a nervous-system protection, not a flaw
-> a closer that echoes a word/phrase from the hook so a replay feels intentional.
Total 30-60 words, 5-8 lines including the hook. Second person. Present tense.

WHAT MAKES IT SENDABLE
Name a behaviour so exact the reader feels watched ("you waited ten minutes, then typed 'sorry, just saw
this'"). Prefer the specific over the universal. Argue a point about the reader's own self-deception
or about how others misread them; do not merely describe a mood. Tender, never pitying.

HARD RULES (a linter rejects violations)
- No emoji, hashtags, exclamation marks, or "POV" unless explicitly allowed this time: {allow_pov}.
- No advice or instructions in recognition series (caught_in_the_act, why_you_do_that,
  show_them_this, the_quiet_cost). small_steps may offer ONE gentle "notice" - never a treatment.
- Banned words/claims: cure, heal, healing, permanent, guarantee, effortless, proven, "in N days",
  get rid of, clinical, diagnose, trauma, journey, toxic, "link in bio", 100%.
- Never imply the book or any technique replaces professional help. Never say "you have social anxiety";
  say what people with social anxiety often do.
- hook must be 3-9 words; every line must use second person ("you"/"your"); 5-8 lines total;
  30-60 words total on screen.

HOOK TYPES (use the one you are given this time): {types}

CAPTION (<=700 chars)
Line 1 contains a search phrase (e.g. "social anxiety", "group chat", "phone calls", "overthinking").
Then 2-3 plain sentences that add one fact or reframe the reel didn't say, then ONE real question
the reader would answer in a comment. If cta_type is guide/book, one sentence offering it. 3-5 lowercase
hashtags from the pool.

OUTPUT: one JSON object only, exactly these keys:
series, hook_type, hook, beats[], cta_type, cta_text, caption, hashtags[], scene_queries[],
scene_family, mood, layout, voice, source_situation

FEW-SHOT EXAMPLES (these pass the linter; match their specificity, do not copy their words):
{json.dumps(_samples(), indent=1)}"""
    return prompt


def _samples() -> list:
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "sample_reels.json"), encoding="utf-8") as f:
        return json.load(f)


def writer_user_prompt(series: str, hook_type: str, corpus_situation: str,
                       length_bucket: str, layout: str, cta_type: str,
                       recent_hooks: list, allow_pov: bool = False,
                       directives: str = "", critic_notes: str = "") -> str:
    parts = [
        "TASK THIS TIME",
        f"- Series: {series}  (job: {SERIES_JOB.get(series, '')})"
        f"  (send trigger: {SERIES_SEND.get(series, '')})",
        f"- Hook type: {hook_type}  (skeleton: {skeleton_for(hook_type)})",
        f"- Situation to build from: {corpus_situation}",
        f"- Length bucket: {length_bucket} seconds; layout: {layout}; CTA type: {cta_type}",
    ]
    cta_text = (config.load_config().get("cta_texts") or {}).get(cta_type, "")
    if cta_type == "none":
        parts.append('- cta_type is none, so cta_text MUST be "" and no CTA line goes on screen.')
    else:
        parts.append(f'- cta_text MUST be: "{cta_text}"')
    if recent_hooks:
        parts.append("- Do NOT reuse or paraphrase these recent hooks: "
                     + " | ".join(str(h) for h in recent_hooks[-60:]))
    if directives:
        parts.append("OPERATOR DIRECTIVES (follow these):\n" + directives.strip())
    if critic_notes:
        parts.append("YOUR PREVIOUS DRAFT WAS REJECTED. The editor said:\n"
                     + critic_notes.strip()
                     + "\nRewrite it completely. Fix exactly what was flagged.")
    return "\n".join(parts)


# ------------------------------------------------------------------ the critic

def critic_pass(reel: dict) -> tuple:
    """PROMPTS.md §2. Returns (passed, verdict_dict_or_reason)."""
    prompt = f"""You are a hard-to-please editor for a social-anxiety account. You receive one reel JSON.
Score 1-5 each, be stingy; 5 is rare:
- specificity: could this sentence be said about ANY anxious person? If yes, max 3. Does a beat name a
  concrete behaviour, object, or timing?
- sendability: name the ONE person who would be sent this and what they'd say. If you can't, max 2.
- tone_safety: tender, not pitying, not edgy; no advice in a recognition reel; no promise, cure, or
  diagnosis; nothing that could read as encouraging avoidance of help.
- originality: does the hook sound like a template ("POV", "Nobody tells you")?
Also flag claims_ok (true/false) and loop_ok (does the closer echo the hook?).
Return JSON only: {{"specificity":n,"sendability":n,"tone_safety":n,"originality":n,
"claims_ok":bool,"loop_ok":bool,"sendability_who":"...","rewrite_notes":"<=60 words, concrete"}}
Pass = all scores >= 4, claims_ok and loop_ok true.

SCORE THE WRITING, NOT THE BRIEF. The reel's cta_type (none/send/guide/book) is
ASSIGNED by the engine from a fixed publishing mix, not chosen by the writer, and
the same goes for its series and hook_type. Do NOT lower sendability or tone_safety
because of a CTA the brief required — a guide CTA on a why_you_do_that reel is a
correct pairing, not a flaw. Judge whether the LINES are specific, sendable, and
tender; judge the cta_text only for its own wording (e.g. a promise or a banned
claim in it).

REEL:
{json.dumps(reel, ensure_ascii=False, indent=1)}"""
    try:
        raw = critic_model.chat(
            [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=500)
    except Exception as exc:  # noqa: BLE001
        # FAIL CLOSED. A missing critic must never look like a pass, or the pipeline
        # ships unreviewed drafts while reporting green.
        return False, {"stage": "critic_unavailable", "error": str(exc)[:200]}

    text = raw if isinstance(raw, str) else json.dumps(raw)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return False, {"stage": "critic_unparseable", "raw": text[:200]}
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        return False, {"stage": "critic_unparseable", "raw": text[:200]}

    scores = [v.get(k) for k in ("specificity", "sendability", "tone_safety", "originality")]
    try:
        scores = [float(s) for s in scores]
    except (TypeError, ValueError):
        return False, {"stage": "critic_bad_scores", "verdict": v}
    passed = (all(s >= 4 for s in scores)
              and bool(v.get("claims_ok")) and bool(v.get("loop_ok")))
    return passed, v


# ------------------------------------------------------------------ assembly

def reel_from_raw(raw: dict, cta_type: str, series: str, hook_type: str,
                  scene_family: str, layout: str, voice: str,
                  corpus_situation: str) -> dict:
    """Normalise the model's JSON into the lint schema (missing pieces filled)."""
    cfg = config.load_config()
    beats = [str(b).strip() for b in (raw.get("beats") or []) if str(b).strip()]
    return {
        "series": series or str(raw.get("series") or ""),
        "hook_type": hook_type or str(raw.get("hook_type") or ""),
        "hook": str(raw.get("hook") or "").strip(),
        "beats": beats,
        "cta_type": cta_type,
        "cta_text": ((cfg.get("cta_texts") or {}).get(cta_type, "")
                     if cta_type != "none" else ""),
        "caption": str(raw.get("caption") or "").strip(),
        "hashtags": [str(t).strip() for t in (raw.get("hashtags") or [])],
        "scene_queries": [str(q).strip() for q in (raw.get("scene_queries") or []) if str(q).strip()][:3],
        "scene_family": scene_family or str(raw.get("scene_family") or ""),
        "mood": str(raw.get("mood") or ""),
        "layout": layout or str(raw.get("layout") or "stack"),
        "voice": voice or str(raw.get("voice") or "none"),
        "source_situation": corpus_situation,
    }


def _lines_of(reel: dict) -> list:
    return [reel["hook"]] + list(reel["beats"])


def write(series: str, hook_type: str, corpus_situation: str,
          length_bucket: str = "18-24", layout: str = "stack",
          cta_type: str = "none", voice: str = "none",
          scene_family: str = "", recent_hooks=None, allow_pov: bool = False,
          directives: str = "", max_rewrites: int = 3) -> dict:
    """One reel through writer -> lint -> critic, with up to `max_rewrites`.

    Returns {"ok": bool, "reel": dict|None, "stage": str, "notes": str,
             "lint": [...], "critic": dict, "attempt": int}
    """
    recent_hooks = list(recent_hooks or [])
    directives = directives or config.load_directives()
    notes = ""
    last = {"ok": False, "stage": "not_run", "reel": None, "lint": [],
            "critic": {}, "notes": "", "attempt": 0}

    for attempt in range(1, max_rewrites + 2):
        user = writer_user_prompt(series, hook_type, corpus_situation, length_bucket,
                                  layout, cta_type, recent_hooks, allow_pov,
                                  directives, notes)
        raw = llm.chat([{"role": "system", "content": writer_system_prompt(allow_pov)},
                        {"role": "user", "content": user}], expect_json=True)
        if not isinstance(raw, dict):
            notes = ("Your previous reply was not a JSON object. Return ONLY the "
                     "JSON object with the required keys.")
            last = {"ok": False, "stage": "no_json", "reel": None, "lint": [],
                    "critic": {}, "notes": notes, "attempt": attempt}
            continue

        reel = reel_from_raw(raw, cta_type, series, hook_type, scene_family,
                             layout, voice, corpus_situation)

        # 2. the lint gate (ENGINE_SPEC §1.1)
        errs = lint.lint(reel, recent_hooks, allow_advice=(series == "small_steps"))
        if errs:
            print(f"[writer] attempt {attempt} failed lint: {errs}", flush=True)
            notes = ("Your previous draft broke these linter rules: "
                     + "; ".join(errs) + ". Fix exactly those and return the full JSON again.")
            last = {"ok": False, "stage": "lint", "reel": reel, "lint": errs,
                    "critic": {}, "notes": notes, "attempt": attempt}
            continue

        # 3. the critic (§2). Scores are the model's judgement; lint already
        #    proved the mechanical rules, so the critic is asked only for taste.
        passed, verdict = critic_pass(reel)
        if not passed:
            print(f"[writer] attempt {attempt} failed critic: "
                  f"{json.dumps(verdict)[:160]}", flush=True)
            notes = str(verdict.get("rewrite_notes") or verdict.get("stage") or verdict)
            if verdict.get("sendability_who"):
                notes += f" (this needed to reach: {verdict['sendability_who']})"
            last = {"ok": False, "stage": "critic", "reel": reel, "lint": [],
                    "critic": verdict, "notes": notes, "attempt": attempt}
            continue

        return {"ok": True, "stage": "ok", "reel": reel, "lint": [],
                "critic": verdict, "notes": "", "attempt": attempt}

    return last


def to_content(reel: dict, memory: dict) -> dict:
    """Shape a passing reel into the content dict the proven pipeline consumes.

    build_video / background / music / publish all read this dict, so the field
    names here are the compatibility contract — they are what keeps v2 content
    flowing through the plumbing that already worked.
    """
    cfg = config.load_config()
    lines = _lines_of(reel)
    queries = reel.get("scene_queries") or []
    scene = queries[0] if queries else "dark aesthetic night city"
    family = reel.get("scene_family") or "quiet_night"
    content = {
        "format": "paced_text",
        "onscreen_text": "\n".join(lines),
        "on_screen_text": " ".join(lines),      # legacy alias kept for reports
        "caption": reel["caption"],
        "hashtags": reel["hashtags"],
        "keyword": "GUIDE" if reel["cta_type"] == "guide" else (
            "BOOK" if reel["cta_type"] == "book" else ""),
        "cta_type": reel["cta_type"],
        "cta_text": reel["cta_text"],
        # attributes the learner owns (ENGINE_SPEC §2.2)
        "series": reel.get("series") or "",
        "hook_type": reel.get("hook_type") or "",
        "hook": reel.get("hook") or "",
        "beats": reel.get("beats") or [],
        "blocks": lines,
        "layout": reel.get("layout") or "stack",
        "voice": reel.get("voice") or "none",
        "scene_family": family,
        "scene": scene,
        "scene_queries": queries,
        "mood": reel.get("mood") or "calm",
        "source_situation": reel.get("source_situation") or "",
        # pipeline settings the video stage reads
        "bg_type": family,
        "cluster": family,
        "archetype": family,
        "topic": reel.get("source_situation") or family,
        "topic_label": reel.get("source_situation") or family,
        "landing": lines[-1],
        "deepening": lines[1:-1],
        "loop_technique": "echo",
        "is_hope": reel["series"] == "small_steps",
        "include_whisper": False,
        "exploit": True,
        "rationale": f"series {reel['series']} / {reel['hook_type']}",
        "bg_source": "",
        "bg_is_video": False,
        "music_source": "",
        "trending_ref": {"title": "", "artist": "", "genre": "", "trend_score": None},
    }
    # duration comes from the reveal schedule, not a fixed 9-10s
    starts, dur = lint.reveal_schedule(lines)
    content["line_starts"] = starts
    content["duration_s"] = dur
    content["words"] = sum(len(lint.words(x)) for x in lines)
    return content