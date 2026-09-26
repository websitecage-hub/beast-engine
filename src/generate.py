"""generate.py — content strategist: 10 candidates -> judge -> dedup -> explore/exploit.

Offline mode uses canned fixture content and mutates nothing.
Dry-run performs real calls but mutates no state.

v4.1: single confession PARAGRAPH reels grounded in the r/socialanxiety +
r/lonely corpus (data/research/corpus.json). Laws enforced in-prompt AND
in code. Saves+shares is the only success metric.
"""
from __future__ import annotations

import difflib
import json
import random
from datetime import timedelta
from pathlib import Path

from . import config, llm

# The Generation Directive (Brain §12) embedded verbatim, plus the
# single-paragraph contract grounded in the r/socialanxiety corpus.
SYSTEM_PROMPT = (
    "You write for one person: a young man who believes, in his core, that he is "
    "fundamentally flawed — and that every social moment is a trial that might prove "
    "it. He is not shy. He is terrified of confirmation. Describe his exact daily "
    "life — the rehearsed order, the ring-out, the freeze when the plan is ready, the "
    "trial that follows every interaction, the mouth that won't open even when he "
    "wants to speak, the body betraying him while he watches — with such precision "
    "that for the first time, being seen outweighs feeling flawed. Never advise. "
    "Never sell. Never confirm the flaw: describe what he does and feels, never what "
    "he is. Never say \"just\". Present tense, second person, his language. Hold the "
    "paradox close — he craves the exact thing he avoids — and let the rare hope "
    "reels point at the door without ever pushing him through it.\n\n"
    "You are also given REAL POSTS from people exactly like him (r/socialanxiety, "
    "r/lonely). Mine them for the exact scenes, the exact wording, the things they "
    "actually say. His language lives there: the rehearsed order fumbled anyway, "
    "the phone left to ring out, the bathroom at parties, the 2am replay, the text "
    "deleted nine times, 'annoyed when invited, sad when not', the manager who "
    "grades the silence instead of the work. Write the way those posts FEEL, "
    "tightened into one paragraph — never a quote-for-quote copy, never the post "
    "titles verbatim.\n\n"
    "Output contract: a JSON object {\"candidates\": [...]}. Each candidate has "
    "paragraph (ONE paragraph, 30-60 words, 3-6 sentences, present tense, second "
    "person, ending on the line that names the unnameable — the last sentence IS "
    "the landing), archetype, topic, mood, scene (3-8 word dark cinematic Pinterest "
    "video query matching the feeling's location), is_hope (true only for rare "
    "recovery reels written as distance traveled, never commands), and rationale. "
    "No advice, no tips, no \"just\", no emoji, no hashtags, no naming disorders, "
    "no toxic positivity, no grind. Tragic, never pathetic. The paragraph is the "
    "whole reel — it must be readable in one breath-hold and hit the 'how did they "
    "know' reflex by the second sentence.\n\n"
    "VARIETY IS MANDATORY inside one batch: no more than two candidates may share "
    "an archetype, and no more than two may open with the same pattern. Vary your "
    "sentence openings — do NOT write 'You [verb]. You [verb]. You [verb].' through "
    "a whole paragraph. Open on the scene itself sometimes: 'The order you "
    "rehearsed, fumbled anyway.', 'Two years ago, the phone rang and you let it.', "
    "'There is a version of you...', 'Same cafe, same order, same rehearsal.' "
    "The reader must never feel a template."
)

BANNED_PHRASES = ("advice", "follow these", "try this", "you should", "stop doing",
                  "start doing", "grind", "hustle", "sigma", "discipline >>>",
                  "social anxiety", "socially anxious", "anxiety disorder",
                  "just do it", "believe in yourself", "you got this")
DEDUP_RATIO = 0.7
HOOK_WINDOW_DAYS = 90
PAIR_WINDOW_DAYS = 7
CORPUS_PATH = config.ROOT / "data" / "research" / "corpus.json"
CORPUS_SAMPLE = 12          # how many real posts go into every prompt

