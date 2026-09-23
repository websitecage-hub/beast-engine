"""generate.py — content strategist: 10 candidates -> judge -> dedup -> explore/exploit.

Offline mode uses canned fixture content and mutates nothing.
Dry-run performs real calls but mutates no state.
"""
from __future__ import annotations

import difflib
import json
import random
from datetime import timedelta

from . import config, llm

SYSTEM_PROMPT = (
    "You are the writer for an Instagram dark-motivation account: Unleash The Beast. "
    "You write QUOTE CARDS in the style of stoic/sigma reels: ONE aphorism per reel, "
    "12-30 words total, sentence case, wrapped naturally into 2-4 display lines, "
    "framed by typographic double quotes. Examples of the exact style: "
    "\"The moment you are disturbed by insult or pleased by praise, you are still a slave.\" "
    "- Marcus Aurelius | \"In a fight, muscles mean nothing if your heart's not ready to bleed.\" "
    "| \"A man with soft fists shouldn't sharpen his tongue.\" | \"discipline >>> motivation\". "
    "Voice: dark, direct, second person or universal truth, no fluff, no emoji, no hashtags, "
    "never 'rise and grind' cliches, no exclamation marks, never preachy. "
    "Optionally attach a short attribution (a stoic philosopher, a fighter, 'your future self', "
    "'Dad', or omit) ONLY if it strengthens the line - never invent fake quotes by living celebrities. "
    "Also choose a SCENE: a 3-8 word English search query for moody vertical VIDEO footage that "
    "visually embodies the quote's metaphor (e.g. 'hooded man shadow boxing dark gym night', "
    "'lone runner fog road dawn', 'tiger behind cage bars dark', 'night city rain streetlamp'). "
    "The footage is the metaphor; the quote is the punchline. Return ONLY valid JSON."
)

DEDUP_RATIO = 0.7
HOOK_WINDOW_DAYS = 90
PAIR_WINDOW_DAYS = 7

OFFLINE_CONTENT = {
    "quote": "The moment you are disturbed by insult or pleased by praise, you are still a slave.",
    "attribution": "Marcus Aurelius",
    "scene": "lone hooded man walking foggy road night",
    "archetype": "hard_truth",
    "topic": "discipline",
    "mood": "aggressive_phonk",
    "bg_type": "lone_figure",
    "rationale": "offline fixture",
    "exploit": True,
}


def _top_weighted(strategy: dict, family: str) -> str:
    weights = (strategy.get("weights") or {}).get(family) or {}
    if not weights:
        return ""
    return max(weights.items(), key=lambda kv: kv[1])[0]


def _recent_hooks(memory, cfg, days=HOOK_WINDOW_DAYS) -> list:
    cutoff = config.today_utc() - timedelta(days=days)
    out = []
    for h in memory.get("used_hooks", []):
        if isinstance(h, dict):
            try:
                if config.date.fromisoformat(str(h.get("date"))[:10]) >= cutoff:
                    out.append(str(h.get("hook", "")))
            except Exception:  # noqa: BLE001
                out.append(str(h.get("hook", "")))
        else:
            out.append(str(h))
    return [h for h in out if h]


def _recent_pairs(memory, days=PAIR_WINDOW_DAYS) -> set:
    cutoff = config.today_utc() - timedelta(days=days)
    pairs = set()
    for h in memory.get("used_hooks", []):
        if isinstance(h, dict):
            try:
                if config.date.fromisoformat(str(h.get("date"))[:10]) >= cutoff:
                    pairs.add((h.get("archetype"), h.get("topic")))
            except Exception:  # noqa: BLE001
                continue
    return pairs


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, (a or "").lower().strip(),
                                   (b or "").lower().strip()).ratio()


