"""config.py — state loading, path constants, embedded defaults.

Every run reads data/*.json at start and the orchestrator commits it back at end.
A missing/corrupt data file is recreated from the embedded defaults (section 5).
"""
from __future__ import annotations

import json
import os
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

ARCHETYPES = ["the_mask", "the_rehearsal", "the_freeze", "the_detour", "the_aftermath",
              "the_bodys_betrayal", "the_sealed_mouth", "the_craving", "the_losses",
              "the_buried_anger", "quiet_hope"]
TOPICS = ["exposure_fear", "fake_phone", "ordering_food", "phone_calls", "freeze_at_work",
          "asking_coworker", "party_bathroom", "mind_blank", "post_interaction_hate",
          "replay_2am", "read_receipts", "neutral_as_negative", "meetings_voice",
          "dating_app_freeze", "dying_alone_thought", "lost_friendships", "behind_at_25",
          "buried_anger", "therapy_irony", "the_grandma_win"]
MOODS = ["quiet_devastating", "heavy_shadow", "muffled_world", "restrained_anger",
         "gentle_hope"]
BG_TYPES = ["mask", "rehearsal", "freeze_detour", "aftermath", "sealed_mouth",
            "craving", "losses", "buried_anger", "quiet_hope"]

DEFAULT_CONFIG = {
    "brand": {
        "name": "Unleash The Beast",
        "handle": "unleashthe.b",
        "voice": ("Describe, never advise. Mirror one specific person's inner life with "
                  "exact scenes. Present tense, second person. Never sell, never confirm "
                  "the flaw, never say 'just'. Tragic, never pathetic."),
        "hook_examples": ["You know exactly what to say. You say nothing. Again.",
                          "The conversation ends. The trial begins.",
                          "You want to talk. Your mouth disagrees.",
                          "She matched with you. And you're suspicious."],
    },
    "archetypes": ARCHETYPES,
    "topics": TOPICS,
    "moods": MOODS,
    "bg_types": {
        "mask": ["glass office night aesthetic", "empty meeting room aesthetic",
                 "conference room dark"],
        "rehearsal": ["cafe window night aesthetic", "phone glow dark room aesthetic"],
        "freeze_detour": ["empty street night rain", "walking alone night city aesthetic"],
        "aftermath": ["3am aesthetic dark", "ceiling fan dark aesthetic",
                      "mirror dark aesthetic", "unmade bed aesthetic"],
        "sealed_mouth": ["face half shadow aesthetic", "silhouette mouth covered dark"],
        "craving": ["phone glow face dark aesthetic", "city lights from window night"],
        "losses": ["empty lecture hall aesthetic", "empty classroom aesthetic",
                   "old playground aesthetic"],
        "buried_anger": ["storm clouds dark aesthetic", "cracked wall dark aesthetic"],
        "quiet_hope": ["sunrise alone aesthetic", "first light window aesthetic"],
    },
    "archetype_bg_map": {
        "the_mask": "mask", "the_rehearsal": "rehearsal", "the_freeze": "freeze_detour",
        "the_detour": "freeze_detour", "the_aftermath": "aftermath",
        "the_bodys_betrayal": "sealed_mouth", "the_sealed_mouth": "sealed_mouth",
        "the_craving": "craving", "the_losses": "losses",
        "the_buried_anger": "buried_anger", "quiet_hope": "quiet_hope",
    },
    "mood_fallback_bpm": {"quiet_devastating": 65, "heavy_shadow": 55,
                          "muffled_world": 70, "restrained_anger": 85,
                          "gentle_hope": 60},
    "music": {
        "trending_api": "https://audi0-scraper.onrender.com",
        "trending_niche": "self-improvement",
        "trending_fallback_niche": "motivation",
        "min_confidence": 0.5,
        "mood_search": {
            "quiet_devastating": "slowed sad piano reverb vinyl crackle",
            "heavy_shadow": "dark ambient drone sub bass",
            "muffled_world": "lofi heard through a wall",
            "restrained_anger": "slow dark phonk soft",
            "gentle_hope": "slow ambient build hopeful",
        },
        "genre_hint": {"rap": " dark phonk", "hip-hop": " dark phonk", "trap": " dark phonk",
                       "edm": " dark trap", "pop": " emotional", "rock": " cinematic",
                       "rnb": " moody", "lofi": " lofi dark"},
    },
    "posting_slots_utc": ["13:30", "15:00", "16:30"],
    "reel": {"min_s": 8, "max_s": 11, "fps": 30, "w": 1080, "h": 1920},
    "explore_rate": 0.2,
    "cta_every_n_posts": 7,
    "cta_line": "the ebook in my bio was written for the person who felt this.",
    "hope_every_n_posts": 10,
    "hashtag_pools": {
        "broad": ["#socialanxiety", "#mentalhealth", "#anxiety", "#introvert",
                  "#overthinking"],
        "medium": ["#socialanxietysupport", "#quietpeople", "#sociallyawkward",
                   "#introvertlife", "#mentalhealthawareness", "#shy"],
        "niche": ["#unleashthebeast", "#thequietones", "#2amthoughts", "#innerwork",
                  "#understoods", "#nightscroll"],
    },
    "font_path": "assets/fonts/Anton-Regular.ttf",
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
        },
        "n": {
            "archetype": _zero_family(ARCHETYPES),
            "topic": _zero_family(TOPICS),
            "mood": _zero_family(MOODS),
            "bg_type": _zero_family(BG_TYPES),
        },
        "adj": {
            "archetype": _null_family(ARCHETYPES),
            "topic": _null_family(TOPICS),
            "mood": _null_family(MOODS),
            "bg_type": _null_family(BG_TYPES),
        },
        "hour_scores": {"13:30": None, "15:00": None, "16:30": None},
        "next_post_hour": "15:00",
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
        "used_hooks": [],
        "candidate_log": [],
        "last_post_date": None,
        "post_counter": 0,
    }


def default_trending_styles() -> list:
    return ["slowed sad piano, vinyl crackle, quiet",
            "dark ambient drone, sub bass, slow",
            "lofi through a wall, muffled, warm",
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
            'Example: "this week focus on loneliness"\n', encoding="utf-8")
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
                    "posting_slots_utc", "reel", "hashtag_pools", "mood_fallback_bpm"]
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
    # strip the seed comment lines so they never influence the model
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
