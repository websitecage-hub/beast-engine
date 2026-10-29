"""template_payloads.py — the data-only contract for the two cloned formats.

A variant is **data**, never markup. The template HTML/CSS is source-immutable:
the renderer copies a template into a run-scoped scratch directory, injects these
fields into the copy and nothing else. Model output therefore cannot reach the
DOM as HTML, CSS or JavaScript.

Two shapes exist because the formats genuinely differ:

  caption_list_title_v1  a fixed four-line title; the promised list lives in the
                         caption. The headline's number and the list length must
                         agree EXACTLY — the reference promises "8 things" and
                         delivers 8, so a mismatch is a content bug, not a
                         styling detail to be hidden by shrinking type.
  social_moment_loop_v1  one static block: hook lines then payoff lines, visible
                         from frame zero, looping naturally.

This module deliberately does NOT extend `blueprint.ALLOWED_FIELDS`: the older
validator expects timed beats with a payoff, which is not the shape of a static
title. Keeping the contracts separate means v2/v3 blueprints validate exactly as
before, and these payloads validate on their own terms.
"""
from __future__ import annotations

import re

SCHEMA_VERSION = "1.0"

FORMATS = {
    "caption_list_title_v1": {
        "template": "caption_list_title_v1",
        "duration_s": 12.2,
        "max_title_lines": 4,
        "max_line_chars": 34,
        "requires_list": True,
    },
    "social_moment_loop_v1": {
        "template": "social_moment_loop_v1",
        "duration_s": 7.0,
        "max_lines": 8,
        "max_line_chars": 42,
        "requires_list": False,
    },
}

# Any of these in a payload string means the writer is trying to inject markup.
_MARKUP = re.compile(r"[<>{}]|javascript:|on\w+\s*=", re.I)


def _strings(payload: dict) -> list:
    out = []
    for v in payload.values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, (list, tuple)):
            out.extend(str(x) for x in v)
    return out


def _num_from_headline(headline: str) -> int | None:
    """'8 things you do after a conversation' -> 8. Words are not guessed."""
    m = re.match(r"\s*(\d{1,2})\b", headline or "")
    return int(m.group(1)) if m else None


def validate(payload: dict, format_id: str) -> list:
    """Return a list of human-readable errors. Empty list = valid."""
    errors: list = []
    spec = FORMATS.get(format_id)
    if not spec:
        return [f"unknown template format {format_id!r}"]
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]

    for key in ("topic_id", "caption", "background_query", "audio", "provenance"):
        if not payload.get(key):
            errors.append(f"missing required field {key!r}")

    for s in _strings(payload):
        if _MARKUP.search(s):
            errors.append(f"payload string contains markup: {s[:40]!r}")

    audio = payload.get("audio") or {}
    if isinstance(audio, dict):
        if audio.get("rights") not in ("unknown", "cleared", "preview_only"):
            errors.append("audio.rights must be one of unknown/cleared/preview_only")
        # A TikTok commercial flag is not Instagram clearance; the only way to
        # claim 'cleared' is an explicit evidence field from the owner.
        if audio.get("rights") == "cleared" and not audio.get("rights_evidence"):
            errors.append("audio.rights='cleared' requires audio.rights_evidence")
    else:
        errors.append("audio must be an object")

    prov = payload.get("provenance") or {}
    if isinstance(prov, dict):
        if not prov.get("source"):
            errors.append("provenance.source is required")
        if prov.get("usage_clearance") not in (None, "unknown", "cleared", "preview_only"):
            errors.append("provenance.usage_clearance must be unknown/cleared/preview_only")
    else:
        errors.append("provenance must be an object")

    if format_id == "caption_list_title_v1":
        lines = payload.get("titleLines") or payload.get("title_lines") or []
        if not isinstance(lines, list) or not lines:
            errors.append("titleLines is required")
        elif len(lines) > spec["max_title_lines"]:
            errors.append(f"title has {len(lines)} lines; max {spec['max_title_lines']}")
        for ln in lines:
            if len(str(ln)) > spec["max_line_chars"]:
                errors.append(f"title line too long ({len(str(ln))} chars): {ln!r}")
        headline = str(payload.get("headline") or "")
        items = payload.get("list_items") or []
        declared = _num_from_headline(headline) if headline else None
        if declared is not None and isinstance(items, list) and len(items) != declared:
            errors.append(
                f"headline promises {declared} items but the list has {len(items)}")
        if not isinstance(items, list) or not items:
            errors.append("list_items is required (the promised list lives in the caption)")
        if not payload.get("caption"):
            errors.append("caption must carry the formatted list")
        elif isinstance(items, list) and items:
            missing = [str(i) for i in items if str(i).strip() and str(i) not in str(payload["caption"])]
            if missing:
                errors.append(f"caption is missing {len(missing)} list item(s)")

    if format_id == "social_moment_loop_v1":
        hook = payload.get("hookLines") or payload.get("hook_lines") or []
        payoff = payload.get("payoffLines") or payload.get("payoff_lines") or []
        if not isinstance(hook, list) or not hook:
            errors.append("hookLines is required")
        if not isinstance(payoff, list) or not payoff:
            errors.append("payoffLines is required")
        all_lines = list(hook) + list(payoff)
        if len(all_lines) > spec["max_lines"]:
            errors.append(f"{len(all_lines)} lines on screen; max {spec['max_lines']}")
        for ln in all_lines:
            if len(str(ln)) > spec["max_line_chars"]:
                errors.append(f"line too long ({len(str(ln))} chars): {ln!r}")
        if not payload.get("loop_hypothesis"):
            errors.append("loop_hypothesis is required (why the ending re-reads as a beginning)")
        if payload.get("caption") and len(str(payload["caption"])) > 300:
            errors.append("caption exceeds 300 chars")

    return errors


def normalise(payload: dict, format_id: str) -> dict:
    """Map either case style onto the keys the templates actually use."""
    out = dict(payload)
    if format_id == "caption_list_title_v1":
        out["titleLines"] = list(payload.get("titleLines") or payload.get("title_lines") or [])
    else:
        out["hookLines"] = list(payload.get("hookLines") or payload.get("hook_lines") or [])
        out["payoffLines"] = list(payload.get("payoffLines") or payload.get("payoff_lines") or [])
        out["payoffStartFraction"] = float(payload.get("payoffStartFraction") or 0.6)
    return out