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


def _fold_system(messages) -> list:
    """Move any `system` text into the first user turn.

    See chat()'s docstring: the upstream endpoint discards the system role, so a
    spec delivered that way never reaches the model.
    """
    system_parts, out = [], []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "system":
            content = str(m.get("content") or "").strip()
            if content:
                system_parts.append(content)
        else:
            out.append(dict(m))
    if system_parts and out:
        first = out[0]
        if first.get("role") == "user":
            first["content"] = ("\n\n".join(system_parts) + "\n\n---\n\n"
                                + str(first.get("content") or ""))
        else:
            out.insert(0, {"role": "user", "content": "\n\n".join(system_parts)})
    elif system_parts:
        out = [{"role": "user", "content": "\n\n".join(system_parts)}]
    return out or list(messages)


def _json_largest(text: str):
    """The LARGEST parseable JSON object in the text, not the first.

    json_first() returns the first balanced block, which can be a small literal from
    an example (`{}` or `{"keyword": "SAFE"}`) rather than the model's answer. The
    answer is the biggest object, so scanning all candidates and taking the largest
    is the safer pick.
    """
    if not text:
        return None
    best, i = None, text.find("{")
    while i != -1:
        depth, in_str, escape, end = 0, False, False, -1
        for j in range(i, len(text)):
            ch = text[j]
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
                    end = j
                    break
        if end != -1:
            blob = text[i:end + 1]
            try:
                obj = json.loads(blob)
            except Exception:  # noqa: BLE001
                try:
                    import ast
                    obj = ast.literal_eval(blob)
                except Exception:  # noqa: BLE001
                    obj = None
            if isinstance(obj, dict) and (best is None or len(obj) > len(best)):
                best = obj
        i = text.find("{", i + 1)
    return best


def chat(messages, expect_json: bool = False, retries: int = 3, timeout: int = CHAT_TIMEOUT,
         temperature: float | None = None, system: str = ""):
    """Stateless chat completion. Returns text (or parsed object when expect_json).

    The upstream Meta endpoint IGNORES the `system` role — verified directly: a
    "reply as a pirate" system message came back as a friendly assistant greeting.
    A spec sent as a system message is silently discarded, which makes the model
    free-style unrelated content while looking like a normal 200 response.

    So system instructions are folded into the user turn. That is correct whether or
    not the endpoint honours `system`, and it is the only form proven to work here.

    `temperature` and `system` exist for the confession engine, which needs the
    SAME endpoint to behave as two roles — a writer and a harsher critic. The
    spec asks for two different model families; this service exposes one model id
    (GET /v1/models -> ["meta-ai-thinking"]), so the separation is reproduced
    with a cold temperature on the critic plus a code-enforced ban list the
    critic cannot waive. Both parameters are best-effort and ignored if the
    upstream rejects them.
    """
    wake()
    last_err = None
    msgs = _fold_system(messages)
    if system:
        # Folded into the user turn for the same reason as above.
        if msgs and msgs[-1].get("role") == "user":
            msgs[-1]["content"] = f"{system}\n\n{msgs[-1]['content']}"
    for attempt in range(retries):
        body = {"model": MODEL, "messages": msgs}
        if temperature is not None:
            body["temperature"] = temperature
        try:
            r = requests.post(f"{BASE}/v1/chat/completions", json=body, timeout=timeout)
            if r.status_code >= 400 and temperature is not None:
                # Retry once without the optional field rather than lose the call.
                body.pop("temperature", None)
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
            if isinstance(parsed, dict) and not parsed:
                # json_first found `{}` — usually a JSON example inside the prompt
                # rather than the answer. Looking for a richer object is closer to
                # intent than accepting an empty one.
                parsed = _json_largest(text)
                if not parsed:
                    raise ValueError("only an empty JSON object in response")
            return parsed
        except Exception as exc:  # noqa: BLE001 — retried below
            last_err = exc
            if expect_json and attempt == 0:
                msgs = list(_fold_system(messages)) + [{
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
