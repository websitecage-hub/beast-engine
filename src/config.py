"""config.py — state loading, path constants, embedded defaults.

v5.0 THE COMPLETE MIND: clusters from Part 2, DNA from Part 7, format from Part 5.
Every run reads data/*.json at start and the orchestrator commits it back at end.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
ASSETS = ROOT / "assets"
TMP = ROOT / "tmp"
LOGS = DATA / "logs"
FIXTURES = ROOT / "tests" / "fixtures"

CONFIG_PATH = DATA / "config.json"
STRATEGY_PATH = DATA / "strategy.json"
MEMORY_PATH = DATA / "memory.json"
TRENDING_STYLES_PATH = DATA / "trending_styles.json"
TOKEN_STATE_PATH = DATA / "token_state.json"
DIRECTIVES_PATH = DATA / "DIRECTIVES.md"
PAUSE_PATH = DATA / "PAUSE"
CONTENT_PATH = OUTPUTS / "content.json"
REPORT_PATH = DATA / "REPORT.md"
AUDIO_MANIFEST_PATH = ASSETS / "audio" / "manifest.json"

BUILD_DATE = date(2026, 9, 20)

_FFMPEG_CACHE = None


def resolve_ffmpeg() -> str:
    """Return a usable ffmpeg executable path.

    ffmpeg is a hard runtime dependency — src/ shells out to it in four modules, so
    a missing binary means no reel can be rendered at all. On the dev box ffmpeg was
    only reachable through a hand-made symlink to imageio-ffmpeg's bundled binary,
    which CI does not have; that masked a total absence of ffmpeg on the runner and
    every CI create run silently skipped before it ever tried to render.

    Resolution order:
      1. BEAST_FFMPEG env override (set it to pin an exact binary)
      2. ffmpeg on PATH (the normal case: apt/pip in CI, system package elsewhere)
      3. imageio-ffmpeg's bundled binary (pipped in via requirements.txt)

    Cached, because this is called on every ffmpeg invocation.
    """
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE

    override = os.environ.get("BEAST_FFMPEG")
    if override and Path(override).exists():
        _FFMPEG_CACHE = override
        return _FFMPEG_CACHE

    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_CACHE = found
        return _FFMPEG_CACHE

    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            _FFMPEG_CACHE = bundled
            return _FFMPEG_CACHE
    except Exception:
        pass

    # Nothing found. Return the bare name so the caller raises a normal
    # FileNotFoundError with a helpful message rather than a confusing None.
    _FFMPEG_CACHE = "ffmpeg"
    return _FFMPEG_CACHE


# Backwards-compatible alias: modules call config.FFMPEG in subprocess arg lists.
FFMPEG = "ffmpeg"      # replaced on first resolve_ffmpeg() by callers

# Part 2.1 — the eleven evidence clusters (archetypes in the DNA)
ARCHETYPES = ["the_mask", "the_rehearsal", "the_freeze", "the_detour", "the_aftermath",
              "the_bodys_betrayal", "the_sealed_mouth", "the_craving", "the_losses",
              "the_buried_anger", "quiet_hope"]

# Part 7.1 — tracked topics
TOPICS = ["exposure_fear", "fake_phone", "ordering_food", "phone_calls", "freeze_at_work",
          "asking_coworker", "party_bathroom", "mind_blank", "post_interaction_hate",
          "replay_2am", "read_receipts", "neutral_as_negative", "meetings_voice",
          "dating_app_freeze", "dying_alone_thought", "lost_friendships", "behind_at_25",
          "buried_anger", "therapy_irony", "the_grandma_win"]

# audio moods (Part 5.4 / evidence library sound families)
MOODS = ["quiet_devastating", "heavy_shadow", "muffled_world", "restrained_anger",
         "gentle_hope"]

# Part 5.2 — background VIDEO queries, keyed 1:1 with the clusters
BG_TYPES = ["mask", "rehearsal", "freeze", "detour", "aftermath", "bodys_betrayal",
            "sealed_mouth", "craving", "losses", "buried_anger", "quiet_hope"]

LOOP_TECHNIQUES = ["visual_echo", "audio_echo", "mid_thought"]

DEFAULT_CONFIG = {
    "brand": {
        "name": "Unleash The Beast",
        "handle": "unleashthe.b",
        "meaning": ("The real person under the mask. Not aggression, not grind. The self "
                    "buried under years of small retreats who still wants to be known."),
        "voice": ("Describe, never advise. Present tense, second person, his language — "
                  "never a psychologist's. Never sell, never confirm the flaw, never say "
                  "'just'. Tragic, never pathetic."),
        "hook_examples": [
            "You know exactly what to say. You say nothing. Again.",
            "The conversation ends. The trial begins.",
            "You want to talk. Your mouth disagrees.",
            "She matched with you. And you're suspicious.",
            "Your manager thinks you're less competent than you are.",
            "You pull out your phone when someone walks by. Nothing's on it.",
        ],
    },
    "archetypes": ARCHETYPES,
    "topics": TOPICS,
    "moods": MOODS,
    "bg_types": {
        # FINAL FORMAT §3 — MUST contain a PERSON/HUMAN FIGURE (not just rain,
        # city lights or smoke). Ordered person-first; each cluster keeps its
        # emotional register through the *setting*, while every query guarantees
        # human presence.
        "mask": ["man standing crowd night video aesthetic",
                 "person walking city crowd night video"],
        "rehearsal": ["man looking out window rain video",
                      "person sitting alone cafe window video aesthetic"],
        "freeze": ["man walking alone night video aesthetic",
                   "lone figure walking city night video"],
        "detour": ["back of person walking night video",
                   "person walking away night video aesthetic"],
        "aftermath": ["person sitting alone dark aesthetic video",
                      "man sitting bed night video aesthetic"],
        "bodys_betrayal": ["man face shadow dark video aesthetic",
                           "person shadow dark aesthetic video"],
        "sealed_mouth": ["silhouette man dark video aesthetic",
                         "silhouette person street light night video"],
        "craving": ["man looking at phone dark room video",
                    "person phone glow night video aesthetic"],
        "losses": ["person alone train night video aesthetic",
                   "man standing empty station night video"],
        "buried_anger": ["man standing cliff dark video aesthetic",
                         "silhouette man storm video aesthetic"],
        "quiet_hope": ["person standing sunrise silhouette video",
                       "man walking dawn light video aesthetic"],
    },
    # cluster -> bg cluster (1:1 except the body-betrayal phrasing variants)
    "archetype_bg_map": {
        "the_mask": "mask", "the_rehearsal": "rehearsal", "the_freeze": "freeze",
        "the_detour": "detour", "the_aftermath": "aftermath",
        "the_bodys_betrayal": "bodys_betrayal", "the_sealed_mouth": "sealed_mouth",
        "the_craving": "craving", "the_losses": "losses",
        "the_buried_anger": "buried_anger", "quiet_hope": "quiet_hope",
    },
    # Part 7.1 — cluster -> preferred audio mood
    "cluster_mood_map": {
        "the_mask": "muffled_world", "the_rehearsal": "quiet_devastating",
        "the_freeze": "quiet_devastating", "the_detour": "heavy_shadow",
        "the_aftermath": "heavy_shadow", "the_bodys_betrayal": "heavy_shadow",
        "the_sealed_mouth": "heavy_shadow", "the_craving": "quiet_devastating",
        "the_losses": "quiet_devastating", "the_buried_anger": "restrained_anger",
        "quiet_hope": "gentle_hope",
    },
    "mood_fallback_bpm": {"quiet_devastating": 65, "heavy_shadow": 55,
                          "muffled_world": 70, "restrained_anger": 85,
                          "gentle_hope": 60},
    "music": {
        "trending_api": "https://audi0-scraper.onrender.com",
        "trending_niche": "self-improvement",
        "trending_fallback_niche": "motivation",
        "min_confidence": 0.5,
        # Part 4.5 — Phase 2 adoption window (days 4-8 of a sound's rise):
        # not the already-peaked top of the chart, not the untested bottom.
        "phase2_score_band": [0.35, 0.85],
        # VISUAL SPEC v1.0 §5: dark trending only — phonk / dark ambient / slowed
        # reverb. Explicitly NOT piano, NOT lofi, NOT generic ambient.
        "mood_search": {
            "quiet_devastating": "slow dark ambient reverb deep sub bass",
            "heavy_shadow": "dark ambient drone sub bass",
            "muffled_world": "slowed reverb dark ambient muffled",
            "restrained_anger": "slow dark phonk soft",
            "gentle_hope": "slow dark ambient build cinematic",
        },
        # Extra queries per mood. One query is one result set: the canonical phrase
        # above returned the SAME track for two consecutive reels, so the song tier
        # walks these in order and rejects anything already fingerprinted. All are
        # inside the §5 palette (no piano/lofi/upbeat) — _spec5_violates still runs.
        "query_hints": {
            "quiet_devastating": [
                "dark ambient reverb drone instrumental",
                "slow cinematic sub bass no drums",
                "night reverb ambient texture",
                "deep atmospheric drone loop",
            ],
            "heavy_shadow": [
                "dark drone ambient instrumental",
                "slow sub bass cinematic no drums",
                "brooding ambient texture loop",
                "deep dark pad drone",
            ],
            "muffled_world": [
                "muffled reverb ambient instrumental",
                "distant slowed ambient drone",
                "underwater reverb texture",
                "soft muted dark ambient loop",
            ],
            "restrained_anger": [
                "slow dark instrumental tension",
                "low phonk instrumental no vocals",
                "tense cinematic drone",
                "dark restrained ambient loop",
            ],
            "gentle_hope": [
                "slow ambient build instrumental",
                "cinematic hope drone soft",
                "gentle dark ambient swell",
                "warm reverb pad cinematic",
            ],
        },
        "banned_music_terms": ["piano", "lofi", "lo-fi", "lo fi", "cheerful",
                               "upbeat", "acoustic guitar"],
        "genre_hint": {"rap": " dark phonk", "hip-hop": " dark phonk", "trap": " dark phonk",
                       "edm": " dark trap", "pop": " emotional", "rock": " cinematic",
                       "rnb": " moody", "lofi": " slowed reverb dark"},
    },
    "posting_slots_utc": ["13:30", "15:00", "16:30", "21:30"],
    # Part 5.5 — 9-10 seconds, locked
    "reel": {"min_s": 9.0, "max_s": 10.0, "fps": 30, "w": 1080, "h": 1920},
    "timing": {"hook_end_frac": 0.45, "deepen_end_frac": 0.88},  # spec §4 (see build_video.state_map)
    "text_limits": {"hook": 60, "deepening": 90, "landing": 70,
                    # TEXT ENGINE §3: 6-9 lines on screen (5-10 absolute bounds in §8.8).
                    # `line` is a wrap width, not a per-line cap: a story line runs
                    # ~21 words. `lines_max` is the hard ceiling the renderer and QA
                    # both enforce.
                    "line": 68, "lines_max": 10, "lines_min": 5,
                    "lines_target_min": 6, "lines_target_max": 9},
    "bg_darken": 0.0,          # NO extra darkening: the footage ships as shot
    "bg_grade": False,         # no brightness/saturation crush on the source video
    # Hide the public like count on every reel. The container accepts the param
    # (HTTP 200), unlike alt_text which Graph rejects outright.
    "hide_like_count": True,
    # Instagram SEO (spec §7.1). Keywords steer caption/alt-text wording and the
    # hashtag pools. Hashtags are content-matched per reel by src/seo.py; these lists
    # are the single source of truth so the pools are never duplicated in code.
    "seo": {
        "primary_keywords": [
            "social anxiety", "overthinking conversations", "socially anxious",
            "quiet people", "introvert struggles",
        ],
        "secondary_keywords": [
            "fear of being judged", "canceling plans relief", "hating phone calls",
            "replaying conversations", "feeling behind everyone",
            "wanting to be alone but lonely", 'performing "fine"',
            "exhausted from socializing",
        ],
        "long_tail_keywords": [
            "why do i overthink every conversation",
            "relief when plans get cancelled",
            "social anxiety at work meetings",
            "how to stop replaying conversations",
            "introvert vs social anxiety",
        ],
        "hashtag_pools": {
            "branded": ["#unleashthebeast", "#socialanxietyhelp"],
            "primary": [
                "#socialanxiety", "#introvert", "#overthinking", "#quietpeople",
                "#mentalhealth", "#anxietyrelief", "#selfimprovement",
                "#introvertlife", "#sociallyanxious", "#overthinker",
                "#quietmind", "#innerwork",
            ],
            "long_tail": [
                "#cancelingplans", "#replayingconversations", "#phonecallanxiety",
                "#meetinganxiety", "#eatingalone", "#2amthoughts",
                "#beingthequietone", "#performingfine", "#socialanxietyatwork",
                "#introvertproblems", "#behindeveryone", "#wantingtobealone",
            ],
        },
        "name_field": "Unleash The Beast | Social Anxiety Help",
        "bio_template": ("Overcoming social anxiety. Real posts for the ones who "
                         "overthink every conversation. The book that gets it → link below."),
        "hashtags_min": 6,
        "hashtags_max": 9,
    },
    "explore_rate": 0.2,
    "whisper_every_n_posts": 7,     # Law 11
    "hope_every_n_posts": 10,       # Law 8 / Part 2.1 cluster J
    "whisper_line": "the ebook in my bio was written for the person who felt this.",
    "hashtag_pools": {
        "broad": ["#socialanxiety", "#mentalhealth", "#anxiety", "#introvert",
                  "#overthinking"],
        "medium": ["#socialanxietysupport", "#quietpeople", "#sociallyawkward",
                   "#introvertlife", "#mentalhealthawareness", "#shy"],
        "niche": ["#unleashthebeast", "#thequietones", "#2amthoughts", "#innerwork",
                  "#understoods", "#nightscroll"],
    },
    "font_path": "assets/fonts/Coolvetica-Regular.otf",
}


def _null_family(values):
    return {v: None for v in values}


def _zero_family(values):
    return {v: 0 for v in values}


def _one_family(values):
    return {v: 1.0 for v in values}


def default_strategy() -> dict:
    return {
        "weights": {
            "archetype": _one_family(ARCHETYPES),
            "topic": _one_family(TOPICS),
            "mood": _one_family(MOODS),
            "bg_type": _one_family(BG_TYPES),
            "loop_technique": _one_family(LOOP_TECHNIQUES),
        },
        "n": {
            "archetype": _zero_family(ARCHETYPES),
            "topic": _zero_family(TOPICS),
            "mood": _zero_family(MOODS),
            "bg_type": _zero_family(BG_TYPES),
            "loop_technique": _zero_family(LOOP_TECHNIQUES),
        },
        "adj": {
            "archetype": _null_family(ARCHETYPES),
            "topic": _null_family(TOPICS),
            "mood": _null_family(MOODS),
            "bg_type": _null_family(BG_TYPES),
            "loop_technique": _null_family(LOOP_TECHNIQUES),
        },
        "hour_scores": {"13:30": None, "15:00": None, "16:30": None, "21:30": None},
        "next_post_hour": "21:30",
        "warmup_until": (BUILD_DATE + timedelta(days=8)).isoformat(),
        "experiments": [],
        "last_updated": None,
    }


def default_memory() -> dict:
    return {
        "posts": [],
        "used_pins": [],
        "used_tracks": [],
        "used_track_urls": [],
        "used_track_profiles": [],
        # every music query already resolved; the service maps a query to one track,
        # so not repeating queries is what keeps the music unique
        "used_music_queries": [],
        "used_hooks": [],
        "candidate_log": [],
        "last_post_date": None,
        "post_counter": 0,
    }


def default_trending_styles() -> list:
    # VISUAL SPEC v1.0 §5 — dark trending audio only (phonk / dark ambient /
    # slowed reverb). Piano and lofi are explicitly excluded.
    return ["slow dark ambient, deep sub bass, reverb",
            "dark ambient drone, sub bass, slow",
            "slowed reverb dark ambient, muffled",
            "soft dark phonk, slow, restrained"]


def default_token_state() -> dict:
    return {"expires_at": (BUILD_DATE + timedelta(days=60)).isoformat(), "last_refresh": None}


DEFAULTS = {
    "config.json": DEFAULT_CONFIG,
    "strategy.json": default_strategy,
    "memory.json": default_memory,
    "trending_styles.json": default_trending_styles,
    "token_state.json": default_token_state,
}


def env(name: str, default=None):
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        if default is None:
            return None
        save_json(path, default() if callable(default) else default)
        return load_json(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        if default is None:
            raise
        save_json(path, default() if callable(default) else default)
        return load_json(path)


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def ensure_data_files():
    """Recreate any missing data file from embedded defaults. Returns list recreated."""
    recreated = []
    for name, factory in DEFAULTS.items():
        p = DATA / name
        if not p.exists() or _corrupt(p):
            save_json(p, factory() if callable(factory) else factory)
            recreated.append(name)
    if not DIRECTIVES_PATH.exists():
        DIRECTIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIRECTIVES_PATH.write_text(
            "# DIRECTIVES — optional weekly guidance for the machine\n"
            'Example: "this week focus on the sealed mouth"\n', encoding="utf-8")
        recreated.append("DIRECTIVES.md")
    LOGS.mkdir(parents=True, exist_ok=True)
    return recreated


def _corrupt(path) -> bool:
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            json.load(fh)
        return False
    except Exception:
        return True


def load_config(validate=True) -> dict:
    ensure_data_files()
    cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    if validate:
        required = ["brand", "archetypes", "topics", "moods", "bg_types", "music",
                    "posting_slots_utc", "reel", "hashtag_pools", "mood_fallback_bpm",
                    "timing", "text_limits", "cluster_mood_map"]
        missing = [k for k in required if k not in cfg]
        if missing:
            raise KeyError(f"config.json missing required keys: {missing}")
    return cfg


def load_strategy() -> dict:
    return load_json(STRATEGY_PATH, default_strategy)


def load_memory() -> dict:
    return load_json(MEMORY_PATH, default_memory)


def load_token_state() -> dict:
    return load_json(TOKEN_STATE_PATH, default_token_state)


def load_trending_styles() -> list:
    return load_json(TRENDING_STYLES_PATH, default_trending_styles)


def load_directives() -> str:
    if not DIRECTIVES_PATH.exists():
        return ""
    text = DIRECTIVES_PATH.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines()
             if not ln.strip().startswith("#") and 'Example: "this week' not in ln]
    return "\n".join(lines).strip()


def save_strategy(s): return save_json(STRATEGY_PATH, s)


def save_memory(m): return save_json(MEMORY_PATH, m)


def save_token_state(t): return save_json(TOKEN_STATE_PATH, t)


def save_content(c): return save_json(CONTENT_PATH, c)


def paused() -> bool:
    return PAUSE_PATH.exists()


def today_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date()


def now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
