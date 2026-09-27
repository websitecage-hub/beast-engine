"""generate.py — content strategist: 10 candidates -> judge -> dedup -> explore/exploit.

v5.0 THE COMPLETE MIND: the system prompt is src/mind.py (Part 8 directive +
evidence library + calibration examples). Candidates are script pieces
(hook / deepening x2 / landing) mapped onto 2-3 static text blocks. Character
limits, laws, loop echo and variety are enforced in code, not merely requested.
"""
from __future__ import annotations

import difflib
import json
import random
from datetime import timedelta
from pathlib import Path

from . import config, llm, mind, seo

SYSTEM_PROMPT = mind.SYSTEM_PROMPT

BANNED_PHRASES = ("advice", "follow these", "try this", "you should", "stop doing",
                  "start doing", "grind", "hustle", "sigma", "discipline >>>",
                  "social anxiety", "socially anxious", "anxiety disorder",
                  "just do it", "believe in yourself", "you got this",
                  "share this", "send this to", "link in bio", "comment below")
DEDUP_RATIO = 0.7
HOOK_WINDOW_DAYS = 90
PAIR_WINDOW_DAYS = 7
CORPUS_PATH = config.ROOT / "data" / "research" / "corpus.json"
CORPUS_SAMPLE = 12

OFFLINE_CONTENT = {
    "hook": "You know exactly what to say. You say nothing. Again.",
    "deepening": ["You ran the conversation on the walk over. Word for word.",
                  "Then the moment came — and your body filed for silence."],
    "landing": "It was never a knowledge problem.",
    "cluster": "the_freeze",
    "archetype": "the_freeze",
    "topic": "freeze_at_work",
    "mood": "quiet_devastating",
    "bg_type": "freeze",
    "loop_technique": "visual_echo",
    "scene": "empty street rain night video",
    "rationale": "offline fixture",
    "exploit": True,
    "is_hope": False,
}


# ------------------------------------------------------------------ corpus

def load_corpus(limit: int = 0) -> list:
    if not CORPUS_PATH.exists():
        return []
    try:
        posts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for p in posts:
        title = str(p.get("title") or "").strip()
        text = str(p.get("text") or "").strip()
        if not title and not text:
            continue
        out.append({"sub": p.get("sub", ""), "title": title, "text": text[:400]})
    return out[:limit] if limit else out


def _corpus_sample() -> list:
    posts = load_corpus()
    if not posts:
        return []
    return random.sample(posts, min(CORPUS_SAMPLE, len(posts)))


# --------------------------------------------------------------- prompting

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
            ("archetype", "topic", "mood", "bg_type", "loop_technique")}
    past = _recent_hooks(memory, cfg)[-40:]
    n_posts = int(memory.get("post_counter", 0))
    lim = cfg["text_limits"]
    return json.dumps({
        "task": ("Write exactly 10 distinct reel scripts for one specific person "
                  "(see the mind). Each is HOOK + 2 DEEPENING blocks + LANDING." +
                  (" THIS BATCH IS THE QUIET HOPE BATCH: recovery as distance "
                    "traveled, never commands." if force_hope
                    else " AT MOST ONE candidate may be is_hope=true.")),
        "strategy_lean": lean,
        "clusters": cfg["archetypes"],
        "topics": cfg["topics"],
        "moods": cfg["moods"],
        "bg_types": list(cfg["bg_types"].keys()),
        "loop_techniques": config.LOOP_TECHNIQUES,
        "character_limits": lim,
        "calibration_examples": cfg["brand"].get("hook_examples") or [],
        "real_posts_from_his_people": _corpus_sample(),
        "banned_hooks_do_not_reuse": past,
        "hope_quota_note": (f"post_counter={n_posts}; hope allowed roughly 1 in "
                            f"{cfg.get('hope_every_n_posts', 10)} posts"),
        "weekly_directives": directives or "none",
        "rules": [
            f"hook <= {lim['hook']} chars, each deepening <= {lim['deepening']} chars, "
            f"landing <= {lim['landing']} chars (enforced in code)",
            "the landing must ECHO the hook so the reel loops invisibly",
            "scene/bg_type: the location of the feeling (dark cinematic video)",
            "exploit the strategy_lean values unless you have a strong reason not to",
            "banned: advice, tips, the word just, grind/hustle/sigma/money, selling, "
            "share-begging, toxic positivity, confirming the flaw, emoji, hashtags",
            "use real_posts as feeling calibration — never copy their sentences",
            "vary the openings across the batch; never ten 'You ...' hooks",
        ],
        "output_schema": {
            "candidates": [{
                "hook": "<=50 chars",
                "deepening": ["<=80 chars", "<=80 chars (may send only one)"],
                "landing": "<=60 chars, echoes the hook",
                "cluster": "one of clusters", "topic": "one of topics",
                "mood": "one of moods", "bg_type": "one of bg_types",
                "loop_technique": "visual_echo | audio_echo | mid_thought",
                "is_hope": False,
                "rationale": "why this triggers 'this is exactly [friend's name]'",
            }]
        },
    }, ensure_ascii=False)


