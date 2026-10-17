"""blueprint.py — the single Content Blueprint contract (Section 6).

Every candidate creative is described here *before* prose is written and before
a renderer is chosen. v2 and v3 both adapt to this one schema, so validation,
critic scoring, evidence references and rights requirements live in one place.

This module is deliberately standalone (no import of renderers or models) so it
can be unit-tested and reused by the critic, the registry and the runners. It
validates STRUCTURE and HARD POLICY only; format-specific constraints (word
counts, duration window, allowed arms) are validated by the format registry and
the renderer's ``validate_blueprint``.

Key principles enforced here:
  * one clear viewer payoff (a `payoff` beat must exist);
  * evidence references are mandatory (no unsourced candidate);
  * a missing metric is ``None`` — never coerced to 0 (see ``as_metric``);
  * unknown fields are rejected, so a typo cannot silently become a no-op.
"""
from __future__ import annotations

import copy
import datetime as _dt

SCHEMA_VERSION = "1.0"

# --- top-level fields allowed by the contract (Section 6 JSON) ---------------
FIELD_ORDER = [
    "schema_version", "experiment_id", "format_id", "series_id", "topic_id",
    "audience_hypothesis", "viewer_moment", "hook_mechanic", "beats", "caption",
    "cta_variant", "scene_query", "audio_plan", "evidence_refs", "hypothesis",
    "variant_of", "rights_requirements", "risk_flags", "critic",
    # v3 production fields (the doc's schema is "suggested"; the v3 path needs a
    # declared visual arm and length bucket, so they are part of the contract).
    "visual_arm", "length_bucket",
]
ALLOWED_FIELDS = set(FIELD_ORDER)

# Critic rubric (Section 6.2). The blueprint carries a subset (the doc's six);
# other rubric dimensions are evaluated by the critic and stored separately.
CRITIC_KEYS = ["specificity", "payoff", "sendability", "novelty",
               "truthfulness", "brand_fit"]
CRITIC_MIN = 0
CRITIC_MAX = 5
# Highest score for a dimension; brand_fit is advisory-only (matches v2 writer).
CRITIC_GATE_KEYS = ["specificity", "payoff", "sendability", "novelty",
                    "truthfulness"]
CRITIC_GATE_MIN = 4

AUDIO_MODES = ["licensed_ambient", "licensed_track", "silent", "trending_licensed"]

# A candidate carrying any of these flags is rejected outright: they are the
# clinical / fabricated-experience / copied-material hard rules (Section 15).
HARD_REJECT_FLAGS = {
    "clinical_overclaim", "diagnosis_claim", "cure_claim", "medical_advice",
    "fake_testimony", "fake_story", "copied_wording", "unsupported_claim",
    "unlicensed_audio", "unlicensed_visual", "promised_result",
}

# Rights labels that are explicitly NOT sufficient to publish (Section 7.1).
UNSUPPORTED_RIGHTS = {
    "pinterest_url_only", "instagram_scraped", "unknown", "none", "unverified",
    "trending_audio",
}


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def as_metric(value):
    """Metric null semantics (Section 9): a missing metric is None.

    ``None`` and "" (not measured) stay None; a genuine 0 stays 0.0. This is the
    only sanctioned coercion, so later scoring can distinguish "not measured"
    from "measured zero".
    """
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def new(format_id: str, *, experiment_id=None, **fields) -> dict:
    """Build a blueprint with sane defaults; caller fills the creative fields."""
    bp = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "format_id": format_id,
        "series_id": None,
        "topic_id": None,
        "audience_hypothesis": None,
        "viewer_moment": None,
        "hook_mechanic": None,
        "beats": [],
        "caption": None,
        "cta_variant": "none_or_caption_only",
        "scene_query": None,
        "audio_plan": {"mode": "silent", "track_id": None},
        "evidence_refs": [],
        "hypothesis": None,
        "variant_of": None,
        "rights_requirements": ["visual license", "audio license"],
        "risk_flags": [],
        "critic": {},
    }
    bp.update(fields)
    return bp


