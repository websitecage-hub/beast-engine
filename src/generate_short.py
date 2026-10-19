"""generate_short.py — the v3 writer (§9 step 1): template-driven short reels.

Produces the v3 content dict: {template, hook, payoff, visual_arm, scene_queries[],
audio_type, caption, hashtags}. Pipeline: writer (Meta) -> lint_short -> critic
(DeepSeek) -> up to 2 note-carrying rewrites — the same loop shape that worked for
v2, with the SHORT format's rules.

Why a different prompt from v2's writer: v2 wrote 30-60-word paced reveals read
like diary lines. v3 writes 8-22-word two-beat jokes read like memes. The critic
prompt changes accordingly: the currency is the specific friend you'd send it to,
not the tender reframe.
"""
from __future__ import annotations

import json
import os
import random

from . import config, critic_model, lint_short, llm

_TEMPLATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates.json")

# §4 — the visual arms the engine tests. BOTH are Pinterest assets (owner
# directive: "use pinterest as assets"); the arms differ in treatment, not source.
VISUAL_ARMS = ["pin_video", "pin_still"]
ARM_JOB = {
    "pin_video": "a 6-10s Pinterest video clip with the two-beat text over it",
    "pin_still": "a Pinterest still image with a slow push-in plus the same text",
}

# §5 — audio arms
AUDIO_TYPES = ["soft_instrumental", "trending_spedup", "quiet_ambient"]

# §3.1 topics -> concrete noun queries for the background search. Backgrounds are
# chosen from the reel's own nouns (§4), never from a mood word.
SCENE_NOUNS = {
    "social battery": ["crowded party from across the room", "person alone at party edge"],
    "group chats": ["phone screen glowing in dark hands"],
    "replying late": ["phone with unread messages dark room"],
    "cancelled plans": ["phone showing cancelled plan notification", "person relieved on couch at night"],
    "phone calls": ["phone ringing on table dark", "hand hesitating over phone"],
    "small talk": ["two people small talk office kitchen", "awkward hallway nod"],
    "saying I'm fine": ["person faint smile mirror night", "tired eyes close up dark"],
    "leaving parties early": ["person leaving party door night", "jacket on shoulder leaving quietly"],
    "3am replays of old conversations": ["awake in bed staring ceiling 3am", "phone glow face in dark bed"],
    "rehearsing sentences before saying them": ["person mouthing words to mirror", "words stuck throat close up"],
    "reading a text five times before answering": ["reading text message over and over phone", "thumb hovering over keyboard"],
    "being fine alone, quiet in groups": ["person content alone window rain", "quiet corner cafe alone"],
}


def templates() -> dict:
    with open(_TEMPLATES_PATH, encoding="utf-8") as f:
        return json.load(f)


def scene_queries_for(topic: str, hook: str, payoff: str) -> list:
    """§4: backgrounds come from the reel's own nouns."""
    base = list(SCENE_NOUNS.get(topic, []))
    # pull concrete nouns from the actual lines (>=5 chars, not stopwords)
    stop = {"there", "their", "about", "would", "could", "think", "think", "really",
            "night", "dark"}  # night/dark are the mood defaults we're avoiding
    toks = [w for w in lint_short.words(hook + " " + payoff) if len(w) >= 5 and w not in stop]
    for t in toks[:2]:
        base.append(f"{t} close up aesthetic video")
    return base[:4] or ["empty room night window rain"]