def build_user_prompt(cfg, strategy, memory, directives) -> str:
    lean = {fam: _top_weighted(strategy, fam) for fam in
            ("archetype", "topic", "mood", "bg_type")}
    past = _recent_hooks(memory, cfg)[-40:]
    refs = random.sample(past, min(5, len(past))) if past else []
    styles = config.load_trending_styles()
    style_pick = random.choice(styles) if styles else ""
    return json.dumps({
        "task": "Write exactly 10 distinct quote-card concepts.",
        "strategy_lean": lean,
        "archetypes": cfg["archetypes"],
        "topics": cfg["topics"],
        "moods": cfg["moods"],
        "bg_types": list(cfg["bg_types"].keys()),
        "style_reference_hooks": refs,
        "banned_quotes_do_not_reuse": past,
        "current_vibe_note": style_pick,
        "weekly_directives": directives or "none",
        "rules": [
            "quote: one aphorism, 12-30 words, sentence case, ends with period, no emoji",
            "attribution: short string or empty",
            "scene: 3-8 word query for moody dark vertical video footage matching the metaphor",
            "every candidate needs a DIFFERENT scene concept",
            "exploit the strategy_lean values unless you have a strong reason not to",
        ],
        "output_schema": {
            "candidates": [{
                "quote": "string with quotes omitted (engine adds them)",
                "attribution": "string or empty",
                "scene": "string",
                "archetype": "one of archetypes", "topic": "one of topics",
                "mood": "one of moods", "bg_type": "one of bg_types",
                "rationale": "why this quote + scene pairing stops the scroll",
            }]
        },
    }, ensure_ascii=False)


def judge(candidates: list) -> list:
    """Score each candidate 1-10. Returns list of {index, score, reason}."""
    payload = json.dumps({
        "task": "Score each dark-motivation quote card for Instagram Reels.",
        "score_dimensions": ["quote strength", "scroll-stop power", "share-worthiness",
                             "brand fit", "how well the scene footage embodies the metaphor"],
        "candidates": [{"index": i, "quote": c.get("quote"),
                        "attribution": c.get("attribution"), "scene": c.get("scene")}
                       for i, c in enumerate(candidates)],
        "output_schema": {"scores": [{"index": 0, "score": 1, "reason": "string"}]},
    }, ensure_ascii=False)
    try:
        out = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": payload}], expect_json=True, retries=2)
    except Exception:  # noqa: BLE001 — retry once more per error matrix
        out = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": payload}], expect_json=True, retries=1)
    scores = (out or {}).get("scores") or []
    cleaned = []
    for s in scores:
        try:
            cleaned.append({"index": int(s["index"]), "score": float(s["score"]),
                            "reason": str(s.get("reason", ""))})
        except Exception:  # noqa: BLE001
            continue
    if not cleaned:
        raise RuntimeError("judge returned no parseable scores")
    return cleaned


def _valid(cand: dict, cfg) -> bool:
    quote = _clean_q(cand.get("quote") or cand.get("hook"))
    if not quote or not (6 <= len(quote.split()) <= 45):
        return False
    if not (cand.get("scene") or "").strip():
        return False
    if cand.get("archetype") not in cfg["archetypes"]:
        return False
    if cand.get("topic") not in cfg["topics"]:
        return False
    if cand.get("mood") not in cfg["moods"]:
        return False
    if cand.get("bg_type") not in cfg["bg_types"]:
        return False
    return True


def _clean_q(text) -> str:
    return " ".join(str(text or "").split()).strip().strip('"').strip("'").strip()


def _dedup_ok(cand: dict, banned: list, recent_pairs: set) -> bool:
    quote = _clean_q(cand.get("quote") or cand.get("hook"))
    if any(_similar(quote, b) > DEDUP_RATIO for b in banned):
        return False
    if (cand.get("archetype"), cand.get("topic")) in recent_pairs:
        return False
    return True


def _hashtags(cfg, memory) -> list:
    pools = cfg["hashtag_pools"]
    tags = []
    tags += random.sample(pools["broad"], min(random.randint(3, 5), len(pools["broad"])))
    tags += random.sample(pools["medium"], min(random.randint(8, 12), len(pools["medium"])))
    tags += random.sample(pools["niche"], min(random.randint(8, 10), len(pools["niche"])))
    seen, out = set(), []
    for t in tags:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            out.append(tl)
    try:
        fresh = llm.chat([{"role": "user", "content": json.dumps({
            "task": "Invent 2 fresh niche hashtags for a dark masculine self-transformation brand. "
                    "Lowercase, no #, max 20 chars, must be plausible and brand-appropriate.",
            "output_schema": {"tags": ["string"]}})},
        ], expect_json=True, retries=2)
        for t in (fresh or {}).get("tags", [])[:2]:
            t = str(t).lower().lstrip("#").strip()
            if t and len(t) <= 20 and t not in seen:
                seen.add(t)
                out.append("#" + t)
    except Exception:  # noqa: BLE001 — hashtag invention is best-effort
        pass
    return out[:28]


