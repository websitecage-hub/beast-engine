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

ARCHETYPES = ["pain_callout", "secret_reveal", "hard_truth", "challenge_dare",
              "identity_shift", "fear_warning", "contrarian", "transformation_promise"]
TOPICS = ["discipline", "loneliness", "confidence", "conversations", "focus",
          "pain", "self_respect", "purpose", "rejection", "silence"]
MOODS = ["aggressive_phonk", "melancholic_piano", "epic_cinematic",
         "dark_ambient", "stoic_minimal"]
BG_TYPES = ["dark_gym", "city_rain_night", "lone_figure", "wolf_dark",
            "smoke_shadow", "mountain_dark", "ocean_storm"]

DEFAULT_CONFIG = {
    "brand": {
        "name": "Unleash The Beast",
        "handle": "unleashthe.b",
        "voice": "Dark, direct, no fluff. Short brutal sentences. Second person ('you'). Never preachy, never emoji, never hashtags in on-screen text.",
        "hook_examples": ["Nobody warns you about this", "Why you can never love yourself",
                          "Every man should see this", "You're not lazy. You're afraid."],
    },
    "archetypes": ARCHETYPES,
    "topics": TOPICS,
    "moods": MOODS,
    "bg_types": {
        "dark_gym": ["dark gym silhouette", "moody gym night"],
        "city_rain_night": ["city rain night neon", "dark city street rain"],
        "lone_figure": ["lone man walking dark", "silhouette man shadow"],
        "wolf_dark": ["dark wolf moon", "wolf black aesthetic"],
        "smoke_shadow": ["dark smoke aesthetic", "black smoke background"],
        "mountain_dark": ["dark mountains fog", "lonely mountain night"],
        "ocean_storm": ["dark ocean storm", "rough sea night"],
    },
    "mood_fallback_bpm": {"aggressive_phonk": 130, "melancholic_piano": 70,
                          "epic_cinematic": 90, "dark_ambient": 60, "stoic_minimal": 75},
    "music": {
        "trending_api": "https://audi0-scraper.onrender.com",
        "trending_niche": "self-improvement",
        "trending_fallback_niche": "motivation",
        "min_confidence": 0.5,
        "mood_search": {
            "aggressive_phonk": "dark phonk beat",
            "melancholic_piano": "sad emotional piano",
            "epic_cinematic": "epic dark cinematic",
            "dark_ambient": "dark ambient drone",
            "stoic_minimal": "minimal dark piano",
        },
        "genre_hint": {"rap": " dark phonk", "hip-hop": " dark phonk", "trap": " dark phonk",
                       "edm": " dark trap", "pop": " emotional", "rock": " cinematic",
                       "rnb": " moody", "lofi": " lofi dark"},
    },
    "posting_slots_utc": ["13:30", "15:00", "16:30"],
    "reel": {"min_s": 8, "max_s": 14, "fps": 30, "w": 1080, "h": 1920},
    "explore_rate": 0.2,
    "cta_every_n_posts": 5,
    "cta_line": "The full system is in my ebook — link in bio.",
    "hashtag_pools": {
        "broad": ["#motivation", "#discipline", "#mindset", "#selfimprovement", "#grind"],
        "medium": ["#stoicism", "#masculinity", "#selfdevelopment", "#mentality",
                   "#dailymotivation", "#growth"],
        "niche": ["#unleashthebeast", "#innerwork", "#becomelion", "#disciplinedmind",
                  "#quietgrind", "#ironmind"],
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
    return ["slowed dark phonk, heavy cowbell, reverb",
            "sad piano loop, vinyl crackle, slow",
            "epic dark cinematic drums"]


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
