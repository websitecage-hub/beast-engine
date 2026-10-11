"""strategy_v2.py — the weekly strategist, listening job, and safety rails.

ENGINE_SPEC §2.5 (strategist), §2.6 (listening), §2.7 (auto-reset).

THE GUARDRAIL THAT MATTERS MOST

The strategist is an LLM editing its own instructions, which is exactly how an
engine talks itself out of its safety rules. It may write ONLY `DIRECTIVES.md` and
the `data/strategy.json` priors. It may not touch lint.py, the banned list, the
CTA caps, or anything in this module. That is enforced by refusing to act on any
field outside the allowlist — an LLM returning a helpful extra key does not get it
applied, and the refusal is written to REPORT.md so it is visible rather than
silent.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, learner

REPORT_PATH = Path("data/REPORT.md")
CORPUS_PATH = Path("data/corpus.json")
HISTORY_PATH = Path("data/strategy_history.json")

# The strategist's entire write surface.
ALLOWED_KEYS = {"directive_text", "retire_hooks", "promote_topics", "hypotheses",
                "confidence"}
MAX_DIRECTIVE_WORDS = 400

# §2.7 rails
RESET_POSTS = 14               # consecutive posts below the reach bar
RESET_REACH = 60
LOW_HOLD_POSTS = 10
LOW_HOLD_RATIO = 0.45
PUBLISH_ERROR_RATE = 0.30
PAUSE_HOURS = 6


# --------------------------------------------------------------- strategist

def strategist_prompt(posts: list, directives: str, priors: dict) -> str:
    slim = []
    for p in posts[-28:]:
        m = _m24(p)
        slim.append({
            "series": p.get("series"), "hook_type": p.get("hook_type"),
            "hook": p.get("hook"), "scene_family": p.get("scene_family"),
            "layout": p.get("layout"), "voice": p.get("voice"),
            "cta_type": p.get("cta_type"), "slot": p.get("slot"),
            "duration_s": p.get("duration_s"), "words": p.get("words"),
            "reach": m.get("reach"), "avg_watch_s": m.get("ig_reels_avg_watch_time"),
            "shares": m.get("shares"), "saves": m.get("saved"),
            "comments": m.get("comments"), "likes": m.get("likes"),
            "skip_rate": m.get("reels_skip_rate"), "score": p.get("score"),
        })
    return f"""You tune a content engine for a social-anxiety Instagram account. Input: the last 28 posts (attributes +
24h metrics: reach, avg_watch_s, duration_s, shares, saves, comments, likes, skip_rate if present),
the current DIRECTIVES.md, and the current strategy priors. Find what actually separates the top quartile
from the bottom (attributes AND wording: hook length, concrete nouns, send trigger). Beware small samples:
state your confidence and do not conclude from n<3. Output JSON only:
{{"directive_text":"<=400 words, plain English steering for the writer",
 "retire_hooks":[hook strings], "promote_topics":[...], "hypotheses":[{{"claim":"","test":""}}],
 "confidence":"low|medium|high"}}
You may NOT change banned phrases, the linter, CTA caps, or safety rules.

CURRENT DIRECTIVES.md:
{directives[:3000]}

CURRENT PRIORS:
{json.dumps(priors, indent=1)[:1500]}

