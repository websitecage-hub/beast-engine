"""notify_send.py — deliver a Telegram message from CI.

Exists so the bot token can stay a repository secret: the local machine has no copy of
it, and this is dispatched with the text to send. Long messages are split because
Telegram rejects anything over 4096 characters.
"""
from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request

TG = "https://api.telegram.org/bot{token}/{method}"


def send(token: str, chat: str, text: str, photo: str = "") -> bool:
    url = TG.format(token=token, method="sendPhoto" if photo else "sendMessage")
    payload: dict = {"chat_id": chat}
    if photo:
        payload.update({"photo": photo, "caption": text[:1024]})
    else:
        payload.update({"text": text, "disable_web_page_preview": False})
    body = urllib.parse.urlencode(payload).encode()
    # stdlib only: this runs in a job that installs no dependencies, so importing
    # requests here crashed with ModuleNotFoundError before sending anything.
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status == 200:
                    return True
                print(f"[notify] attempt {attempt + 1}: {r.status}")
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] attempt {attempt + 1} failed: {exc}")
    return False


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    text = os.environ.get("MSG", "")
    photo = os.environ.get("PHOTO", "").strip()
    if not token or not chat:
        print("[notify] missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return 1
    if not text.strip():
        print("[notify] empty message")
        return 1

    # Telegram caps a message at 4096 chars; split on paragraph boundaries.
    limit = 3900 if not photo else 1000
    chunks, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        chunks.append(cur)

    ok = True
    for i, chunk in enumerate(chunks, 1):
        part = chunk if len(chunks) == 1 else f"{chunk}\n\n[part {i}/{len(chunks)}]"
        sent = send(token, chat, part, photo if i == 1 else "")
        print(f"[notify] part {i}/{len(chunks)}: {'delivered' if sent else 'FAILED'}")
        ok = ok and sent
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())