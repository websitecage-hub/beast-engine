"""alerts.py — Telegram alerts (optional) + GitHub Issue fallback. Never fatal."""
from __future__ import annotations

import os
from pathlib import Path

import requests

from . import config

REPO = os.environ.get("GITHUB_REPOSITORY", "websitecage-hub/beast-engine")
PREFIX = "🚨 Beast Engine"


def _tg() -> tuple:
    return config.env("TELEGRAM_BOT_TOKEN"), config.env("TELEGRAM_CHAT_ID")


def telegram(text: str) -> bool:
    token, chat = _tg()
    if not token or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[:4000]}, timeout=30)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — alerts must never fail the run
        return False


def telegram_video(path, caption: str = "") -> bool:
    token, chat = _tg()
    if not token or not chat or not path or not Path(path).exists():
        return False
    try:
        with Path(path).open("rb") as fh:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendVideo",
                data={"chat_id": chat, "caption": caption[:1000], "supports_streaming": True},
                files={"video": (Path(path).name, fh, "video/mp4")},
                timeout=300)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def issue(title: str, body: str) -> bool:
    token = config.env("GITHUB_TOKEN") or config.env("GH_TOKEN")
    if not token:
        return False
    try:
        r = requests.post(
            f"https://api.github.com/repos/{REPO}/issues",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json={"title": f"{PREFIX} {title}", "body": body or "no details"},
            timeout=30)
        return r.status_code in (200, 201)
    except Exception:  # noqa: BLE001
        return False


def fail(reason: str, log_tail: str = "") -> None:
    text = f"{PREFIX} — {reason}"
    if log_tail:
        text += "\n\n" + log_tail[-1500:]
    telegram(text)
    issue(reason, f"```\n{log_tail[-5000:]}\n```" if log_tail else "no log tail")


def success(post_number, hook: str, video_path=None) -> None:
    text = f"✅ Reel #{post_number} live — {hook}"
    telegram(text)
    if video_path:
        telegram_video(video_path, text)
