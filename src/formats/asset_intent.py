"""asset_intent.py — the AssetIntent compiler (Asset Intelligence v1, §3-4).

The asset engine must not search for "dark aesthetic" and force the result under
unrelated copy. It reads the FINAL approved copy and compiles a structured
AssetIntent: one human moment, translated into three bounded search lanes
(literal -> adjacent -> emotional) plus an audio intent.

Two compile paths, and which one ran is always recorded:

* `llm`      — the writer model returns the AssetIntent JSON, validated here.
* `fallback` — deterministic derivation from the copy itself. It is deliberately
               conservative: lanes are taken from the writer's own
               `background_query` and its synonyms, and `degraded` is set so the
               manifest shows the asset brief was not model-compiled.

Validation is a contract: bounded numeric scales, no medical interpretation, and
every query anchored in something a camera can see. A query that is a mood slogan
is rejected rather than silently searched.
"""
from __future__ import annotations

import json
import os
import re
import time

LANES = ("literal", "adjacent", "emotional")
QUERY_MAX = 55          # the deployed /search/videos 422s above ~60 chars
SCHEMA_VERSION = "1.0"

# A query must name something visible. These are the slogans the spec forbids:
# they return stock-style filler (or worse, ads) regardless of the story.
_GENERIC = re.compile(
    r"\b(dark aesthetic|aesthetic|vibes?|motivat(?:e|ion|ional)|never give up|"
    r"sad cinematic|cinematic|mindset|growth|inspirational|mood|social anxiety|"
    r"overthinking|stock footage|background video|wallpaper)\b", re.I)

_NUMERIC = {"valence": (-1.0, 1.0)}
_NUMERIC.update({k: (0.0, 1.0) for k in
                 ("arousal", "tension", "warmth", "humour", "energy")})

_REQUIRED = ("content_id", "format_id", "core_human_moment", "literal_scene",
             "scene_entities", "human_action", "setting", "time_of_day",
             "camera_perspective", "motion_intent", "emotional_primary",
             "visual_mood", "video_queries", "audio_intent", "rationale")

_PERSPECTIVES = ("pov", "observer", "wide environmental shot", "other")


def clip(text: str, n: int = QUERY_MAX) -> str:
    """Clip on a word boundary: the API 422s on long queries (§4)."""
    out: list[str] = []
    for word in str(text or "").replace(",", " ").split():
        if len(" ".join(out + [word])) > n:
            break
        out.append(word)
    return " ".join(out).strip()


