"""platform.py — BEAST ENGINE v2 platform features (ENGINE_SPEC §3.2, §3.4).

Trial Reels and cover_url, both FEATURE-DETECTED.

WHY FEATURE-DETECT RATHER THAN TRY-AND-HOPE

The docs disagree with each other and with practice on three points the strategy
flags as unverified: the Trial Reels follower minimum (200 for professional
accounts vs 1,000), whether `cover_url` is accepted on the Instagram-Login route,
and per-hour DM limits. Guessing wrong here is expensive in one direction only:
a wrong `trial_params` on a real publish is a failed post.

So the engine probes, remembers the answer, and degrades to a normal publish. A
probe failure costs one skipped experiment; a bad assumption costs the day.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

STATE_PATH = Path("data/platform_state.json")
RETRY_DAYS = 7


def _load() -> dict:
    if STATE_PATH.exists():
        try:
            d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _recently_failed(state: dict, key: str) -> bool:
    raw = state.get(f"{key}_failed_at")
    if not raw:
        return False
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return False
    return datetime.now(timezone.utc) - t < timedelta(days=RETRY_DAYS)


def mark_failed(key: str, reason: str = "") -> None:
    st = _load()
    st[f"{key}_failed_at"] = datetime.now(timezone.utc).isoformat()
    st[f"{key}_reason"] = str(reason)[:200]
    _save(st)


def mark_ok(key: str, detail: str = "") -> None:
    st = _load()
    st.pop(f"{key}_failed_at", None)
    st[f"{key}_ok_at"] = datetime.now(timezone.utc).isoformat()
    if detail:
        st[f"{key}_detail"] = str(detail)[:200]
    _save(st)


# ------------------------------------------------------------------ trial reels

def trial_eligible(followers: int, cfg: dict) -> bool:
    """ENGINE_SPEC §3.2: eligible from cfg trial_follower_min (default 200).

    Below the threshold we do not even try: the strategy notes the account is at
    ~14 followers, so a probe here would burn a publish for nothing.
    """
    if not cfg.get("trial_enabled"):
        return False
    minimum = int(cfg.get("trial_follower_min") or 200)
    return int(followers or 0) >= minimum


def trial_params(cfg: dict) -> dict | None:
    """The trial_params body, or None when trials are off / recently failed."""
    st = _load()
    if not cfg.get("trial_enabled"):
        return None
    if _recently_failed(st, "trial"):
        return None
    return {"graduation_strategy": "SS_PERFORMANCE"}


def should_route_through_trial(run_index: int, cfg: dict, followers: int) -> bool:
    """Route a NEW-attribute experiment through trial, keep most posts non-trial.

    ENGINE_SPEC §3.2: "Keep >= 50% of posts non-trial so the grid isn't empty."
    """
    if not trial_eligible(followers, cfg):
        return False
    return run_index % 2 == 1


def record_trial_result(ok: bool, reason: str = "") -> None:
    st = _load()
    if ok:
        st["trial_posts"] = int(st.get("trial_posts") or 0) + 1
        _save(st)
        mark_ok("trial")
    else:
        mark_failed("trial", reason)


# ------------------------------------------------------------------ cover_url

def cover_supported() -> bool:
    """Whether `cover_url` was accepted on this route. Default False until proven.

    The docs list cover_url for the Facebook-Login route; the strategy flags
    acceptance on the Instagram-Login route as unverified. Defaulting to False
    means the engine skips it rather than risking a 400 on a real publish, and a
    successful probe flips it for good.
    """
    st = _load()
    return bool(st.get("cover_url_ok_at")) and not _recently_failed(st, "cover_url")


def record_cover_result(ok: bool, reason: str = "") -> None:
    if ok:
        mark_ok("cover_url")
    else:
        mark_failed("cover_url", reason)


def cover_field(cfg: dict, cover_jpg: Path | None) -> dict:
    """The cover_url field for a container request, or {} to omit it entirely."""
    if not cover_supported() or not cover_jpg or not Path(cover_jpg).exists():
        return {}
    from . import upload_host
    try:
        url, _host = upload_host.publicize(Path(cover_jpg))
    except Exception:  # noqa: BLE001
        return {}
    return {"cover_url": url} if url else {}


def status() -> dict:
    st = _load()
    return {
        "trial_enabled": config.load_config().get("trial_enabled"),
        "trial_ok_at": st.get("trial_ok_at"),
        "trial_failed_at": st.get("trial_failed_at"),
        "trial_posts": st.get("trial_posts"),
        "cover_url_supported": cover_supported(),
        "cover_url_ok_at": st.get("cover_url_ok_at"),
    }