def judge(candidates: list) -> list:
    payload = json.dumps({
        "task": ("Score each reel script for an account whose ONLY success metric is "
                 "saves + sends-per-reach from one specific socially anxious young "
                 "man. A save means 'this is me'; a share means 'this is YOU, I'm "
                 "sending it to you'."),
        "score_dimensions": ["hook specificity ('this is exactly [friend's name]')",
                             "save-worthiness (it IS him)",
                             "send-worthiness (forwardable to the one friend)",
                             "law compliance (no advice, no 'just', no flaw-confirming)",
                             "landing strength + how well it echoes the hook for the loop"],
        "candidates": [{"index": i, "hook": c.get("hook"),
                        "deepening": c.get("deepening"), "landing": c.get("landing"),
                        "cluster": c.get("cluster")}
                       for i, c in enumerate(candidates)],
        "output_schema": {"scores": [{"index": 0, "score": 1, "reason": "string"}]},
    }, ensure_ascii=False)
    try:
        out = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": payload}], expect_json=True, retries=2)
    except Exception:  # noqa: BLE001
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


# ------------------------------------------------------------ law gates

def _clean_q(text) -> str:
    return " ".join(str(text or "").split()).strip().strip('"').strip("'").strip()


def text_pieces(cand: dict) -> list:
    """Ordered text blocks for the reel: hook, deepening..., landing (2-3 blocks)."""
    pieces = [_clean_q(cand.get("hook"))]
    deep = [_clean_q(d) for d in (cand.get("deepening") or [])]
    pieces += [d for d in deep if d]
    pieces.append(_clean_q(cand.get("landing")))
    return [p for p in pieces if p]


def _laws_ok(text: str) -> tuple:
    """Law gate on any text block. Returns (ok, reason)."""
    low = " " + text.lower() + " "
    if " just " in low or " just." in low or " just," in low:
        return False, "'just' appears"
    for phrase in BANNED_PHRASES:
        if phrase in low:
            return False, f"banned phrase: {phrase}"
    if "!" in text:
        return False, "exclamation mark"
    return True, ""


def _limits_ok(cand: dict, cfg) -> tuple:
    lim = cfg["text_limits"]
    hook = _clean_q(cand.get("hook"))
    deep = [_clean_q(d) for d in (cand.get("deepening") or []) if _clean_q(d)]
    landing = _clean_q(cand.get("landing"))
    if not hook or not landing:
        return False, "missing hook or landing"
    if len(hook) > lim["hook"]:
        return False, f"hook {len(hook)} > {lim['hook']} chars"
    if not (1 <= len(deep) <= 2):
        return False, f"deepening blocks = {len(deep)} (want 1-2)"
    for d in deep:
        if len(d) > lim["deepening"]:
            return False, f"deepening {len(d)} > {lim['deepening']} chars"
    if len(landing) > lim["landing"]:
        return False, f"landing {len(landing)} > {lim['landing']} chars"
    for piece in [hook] + deep + [landing]:
        if len(piece.split()) < 2:
            return False, f"fragment too short: {piece!r}"
        ok, why = _laws_ok(piece)
        if not ok:
            return False, why
    return True, ""