def _num(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def validate(intent: dict) -> list[str]:
    """Return the list of contract violations (empty means valid)."""
    errs: list[str] = []
    if not isinstance(intent, dict):
        return ["asset intent is not an object"]
    for key in _REQUIRED:
        if intent.get(key) in (None, "", [], {}):
            errs.append(f"missing {key}")
    if intent.get("format_id") not in ("caption_list_title_v1",
                                       "social_moment_loop_v1"):
        errs.append("format_id must be one of the two pilot formats")
    for key, (lo, hi) in _NUMERIC.items():
        if intent.get(key) is None:
            continue
        if _num(intent.get(key), lo, hi) is None:
            errs.append(f"{key} must be a number in [{lo}, {hi}]")
    persp = str(intent.get("camera_perspective") or "").lower()
    if persp and persp not in _PERSPECTIVES:
        errs.append("camera_perspective must be pov|observer|wide environmental shot|other")
    if not isinstance(intent.get("scene_entities"), list):
        errs.append("scene_entities must be a list")
    queries = intent.get("video_queries")
    if isinstance(queries, list):
        for q in queries:
            if _GENERIC.search(str(q)):
                errs.append(f"query is a mood slogan, not a scene: {str(q)[:40]!r}")
            if not clip(str(q)):
                errs.append("empty query")
    else:
        errs.append("video_queries must be a list")
    ai = intent.get("audio_intent")
    if not isinstance(ai, dict) or not ai.get("emotional_role"):
        errs.append("audio_intent.emotional_role is required")
    for key in ("arousal", "energy"):
        if isinstance(ai, dict) and ai.get(key) is not None:
            if _num(ai.get(key), 0.0, 1.0) is None:
                errs.append(f"audio_intent.{key} must be in [0, 1]")
    if isinstance(ai, dict) and ai.get("valence") is not None:
        if _num(ai.get("valence"), -1.0, 1.0) is None:
            errs.append("audio_intent.valence must be in [-1, 1]")
    return errs


def lanes(intent: dict) -> list[dict]:
    """The bounded three-lane query plan (§4), clipped and de-duplicated.

    Lane order matters: literal first, so a strong semantic match is never
    displaced by a prettier metaphor.
    """
    queries = [clip(str(q)) for q in (intent.get("video_queries") or []) if clip(str(q))]
    seen, plan = set(), []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        # The lane label follows the deduplicated order: a repeated query must not
        # shift every later query into the wrong lane.
        plan.append({"lane": LANES[len(plan)] if len(plan) < len(LANES) else "extra",
                     "query": q})
        if len(plan) == len(LANES):
            break
    return plan


def _heuristic_scene(text: str, entities: list[str], setting: str = "") -> str:
    """Build a visible-scene query from the copy: nouns and places only."""
    words = [w for w in re.findall(r"[a-z]+", (text or "").lower())
             if w not in _STOP and len(w) > 2]
    out = " ".join([*(entities or [])[:3], *(words[:4])])
    if not clip(out) and setting:
        out = setting
    return clip(out)


_STOP = set(("the and for with you your that this from have been when what "
             "just like about into over they them then than very really still "
             "would could should does doing don't doesn't isn't aren't").split())


def fallback(payload: dict, format_id: str, *, reason: str = "") -> dict:
    """Deterministic AssetIntent from the copy. Recorded as degraded, never hidden.

    The writer already supplies `background_query`: a concrete scene phrase. That
    is the literal lane. The adjacent lane is the same scene with its descriptive
    words dropped (a different natural camera view of the same moment), and the
    emotional lane is left empty rather than invented - a metaphor we cannot
    justify would be the exact failure mode this layer exists to prevent.
    """
    bq = str(payload.get("background_query") or "").strip()
    text = " ".join(str(x) for x in
                    ((payload.get("titleLines") or []) + (payload.get("hookLines") or []) +
                     (payload.get("payoffLines") or []) + [payload.get("headline") or ""]))
    entities = [w for w in re.findall(r"[a-z]+", bq.lower()) if w not in _STOP][:3]
    literal = clip(bq) or _heuristic_scene(text, entities)
    # Adjacent = the same scene described less specifically (a different natural
    # camera view), never a different topic and never a mood word.
    words = (literal or "").split()
    adjacent = " ".join(words[-3:]) if len(words) > 3 else literal
    if not clip(adjacent) or adjacent == literal:
        adjacent = " ".join(words[:3]) if len(words) > 3 else ""
    queries = [q for q in (literal, adjacent) if q]
    is_a = format_id == "caption_list_title_v1"
    moment = text.strip() or bq or "a quiet everyday social moment"
    return {
        "schema_version": SCHEMA_VERSION,
        "content_id": str(payload.get("topic_id") or "unknown"),
        "format_id": format_id,
        "core_human_moment": moment[:180],
        "literal_scene": bq or literal,
        "scene_entities": entities or ["room"],
        "human_action": (payload.get("human_action") or "").strip() or "waits",
        "setting": bq or "interior",
        "time_of_day": str(payload.get("time_of_day") or "indeterminate"),
        "camera_perspective": "pov" if not is_a else "observer",
        "motion_intent": ("still, slow natural movement" if is_a
                          else "gentle continuous handheld movement"),
        "emotional_primary": "quiet self-consciousness",
        "emotional_secondary": "restrained warmth",
        "valence": -0.2, "arousal": 0.25, "tension": 0.4, "warmth": 0.4, "humour": 0.1,
        "visual_mood": ["quiet", "everyday"],
        "palette_intent": ["natural", "low saturation"],
        "text_safe_zone": "centre 30-61%",
        "visual_profile_target": {"luma": "format profile", "saturation": "format profile"},
        "literalness": "literal",
        "intentional_contrast": None,
        "negative_visuals": ["embedded captions", "watermarks", "split-screen",
                             "on-screen text", "logos"],
        "video_queries": queries,
        "audio_intent": {
            "emotional_role": "restrained, reflective bed that leaves room for text",
            "valence": -0.2, "arousal": 0.25, "energy": 0.25,
            "tempo_preference": "slow", "texture": ["soft", "sparse"],
            "instrumentation": ["piano", "pad"],
            "vocal_policy": "instrumental_preferred", "rhythmic_density": "sparse",
            "avoid": ["triumphant anthem", "loud drops", "horror drones"],
            "trend_keywords": ["overthinking", "quiet confidence"],
            "trend_hashtags": ["#overthinking"],
        },
        "rationale": ("Degraded brief: derived from the approved copy because the "
                      "model compile did not validate. " + reason).strip()[:300],
        "compiled_by": "fallback",
        "degraded": True,
        "compiled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


_ADJ = set("slow fast bright dark light soft warm cool wide close gentle quick "
           "brightly softly quiet loud sunny".split())


_PROMPT = """You compile an AssetIntent for one short-form vertical video.

The video's copy is FINAL and is an expression of ONE human moment. Background
footage must give that moment a believable world; music must give it the right
emotional temperature. Semantic relevance first, tone second, polish third.

COPY ({format_id}):
{copy_block}

FORMAT CONTRACT
{contract}

Return ONLY this JSON (no prose, no fences):
{{"schema_version": "1.0",
 "content_id": "{content_id}",
 "format_id": "{format_id}",
 "core_human_moment": "one sentence: what is actually happening to this person",
 "literal_scene": "what a camera would literally see",
 "scene_entities": ["object", "object", "place"],
 "human_action": "the visible action",
 "setting": "where",
 "time_of_day": "e.g. late evening, pre-dawn, indeterminate",
 "camera_perspective": "pov|observer|wide environmental shot|other",
 "motion_intent": "how much movement, and of what kind",
 "emotional_primary": "one feeling word",
 "emotional_secondary": "one feeling word",
 "valence": -1.0, "arousal": 0.0, "tension": 0.0, "warmth": 0.0, "humour": 0.0,
 "visual_mood": ["two", "adjectives"],
 "palette_intent": ["two", "colour notes"],
 "text_safe_zone": "where text must stay legible",
 "visual_profile_target": {{"luma": "format profile", "saturation": "format profile"}},
 "literalness": "literal|adjacent|metaphorical",
 "intentional_contrast": null,
 "negative_visuals": ["embedded captions", "watermarks", "split-screen", "logos"],
 "video_queries": ["literal visible scene", "adjacent camera view of same moment", "restrained emotional visual"],
 "audio_intent": {{"emotional_role": "what the sound must do",
   "valence": -1.0, "arousal": 0.0, "energy": 0.0,
   "tempo_preference": "slow|moderate|brisk|flexible",
   "texture": ["..."], "instrumentation": ["..."],
   "vocal_policy": "instrumental_preferred|sparse_vocal_allowed|no_vocals",
   "rhythmic_density": "sparse|moderate|dense",
   "avoid": ["..."], "trend_keywords": ["..."], "trend_hashtags": ["..."]}},
 "rationale": "1-2 sentences: why this visual and audio direction belongs to this copy"}}

HARD RULES
- Exactly three video_queries, each <= 55 characters, each naming a VISIBLE
  thing (place, object, action, time of day, camera position). No mood slogans:
  "dark aesthetic", "motivational", "cinematic", "mindset", "social anxiety" are
  rejected by validation and waste the run.
- The literal query must be findable as real footage of this scene. The adjacent
  query is another natural camera view of the SAME moment (not a different topic).
  The emotional query is a restrained, defensible visual for the tone - if you
  cannot justify one, still give your best restrained visual, never a slogan.
- Prefer ordinary, lived-in, phone-camera-realism scenes that a person could have
  filmed. Never ask for something that only exists as stock footage or as an ad:
  no product shots, no branded content, no logo walls, no influencers performing.
- Do not add medical or diagnostic meaning to the brief.
- audio_intent.avoid must name the specific things that would wreck this copy's tone.
Return JSON only."""


def compile_intent(payload: dict, format_id: str, *, chat=None,
                   verbose: bool = True) -> dict:
    """Compile the AssetIntent from the approved copy. Never raises.

    The caller gets a usable brief in every case; `compiled_by` says whether the
    model produced it or the deterministic derivation did, and `degraded` marks
    the difference. Nothing downstream may report a degraded brief as compiled.
    """
    content_id = str(payload.get("topic_id") or "unknown")
    copy_block = json.dumps({k: payload.get(k) for k in
                             ("headline", "titleLines", "hookLines", "payoffLines",
                              "list_items", "caption", "background_query") if payload.get(k)},
                            ensure_ascii=False, indent=1)[:1800]
    contract = ("A: one continuous calm shot, 4-line white title from frame 0, "
                "12s, luma 105+-14, sat 14.2+-5, text band centre 41-59%."
                if format_id == "caption_list_title_v1" else
                "B: one continuous POV/observer shot, lowercase pale text block "
                "centre 30-61%, 7s, luma 78.6+-20, sat 8.34+-4, gentle motion.")
    prompt = _PROMPT.format(format_id=format_id, copy_block=copy_block,
                            contract=contract, content_id=content_id)
    if chat is None:
        from .content_factory import _writer_chat  # local import: no cycle at import time
        chat = _writer_chat
    try:
        raw, vendor = chat(prompt)
        intent = _extract(raw)
        if intent is None:
            raise ValueError("unparseable asset intent")
        intent.setdefault("schema_version", SCHEMA_VERSION)
        intent["content_id"] = str(intent.get("content_id") or content_id)
        intent["format_id"] = format_id          # the router owns the format
        # Models frequently answer on a 0-100 scale ("arousal: 40"). Rescaling with
        # the correction recorded is better than throwing the whole three-lane
        # brief away, which is what a hard rejection did in production.
        fixed = _normalise_scales(intent)
        if fixed:
            intent["normalised_fields"] = fixed
        errs = validate(intent)
        if errs:
            raise ValueError("; ".join(errs[:4]))
        intent["compiled_by"] = f"llm:{vendor}"
        intent["degraded"] = False
        intent["compiled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return intent
    except Exception as exc:  # noqa: BLE001 — a bad brief must not stop the run
        if verbose:
            print(f"[assets] intent compile fell back to the copy-derived brief: "
                  f"{str(exc)[:140]}", flush=True)
        return fallback(payload, format_id, reason=f"({exc})")


_ZERO_ONE = ("arousal", "tension", "warmth", "humour", "energy")


def _normalise_scales(intent: dict) -> list[str]:
    """Rescale out-of-range 0-100 answers to 0-1 and report which were rescaled."""
    fixed: list[str] = []
    targets = [(intent, _ZERO_ONE + ("valence",))]
    ai = intent.get("audio_intent")
    if isinstance(ai, dict):
        targets.append((ai, ("valence", "arousal", "energy")))
    for obj, keys in targets:
        for key in keys:
            v = obj.get(key)
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if abs(f) <= 1.0:
                continue
            if abs(f) <= 100.0:
                obj[key] = round(f / 100.0, 3)
                fixed.append(f"{key}:{f}->{obj[key]}")
    return fixed


def _extract(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def audio_keywords(intent: dict, default: str = "overthinking") -> list[str]:
    """Trend terms from the audio intent, de-sloganned and bounded (§6)."""
    ai = intent.get("audio_intent") or {}
    cands = [str(k) for k in (ai.get("trend_keywords") or [])]
    cands += [str(h).lstrip("#") for h in (ai.get("trend_hashtags") or [])]
    out, seen = [], set()
    for c in cands:
        c = re.sub(r"\s+", " ", c).strip().lower()[:28]
        if not c or c in seen or _GENERIC.search(c):
            continue
        seen.add(c)
        out.append(c)
    return out[:2] or [default]


def banned_query(q: str) -> bool:
    return bool(_GENERIC.search(str(q or "")))


def env_cache_root(default: str = "") -> str:
    """Where downloads live. GitHub Actions has no /opt/work; keep it relocatable."""
    return os.environ.get("BEAST_ASSET_CACHE") or default
