"""llm.py — Meta LLM API (text brain + image fallback). No key required.

Base: https://meta-api-chat-h326.onrender.com
Cold start on Render free tier: GET /health up to 6 retries x 15s before first use.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = "https://meta-api-chat-h326.onrender.com"
MODEL = "meta-ai-thinking"
CHAT_TIMEOUT = 180
IMAGE_TIMEOUT = 240
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")

_woken = False


def wake(retries: int = 6, wait: float = 15.0) -> bool:
    """Ping /health until awake. Returns True when healthy."""
    global _woken
    if _woken:
        return True
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE}/health", timeout=30)
            if r.status_code == 200:
                _woken = True
                return True
        except requests.RequestException:
            pass
        if attempt < retries - 1:
            time.sleep(wait)
    return False


def json_first(text: str):
    """Extract the first balanced {...} block via brace scanning (not greedy regex)."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = text[start:i + 1]
                    try:
                        return json.loads(blob)
                    except json.JSONDecodeError:
                        # tolerate trailing commas / python-ish literals
                        try:
                            import ast
                            return ast.literal_eval(blob) if isinstance(
                                ast.literal_eval(blob), (dict, list)) else None
                        except Exception:
                            start = text.find("{", start + 1)
                            break
        else:
            return None
    return None


def chat(messages, expect_json: bool = False, retries: int = 3, timeout: int = CHAT_TIMEOUT):
    """Stateless chat completion. Returns text (or parsed object when expect_json)."""
    wake()
    last_err = None
    msgs = list(messages)
    for attempt in range(retries):
        body = {"model": MODEL, "messages": msgs}
        try:
            r = requests.post(f"{BASE}/v1/chat/completions", json=body, timeout=timeout)
            if r.status_code >= 500:
                raise RuntimeError(f"upstream {r.status_code}")
            r.raise_for_status()
            data = r.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not expect_json:
                return text
            parsed = json_first(text)
            if parsed is None:
                raise ValueError("no JSON block found")
            return parsed
        except Exception as exc:  # noqa: BLE001 — retried below
            last_err = exc
            if expect_json and attempt == 0:
                msgs = list(messages) + [{
                    "role": "user",
                    "content": "Respond with ONLY valid JSON, no prose. No markdown fences.",
                }]
            elif attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM chat failed after {retries} attempts: {last_err}")


REPHRASES = [
    "{prompt}. Emphasise cinematic darkness, high contrast, film grain.",
    "abstract dark moody aesthetic, heavy shadows, {prompt}",
    "{prompt} — no text, no letters, no watermark, photographic",
]


def image(prompt: str, out_path) -> bool:
    """Generate an image and download it immediately (CDN URLs expire).

    Returns True on success. Handles: 200+empty urls+error = refusal -> rephrase once;
    quota error -> retry same body on /api/image2.
    """
    wake()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    urls = _image_call(prompt, "/api/image")
    if not urls:
        urls = _image_call(REPHRASES[1].format(prompt=prompt), "/api/image")
    if not urls:
        urls = _image_call(prompt, "/api/image2")
    if not urls:
        return False
    for url in urls:
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": UA})
            if r.status_code == 200 and len(r.content) > 2048:
                out_path.write_bytes(r.content)
                return True
        except requests.RequestException:
            continue
    return False


def _image_call(prompt: str, path: str):
    try:
        r = requests.post(f"{BASE}{path}", json={"prompt": prompt, "mode": "instant"},
                          timeout=IMAGE_TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        urls = data.get("urls") or []
        return [u for u in urls if isinstance(u, str) and u.startswith("http")]
    except Exception:  # noqa: BLE001
        return []
