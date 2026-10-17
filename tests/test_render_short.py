"""Step 3/4 acceptance: the v3 renderer produces a REAL file that passes the gate.

NO MOCKS: this runs the actual v3 renderer (offline arm C, generated background)
with the real ffmpeg binary and asserts the finished MP4 passes `render_gate` in
the v3 6-12s window. It also checks the ASS text layer that replaced drawtext
(the pinned static ffmpeg has libass but not drawtext).

Run: python3 -m tests.test_render_short
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import build_short, config, render_gate  # noqa: E402


def _reel(arm="C") -> dict:
    return {
        "template": "T3", "topic": "saying I'm fine",
        "hook": "Send this to the friend who types 'haha' and means 'I'm fine'.",
        "payoff": "She typed 'haha' and sat there shaking.",
        "visual_arm": arm, "scene_queries": ["empty room night window rain"],
        "audio_type": "quiet_ambient", "length_bucket": "8-10",
    }


def test_ass_layer_keeps_every_word():
    # _fit_beat must never drop words from a lint-valid beat.
    text = "Send this to the friend who types 'haha' and means 'I'm fine'."
    lines, size = build_short._fit_beat(text, start_size=74, max_lines=3)
    assert " ".join(lines) == " ".join(text.split())
    assert 40 <= size <= 74


def test_write_ass_has_two_timed_events(tmp_path):
    p = tmp_path / "x.ass"
    build_short.write_ass(p, [
        {"start": 0.0, "end": 5.4, "size": 60, "lines": ["hook a", "hook b"]},
        {"start": 5.4, "end": 9.0, "size": 58, "lines": ["payoff"]},
    ])
    txt = p.read_text(encoding="utf-8")
    assert txt.count("Dialogue:") == 2
    assert "\\an5" in txt and "\\fs60" in txt
    assert "0:00:00.00,0:00:05.40" in txt


def test_offline_render_passes_gate():
    out = config.OUTPUTS / "test_short_gate.mp4"
    res = build_short.build(_reel("C"), {}, out_path=out, offline=True,
                            duration_s=9.0, verbose=False)
    assert res.get("ok"), f"render failed: {res.get('stage')} {res.get('why')}"
    assert out.exists() and out.stat().st_size > 0
    ok, report = render_gate.check(out, expect_audio=True, duration_bounds=(6.0, 12.0))
    assert ok, f"gate failed: {report.get('failures')}"
    assert report["width"] == 1080 and report["height"] == 1920
    # the v3 window, not the v2 14-34s window
    assert 6.0 - 0.05 <= report["duration_s"] <= 12.0 + 0.05


def test_short_format_duration_rejected_by_v2_window():
    """A valid 9s short must FAIL the old 14-34s window — proving the duration
    bound is per-format, not global."""
    out = config.OUTPUTS / "test_short_gate.mp4"
    if not out.exists():
        res = build_short.build(_reel("C"), {}, out_path=out, offline=True,
                                duration_s=9.0, verbose=False)
        assert res.get("ok")
    ok, report = render_gate.check(out, expect_audio=True)
    assert ok is False
    assert any("outside" in f for f in report.get("failures", []))
