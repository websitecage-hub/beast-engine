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
def test_there_is_no_colour_grading_at_all():
    """The owner's instruction: search -> OCR filter -> download the best -> compose.

    No grade, no saturation pass, no brightness correction, and no hook left in the
    templates that could apply one.
    """
    assert not hasattr(template_renderer, "grade_for"), "grade_for still exists"
    assert not hasattr(template_renderer, "_background_grade_enabled")
    for fmt in ("caption_list_title_v1", "social_moment_loop_v1"):
        html = (ROOT / "templates" / fmt / "index.html").read_text()
        assert "bg-filter" not in html, f"{fmt} still has a filter hook"
        assert "filter:" not in html, f"{fmt} still applies a filter"


def test_the_renderer_injects_media_and_no_filter():
    src = (ROOT / "src" / "formats" / "template_renderer.py").read_text()
    assert "saturate(" not in src and "brightness(" not in src, \
        "a colour transform is still being written into the render"
    assert "no colour grading" in src
    assert "fixed_grade" not in src, "a correction pass still exists"


def test_text_colour_follows_the_raw_footage():
    assert text_style.for_background({"yavg": 60.0})["fill"] == "#E0D29B"
    assert text_style.for_background({"yavg": 150.0})["fill"] == "#FFFFFF"
    assert text_style.for_background({"yavg": 105.0})["fill"] == "#F7F0D2"


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


# ------------------------------------------------------- the staging path
def _tiny_media(tmp_path):
    """A 1.5s clip + silent audio, so the staging path can be exercised for real."""
    import subprocess
    from src import config
    ff = config.resolve_ffmpeg()
    vid, aud = tmp_path / "bg.mp4", tmp_path / "bgm.m4a"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=size=1080x1920:rate=30:duration=1.5",
                    "-pix_fmt", "yuv420p", str(vid)], check=True, timeout=120)
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=stereo", "-t", "1.5",
                    "-c:a", "aac", str(aud)], check=True, timeout=120)
    return vid, aud


def test_staging_injects_content_and_never_a_filter(tmp_path, monkeypatch):
    """Regression: a leftover variable here raised NameError inside the CI run.

    The staging path is pure Python plus a template copy, so it can be exercised
    without the HyperFrames toolchain - which is exactly why it should never break a
    render silently again.
    """
    vid, aud = _tiny_media(tmp_path)
    payload = {"titleLines": ["a", "b"], "caption": "c",
               "background_query": "night window", "topic_id": "t"}
    import uuid
    proj, info = template_renderer.stage_project(
        "caption_list_title_v1", payload, run_id=f"stage-{uuid.uuid4().hex[:8]}",
        background=vid, audio=aud)
    html = (proj / "index.html").read_text()
    assert "const CONTENT" in html
    assert "filter" not in html.lower().replace("filter: none", "")
    assert info["grade_css"] is None
    assert info["grade"]["applied"] is False
    assert (proj / "assets" / "media" / "bg.mp4").exists()
