"""keepalive.py — the heartbeat that keeps the scheduler alive, and the two
numbers that rot silently.

Two jobs:

1. WRITE ACTIVITY. GitHub disables scheduled workflows on a repo with 60 days of
   no activity. This commits a dated heartbeat, which is real activity. Without
   it the crons stop firing and the failure is silent — no error appears
   anywhere, the account simply goes quiet.

2. REPORT THE THINGS THAT GO STALE WITHOUT ANYONE NOTICING:
   - how many days the Instagram token has left (it dies at 0 and posting stops)
   - how long since the last post (a silent stall looks identical to a quiet week)

PUBLISHES NOTHING. It never calls media_publish, never renders, never posts.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

HEARTBEAT = config.DATA / "keepalive.json"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def heartbeat() -> dict:
    prev = {}
    if HEARTBEAT.exists():
        try:
            prev = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}

    now = utcnow()
    hb = {
        "last_beat": now.isoformat(),
        "beat_count": int(prev.get("beat_count") or 0) + 1,
        "first_beat": prev.get("first_beat") or now.isoformat(),
        "purpose": ("Keeps GitHub's 60-day scheduled-workflow disable from "
                    "firing. Publishes nothing."),
    }
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps(hb, indent=2), encoding="utf-8")
    return hb


def token_days_left() -> int | None:
    """Days until the Instagram token expires, or None if unknown."""
    ts = config.load_token_state()
    raw = str(ts.get("expires_at") or "")[:10]
    if not raw:
        return None
    try:
        expires = datetime.fromisoformat(raw).date()
    except ValueError:
        return None
    return (expires - utcnow().date()).days


def token_is_alive() -> bool:
    """GET /me. This is the only honest test — expiry dates lie when a token has
    been revoked."""
    tok = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not tok:
        return False
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(
            "https://graph.instagram.com/v23.0/me?fields=username",
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def days_since_last_post() -> int | None:
    mem = config.load_memory()
    last = str(mem.get("last_post_date") or "")[:10]
    if not last:
        return None
    try:
        return (utcnow().date() - datetime.fromisoformat(last).date()).days
    except ValueError:
        return None


def main() -> int:
    hb = heartbeat()
    days_tok = token_days_left()
    alive = token_is_alive()
    days_post = days_since_last_post()

    lines = [
        "beast-engine keepalive",
        f"heartbeat #{hb['beat_count']} at {hb['last_beat'][:16]}Z",
        "",
        f"Instagram token: {'ALIVE' if alive else 'DEAD or unset'}"
        + (f", {days_tok} days left" if days_tok is not None else ", expiry unknown"),
        f"Last post: {days_post} days ago" if days_post is not None
        else "Last post: never recorded",
        "",
        "This job publishes nothing.",
    ]
    report = "\n".join(lines)
    print(report)

    # Alert on the two conditions that would otherwise go unnoticed. Both are
    # worth a message because BOTH stop the account silently.
    alert = []
    if not alive:
        alert.append("TOKEN IS DEAD or IG_ACCESS_TOKEN is unset — posting cannot "
                     "work. Regenerate at developers.facebook.com -> your app -> "
                     "Instagram -> API setup with Instagram login.")
    elif days_tok is not None and days_tok <= 14:
        alert.append(f"TOKEN EXPIRES IN {days_tok} DAYS. The health job refreshes "
                     f"inside 21 days; if it has not, rotate manually.")
    if days_post is not None and days_post >= 10:
        alert.append(f"NO POST FOR {days_post} DAYS. Either the schedule is off "
                     f"(expected while publish is disabled) or the pipeline is "
                     f"failing silently.")

    if alert:
        try:
            from . import notify_send
            tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            if tok and chat:
                notify_send.send(tok, chat, report + "\n\n!! " + "\n!! ".join(alert))
                print("[keepalive] alert sent")
            else:
                print("[keepalive] alert NOT sent — TELEGRAM_* not configured")
        except Exception as exc:  # noqa: BLE001
            print(f"[keepalive] alert failed: {exc}")

    try:
        log = config.DATA / "logs" / f"{utcnow().date().isoformat()}-keepalive.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({
            "at": hb["last_beat"], "beat_count": hb["beat_count"],
            "token_alive": alive, "token_days_left": days_tok,
            "days_since_last_post": days_post, "alerts": alert,
        }, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())