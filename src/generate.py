"""generate.py — content strategist: 10 candidates -> judge -> dedup -> explore/exploit.

Offline mode uses canned fixture content and mutates nothing.
Dry-run performs real calls but mutates no state.

v4.0 BRAIN: the account mirrors ONE person (social anxiety wound), script-block
reels (HOOK -> DEEPENING -> LANDING), laws enforced in-prompt and in-code.
"""
from __future__ import annotations

import difflib
import json
import random
from datetime import timedelta

from . import config, llm

# The Generation Directive (Brain §12) embedded verbatim, plus the structured
# JSON contract for script reels. Laws 1-12 are enforced in _valid() too.
SYSTEM_PROMPT = (
    "You write for one person: a young man who believes, in his core, that he is "
    "fundamentally flawed — and that every social moment is a trial that might prove "
    "it. He is not shy. He is terrified of confirmation. Describe his exact daily "
    "life — the rehearsed order, the ring-out, the freeze when the plan is ready, the "
    "trial that follows every interaction, the mouth that won't open even when he "
    "wants to speak, the body betraying him while he watches — with such precision "
    "that for the first time, being seen outweighs feeling flawed. Never advise. "
    "Never sell. Never confirm the flaw: describe what he does and feels, never what "
    "he is. Never say \"just\". Every reel: HOOK (one exact scene, 2-second hold) -> "
    "DEEPENING (under the behavior, to the fear) -> LANDING (one line that names the "
    "unnameable). Present tense, second person, his language. Hold the paradox close "
    "— he craves the exact thing he avoids — and let the rare hope reels point at the "
    "door without ever pushing him through it.\n\n"
    "Output contract: a JSON object {\"candidates\": [...]}. Each candidate has "
    "blocks (3 to 5 text blocks, in order: hook, deepening..., landing), "
    "archetype, topic, mood, scene (3-8 word dark cinematic Pinterest video query "
    "matching the feeling's location), fonts (list, same length as blocks, each one "
    "of anton, playfair_italic, cormorant_italic, caveat, bebas; the hook and landing "
    "MUST be anton; never the same font twice in a row), is_hope (boolean, true only "
    "for rare recovery reels written as distance traveled, never commands), and "
    "rationale (why this stops HIS scroll). Every block is a complete thought, "
    "present tense, second person. No advice, no tips, no \"just\", no emoji, no "
    "hashtags on screen, no naming disorders, no toxic positivity, no grind. "
    "Tragic, never pathetic."
)

BANNED_WORDS = ("just ", " just", "advice", "follow these", "try this", "you should",
                "stop doing", "start doing", "grind", "hustle", "sigma", "discipline")
DEDUP_RATIO = 0.7
HOOK_WINDOW_DAYS = 90
PAIR_WINDOW_DAYS = 7
VALID_FONTS = ("anton", "playfair_italic", "cormorant_italic", "caveat", "bebas")

