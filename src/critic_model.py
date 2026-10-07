"""critic_model.py — the SECOND MODEL FAMILY for the critic (ENGINE_SPEC.md).

ENGINE_SPEC requires the writer and the critic to be different model families.
The writer runs on this stack's own Meta service (`meta-ai-thinking`). The critic
runs here, on DeepSeek, through the operator's self-hosted bridge. Two different
vendors, two different weights — that is the separation the spec asks for, and it
is now real rather than simulated.

Verified live before this module was written:
    GET  /v1/models          -> ["deepseek-chat", "deepseek-reasoner"]
    POST /v1/chat/completions -> 200, content "BRIDGE OK"

The bridge is an OpenAI-compatible surface with NO API KEY, so there is no
credential to leak and nothing to rotate. If the bridge is asleep or down, this
module returns (None, reason) and the caller must treat "no critic" as a FAIL,
never as a pass — a critic that silently disappears is worse than no critic at
all, because the pipeline would ship unreviewed drafts while reporting green.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CRITIC_BASE_URL",
                      "https://deepseek-web-bridge.onrender.com").rstrip("/")
MODEL = os.environ.get("CRITIC_MODEL", "deepseek-chat")
TIMEOUT = int(os.environ.get("CRITIC_TIMEOUT", "300"))

# Cold starts on the free tier: the first call can take a while, so the wake
# ping gets its own budget rather than eating the generation timeout.
_woken = False


def available() -> bool:
    """Is the bridge up? Used by tests and by the health path."""
    try:
        req = urllib.request.Request(f"{BASE}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def model_ids() -> list:
    try:
        req = urllib.request.Request(f"{BASE}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=45) as r:
            d = json.load(r)
        return [m.get("id") for m in (d.get("data") or [])]
    except Exception:  # noqa: BLE001
        return []


def chat(messages: list, temperature: float = 0.0, max_tokens: int = 400,
         retries: int = 2) -> str:
    """One completion. Raises on failure so the caller can fail closed."""
    global _woken
    if not _woken:
        _woken = available()

    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()

    last = "unknown"
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            f"{BASE}/v1/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                d = json.load(r)
            choices = d.get("choices") or []
            if not choices:
                last = f"no choices in response: {str(d)[:200]}"
            else:
                return (choices[0].get("message") or {}).get("content") or ""
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read()[:200]!r}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"critic model unavailable ({MODEL} @ {BASE}): {last}")


def family() -> str:
    """For the logs: proof the critic was not the writer's family."""
    return f"deepseek:{MODEL}"