def _echo_score(cand: dict) -> float:
    """How much the landing shares with the hook (Part 4.2 loop).

    ADVISORY ONLY — Part 6's own calibration example (hook: "The conversation
    ends. The trial begins." / landing: "You've been cross-examining yourself
    since school.") shares no words, so lexical overlap can never be a hard
    gate. The loop is guaranteed structurally instead: the landing is rendered
    in the hook's font, size and anchor position, and the final frame's text
    position is compared against the first frame in the QA stamp check.
    """
    hook, landing = _clean_q(cand.get("hook")), _clean_q(cand.get("landing"))
    if not hook or not landing:
        return 0.0
    stop = {"you", "your", "the", "a", "an", "and", "it", "is", "was", "to", "of",
            "in", "that", "this", "for", "on", "with", "as", "at", "be", "not", "again"}
    hw = {w.strip(".,'\"—").lower() for w in hook.split()} - stop
    lw = {w.strip(".,'\"—").lower() for w in landing.split()} - stop
    if not hw:
        return 1.0
    return len(hw & lw) / max(len(hw), 1)


def _valid(cand: dict, cfg) -> bool:
    ok, _why = _limits_ok(cand, cfg)
    if not ok:
        return False
    cluster = cand.get("cluster") or cand.get("archetype")
    if cluster not in cfg["archetypes"]:
        return False
    if cand.get("topic") not in cfg["topics"]:
        return False
    if cand.get("mood") not in cfg["moods"]:
        return False
    if cand.get("bg_type") not in cfg["bg_types"]:
        return False
    if cand.get("loop_technique") not in config.LOOP_TECHNIQUES:
        return False
    return True


def _dedup_ok(cand: dict, banned: list, recent_pairs: set) -> bool:
    hook = _clean_q(cand.get("hook"))
    if any(_similar(hook, b) > DEDUP_RATIO for b in banned):
        return False
    cluster = cand.get("cluster") or cand.get("archetype")
    if (cluster, cand.get("topic")) in recent_pairs:
        return False
    return True


def _opening_pattern(text: str) -> str:
    low = _clean_q(text).lower()
    if low.startswith("you "):
        return "you"
    if low.startswith("there"):
        return "there"
    for starter in ("two years", "three years", "last year", "years ago", "tonight",
                    "at 2am", "some nights", "every morning", "same cafe", "each time",
                    "the "):
        if low.startswith(starter):
            return "scene"
    return "other"


def diversify(candidates: list, max_per_cluster: int = 3,
              max_per_opening: int = 5) -> list:
    """Cap cluster + opening-pattern repetition before judging (Part: variety)."""
    c_counts, o_counts, kept = {}, {}, []
    for c in candidates:
        cl = c.get("cluster") or c.get("archetype")
        op = _opening_pattern(_clean_q(c.get("hook")))
        if c_counts.get(cl, 0) >= max_per_cluster:
            continue
        if o_counts.get(op, 0) >= max_per_opening:
            continue
        c_counts[cl] = c_counts.get(cl, 0) + 1
        o_counts[op] = o_counts.get(op, 0) + 1
        kept.append(c)
    return kept or candidates


# ------------------------------------------------------------ assembly

def _hashtags(cfg, memory, topic: str = "", on_screen_text: str = "") -> list:
    """SEO §4: 1-2 branded + 3-4 primary + 2-3 long-tail, matched to this reel.

    Falls back to the legacy broad/medium/niche pools only if the SEO pool is absent,
    so an older config.json still generates something usable.
    """
    if "seo" in cfg or not cfg.get("hashtag_pools", {}).get("broad"):
        return seo.build_hashtags(topic=topic, on_screen_text=on_screen_text)
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
    return out[:20]


def _build_caption(cfg, hook: str, landing: str, include_whisper: bool,
                   is_hope: bool, *, body: str = "", topic: str = "",
                   on_screen_text: str = "", hashtags: list | None = None) -> str:
    """SEO §3: keyword-first caption that still reads like a person wrote it.

    Law 12: no share-CTA language ever. Law 11: the rare whisper line only.
    """
    cap = seo.build_caption(
        hook, body, landing,
        topic=topic, on_screen_text=on_screen_text, hashtags=hashtags,
        include_whisper=include_whisper,
        whisper_line=cfg.get("whisper_line", ""),
    )
    if is_hope:
        cap = cap.replace("\n\n", "\n\n(quiet hope)\n\n", 1)
    return cap[:2200]