def validate(bp: dict) -> list:
    """Return a list of error strings; empty means structurally valid.

    Structural + hard-policy only. Format duration/word constraints are checked
    by ``formats.registry.validate_for_format``.
    """
    errors = []
    if not isinstance(bp, dict):
        return ["blueprint is not an object"]

    unknown = sorted(set(bp) - ALLOWED_FIELDS)
    if unknown:
        errors.append(f"unsupported field(s): {', '.join(unknown)}")

    if bp.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}, "
                      f"got {bp.get('schema_version')!r}")
    for f in ("format_id", "hook_mechanic"):
        if not str(bp.get(f) or "").strip():
            errors.append(f"{f} is required")

    beats = bp.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append("beats must be a non-empty list")
        beats = []
    prev = -1.0
    for i, b in enumerate(beats):
        if not isinstance(b, dict):
            errors.append(f"beat[{i}] is not an object")
            continue
        if not str(b.get("text") or "").strip():
            errors.append(f"beat[{i}].text is required")
        r = b.get("start_ratio")
        if not isinstance(r, (int, float)) or not (0.0 <= r <= 1.0):
            errors.append(f"beat[{i}].start_ratio must be within 0..1")
        else:
            if r < prev:
                errors.append(f"beat[{i}].start_ratio regresses (not ascending)")
            prev = r
    if beats and isinstance(beats[0], dict) and beats[0].get("start_ratio") != 0.0:
        errors.append("first beat must start at ratio 0.0")
    kinds = {b.get("kind") for b in beats if isinstance(b, dict)}
    if beats and "payoff" not in kinds:
        errors.append("a 'payoff' beat is required (one clear viewer payoff)")

    refs = bp.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        errors.append("evidence_refs is required (no unsourced candidate)")

    ap = bp.get("audio_plan")
    if not isinstance(ap, dict):
        errors.append("audio_plan must be an object")
    elif ap.get("mode") not in AUDIO_MODES:
        errors.append(f"audio_plan.mode must be one of {AUDIO_MODES}")

    flags = bp.get("risk_flags")
    if flags is None:
        flags = []
    if not isinstance(flags, list):
        errors.append("risk_flags must be a list")
        flags = []
    hard = sorted(set(flags) & HARD_REJECT_FLAGS)
    if hard:
        errors.append(f"hard-reject risk flag(s): {', '.join(hard)}")

    rr = bp.get("rights_requirements")
    if not isinstance(rr, list) or not rr:
        errors.append("rights_requirements must be a non-empty list")
    else:
        bad = sorted({r for r in rr if isinstance(r, str)
                      and r.strip().lower().replace(" ", "_") in UNSUPPORTED_RIGHTS})
        if bad:
            errors.append(f"unsupported rights state(s): {', '.join(bad)}")

    critic = bp.get("critic")
    if critic is not None and not isinstance(critic, dict):
        errors.append("critic must be an object")
    return errors


def critic_passed(critic: dict) -> tuple:
    """(ok, reasons). Every gate dimension must be present and >= CRITIC_GATE_MIN.

    `brand_fit` is advisory. A missing dimension fails closed (Section 6.2).
    """
    reasons = []
    if not isinstance(critic, dict):
        return False, ["critic missing"]
    for k in CRITIC_GATE_KEYS:
        v = critic.get(k)
        if not isinstance(v, (int, float)):
            reasons.append(f"critic.{k} missing")
        elif v < CRITIC_GATE_MIN:
            reasons.append(f"critic.{k}={v} < {CRITIC_GATE_MIN}")
    return (not reasons), reasons


def timeline_ratios(bp: dict) -> list:
    """Beat start ratios in order — the timeline hooks the renderer consumes."""
    return [float(b.get("start_ratio") or 0.0)
            for b in bp.get("beats", []) if isinstance(b, dict)]


def clone(bp: dict) -> dict:
    return copy.deepcopy(bp)
