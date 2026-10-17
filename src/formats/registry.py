"""registry.py — declarative format contracts (Section 6, Section 12).

A *format* is data: its purpose, the input fields it needs, its duration window,
its pacing rules, the gates it must pass, its eligibility conditions and its
status. The runner reads this table; it does not hard-code format behavior.

Status is one of:
  * "active"        — eligible to be scheduled by the experiment engine.
  * "not_eligible"  — registered but deliberately NOT scheduled (e.g. the
                      cinematic arm, which has no rights-cleared asset path yet).

`validate_for_format` layers FORMAT-SPECIFIC constraints on top of the
structural `blueprint.validate`, and `duration_ok` is the per-format version of
the old single 14-34s rule (acceptance: "format-specific durations are allowed").
"""
from __future__ import annotations

from .. import blueprint as _bp

CONTRACT_VERSION = "1.0"

# Declarative format table. Keep additions minimal and reversible.
FORMATS = {
    "two_beat_v3": {
        "id": "two_beat_v3",
        "name": "Two-beat short (v3)",
        "status": "active",
        "purpose": "One specific situation delivered as a hook then a payoff, "
                   "short enough to loop and specific enough to send.",
        "renderer": "two_beat_v3",
        "duration_bounds": [6.0, 12.0],
        "pacing": {"hook_alone_at_frame0": True, "payoff_start_ratio": 0.60,
                   "text_chunk": "phrase", "loop_seamless": True},
        "text_constraints": {"words": [8, 22], "hook_words_max": 14,
                             "payoff_words_max": 12},
        "arms": ["A", "B", "C"],
        "gates": ["lint_short", "render_gate"],
        "eligibility": {
            "requires_rights": [],
            "requires_assets": False,
            "notes": "Arm C (plain text) needs no asset; arms A/B need "
                     "rights-cleared visual + audio.",
        },
        "publishing": "disabled",
        "contract_version": CONTRACT_VERSION,
    },
    "paced_v2": {
        "id": "paced_v2",
        "name": "Paced-reveal confession (v2)",
        "status": "active",
        "purpose": "Baseline controlled arm: a short confession where each line "
                   "reveals in turn and prior lines dim.",
        "renderer": "paced_v2",
        "duration_bounds": [14.0, 34.0],
        "pacing": {"dim_alpha": 0.55, "cta_tail_s": 2.5, "reveal": "per_line",
                   "final_hold_s": 2.0},
        "text_constraints": {"lines": [5, 8]},
        "arms": ["A", "B"],
        "gates": ["lint", "render_gate"],
        "eligibility": {"requires_rights": [], "requires_assets": False,
                        "notes": "Existing production format; kept as control."},
        "publishing": "disabled",
        "contract_version": CONTRACT_VERSION,
    },
    "cinematic_v1": {
        "id": "cinematic_v1",
        "name": "Cinematic multi-scene phrase-synced (v1)",
        "status": "not_eligible",
        "purpose": "Multi-shot montage with phrase-synced captions and "
                   "voiceover (the third-party visual evidence's structure).",
        "renderer": None,
        "duration_bounds": None,
        "pacing": None,
        "text_constraints": None,
        "arms": [],
        "gates": [],
        "eligibility": {
            "requires_rights": ["visual license", "audio license"],
            "requires_assets": True,
            "reason": "No rights-cleared semantic b-roll source or verified "
                      "authentic footage pipeline; the only supporting evidence "
                      "is third-party and uncontrolled. Not scheduled. "
                      "See docs/research/RESEARCH_AUDIT.md section 9.",
        },
        "publishing": "disabled",
        "contract_version": CONTRACT_VERSION,
    },
}


def all_formats() -> dict:
    return FORMATS


def get(format_id: str) -> dict:
    if format_id not in FORMATS:
        raise KeyError(f"unknown format_id {format_id!r}; "
                       f"known: {sorted(FORMATS)}")
    return FORMATS[format_id]


def list_formats() -> list:
    """Compact summaries for reports/logs."""
    return [{"id": f["id"], "name": f["name"], "status": f["status"],
             "duration_bounds": f["duration_bounds"], "renderer": f["renderer"],
             "publishing": f["publishing"]} for f in FORMATS.values()]


def eligible() -> list:
    """Format ids the experiment engine may schedule."""
    return [f["id"] for f in FORMATS.values() if f["status"] == "active"]


def is_eligible(format_id: str) -> bool:
    return format_id in FORMATS and FORMATS[format_id]["status"] == "active"


def duration_ok(format_id: str, seconds) -> bool:
    """Per-format duration window. Unknown/None bounds => not ok (fail closed)."""
    bounds = get(format_id).get("duration_bounds")
    if not bounds or seconds is None:
        return False
    lo, hi = bounds
    return (lo - 0.05) <= float(seconds) <= (hi + 0.05)


def validate_for_format(bp: dict) -> list:
    """Structural validation + this format's constraints. Returns error list."""
    errors = list(_bp.validate(bp))
    fmt_id = bp.get("format_id")
    if fmt_id not in FORMATS:
        errors.append(f"unknown format_id {fmt_id!r}")
        return errors
    fmt = FORMATS[fmt_id]
    if fmt["status"] != "active":
        errors.append(f"format {fmt_id!r} is not eligible "
                      f"(status={fmt['status']})")

    tc = fmt.get("text_constraints") or {}
    beats = [b for b in bp.get("beats", []) if isinstance(b, dict)]
    if "words" in tc:
        wmin, wmax = tc["words"]
        total = sum(len(str(b.get("text") or "").split()) for b in beats)
        if not (wmin <= total <= wmax):
            errors.append(f"total words {total} outside {wmin}-{wmax} "
                          f"for {fmt_id}")
    if "hook_words_max" in tc:
        hook = next((b for b in beats if b.get("kind") == "hook"), None)
        if hook and len(str(hook.get("text") or "").split()) > tc["hook_words_max"]:
            errors.append(f"hook exceeds {tc['hook_words_max']} words")
    if "payoff_words_max" in tc:
        po = next((b for b in beats if b.get("kind") == "payoff"), None)
        if po and len(str(po.get("text") or "").split()) > tc["payoff_words_max"]:
            errors.append(f"payoff exceeds {tc['payoff_words_max']} words")
    if "lines" in tc and beats:
        lmin, lmax = tc["lines"]
        if not (lmin <= len(beats) <= lmax):
            errors.append(f"{len(beats)} beats outside {lmin}-{lmax} lines "
                          f"for {fmt_id}")

    arm = bp.get("visual_arm")
    if arm and fmt.get("arms") and arm not in fmt["arms"]:
        errors.append(f"visual_arm {arm!r} not allowed for {fmt_id} "
                      f"(allowed {fmt['arms']})")
    return errors


def renderer_for(format_id: str, **kw):
    """Instantiate the renderer declared by the format (lazy import).

    Extra kwargs (memory=, offline=, cfg=) are passed to the adapter's
    constructor so the caller can inject run context.
    """
    fmt = get(format_id)
    if not fmt.get("renderer"):
        raise RuntimeError(f"format {format_id!r} has no renderer "
                           f"(status={fmt['status']})")
    from . import renderers
    return renderers.build(fmt["renderer"], **kw)


if __name__ == "__main__":
    import json
    print(json.dumps(list_formats(), indent=2))