OFFLINE_CONTENT = {
    "hook": "You know exactly what to say. You say nothing. Again.",
    "blocks": [
        "You know exactly what to say. You say nothing. Again.",
        "You ran the conversation on the walk over. Word for word.",
        "Then the moment came — and your body filed for silence.",
        "It was never a knowledge problem.",
    ],
    "fonts": ["anton", "playfair_italic", "cormorant_italic", "anton"],
    "scene": "empty street night rain",
    "archetype": "the_freeze",
    "topic": "exposure_fear",
    "mood": "quiet_devastating",
    "bg_type": "freeze_detour",
    "rationale": "offline fixture",
    "exploit": True,
    "is_hope": False,
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


def build_user_prompt(cfg, strategy, memory, directives, force_hope=False) -> str:
    lean = {fam: _top_weighted(strategy, fam) for fam in
            ("archetype", "topic", "mood", "bg_type")}
    past = _recent_hooks(memory, cfg)[-40:]
    refs = random.sample(past, min(5, len(past))) if past else []
    calib = cfg["brand"].get("hook_examples") or []
    n_posts = int(memory.get("post_counter", 0))
    # Hope quota: max 1 in 10. Whisper quota: max 1 in 7. Compute the next eligible
    # post indexes so the model can't overspend them.
    hope_mod = n_posts % int(cfg.get("hope_every_n_posts", 10))
    whisper_mod = n_posts % int(cfg.get("cta_every_n_posts", 7))
    return json.dumps({
        "task": ("Write exactly 10 distinct script-reel concepts for one specific "
                  "person (see system). Each is a multi-block script." +
                  (" THIS SET MUST BE A QUIET HOPE SET: recovery written as distance "
                    "traveled, never commands, still second person." if force_hope
                    else " AT MOST ONE candidate may be is_hope=true.")),
        "strategy_lean": lean,
        "archetypes": cfg["archetypes"],
        "topics": cfg["topics"],
        "moods": cfg["moods"],
        "bg_types": list(cfg["bg_types"].keys()),
        "calibration_hooks": calib,
        "style_reference_hooks": refs,
        "banned_hooks_do_not_reuse": past,
        "hope_quota_note": f"post_counter={n_posts}; hope allowed when 0 of this set is hope"
                           f" and post_counter % hope_every_n_posts == {hope_mod}",
        "weekly_directives": directives or "none",
        "rules": [
            "blocks: 3-5 complete-thought text blocks; first = HOOK (one exact scene), "
            "last = LANDING (one line naming the unnameable); middle = DEEPENING",
            "every block is present tense, second person, describes what he does/feels",
            "fonts: hook and landing MUST be anton; never the same font twice in a row; "
            "middle fonts from playfair_italic, cormorant_italic, caveat, bebas",
            "scene: 3-8 word query for dark cinematic vertical video footage — the "
            "location of the feeling",
            "exploit the strategy_lean values unless you have a strong reason not to",
            "banned: advice, tips, the word just, grind/hustle/sigma/money, toxic "
            "positivity, confirming the flaw, emoji, on-screen hashtags",
        ],
        "output_schema": {
            "candidates": [{
                "blocks": ["HOOK", "deepening line", "LANDING"],
                "fonts": ["anton", "playfair_italic", "anton"],
                "archetype": "one of archetypes", "topic": "one of topics",
                "mood": "one of moods", "bg_type": "one of bg_types",
                "scene": "string", "is_hope": False,
                "rationale": "why this stops HIS scroll",
            }]
        },
    }, ensure_ascii=False)


def judge(candidates: list) -> list:
    """Score each candidate 1-10 on saves+shares potential. Returns list of dicts."""
    payload = json.dumps({
        "task": ("Score each reel script for an account whose ONLY success metric is "
                 "saves + shares from one specific socially anxious young man. A save "
                 "means 'this is me'; a share means 'this is you'."),
        "score_dimensions": ["precision of the scene ('how did they know' reflex)",
                             "save-worthiness (it IS him)",
                             "share-worthiness (sendable to the one friend)",
                             "law compliance (no advice, no 'just', no flaw-confirming)",
                             "landing-line strength"],
        "candidates": [{"index": i, "blocks": c.get("blocks"),
                        "scene": c.get("scene"), "archetype": c.get("archetype")}
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


def _clean_q(text) -> str:
    return " ".join(str(text or "").split()).strip().strip('"').strip("'").strip()


def _laws_ok(blocks: list) -> tuple:
    """Enforce Brain laws in code. Returns (ok, reason)."""
    if not (3 <= len(blocks) <= 5):
        return False, f"block count {len(blocks)}"
    text = " ".join(blocks).lower()
    if "just" in text.replace("justified", "").replace("adjust", ""):
        # Law 5: the word 'just' is banned (allow inside other words only)
        for blk in blocks:
            low = blk.lower()
            if any(f"just{c}" in low and f"just{c}" not in ("justified", "adjust")
                   and f" just " in f" {low} " for c in (" ", ".", ",", "!", "?")):
                return False, "'just' appears"
    for word in ("advice", "you should", "try this", "follow these", "grind", "hustle",
                 "sigma", "discipline >>>", "social anxiety", "socially anxious",
                 "social anxiety disorder"):
        if word in text:
            return False, f"banned phrase: {word}"
    # Hope law: only the hook block may carry hope and it must read as distance
    # traveled — heuristic guard, the prompt carries the real rule.
    if any("just do" in b.lower() for b in blocks):
        return False, "imperative hope"
    return True, ""


def _fonts_ok(fonts: list, n_blocks: int) -> bool:
    if len(fonts) != n_blocks:
        return False
    if any(f not in VALID_FONTS for f in fonts):
        return False
    if fonts[0] != "anton" or fonts[-1] != "anton":
        return False
    for a, b in zip(fonts, fonts[1:]):
        if a == b:
            return False
    return True


def _valid(cand: dict, cfg) -> bool:
    blocks = [ _clean_q(b) for b in (cand.get("blocks") or []) ]
    blocks = [b for b in blocks if b]
    if len(blocks) < 3:
        return False
    if not all(2 <= len(b.split()) <= 30 for b in blocks):
        return False
    ok, _why = _laws_ok(blocks)
    if not ok:
        return False
    fonts = [str(f).strip() for f in (cand.get("fonts") or [])]
    if not fonts or not _fonts_ok(fonts, len(blocks)):
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


def _dedup_ok(cand: dict, banned: list, recent_pairs: set) -> bool:
    hook = _clean_q((cand.get("blocks") or [""])[0])
    if any(_similar(hook, b) > DEDUP_RATIO for b in banned):
        return False
    if (cand.get("archetype"), cand.get("topic")) in recent_pairs:
        return False
    return True


def _hashtags(cfg, memory) -> list:
    pools = cfg["hashtag_pools"]
    tags = []
    tags += random.sample(pools["broad"], min(random.randint(3, 5), len(pools["broad"])))
    tags += random.sample(pools["medium"], min(random.randint(5, 8), len(pools["medium"])))
    tags += random.sample(pools["niche"], min(random.randint(5, 8), len(pools["niche"])))
    seen, out = set(), []
    for t in tags:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            out.append(tl)
    try:
        fresh = llm.chat([{"role": "user", "content": json.dumps({
            "task": "Invent 2 fresh niche hashtags for a quiet confessional page about "
                    "social anxiety and being seen. Lowercase, no #, max 20 chars, "
                    "gentle not clinical.",
            "output_schema": {"tags": ["string"]}})},
        ], expect_json=True, retries=2)
        for t in (fresh or {}).get("tags", [])[:2]:
            t = str(t).lower().lstrip("#").strip()
            if t and len(t) <= 20 and t not in seen:
                seen.add(t)
                out.append("#" + t)
    except Exception:  # noqa: BLE001 — hashtag invention is best-effort
        pass
    return out[:20]


def _build_caption(cfg, blocks: list, include_cta: bool, is_hope: bool) -> str:
    cap = "\n\n".join(blocks)
    if is_hope:
        cap += "\n\n(quiet hope)"
    if include_cta:
        cap += "\n\n" + cfg["cta_line"]
    return cap[:2200]


def generate(dry_run: bool = False, offline: bool = False) -> dict:
    cfg = config.load_config()
    strategy = config.load_strategy()
    memory = config.load_memory()
    directives = config.load_directives()

    if offline:
        winner = dict(OFFLINE_CONTENT)
        content = dict(winner)
        content["bg_source"] = "bundled"
        content["music_source"] = "drone"
        content["trending_ref"] = {"title": "", "artist": "", "genre": "", "trend_score": None}
        content["caption"] = _build_caption(cfg, content["blocks"], False, False)
        content["hashtags"] = []
        content["include_cta"] = False
        content["exploit"] = True
        config.save_content(content)
        return content

    banned = _recent_hooks(memory, cfg)
    recent_pairs = _recent_pairs(memory)
    n_posts = int(memory.get("post_counter", 0))
    force_hope = (n_posts > 0 and n_posts % int(cfg.get("hope_every_n_posts", 10)) == 0)

    candidates, scores = [], []
    for round_no in range(2):  # one regeneration round if everything is rejected
        raw = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(
                            cfg, strategy, memory, directives, force_hope=force_hope)}],
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

    # Hope quota: if the winner is hope but the quota is spent, take the best non-hope.
    is_hope = bool(winner.get("is_hope"))
    if is_hope and not force_hope:
        non_hope = [c for c in alive if not c.get("is_hope")]
        if non_hope:
            winner = non_hope[0]
            is_hope = False

    include_cta = (n_posts + 1) % int(cfg.get("cta_every_n_posts", 7)) == 0
    blocks = [_clean_q(b) for b in (winner.get("blocks") or []) if _clean_q(b)]
    fonts = [str(f).strip() for f in (winner.get("fonts") or [])][:len(blocks)]
    while len(fonts) < len(blocks):
        fonts.append("playfair_italic")
    hook = blocks[0]
    content = {
        "hook": hook,
        "blocks": blocks,
        "fonts": fonts,
        "quote": hook,                     # legacy key: captions/logs
        "attribution": "",
        "scene": _clean_q(winner.get("scene")),
        "archetype": winner["archetype"],
        "topic": winner["topic"],
        "mood": winner["mood"],
        "bg_type": winner["bg_type"],
        "is_hope": is_hope,
        "caption": _build_caption(cfg, blocks, include_cta, is_hope),
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
            "hook": hook, "date": config.today_utc().isoformat(),
            "archetype": content["archetype"], "topic": content["topic"],
        })
        memory.setdefault("candidate_log", []).append({
            "date": config.today_utc().isoformat(),
            "candidates": [{"hook": (c.get("blocks") or [""])[0],
                            "blocks": c.get("blocks"),
                            "archetype": c.get("archetype"),
                            "topic": c.get("topic"), "mood": c.get("mood"),
                            "scene": c.get("scene")} for c in candidates],
            "scores": scores,
            "winner": hook,
        })
        config.save_memory(memory)

    config.save_content(content)
    return content