OFFLINE_CONTENT = {
    "paragraph": ("You know the exact words. You ran them on the walk over, word for "
                  "word. Then the moment arrived and your mouth filed for silence — "
                  "and the trial starts tonight at 2am, reviewing what you didn't "
                  "say. It was never a knowledge problem."),
    "hook": "You know the exact words.",
    "scene": "empty street night rain",
    "archetype": "the_freeze",
    "topic": "exposure_fear",
    "mood": "quiet_devastating",
    "bg_type": "freeze_detour",
    "rationale": "offline fixture",
    "exploit": True,
    "is_hope": False,
}


# ------------------------------------------------------------------ corpus

def load_corpus(limit: int = 0) -> list:
    """Real confession posts (title + text) for prompt grounding."""
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
        out.append({"sub": p.get("sub", ""), "title": title,
                    "text": text[:400]})
    return out[:limit] if limit else out


def _corpus_sample() -> list:
    posts = load_corpus()
    if not posts:
        return []
    k = min(CORPUS_SAMPLE, len(posts))
    return random.sample(posts, k)


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
            ("archetype", "topic", "mood", "bg_type")}
    past = _recent_hooks(memory, cfg)[-40:]
    calib = cfg["brand"].get("hook_examples") or []
    n_posts = int(memory.get("post_counter", 0))
    hope_mod = n_posts % int(cfg.get("hope_every_n_posts", 10))
    corpus = _corpus_sample()
    return json.dumps({
        "task": ("Write exactly 10 distinct single-paragraph confession reels for "
                  "one specific person (see system). Each paragraph is one reel." +
                  (" THIS SET MUST BE A QUIET HOPE SET: recovery written as distance "
                    "traveled, never commands, still second person." if force_hope
                    else " AT MOST ONE candidate may be is_hope=true.")),
        "strategy_lean": lean,
        "archetypes": cfg["archetypes"],
        "topics": cfg["topics"],
        "moods": cfg["moods"],
        "bg_types": list(cfg["bg_types"].keys()),
        "calibration_examples": calib,
        "real_posts_from_his_people": corpus,
        "banned_hooks_do_not_reuse": past,
        "hope_quota_note": f"post_counter={n_posts}; hope_every_n_posts=10; "
                           f"hope allowed only when this batch forces it",
        "weekly_directives": directives or "none",
        "rules": [
            "paragraph: ONE paragraph, 30-60 words, 3-6 sentences, present tense, "
            "second person; the last sentence is the landing — it names the "
            "unnameable, it never advises",
            "scene: 3-8 word query for dark cinematic vertical video footage — the "
            "location of the feeling",
            "exploit the strategy_lean values unless you have a strong reason not to",
            "banned: advice, tips, the word just, grind/hustle/sigma/money, toxic "
            "positivity, confirming the flaw, emoji, hashtags, naming disorders",
            "use the real_posts as FEELING calibration — never copy sentences from "
            "them into the paragraph",
        ],
        "output_schema": {
            "candidates": [{
                "paragraph": "the whole reel, one paragraph",
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
        "task": ("Score each confession paragraph for an account whose ONLY success "
                 "metric is saves + shares from one specific socially anxious young "
                 "man. A save means 'this is me'; a share means 'this is you'."),
        "score_dimensions": ["precision of the scene ('how did they know' reflex)",
                             "save-worthiness (it IS him)",
                             "share-worthiness (sendable to the one friend)",
                             "law compliance (no advice, no 'just', no flaw-confirming)",
                             "landing-sentence strength"],
        "candidates": [{"index": i, "paragraph": c.get("paragraph"),
                        "scene": c.get("scene"), "archetype": c.get("archetype")}
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


def _laws_ok(paragraph: str) -> tuple:
    """Enforce Brain laws in code. Returns (ok, reason)."""
    low = " " + paragraph.lower() + " "
    # Law 5: the word 'just' is banned as a standalone word
    if " just " in low or " just." in low or " just," in low or " just;" in low:
        return False, "'just' appears"
    for phrase in BANNED_PHRASES:
        if phrase in low:
            return False, f"banned phrase: {phrase}"
    sentences = [s.strip() for s in paragraph.split(".") if s.strip()]
    if len(sentences) < 3:
        return False, "fewer than 3 sentences"
    return True, ""


def _valid(cand: dict, cfg) -> bool:
    paragraph = _clean_q(cand.get("paragraph"))
    if not (20 <= len(paragraph.split()) <= 80):
        return False
    ok, _why = _laws_ok(paragraph)
    if not ok:
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
    paragraph = _clean_q(cand.get("paragraph"))
    if any(_similar(paragraph, b) > DEDUP_RATIO for b in banned):
        return False
    if (cand.get("archetype"), cand.get("topic")) in recent_pairs:
        return False
    return True


def _opening_pattern(paragraph: str) -> str:
    """Classify how the paragraph opens, to block template monotony.

    'you_verb' (You smile...), 'scene' (The order you...), 'time' (Two years
    ago...), 'there' (There is...), 'other'.
    """
    low = paragraph.lower().strip()
    if low.startswith("you "):
        return "you_verb"
    if low.startswith("there "):
        return "there"
    for starter in ("two years", "three years", "last year", "years ago", "tonight",
                    "at 2am", "some nights", "every morning", "same cafe", "each time"):
        if low.startswith(starter):
            return "time"
    return "scene"


def diversify(candidates: list, max_per_archetype: int = 3,
              max_per_opening: int = 5) -> list:
    """Keep batch variety: cap archetype + opening-pattern repetition.

    Applied before judging so the LLM can't hand back ten variations of one
    template. Order is preserved (the model's own ranking intent survives).
    """
    arch_counts, open_counts, kept = {}, {}, []
    for c in candidates:
        a = c.get("archetype")
        o = _opening_pattern(_clean_q(c.get("paragraph")))
        if arch_counts.get(a, 0) >= max_per_archetype:
            continue
        if open_counts.get(o, 0) >= max_per_opening:
            continue
        arch_counts[a] = arch_counts.get(a, 0) + 1
        open_counts[o] = open_counts.get(o, 0) + 1
        kept.append(c)
    return kept or candidates


def opening_variety(candidates: list) -> int:
    """How many distinct opening patterns survive — used in the QA log."""
    return len({_opening_pattern(_clean_q(c.get("paragraph"))) for c in candidates})


# ------------------------------------------------------------ assembly

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
    return out[:20]


def _build_caption(cfg, paragraph: str, include_cta: bool, is_hope: bool) -> str:
    cap = paragraph
    if is_hope:
        cap += "\n\n(quiet hope)"
    if include_cta:
        cap += "\n\n" + cfg["cta_line"]
    return cap[:2200]


def _first_sentence(paragraph: str) -> str:
    for sep in (". ", "! ", "? "):
        if sep in paragraph:
            return paragraph.split(sep)[0] + sep.strip()
    return paragraph


def generate(dry_run: bool = False, offline: bool = False) -> dict:
    cfg = config.load_config()
    strategy = config.load_strategy()
    memory = config.load_memory()
    directives = config.load_directives()

    if offline:
        content = dict(OFFLINE_CONTENT)
        content["bg_source"] = "bundled"
        content["music_source"] = "drone"
        content["trending_ref"] = {"title": "", "artist": "", "genre": "", "trend_score": None}
        content["caption"] = _build_caption(cfg, content["paragraph"], False, False)
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
        candidates = diversify(candidates)          # kill template monotony
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
    paragraph = _clean_q(winner.get("paragraph"))
    hook = _first_sentence(paragraph)
    content = {
        "paragraph": paragraph,
        "hook": hook,                          # logs / dedup key
        "quote": paragraph,                    # legacy key: captions/logs
        "attribution": "",
        "scene": _clean_q(winner.get("scene")),
        "archetype": winner["archetype"],
        "topic": winner["topic"],
        "mood": winner["mood"],
        "bg_type": winner["bg_type"],
        "is_hope": is_hope,
        "caption": _build_caption(cfg, paragraph, include_cta, is_hope),
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
            "candidates": [{"hook": _first_sentence(_clean_q(c.get("paragraph"))),
                            "paragraph": c.get("paragraph"),
                            "archetype": c.get("archetype"),
                            "topic": c.get("topic"), "mood": c.get("mood"),
                            "scene": c.get("scene")} for c in candidates],
            "scores": scores,
            "winner": hook,
        })
        config.save_memory(memory)

    config.save_content(content)
    return content
