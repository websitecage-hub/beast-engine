"""Contract tests for the two cloned templates and their engine integration.

Nothing here renders video or touches the network: these tests pin the rules
that make a render trustworthy — payload shape, template immutability, bounded
grading, the freshness ledger, and the fact that a missing toolchain is an
ERROR rather than a quietly-missing output.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import audio_features as af
from src.formats import registry, run_manifest, template_payloads, template_renderer

ROOT = Path(__file__).resolve().parents[1]
A_ITEMS = ["one", "two", "three", "four"]


def payload_a(**over):
    p = {
        "topic_id": "t-a",
        "headline": "4 things you do after a conversation",
        "titleLines": ["4 things you do", "after a", "conversation", "at night"],
        "list_items": A_ITEMS,
        "caption": "4 things:\n1. one\n2. two\n3. three\n4. four",
        "background_query": "walker on a forest trail",
        "audio": {"source": "audi0-scraper", "rights": "preview_only"},
        "provenance": {"source": "pinterest", "usage_clearance": "unknown"},
    }
    p.update(over)
    return p


def payload_b(**over):
    p = {
        "topic_id": "t-b",
        "hookLines": ["you sent hey.", "two hours ago."],
        "payoffLines": ["they're online.", "you're not."],
        "loop_hypothesis": "the last line re-reads as the first",
        "caption": "the two hours after hey",
        "background_query": "night window rain",
        "audio": {"source": "audi0-scraper", "rights": "preview_only"},
        "provenance": {"source": "pinterest", "usage_clearance": "unknown"},
    }
    p.update(over)
    return p


# --------------------------------------------------------------- payload rules
def test_valid_payloads_pass():
    assert template_payloads.validate(payload_a(), "caption_list_title_v1") == []
    assert template_payloads.validate(payload_b(), "social_moment_loop_v1") == []


def test_headline_count_must_match_list_exactly():
    p = payload_a(list_items=A_ITEMS[:3], caption="4 things:\n1. one\n2. two\n3. three")
    errs = template_payloads.validate(p, "caption_list_title_v1")
    assert any("promises 4 items" in e for e in errs), errs


def test_five_title_lines_rejected_not_shrunk():
    p = payload_a(titleLines=["a", "b", "c", "d", "e"])
    errs = template_payloads.validate(p, "caption_list_title_v1")
    assert any("5 lines" in e for e in errs), errs


def test_markup_injection_rejected():
    p = payload_a(headline="4 things <script>alert(1)</script>")
    errs = template_payloads.validate(p, "caption_list_title_v1")
    assert any("markup" in e for e in errs), errs


def test_b_line_budget_enforced():
    # 8 lines on screen is the measured reference maximum; 9 is over budget.
    p = payload_b(hookLines=["a", "b", "c", "d", "e"],
                  payoffLines=["f", "g", "h", "i"])
    errs = template_payloads.validate(p, "social_moment_loop_v1")
    assert any("max 8" in e for e in errs), errs
    assert template_payloads.validate(payload_b(), "social_moment_loop_v1") == []


def test_cleared_audio_requires_evidence():
    p = payload_a(audio={"source": "x", "rights": "cleared"})
    errs = template_payloads.validate(p, "caption_list_title_v1")
    assert any("rights_evidence" in e for e in errs), errs


# ------------------------------------------------------- template immutability
EXPECTED_INDEX = {
    "caption_list_title_v1":
        "fa13af1aa2efc2538f042bd2c7edae6690b213a3c2719900aa11339df3de50fc",
    "social_moment_loop_v1":
        "42c63aa0781a3221bf254091fc3872139d0335f72ff9307b61e58f0e1a32ae1b",
}


@pytest.mark.parametrize("fmt", ["caption_list_title_v1", "social_moment_loop_v1"])
def test_engine_template_matches_registered_hash(fmt):
    fmt_meta = registry.get(fmt)
    actual = run_manifest.sha256_file(ROOT / "templates" / fmt / "index.html")
    assert actual == EXPECTED_INDEX[fmt], "template bytes drifted from the source"
    assert actual == fmt_meta["template"]["index_sha256"]


def test_staging_writes_only_the_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(template_renderer, "SCRATCH", tmp_path)
    src = ROOT / "templates" / "social_moment_loop_v1" / "index.html"
    before = run_manifest.sha256_file(src)
    proj, info = template_renderer.stage_project(
        "social_moment_loop_v1", payload_b(), run_id="unit-stage")
    after = run_manifest.sha256_file(src)
    assert before == after, "source template was modified"
    html = (proj / "index.html").read_text()
    assert "you sent hey." in html and "--bg-filter:" in html
    assert info["grade"]["brightness"] > 0


# ------------------------------------------------------------------- grading
def test_grade_is_bounded_even_for_an_extreme_clip():
    css, vals = template_renderer.grade_for("caption_list_title_v1",
                                            {"yavg": 5.0, "sat": 60.0})
    lim = template_renderer.PROFILES["caption_list_title_v1"]["grade_limits"]
    assert lim["brightness"][0] <= vals["brightness"] <= lim["brightness"][1]
    assert lim["saturate"][0] <= vals["saturate"] <= lim["saturate"][1]
    assert vals["natural_fit"] is False
    assert "brightness(" in css


def test_natural_fit_is_reported():
    p = template_renderer.PROFILES["social_moment_loop_v1"]
    _, vals = template_renderer.grade_for("social_moment_loop_v1",
                                          {"yavg": p["yavg"], "sat": p["sat"]})
    assert vals["natural_fit"] is True


# ----------------------------------------------------------- registry entries
@pytest.mark.parametrize("fmt", ["caption_list_title_v1", "social_moment_loop_v1"])
def test_registered_but_not_schedulable(fmt):
    entry = registry.get(fmt)
    assert entry["status"] == "not_eligible"
    assert entry["preview_only"] is True
    assert entry["publishing"] == "disabled"
    assert registry.is_eligible(fmt) is False
    assert registry.renderer_for(fmt).id == fmt


def test_old_formats_still_validate():
    assert registry.is_eligible("two_beat_v3") is True
    assert registry.is_eligible("paced_v2") is True


# ---------------------------------------------------------------- toolchain
def test_missing_toolchain_raises_not_fakes(monkeypatch, tmp_path):
    monkeypatch.setattr(template_renderer, "toolchain_status",
                        lambda: {"node": None, "npx": None, "ok": False})
    with pytest.raises(template_renderer.TemplateUnavailable):
        template_renderer.render_project(tmp_path, tmp_path / "x.mp4")


# --------------------------------------------------------- manifest / ledger
def test_ledger_consumes_only_successful_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(run_manifest, "MANIFEST", tmp_path / "m.json")
    bg = {"sha256": "bg1", "stable_id": "pin1"}
    au = {"sha256": "au1", "stable_id": "sound1"}
    ok = run_manifest.make_entry(run_id="r1", format_id="caption_list_title_v1",
                                 template_dir="t", template_sha256="h", payload=payload_a(),
                                 background=bg, audio=au,
                                 render={"duration_s": 12.2}, qa={"ok": True},
                                 output=tmp_path / "missing.mp4")
    bad = dict(ok, run_id="r2", qa_ok=False, status="needs_review")
    run_manifest.save([ok, bad])
    used = run_manifest.consumed()
    assert "bg1" in used["backgrounds"] and "au1" in used["audio"]
    assert "r2" not in used["run_ids"], "a failed run must not consume assets"


def test_release_frees_a_reservation(monkeypatch, tmp_path):
    monkeypatch.setattr(run_manifest, "MANIFEST", tmp_path / "m.json")
    run_manifest.save([{"run_id": "r1", "status": "rendered", "qa_ok": True,
                        "background": {"sha256": "bg1"}, "audio": {}}])
    assert run_manifest.release("r1") is True
    assert run_manifest.consumed()["backgrounds"] == set()


# ------------------------------------------------------- audio feature errors
def test_missing_audio_fails_loudly():
    with pytest.raises(af.AudioFeatureError):
        af.analyze("/tmp/definitely-not-here.m4a")


def test_unmeasured_track_cannot_score():
    with pytest.raises(af.AudioFeatureError):
        af.distance({"ref_tempo_bpm": 100}, {})


def test_reference_accepts_both_shapes():
    feats = {"tempo_bpm": 100.0, "spectral_centroid_hz": 1000.0,
             "rms_db": -16.0, "onset_density_per_s": 2.0}
    assert af.closeness({"ref_tempo_bpm": 100, "ref_spectral_centroid_hz": 1000,
                         "ref_rms_db": -16, "ref_onset_density_per_s": 2}, feats) > 0.9
    assert af.closeness({"tempo_bpm": 100, "spectral_centroid_hz": 1000,
                         "rms_db": -16, "onset_density_per_s": 2}, feats) > 0.9