"""Contracts of the live production line (the legacy suites went with the legacy code).

Kept deliberately small and specific: every test here guards a rule that was broken at
least once in production.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.formats import asset_ledger, template_renderer, text_style

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- colour layer
def test_no_colour_layer_is_applied_to_the_background(monkeypatch):
    """The clip the search returned is the clip the viewer sees."""
    monkeypatch.delenv("BEAST_BG_GRADE", raising=False)
    css, vals = template_renderer.grade_for("caption_list_title_v1",
                                            {"yavg": 5.0, "sat": 60.0})
    assert css == "none", "a filter was applied to the footage"
    assert vals["applied"] is False
    assert vals["brightness"] == 1.0 and vals["saturate"] == 1.0
    assert vals["delta"]["yavg"] < 0        # drift is still reported


def test_the_bounded_grade_still_exists_behind_the_switch(monkeypatch):
    monkeypatch.setenv("BEAST_BG_GRADE", "1")
    css, vals = template_renderer.grade_for("caption_list_title_v1",
                                            {"yavg": 5.0, "sat": 60.0})
    lim = template_renderer.PROFILES["caption_list_title_v1"]["grade_limits"]
    assert lim["brightness"][0] <= vals["brightness"] <= lim["brightness"][1]
    assert lim["saturate"][0] <= vals["saturate"] <= lim["saturate"][1]
    assert "brightness(" in css and vals["applied"] is True


def test_templates_apply_the_filter_from_the_engine(monkeypatch):
    """The template's only grade hook is --bg-filter; with no grade it resolves none."""
    for fmt in ("caption_list_title_v1", "social_moment_loop_v1"):
        html = (ROOT / "templates" / fmt / "index.html").read_text()
        assert "var(--bg-filter, none)" in html, f"{fmt} is not using the engine hook"


def test_text_colour_follows_the_raw_footage():
    assert text_style.for_background({"yavg": 60.0}, {"brightness": 1.0})["fill"] == "#E0D29B"
    assert text_style.for_background({"yavg": 150.0}, {"brightness": 1.0})["fill"] == "#FFFFFF"
    assert text_style.for_background({"yavg": 105.0}, {"brightness": 1.0})["fill"] == "#F7F0D2"


# ------------------------------------------------------------------- state
def test_template_manifest_is_valid_json():
    """A rebase once committed conflict markers here and every run died on load."""
    data = json.loads((ROOT / "data" / "template_manifest.json").read_text())
    assert isinstance(data, list)
    assert all("run_id" in e for e in data)


def test_a_list_shaped_ledger_is_treated_as_corrupt_not_crashing(monkeypatch, tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text("[]")
    monkeypatch.setenv("BEAST_LEDGER", str(p))
    data = asset_ledger.load()
    assert isinstance(data, dict) and data.get("recovered_from_corrupt") is True
    assert data["visual"] == {}


def test_publishing_is_enabled_in_config():
    cfg = json.loads((ROOT / "data" / "config.json").read_text())
    assert cfg.get("short_publish_enabled") is True


# ------------------------------------------------------------- the schedule
def test_the_workflow_runs_twice_a_day_and_publishes_by_default():
    """Two wakes, one format each, publishing without a human filling in a form."""
    text = (ROOT / ".github" / "workflows" / "preview.yml").read_text()
    assert 'cron: "0 9 * * *"' in text and 'cron: "0 17 * * *"' in text
    assert "github.event_name == 'schedule' || inputs.publish" in text
    assert "env.RUN_PUBLISH == 'true' && success()" in text, \
        "publishing must not run after a failed render"
    assert "python -m src.run_preview" in text and "python -m src.run_publish" in text


def test_no_legacy_workflows_remain():
    names = {p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")}
    assert names == {"preview.yml"}, f"legacy workflows still present: {sorted(names)}"


def test_only_the_live_modules_remain(monkeypatch):
    """The tree is the pipeline: 29 modules, nothing retired hanging off it."""
    mods = {p.stem for p in (ROOT / "src").glob("*.py")}
    mods |= {p.stem for p in (ROOT / "src" / "formats").glob("*.py")}
    legacy = {"run_create", "run_short", "run_v2", "build_video", "build_short",
              "learner", "harvest", "pexels", "voiceover", "carousel", "registry",
              "renderers", "analyze", "insights", "strategy_v2"}
    assert not (mods.intersection(legacy)), f"legacy modules still present: " \
                                            f"{sorted(mods.intersection(legacy))}"
