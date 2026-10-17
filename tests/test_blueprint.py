"""Step 2 acceptance: ContentBlueprint schema validation.

Covers the brief's list — schema validation, unsupported fields, unsupported
rights state and metric null semantics — plus critic fail-closed behaviour.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import blueprint as bp  # noqa: E402


def _valid() -> dict:
    return bp.new(
        "two_beat_v3", experiment_id="exp-1",
        hook_mechanic="T3", evidence_refs=["rec-1"],
        beats=[{"kind": "hook", "text": "Send this to the friend who types haha",
                "start_ratio": 0.0},
               {"kind": "payoff", "text": "She typed haha and shook",
                "start_ratio": 0.6}],
        audio_plan={"mode": "licensed_ambient", "track_id": None},
        visual_arm="C", length_bucket="8-10")


def test_valid_blueprint_passes():
    assert bp.validate(_valid()) == []


def test_unsupported_field_is_rejected():
    b = _valid()
    b["totally_bogus"] = 1
    errs = bp.validate(b)
    assert any("unsupported field" in e for e in errs), errs


def test_missing_payoff_beat_is_rejected():
    b = _valid()
    b["beats"] = [{"kind": "hook", "text": "only a hook here", "start_ratio": 0.0}]
    assert any("payoff" in e for e in bp.validate(b))


def test_first_beat_must_start_at_zero():
    b = _valid()
    b["beats"][0]["start_ratio"] = 0.2
    assert any("first beat" in e for e in bp.validate(b))


def test_beat_ratios_must_ascend():
    b = _valid()
    b["beats"] = [
        {"kind": "hook", "text": "a", "start_ratio": 0.0},
        {"kind": "line", "text": "b", "start_ratio": 0.5},
        {"kind": "payoff", "text": "c", "start_ratio": 0.2},
    ]
    assert any("regress" in e for e in bp.validate(b))


def test_evidence_refs_are_mandatory():
    b = _valid()
    b["evidence_refs"] = []
    assert any("evidence_refs" in e for e in bp.validate(b))


def test_hard_reject_risk_flag():
    b = _valid()
    b["risk_flags"] = ["clinical_overclaim"]
    assert any("hard-reject" in e for e in bp.validate(b))


def test_unsupported_rights_state_is_rejected():
    b = _valid()
    b["rights_requirements"] = ["pinterest_url_only"]
    assert any("unsupported rights" in e for e in bp.validate(b))


def test_invalid_audio_mode_is_rejected():
    b = _valid()
    b["audio_plan"] = {"mode": "whatever", "track_id": None}
    assert any("audio_plan.mode" in e for e in bp.validate(b))


# ---------------------------------------------------- metric null semantics

def test_missing_metric_is_none_not_zero():
    assert bp.as_metric(None) is None
    assert bp.as_metric("") is None
    assert bp.as_metric("  ") is None
    assert bp.as_metric("n/a") is None


def test_real_zero_stays_zero():
    assert bp.as_metric(0) == 0.0
    assert bp.as_metric("0") == 0.0


def test_numeric_string_coerces():
    assert bp.as_metric("12.5") == 12.5


# ---------------------------------------------------- critic fail-closed

def test_critic_missing_dimension_fails_closed():
    ok, reasons = bp.critic_passed({"specificity": 5})
    assert ok is False
    assert any("missing" in r for r in reasons)


def test_critic_below_bar_fails():
    c = {k: 4 for k in bp.CRITIC_GATE_KEYS}
    c["payoff"] = 3
    ok, reasons = bp.critic_passed(c)
    assert ok is False and any("payoff" in r for r in reasons)


def test_critic_all_pass():
    c = {k: 4 for k in bp.CRITIC_GATE_KEYS}
    ok, reasons = bp.critic_passed(c)
    assert ok is True and reasons == []


def test_new_defaults_are_structural_only():
    b = bp.new("two_beat_v3")
    # new() intentionally returns an incomplete blueprint; validate reports it.
    assert bp.validate(b)
    assert b["schema_version"] == bp.SCHEMA_VERSION