LAST {len(slim)} POSTS:
{json.dumps(slim, indent=1)}"""


def run_strategist(dry_run: bool = False, chat=None) -> dict:
    """Ask the reasoning model for new directives; apply only allowed fields."""
    from . import critic_model
    memory = config.load_memory()
    posts = [p for p in (memory.get("posts") or []) if p.get("score") is not None]
    directives = config.load_directives()
    priors = config.load_strategy()
    if len(posts) < 3:
        return {"ok": False, "reason": f"only {len(posts)} scored posts; need >= 3",
                "n": len(posts)}

    prompt = strategist_prompt(posts, directives, priors)
    if chat is None:
        chat = lambda msgs: critic_model.chat(msgs, temperature=0.2, max_tokens=1200)
    raw = chat([{"role": "user", "content": prompt}])
    text = raw if isinstance(raw, str) else json.dumps(raw)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"ok": False, "reason": "strategist returned no JSON"}
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "reason": "strategist JSON unparseable"}

    # GUARDRAIL: anything outside the allowlist is refused and recorded.
    refused = sorted(set(out.keys()) - ALLOWED_KEYS)
    applied = {k: v for k, v in out.items() if k in ALLOWED_KEYS}

    words = len(str(applied.get("directive_text") or "").split())
    if words > MAX_DIRECTIVE_WORDS:
        applied["directive_text"] = " ".join(
            str(applied["directive_text"]).split()[:MAX_DIRECTIVE_WORDS])

    res = {"ok": True, "applied": sorted(applied.keys()), "refused": refused,
           "confidence": applied.get("confidence"),
           "n_posts": len(posts)}

    if not dry_run:
        if applied.get("directive_text"):
            _write_directives(applied["directive_text"])
        _write_report(posts, applied, refused, len(posts))
        _stamp_history(applied)
    return res


def _write_directives(text: str) -> None:
    """Replace DIRECTIVES.md, keeping the safety preamble.

    The preamble is re-prepended rather than trusted to the model: it is the one
    part of the file that must not drift.
    """
    path = Path("data/DIRECTIVES.md")
    header = ("# Directives (rewritten weekly by the strategist; read by the writer)\n\n"
              "HARD RULES THAT DO NOT CHANGE: never promise, cure, diagnose, or imply\n"
              "any technique replaces professional help. No advice in a recognition reel.\n"
              "No \"link in bio\". The linter is authoritative.\n\n")
    path.write_text(header + text.strip() + "\n", encoding="utf-8")


def _write_report(posts: list, applied: dict, refused: list, n: int) -> None:
    top = sorted(posts, key=lambda p: -(p.get("score") or 0))[:5]
    bot = sorted(posts, key=lambda p: (p.get("score") or 0))[:5]

    def row(p):
        m = _m24(p)
        return (f"| {p.get('hook','')[:44]} | {p.get('series','')} | "
                f"{m.get('reach')} | {p.get('score')} |")

    lines = [
        "# Weekly report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}  ",
        f"Scored posts considered: {n}  ",
        f"Model confidence: {applied.get('confidence')}",
        "",
        "## Statistician's hypotheses",
        "",
    ]
    for h in applied.get("hypotheses") or []:
        lines.append(f"- **{h.get('claim','')}** — test: {h.get('test','')}")
    lines += ["", "## Top 5 by score", "", "| hook | series | reach | score |",
              "|---|---|---|---|"]
    lines += [row(p) for p in top]
    lines += ["", "## Bottom 5 by score", "", "| hook | series | reach | score |",
              "|---|---|---|---|"]
    lines += [row(p) for p in bot]
    if refused:
        lines += ["", "## Refused (outside the strategist's write surface)",
                  "", ", ".join(f"`{k}`" for k in refused),
                  "", "These keys were NOT applied. The strategist may only edit "
                  "DIRECTIVES.md and strategy priors."]
    lines += ["", "## Directives written", "", str(applied.get("directive_text") or "")]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stamp_history(applied: dict) -> None:
    hist = []
    if HISTORY_PATH.exists():
        try:
            hist = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(hist, list):
                hist = []
        except Exception:  # noqa: BLE001
            hist = []
    hist.append({"at": datetime.now(timezone.utc).isoformat(),
                 "directive_text": applied.get("directive_text"),
                 "confidence": applied.get("confidence")})
    HISTORY_PATH.write_text(json.dumps(hist[-60:], indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------- listening

def listening_prompt(existing: list) -> str:
    return """Search forums and public comment sections where young adults describe social anxiety in their own words.
Return 30 NEW one-line situations (<=18 words each) describing an exact moment (what they did, in what
setting, what they feared). Paraphrase; no quotes, no usernames, no identifying details; skip anything
about self-harm. JSON array of strings. Avoid overlap with: """ + json.dumps(
        [str(x) for x in existing[-30:]])


def run_listening(dry_run: bool = False, chat=None) -> dict:
    """Refresh the situation corpus (ENGINE_SPEC §2.6).

    Stores SITUATIONS ONLY — never quotes, usernames, or identifying details. The
    prompt says so, and this function enforces it by rejecting entries that look
    like a quote or a handle, rather than trusting the model to have complied.
    """
    from . import llm
    corpus = load_corpus()
    if chat is None:
        chat = lambda msgs: llm.chat(msgs, expect_json=False)
    raw = chat([{"role": "user", "content": listening_prompt(corpus)}])
    text = raw if isinstance(raw, str) else json.dumps(raw)
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return {"ok": False, "reason": "listening returned no JSON array"}
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "reason": "listening JSON unparseable"}

    clean, rejected = [], []
    have = {str(c).strip().lower() for c in corpus}
    for it in items if isinstance(items, list) else []:
        s = " ".join(str(it).split())
        if not s or len(s.split()) > 18 or s.lower() in have:
            continue
        # A quote or a handle is a privacy failure, not a style issue.
        if any(t in s for t in ('"', "“", "”", "@")) or re.search(r"\bu/", s):
            rejected.append(s[:60])
            continue
        clean.append(s)
        have.add(s.lower())
    if not dry_run and clean:
        save_corpus(corpus + clean)
    return {"ok": True, "added": len(clean), "rejected": rejected,
            "total": len(corpus) + (0 if dry_run else len(clean))}


def load_corpus() -> list:
    if not CORPUS_PATH.exists():
        return []
    try:
        d = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:  # noqa: BLE001
        return []


def save_corpus(items: list) -> None:
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(json.dumps(items[-600:], indent=2) + "\n", encoding="utf-8")


def next_situation(memory: dict) -> str:
    """The corpus item for today's reel: least recently used, never a repeat."""
    corpus = load_corpus()
    if not corpus:
        return ""
    used = {str(p.get("source_situation") or "").strip().lower()
            for p in (memory.get("posts") or [])}
    fresh = [c for c in corpus if str(c).strip().lower() not in used]
    return fresh[0] if fresh else corpus[-1]