def generate(dry_run: bool = False, offline: bool = False) -> dict:
    cfg = config.load_config()
    strategy = config.load_strategy()
    memory = config.load_memory()
    directives = config.load_directives()

    if offline:
        content = dict(OFFLINE_CONTENT)
        content["blocks"] = text_pieces(content)
        content["bg_is_video"] = False
        content["bg_source"] = "bundled"
        content["music_source"] = "drone"
        content["trending_ref"] = {"title": "", "artist": "", "genre": "", "trend_score": None}
        on_screen = " ".join(content.get("blocks") or [])
        content["on_screen_text"] = on_screen
        tags = _hashtags(cfg, memory, topic=content.get("topic", ""),
                         on_screen_text=on_screen)
        content["caption"] = _build_caption(
            cfg, content["hook"], content["landing"], False, False,
            topic=content.get("topic", ""), on_screen_text=on_screen, hashtags=tags)
        content["hashtags"] = tags
        content["alt_text"] = seo.build_alt_text(
            scene=content.get("scene", ""), topic=content.get("topic", ""),
            hook=content["hook"], on_screen_text=on_screen)
        content["include_whisper"] = False
        content["exploit"] = True
        config.save_content(content)
        return content

    banned = _recent_hooks(memory, cfg)
    recent_pairs = _recent_pairs(memory)
    n_posts = int(memory.get("post_counter", 0))
    force_hope = (n_posts > 0 and n_posts % int(cfg.get("hope_every_n_posts", 10)) == 0)

    candidates, scores = [], []
    for round_no in range(2):
        raw = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(
                            cfg, strategy, memory, directives, force_hope=force_hope)}],
                       expect_json=True)
        candidates = [c for c in (raw or {}).get("candidates", []) if isinstance(c, dict)]
        candidates = [c for c in candidates if _valid(c, cfg)]
        candidates = diversify(candidates)
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

    if random.random() < float(cfg.get("explore_rate", 0.2)):
        winner = random.choice(candidates)
        exploit = False
    else:
        winner = alive[0]
        exploit = True

    is_hope = bool(winner.get("is_hope"))
    if is_hope and not force_hope:
        non_hope = [c for c in alive if not c.get("is_hope")]
        if non_hope:
            winner = non_hope[0]
            is_hope = False

    # Law 11 — the whisper, at most 1 in 7
    include_whisper = (n_posts + 1) % int(cfg.get("whisper_every_n_posts", 7)) == 0

    hook = _clean_q(winner.get("hook"))
    landing = _clean_q(winner.get("landing"))
    cluster = winner.get("cluster") or winner.get("archetype")
    blocks = text_pieces(winner)
    # SEO: on-screen text is the whole message now (one always-visible block).
    on_screen = " ".join(blocks or [])
    tags = _hashtags(cfg, memory, topic=winner["topic"], on_screen_text=on_screen)
    content = {
        "hook": hook,
        "deepening": [_clean_q(d) for d in (winner.get("deepening") or []) if _clean_q(d)],
        "landing": landing,
        "blocks": blocks,
        "cluster": cluster,
        "archetype": cluster,                 # legacy alias
        "topic": winner["topic"],
        "mood": winner["mood"],
        "bg_type": winner["bg_type"],
        "loop_technique": winner["loop_technique"],
        "scene": _clean_q(winner.get("scene")) or "",
        "is_hope": is_hope,
        "on_screen_text": on_screen,
        "caption": _build_caption(cfg, hook, landing, include_whisper, is_hope,
                                  body=" ".join(blocks[1:2]) if len(blocks) > 1 else "",
                                  topic=winner["topic"], on_screen_text=on_screen,
                                  hashtags=tags),
        "hashtags": tags,
        "include_whisper": include_whisper,
        "exploit": exploit,
        "rationale": winner.get("rationale", ""),
        "bg_source": "",
        "bg_is_video": False,
        "music_source": "",
        "trending_ref": {"title": "", "artist": "", "genre": "", "trend_score": None},
    }

    # SEO §6: alt text is filled in once the background scene is chosen (it needs the
    # visual description), so build.py / run_create calls refresh_alt_text().
    content["alt_text"] = seo.build_alt_text(
        scene=content["scene"], topic=content["topic"], hook=hook,
        on_screen_text=on_screen)

    if not dry_run and not offline:
        memory.setdefault("used_hooks", []).append({
            "hook": hook, "date": config.today_utc().isoformat(),
            "archetype": cluster, "topic": content["topic"],
        })
        memory.setdefault("candidate_log", []).append({
            "date": config.today_utc().isoformat(),
            "candidates": [{"hook": c.get("hook"), "cluster": c.get("cluster"),
                            "topic": c.get("topic"), "mood": c.get("mood")}
                           for c in candidates],
            "scores": scores, "winner": hook,
        })
        config.save_memory(memory)

    config.save_content(content)
    return content