def writer_prompt(template: dict, topic: str, visual_arm: str, audio_type: str,
                  length_bucket: str, recent_lines: list, notes: str = "") -> str:
    t = templates()
    recent_block = "\n".join(f"- {ln}" for ln in (recent_lines or [])[-30:]) or "(none yet)"
    shots = t.get("few_shot") or []
    shot_block = "\n".join(f"- ({s['code']}, {s['template']}) {s['text']}" for s in shots)
    notes_block = ""
    if notes:
        notes_block = ("YOUR PREVIOUS DRAFT WAS REJECTED. The editor said:\n" + notes.strip()
                       + "\nRewrite completely; fix exactly what was flagged.\n")
    return f"""You write ultra-short comedy reels for @unleashthe.b (social anxiety, relatable humour,
audience 18-30 in the US/UK/Canada, scrolling at night). The reel wins when a stranger
sends it to ONE specific friend. Funny first, tender second. One idea per reel.

THE REGISTER YOU ARE WRITING IN (match this aim, NEVER copy these words — a linter
rejects any 5-content-word overlap with them):
{shot_block}

TEMPLATE THIS REEL: {template['code']} - "{template['structure']}"
Example of the STRUCTURE (never copy its words): {template['example']}
Send trigger: {template['send_trigger']}

TOPIC: {topic}
VISUAL ARM: {visual_arm} ({ARM_JOB[visual_arm]}) - scene_queries[] must name CONCRETE
nouns/visuals that match the reel's own words (a place, an object, an action). Never a mood word.
AUDIO: {audio_type}
LENGTH: {length_bucket} seconds total, hook on screen at frame 0, payoff appears at
55-65% of the runtime, hold the final frame ~0.8s so the loop back to the hook feels intended.

HARD RULES (a linter rejects violations)
- EXACTLY two beats: hook (max 14 words) + payoff (max 12 words). 8-22 words TOTAL.
- Setup in the hook, payoff at 55-65%. The payoff must share at least one word with
  the hook so the loop closes.
- Banned: cure, heal, fix, permanent, guarantee, diagnose, trauma, clinical, medication,
  suicide, self-harm, "you have social anxiety", 100%, link in bio.
- Humour targets the behaviour/situation, never the person, never an illness.
- Never copy the template example's wording or any recent reel (5+ content words).
- caption: ONE line, lowercase, like a meme page would write it (max 80 chars).
- hashtags: 3-4, lowercase, from: #relatable #introvert #socialanxiety #overthinking
  #introvertmemes #socialbattery #anxietythings #quietpeople

RECENT LINES (never paraphrase these):{recent_block}
{notes_block}
OUTPUT: one JSON object only, exactly these keys:
{{"template": "{template['code']}", "hook": "...", "payoff": "...", "topic": "{topic}",
"visual_arm": "{visual_arm}", "scene_queries": ["...", "..."], "audio_type": "{audio_type}",
"caption": "...", "hashtags": ["#..."]}}"""


def critic_prompt(draft: dict, template: dict) -> str:
    return f"""You are the editor for a relatable-memes reel account. Score this SHORT reel
(two beats on screen, ~10 seconds).

Template: {template['code']} "{template['structure']}"
Draft: {json.dumps(draft, ensure_ascii=False)}

Score 1-5, be stingy, 5 is rare:
- specificity: could this be about ANYONE? Name the exact behaviour. Generic = max 2.
- sendability: name the ONE friend this gets sent to and what they'd reply. Can't name
  them = max 2. This is the score that matters most.
- tone_safety: funny about behaviour, never mocking the person, no diagnosis/claim, nothing
  that trivialises mental illness.
- originality: is the WORDING fresh? Judge the words, not the structure: this writer is
  REQUIRED to use an assigned template (T1-T10), so using one is never a penalty. Score
  down only for genuinely stale meme phrasing or a line already seen in the wild
  ("...it's giving", "name a more iconic duo", "tell me you're X without telling me") = max 3.
  A fresh, concrete, specific line that happens to fit a template scores 4-5.
Also: loop_ok (does the payoff share a word with the hook / does it loop cleanly?) and
claims_ok (no banned claims).
Return JSON only: {{"specificity":n,"sendability":n,"tone_safety":n,"originality":n,
"loop_ok":bool,"claims_ok":bool,"sendability_who":"...","rewrite_notes":"<=50 words concrete"}}
Pass = all scores >= 4, loop_ok and claims_ok true."""