# --------------------------------------------------------------- safety rails

def _m24(post: dict) -> dict:
    """The 24h snapshot, falling back to the legacy flat `metrics` dict.

    WHY THE FALLBACK EXISTS: every post published before v2 stores its metrics
    flat (post["metrics"]["reach"]), not under a snapshot key. Reading only
    metrics["24h"] made all 24 legacy posts look like reach 0, which fired the
    flat-reach reset on the very first v2 run — a rail that trips on day one
    because of history it should be ignoring is worse than no rail.
    """
    m = post.get("metrics") or {}
    snap = m.get("24h")
    if isinstance(snap, dict) and snap:
        return snap
    return m if isinstance(m, dict) else {}


def reset_triggers(memory: dict) -> dict:
    """ENGINE_SPEC §2.7. Returns which rails fired, with the evidence."""
    posts = [p for p in (memory.get("posts") or [])]
    scored = [p for p in posts if p.get("score") is not None]
    out = {"flat_reach": False, "low_hold": False, "publish_errors": False}

    recent = scored[-RESET_POSTS:]
    if len(recent) >= RESET_POSTS:
        reaches = [float(_m24(p).get("reach") or 0) for p in recent]
        med = learner.median(reaches)
        any_breakout = any(
            r >= max(3 * learner.median(reaches[:-1] or [0]), 400)
            for r in reaches[-5:])
        if med < RESET_REACH and not any_breakout:
            out["flat_reach"] = True
            out["flat_reach_median"] = med

    holds = []
    for p in scored[-LOW_HOLD_POSTS:]:
        m = _m24(p)
        try:
            aw = float(m.get("ig_reels_avg_watch_time") or 0)
            dur = float(p.get("duration_s") or 0)
        except (TypeError, ValueError):
            continue
        if aw and dur:
            holds.append(aw / dur)
    if len(holds) >= LOW_HOLD_POSTS and learner.median(holds) < LOW_HOLD_RATIO:
        out["low_hold"] = True
        out["median_hold"] = round(learner.median(holds), 3)

    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    attempts = [p for p in posts
                if _after(p.get("created_at"), day_ago)]
    fails = [p for p in attempts if p.get("publish_error")]
    if attempts and len(fails) / len(attempts) > PUBLISH_ERROR_RATE:
        out["publish_errors"] = True
        out["error_rate"] = round(len(fails) / len(attempts), 2)
    return out


def _after(ts, when) -> bool:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")) > when
    except Exception:  # noqa: BLE001
        return False


def apply_rails(triggers: dict, cfg: dict) -> dict:
    """Turn fired rails into concrete forcing, without editing code.

    Returns the overrides the daily loop must honour this run.
    """
    over = {}
    if triggers.get("flat_reach"):
        over["explore_rate"] = 0.50
        over["force_bg_source"] = "pexels"
        over["force_bg_posts"] = 3
        over["force_voice_tts"] = 1
        over["force_carousel"] = 1
        over["alert"] = (f"flat reach: median {triggers.get('flat_reach_median')} "
                         f"over {RESET_POSTS} posts with no breakout")
    if triggers.get("low_hold"):
        # Shorten the length bucket rather than guessing at the script.
        over["length_bucket"] = "14-18"
        over["alert"] = (over.get("alert", "") +
                         f" | low hold: median {triggers.get('median_hold')}")
    if triggers.get("publish_errors"):
        over["pause_hours"] = PAUSE_HOURS
        over["alert"] = (over.get("alert", "") +
                         f" | publish error rate {triggers.get('error_rate')}")
    return over


def should_pause(memory: dict) -> tuple:
    """(pause, reason). A publish-error storm pauses rather than hammering."""
    t = reset_triggers(memory)
    if t.get("publish_errors"):
        return True, f"publish error rate {t.get('error_rate')} in 24h"
    return False, ""


def clone_target(posts: list) -> dict | None:
    """A breakout worth cloning (ENGINE_SPEC §2.4).

    Returns the attributes the next 5 posts must reuse, or None. The HOOK is
    deliberately not returned: the strategy says clone the winner's shape, never
    re-run the same hook.
    """
    if not posts:
        return None
    reaches = [float(_m24(p).get("reach") or 0) for p in posts]
    shares = [float(_m24(p).get("shares") or 0) for p in posts]
    if not learner.breakout(reaches, shares):
        return None
    winner = posts[-1]
    return {"series": winner.get("series"), "hook_type": winner.get("hook_type"),
            "scene_family": winner.get("scene_family"),
            "layout": winner.get("layout"),
            "clone_of": winner.get("id") or winner.get("media_id")}