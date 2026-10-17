"""Step 2 acceptance: format registry, renderer interface, duration bounds.

Covers format eligibility, per-format duration constraints, unsupported rights
state at the format layer, and the renderer's deterministic timeline build.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import blueprint as bp  # noqa: E402
from src.formats import registry, renderers  # noqa: E402


def _raises(exc, fn, *a, **k) -> bool:
    """Local raises helper so the test needs no third-party import (the
    acceptance gate requires every test import to be declared in requirements)."""
    try:
        fn(*a, **k)
    except exc:
        return True
    return False


def _v3(arm="C", rights=("visual license", "audio license")) -> dict:
    return bp.new(
        "two_beat_v3", evidence_refs=["rec-1"], hook_mechanic="T3",
        beats=[{"kind": "hook", "text": "Send this to the friend who types haha",
                "start_ratio": 0.0},
               {"kind": "payoff", "text": "She typed haha and shook",
                "start_ratio": 0.6}],
        audio_plan={"mode": "licensed_ambient", "track_id": None},
        rights_requirements=list(rights), visual_arm=arm, length_bucket="8-10")


# ----------------------------------------------------------------- registry

def test_unknown_format_raises():
    assert _raises(KeyError, registry.get, "nope")


def test_eligible_is_active_only():
    elig = registry.eligible()
    assert "two_beat_v3" in elig and "paced_v2" in elig
    assert "cinematic_v1" not in elig


def test_cinematic_is_not_eligible():
    assert registry.is_eligible("cinematic_v1") is False
    assert registry.get("cinematic_v1")["status"] == "not_eligible"


def test_duration_bounds_are_format_specific():
    assert registry.duration_ok("two_beat_v3", 10.0) is True
    assert registry.duration_ok("two_beat_v3", 20.0) is False
    assert registry.duration_ok("paced_v2", 20.0) is True
    assert registry.duration_ok("paced_v2", 40.0) is False
    # unknown bounds / value fail closed
    assert registry.duration_ok("cinematic_v1", 10.0) is False
    assert registry.duration_ok("two_beat_v3", None) is False


def test_validate_for_format_word_budget():
    b = _v3()
    b["beats"][0]["text"] = ("way too many words in this single hook beat here "
                             "right now ok friend sure")
    errs = registry.validate_for_format(b)
    assert any("hook exceeds" in e for e in errs), errs


def test_validate_for_format_rejects_ineligible():
    b = _v3()
    b["format_id"] = "cinematic_v1"
    assert any("not eligible" in e for e in registry.validate_for_format(b))


def test_validate_for_format_rejects_bad_arm():
    b = _v3(arm="Z")
    assert any("visual_arm" in e for e in registry.validate_for_format(b))


def test_renderer_for_returns_adapted_renderer():
    r = registry.renderer_for("two_beat_v3")
    assert isinstance(r, renderers.TwoBeatV3Renderer)
    assert _raises(RuntimeError, registry.renderer_for, "cinematic_v1")


# ----------------------------------------------------------------- renderer

def test_renderer_validate_accepts_clean_v3():
    assert renderers.TwoBeatV3Renderer().validate_blueprint(_v3()) == []


def test_renderer_rejects_wrong_beat_kinds():
    b = _v3()
    b["beats"][0]["kind"] = "line"
    assert any("hook, payoff" in e
               for e in renderers.TwoBeatV3Renderer().validate_blueprint(b))


def test_arm_ab_requires_declared_rights():
    r = renderers.TwoBeatV3Renderer()
    b = _v3(arm="A", rights=["visual license"])
    assert any("rights" in e for e in r.validate_blueprint(b))


def test_arm_c_needs_no_third_party_rights():
    # generic declared labels satisfy structure; arm C adds no extra asset rights
    assert renderers.TwoBeatV3Renderer().validate_blueprint(_v3(arm="C")) == []


def test_v3_timeline_is_deterministic_and_in_bounds():
    r = renderers.TwoBeatV3Renderer()
    t = r.build_timeline(_v3())
    assert registry.duration_ok("two_beat_v3", t["duration_s"])
    assert t["beats"][0]["start_s"] == 0.0
    assert abs(t["beats"][1]["start_s"] - 0.6 * t["duration_s"]) < 0.05
    # same input -> same timeline
    assert t == r.build_timeline(_v3())


def test_v2_timeline_uses_reveal_schedule():
    from src import lint
    b = bp.new("paced_v2", evidence_refs=["rec-1"], hook_mechanic="recognition",
               beats=[{"kind": "hook", "text": "I rehearse the call",
                       "start_ratio": 0.0},
                      {"kind": "line", "text": "then I don't make it",
                       "start_ratio": 0.2},
                      {"kind": "line", "text": "and I say I was busy",
                       "start_ratio": 0.4},
                      {"kind": "line", "text": "busy being scared",
                       "start_ratio": 0.6},
                      {"kind": "payoff", "text": "every single time",
                       "start_ratio": 0.8}],
               audio_plan={"mode": "silent", "track_id": None})
    r = renderers.PacedV2Renderer()
    t = r.build_timeline(b)
    _, dur = lint.reveal_schedule([x["text"] for x in b["beats"]])
    assert t["duration_s"] == dur
    assert len(t["beats"]) == len(b["beats"])


def test_qa_fails_closed_without_artifact():
    r = renderers.TwoBeatV3Renderer()
    q = r.qa({"ok": False, "stage": "render", "why": "boom"}, _v3())
    assert q["ok"] is False and q["why"] == "boom"


def test_v2_render_without_preconditions_fails_closed():
    r = renderers.PacedV2Renderer()
    out = r.render({"format_id": "paced_v2", "duration_s": 20.0}, "/tmp/none.mp4")
    assert out["ok"] is False and out["stage"] == "precondition"