def write_one(template: dict, topic: str, visual_arm: str, audio_type: str,
              length_bucket: str = "8-10", recent_lines=None, recent_pov_count: int = 0,
              max_rewrites: int = 2, verbose: bool = True) -> dict:
    """writer -> lint -> critic -> rewrites. Returns the v3 result dict."""
    recent_lines = list(recent_lines or [])
    notes = ""
    last = {"ok": False, "stage": "not_run", "reel": None, "lint": [], "critic": {},
            "notes": "", "attempt": 0}

    for attempt in range(1, max_rewrites + 2):
        prompt = writer_prompt(template, topic, visual_arm, audio_type,
                               length_bucket, recent_lines, notes)
        try:
            raw = llm.chat([{"role": "user", "content": prompt}], expect_json=True)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "writer_error", "reel": None, "lint": [],
                    "critic": {}, "notes": str(exc)[:200], "attempt": attempt}
        if not isinstance(raw, dict):
            notes = "Return ONLY the JSON object with the required keys."
            last = {"ok": False, "stage": "no_json", "reel": None, "lint": [],
                    "critic": {}, "notes": notes, "attempt": attempt}
            continue

        hook = str(raw.get("hook") or "").strip()
        payoff = str(raw.get("payoff") or "").strip()
        beats = [hook, payoff]
        errs = lint_short.check(beats, template=template["code"], topic=topic,
                                recent_beats=recent_lines[-6:],
                                recent_pov_count=recent_pov_count)
        if errs:
            if verbose:
                print(f"[gen] attempt {attempt} failed lint: {errs}", flush=True)
            notes = "Your previous draft broke these rules: " + "; ".join(errs) + \
                    ". Fix exactly those and return the full JSON again."
            last = {"ok": False, "stage": "lint", "reel": None, "lint": errs,
                    "critic": {}, "notes": notes, "attempt": attempt,
                    "draft": beats}
            # persist every rejected draft — the owner reviews these
            try:
                from . import draftlog
                draftlog.append({"ok": False, "stage": "lint",
                                 "template": template["code"], "topic": topic,
                                 "hook": hook, "payoff": payoff, "lint": errs,
                                 "attempt": attempt})
            except Exception:  # noqa: BLE001
                pass
            continue

        # hard re-check passes; now the critic
        try:
            vraw = critic_model.chat([{"role": "user",
                                       "content": critic_prompt(
                                           {"template": template["code"], "hook": hook,
                                            "payoff": payoff, "caption": raw.get("caption", ""),
                                            "hashtags": raw.get("hashtags", [])},
                                           template)}],
                                      temperature=0.0, max_tokens=400)
        except Exception as exc:  # noqa: BLE001
            # FAIL CLOSED — a missing critic must never look like a pass.
            return {"ok": False, "stage": "critic_error", "reel": None, "lint": [],
                    "critic": {}, "notes": str(exc)[:200], "attempt": attempt}
        import re
        m = re.search(r"\{.*\}", vraw if isinstance(vraw, str) else json.dumps(vraw), re.S)
        verdict = {}
        if m:
            try:
                verdict = json.loads(m.group(0))
            except json.JSONDecodeError:
                verdict = {}
        scores = [verdict.get(k) for k in
                  ("specificity", "sendability", "tone_safety", "originality")]
        try:
            scores = [float(s) for s in scores]
        except (TypeError, ValueError):
            passed, verdict = False, {"stage": "critic_unparseable"}
        else:
            passed = (all(s >= 4 for s in scores)
                      and bool(verdict.get("loop_ok")) and bool(verdict.get("claims_ok")))
        if not passed:
            if verbose:
                print(f"[gen] attempt {attempt} failed critic: "
                      f"{json.dumps(verdict)[:150]}", flush=True)
            notes = str(verdict.get("rewrite_notes") or verdict.get("stage") or verdict)
            if verdict.get("sendability_who"):
                notes += f" (this needed to reach: {verdict['sendability_who']})"
            last = {"ok": False, "stage": "critic", "reel": None, "lint": [],
                    "critic": verdict, "notes": notes, "attempt": attempt,
                    "draft": beats}
            from . import draftlog
            draftlog.append({"ok": False, "stage": "critic",
                             "template": template["code"], "topic": topic,
                             "hook": hook, "payoff": payoff,
                             "notes": notes, "attempt": attempt})
            continue

        reel = {
            "format": "short2",
            "template": template["code"],
            "hook": hook,
            "payoff": payoff,
            "topic": topic,
            "visual_arm": visual_arm,
            "scene_queries": scene_queries_for(topic, hook, payoff),
            "audio_type": audio_type,
            "caption": str(raw.get("caption") or "").strip()[:2200],
            "hashtags": [str(h).strip().lower() for h in (raw.get("hashtags") or [])][:4],
            "length_bucket": length_bucket,
        }
        return {"ok": True, "stage": "ok", "reel": reel, "lint": [],
                "critic": verdict, "notes": "", "attempt": attempt}

    return last


def pick_template(recent_templates: list) -> dict:
    """Rotate templates; T10 capped at 1-in-5 by the caller's constraint check."""
    tlist = templates()["templates"]
    recent = (recent_templates or [])[-4:]
    # don't repeat any template within the last 4 reels
    fresh = [t for t in tlist if t["code"] not in recent]
    pool = fresh or tlist
    return random.choice(pool)