def _build_caption(cfg, quote: str, attribution: str, include_cta: bool) -> str:
    parts = [quote]
    if attribution:
        parts += ["", attribution]
    caption = "\n".join(parts)
    if include_cta:
        caption += "\n\n" + cfg["cta_line"]
    return caption[:2200]


def generate(dry_run: bool = False, offline: bool = False) -> dict:
    cfg = config.load_config()
    strategy = config.load_strategy()
    memory = config.load_memory()
    directives = config.load_directives()

    if offline:
        winner = dict(OFFLINE_CONTENT)
        content = dict(winner)
        content["include_cta"] = False
        content["bg_source"] = "bundled"
        content["music_source"] = "drone"
        content["trending_ref"] = {"title": "", "artist": "", "genre": "", "trend_score": None}
        content["caption"] = (content["quote"] + "\n\n- " + content["attribution"])
        content["exploit"] = True
        config.save_content(content)
        return content

    banned = _recent_hooks(memory, cfg)
    recent_pairs = _recent_pairs(memory)

    candidates, scores = [], []
    for round_no in range(2):  # one regeneration round if everything is rejected
        raw = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(cfg, strategy, memory, directives)}],
                       expect_json=True)
        candidates = [c for c in (raw or {}).get("candidates", []) if isinstance(c, dict)]
        candidates = [c for c in candidates if _valid(c, cfg)]
        if not candidates:
            continue
        scores = judge(candidates)
        ranked = sorted((s for s in scores if 0 <= s["index"] < len(candidates)),
                        key=lambda s: s["score"], reverse=True)
        top3 = [candidates[s["index"]] for s in ranked[:3]]
        survivors = [c for c in top3 if _dedup_ok(c, banned, recent_pairs)]
        if survivors:
            break
        candidates = []
    if not candidates or not scores:
        raise RuntimeError("content generation produced no valid candidates")
    ranked = sorted((s for s in scores if 0 <= s["index"] < len(candidates)),
                    key=lambda s: s["score"], reverse=True)
    alive = [c for c in [candidates[s["index"]] for s in ranked[:3]]
             if _dedup_ok(c, banned, recent_pairs)]
    if not alive:
        raise RuntimeError("all judged candidates rejected by dedup — aborting day (no weak fallback)")

    # Explore / exploit
    if random.random() < float(cfg.get("explore_rate", 0.2)):
        winner = random.choice(candidates)
        exploit = False
    else:
        winner = alive[0]
        exploit = True

    include_cta = (memory.get("post_counter", 0) + 1) % int(cfg.get("cta_every_n_posts", 5)) == 0
    quote = _clean_q(winner.get("quote") or winner.get("hook"))
    attribution = _clean_q(winner.get("attribution"))
    content = {
        "quote": quote,
        "attribution": attribution,
        "scene": _clean_q(winner.get("scene")),
        "hook": quote,                      # keep the hook key for captions/logs
        "archetype": winner["archetype"],
        "topic": winner["topic"],
        "mood": winner["mood"],
        "bg_type": winner["bg_type"],
        "caption": _build_caption(cfg, quote, attribution, include_cta),
        "hashtags": _hashtags(cfg, memory),
        "include_cta": include_cta,
        "exploit": exploit,
        "rationale": winner.get("rationale", ""),
        "bg_source": "",
        "music_source": "",
        "trending_ref": {"title": "", "artist": "", "genre": "", "trend_score": None},
    }

    if not dry_run and not offline:
        memory.setdefault("used_hooks", []).append({
            "hook": quote, "date": config.today_utc().isoformat(),
            "archetype": content["archetype"], "topic": content["topic"],
        })
        memory.setdefault("candidate_log", []).append({
            "date": config.today_utc().isoformat(),
            "candidates": [{"quote": c.get("quote") or c.get("hook"),
                            "archetype": c.get("archetype"),
                            "topic": c.get("topic"), "mood": c.get("mood"),
                            "scene": c.get("scene")} for c in candidates],
            "scores": scores,
            "winner": quote,
        })
        config.save_memory(memory)

    config.save_content(content)
    return content
