"""Offline contract tests for the asset intelligence layer.

Everything here is deterministic and network-free. The rules being pinned:

* the intent compiler refuses mood slogans and keeps queries inside the 55-char
  practical limit that the deployed search endpoint enforces
* ads, promoted pins and branded domains never become a background
* an unmeasurable property scores 0 and forces `needs_human_review` - it can
  never be silently accepted
* the minimum candidate score is never lowered to force a run to finish
* freshness is a reservation: released on failure, committed only on delivery
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src.formats import (asset_broker, asset_intent, asset_ledger, asset_probe,
                         asset_scoring)

INTENT = {
    "schema_version": "1.0", "content_id": "bus-ride", "format_id": "caption_list_title_v1",
    "core_human_moment": "replaying the goodbye on the bus home",
    "literal_scene": "night bus window city lights", "scene_entities": ["bus", "window"],
    "human_action": "looking out of the window", "setting": "night bus",
    "time_of_day": "late evening", "camera_perspective": "pov",
    "motion_intent": "gentle continuous movement",
    "emotional_primary": "quiet self-consciousness", "emotional_secondary": "relief",
    "valence": -0.2, "arousal": 0.2, "tension": 0.4, "warmth": 0.4, "humour": 0.1,
    "visual_mood": ["quiet", "night"], "palette_intent": ["cool", "low saturation"],
    "text_safe_zone": "centre", "visual_profile_target": {"luma": "format profile",
                                                          "saturation": "format profile"},
    "literalness": "literal", "intentional_contrast": None,
    "negative_visuals": ["embedded captions", "watermarks"],
    "video_queries": ["night bus window city lights", "bus window city lights night",
                      "city lights through window night"],
    "audio_intent": {"emotional_role": "restrained reflective bed", "valence": -0.2,
                     "arousal": 0.2, "energy": 0.25, "tempo_preference": "slow",
                     "texture": ["soft"], "instrumentation": ["piano"],
                     "vocal_policy": "instrumental_preferred",
                     "rhythmic_density": "sparse", "avoid": ["anthem"],
                     "trend_keywords": ["overthinking"], "trend_hashtags": ["#overthinking"]},
    "rationale": "the bus window makes the post-party journey tangible",
}

PROF = {"duration_s": 12.2, "yavg": 105.0, "yavg_tol": 14.0, "sat": 14.2, "sat_tol": 5.0,
        "music": {"ref_tempo_bpm": 139.7, "ref_spectral_centroid_hz": 951.4,
                  "ref_rms_db": -16.9, "ref_onset_density_per_s": 2.73}}


def _measured(**over) -> dict:
    base = {"width": 1080, "height": 1920, "duration_s": 20.0,
            "metrics": {"yavg": 105.0, "sat": 14.2},
            "motion": {"ok": True, "motion": 0.03, "max_jump": 0.05, "cuts": 0},
            "text": {"text_suspected": False, "reason": "clean"},
            "band_busy": 0.04}
    base.update(over)
    return base


def _entry(**over) -> dict:
    base = {"id": "1", "title": "night bus window city lights", "description": "",
            "domain": "Uploaded by user", "is_promoted": False, "type": "video"}
    base.update(over)
    return base


# ------------------------------------------------------------------ intent
def test_clip_keeps_queries_inside_the_55_char_limit():
    long = "night bus window city lights reflection on the glass late evening downtown"
    clipped = asset_intent.clip(long)
    assert len(clipped) <= 55
    assert long.startswith(clipped)


def test_validate_rejects_mood_slogans():
    bad = dict(INTENT, video_queries=["dark aesthetic night city", "city lights night"])
    errs = asset_intent.validate(bad)
    assert any("mood slogan" in e for e in errs)


def test_validate_bounds_numeric_scales():
    bad = dict(INTENT, arousal=1.7, valence=-4)
    errs = asset_intent.validate(bad)
    assert any("arousal" in e for e in errs) and any("valence" in e for e in errs)


def test_lanes_are_bounded_and_deduplicated():
    intent = dict(INTENT, video_queries=["a night bus window", "A NIGHT BUS WINDOW",
                                         "bus window night", "fourth query", "fifth"])
    plan = asset_intent.lanes(intent)
    assert [p["lane"] for p in plan] == ["literal", "adjacent", "emotional"]
    assert len(plan) == 3


def test_fallback_intent_is_valid_and_marked_degraded():
    intent = asset_intent.fallback({"background_query": "night bus window city lights",
                                    "topic_id": "bus"}, "social_moment_loop_v1")
    assert asset_intent.validate(intent) == []
    assert intent["degraded"] is True and intent["compiled_by"] == "fallback"


def test_intent_compile_falls_back_when_the_model_returns_junk():
    intent = asset_intent.compile_intent({"background_query": "quiet kitchen table",
                                          "topic_id": "x"},
                                         "caption_list_title_v1",
                                         chat=lambda p: ("not json", "test"),
                                         verbose=False)
    assert intent["degraded"] is True
    assert asset_intent.validate(intent) == []


def test_audio_keywords_drop_slogans():
    intent = dict(INTENT, audio_intent={**INTENT["audio_intent"],
                                        "trend_keywords": ["dark aesthetic", "overthinking"]})
    assert asset_intent.audio_keywords(intent) == ["overthinking"]


# ------------------------------------------------------------------ ad filters
def test_promoted_and_commerce_candidates_are_hard_rejected():
    assert "promoted" in asset_scoring.hard_reject(_entry(is_promoted=True), needed_s=7)
    assert "call-to-action" in asset_scoring.hard_reject(
        _entry(description="shop now for 20% off"), needed_s=7)
    assert "commerce" in asset_scoring.hard_reject(
        _entry(domain="shopify.com"), needed_s=7)
    assert asset_scoring.hard_reject(_entry(), needed_s=7) is None


def test_social_platform_domains_are_soft_flags_not_rejections():
    """Measured: rejecting every non-user domain killed a whole lane (instagram.com)."""
    for dom in ("instagram.com", "tiktok.com", "youtube.com"):
        assert asset_scoring.hard_reject(_entry(domain=dom), needed_s=7) is None
        assert any("domain" in f for f in asset_scoring.soft_flags(_entry(domain=dom)))


def test_non_video_and_wrong_shape_are_rejected_by_the_broker(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "l.json"))
    monkeypatch.setattr(asset_broker.pinterest, "search_videos",
                        lambda q, page_size=25: [_entry(id="img", type="image")])
    with pytest.raises(asset_broker.AssetError):
        asset_broker.acquire(INTENT, "caption_list_title_v1", tmp_path, run_id="x",
                             verbose=False)


# ------------------------------------------------------------------ scoring
def test_score_candidate_passes_an_ideal_clip():
    grade = {"natural_fit": True, "brightness": 1.0, "saturate": 1.0}
    score = asset_scoring.score_candidate(_entry(), intent=INTENT, lane="literal",
                                          measured=_measured(), prof=PROF, grade=grade)
    assert score["total"] >= 75 and score["passes"] and score["review_status"] == "accept"
    assert score["dimensions"]["semantic_scene_fit"]["points"] > 0


def test_score_candidate_forces_review_when_something_is_unmeasured():
    measured = _measured(motion={"ok": False, "motion": None, "cuts": None},
                         text={"text_suspected": None, "reason": "frames undecodable"})
    score = asset_scoring.score_candidate(_entry(), intent=INTENT, lane="literal",
                                          measured=measured, prof=PROF,
                                          grade={"natural_fit": True})
    assert score["review_status"] == "needs_human_review"
    assert score["passes"] is False
    assert "motion_compatibility" in score["unknown_dimensions"]


def test_score_candidate_refuses_a_clip_the_grade_cannot_fix():
    """A bright daytime clip cannot be darkened into a night-loop band."""
    bright = _measured(metrics={"yavg": 210.0, "sat": 30.0})
    score = asset_scoring.score_candidate(_entry(), intent=INTENT, lane="literal",
                                          measured=bright, prof=PROF,
                                          grade={"natural_fit": False, "brightness": 0.85})
    assert score["passes"] is False
    assert any("grade can only reach" in b for b in score["blockers"])


def test_gradeable_accepts_a_clip_the_bounded_grade_can_land():
    """99 luma graded at the 0.85 limit reaches ~84, inside 105+-14? No - so it must fail,
    but a 105-luma clip with a non-ideal saturate must still pass."""
    at_target = _measured(metrics={"yavg": 105.0, "sat": 22.0})
    ok, why = asset_scoring.gradeable(PROF, at_target,
                                      {"natural_fit": False, "saturate": 0.65})
    assert ok is True and "reaches luma" in why


def test_uniform_dark_areas_are_framing_not_text():
    """A dark interior was mis-flagged as a caption; uniform bars are framing."""
    frame = np.full((360, 360), 70, dtype=np.uint8)
    frame[:60, :] = 25                      # dark uniform top bar
    assert asset_probe._uniform_bands(frame)
    assert asset_probe._text_bands(frame) == []


def test_score_candidate_rejects_burned_in_text():
    measured = _measured(text={"text_suspected": True, "reason": "persistent band"})
    score = asset_scoring.score_candidate(_entry(), intent=INTENT, lane="literal",
                                          measured=measured, prof=PROF,
                                          grade={"natural_fit": True})
    assert score["dimensions"]["cleanliness_provenance"]["points"] == 0


def test_pair_score_reads_the_measured_audio_and_video():
    video = {"measured": {"motion": 0.03, "band_busy": 0.04}, "title": "bus window",
             "query_lane": "literal", "fresh": True}
    audio = {"features": {"onset_density_per_s": 0.8, "tempo_bpm": 80.0}, "fresh": True,
             "title": "quiet piano"}
    audit = {"semantic_scene_fit": {"points": 30}}
    pair = asset_scoring.score_pair(INTENT, video=video, audio=audio,
                                    format_id="caption_list_title_v1", audit=audit)
    assert pair["total"] >= 70 and pair["passes"]
    assert "makes" in pair["rationale"]


def test_audio_score_prefers_the_intent_matching_track():
    row = {"video_count": 50000}
    close = asset_scoring.score_audio(INTENT, row,
                                      {"tempo_bpm": 80.0, "onset_density_per_s": 0.9,
                                       "spectral_centroid_hz": 900.0, "rms_db": -18.0,
                                       "duration_s": 30.0}, PROF)
    loud_fast = asset_scoring.score_audio(INTENT, row,
                                          {"tempo_bpm": 170.0, "onset_density_per_s": 5.0,
                                           "spectral_centroid_hz": 4200.0, "rms_db": -8.0,
                                           "duration_s": 30.0}, PROF)
    assert close["total"] > loud_fast["total"]


# ------------------------------------------------------------------ burned-in text
def test_text_suspicion_flags_a_persistent_caption_band():
    """Letter-shaped strokes in a band (not a solid bar) must read as burned-in text."""
    frame = np.full((360, 360), 80, dtype=np.uint8)
    band = np.full((26, 360), 80, dtype=np.uint8)
    band[:, 30:330:6] = 245               # vertical strokes: looks like glyphs
    frame[150:176] = band
    bands = asset_probe._text_bands(frame)
    assert bands, "glyph-like strokes must produce a text band"


def test_text_suspicion_ignores_a_clean_gradient():
    grad = np.tile(np.linspace(20, 200, 360, dtype=np.uint8), (360, 1))
    assert asset_probe._text_bands(grad) == []


def test_uniform_overlay_bar_is_flagged():
    frame = np.full((360, 360), 120, dtype=np.uint8)
    frame[:40, :] = 5                      # letterbox/banner bar
    assert asset_probe._uniform_bands(frame)


# ------------------------------------------------------------------ ledger
def test_ledger_reserve_release_and_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "ledger.json"))
    asset_ledger.reserve("run1", key="pin:42", kind="visual", pin_id="42")
    data = asset_ledger.load()
    assert data["reservations"]["run1"]["visual"] == "pin:42"
    assert data["visual"]["pin:42"]["last_used_at"] is None
    asset_ledger.release("run1", kind="visual")
    assert asset_ledger.load()["visual"]["pin:42"]["status"] == "released"
    assert asset_ledger.load()["visual"]["pin:42"].get("last_used_at") is None

    asset_ledger.reserve("run2", key="pin:42", kind="visual")
    asset_ledger.commit("run2", key="pin:42", kind="visual", sha256="abc")
    data = asset_ledger.load()
    assert data["visual"]["pin:42"]["last_used_at"]
    assert "run2" not in data["reservations"]
    assert asset_ledger.is_fresh("pin:42", data=data) is False


def test_ledger_no_repeat_window_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "ledger.json"))
    old = {"version": 1, "visual": {"pin:9": {"last_used_at": "2020-01-01T00:00:00Z"}},
           "audio": {}, "reservations": {}}
    asset_ledger.save(old)
    assert asset_ledger.is_fresh("pin:9", days=30) is True


def test_ledger_survives_a_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    path.write_text("{not json")
    monkeypatch.setenv("BEAST_LEDGER", str(path))
    data = asset_ledger.load()
    assert data["recovered_from_corrupt"] is True
    assert asset_ledger.is_fresh("pin:anything", data=data) is True


def test_ledger_syncs_from_the_delivered_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(asset_ledger, "load", asset_ledger.load)
    from src.formats import run_manifest

    class E(dict):
        pass
    entry = E({"status": sorted(run_manifest.CONSUMED)[0], "run_id": "r9",
               "delivery": {"at": "2026-10-01T00:00:00Z"},
               "background": {"stable_id": "777", "sha256": "deadbeef"},
               "audio": {"stable_id": "song-1", "sha256": "cafe"}})
    monkeypatch.setattr(run_manifest, "load", lambda: [entry])
    data = asset_ledger.sync_from_manifest()
    assert data["visual"]["pin:777"]["last_used_at"] == "2026-10-01T00:00:00Z"
    assert data["audio"]["song-1"]["used_by"] == "r9"


# ------------------------------------------------------------------ broker behaviour
def test_broker_never_lowers_the_minimum_to_finish(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "l.json"))
    monkeypatch.setattr(asset_broker.pinterest, "search_videos",
                        lambda q, page_size=25: [_entry()])
    monkeypatch.setattr(asset_broker.pinterest, "media_url", lambda e: "https://x/v.mp4")
    monkeypatch.setattr(asset_broker.pinterest, "download",
                        lambda url, dest, **k: (Path(dest).write_bytes(b"x" * 4096), True)[1])
    # A dark, busy, clipped, cut-heavy candidate cannot reach 75.
    poor = _measured(metrics={"yavg": 30.0, "sat": 40.0}, band_busy=0.4,
                     motion={"ok": True, "motion": 0.5, "max_jump": 0.4, "cuts": 3})
    monkeypatch.setattr(asset_broker.asset_probe, "analyze", lambda p, fmt: dict(poor))
    rec = asset_broker.acquire(INTENT, "caption_list_title_v1", tmp_path, run_id="low",
                               max_downloads=1, verbose=False)
    assert rec["passes_minimum"] is False
    assert rec["review_status"] == "needs_human_review"
    assert rec["score"]["total"] < 75


def test_broker_skips_a_pin_used_inside_the_no_repeat_window(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "l.json"))
    asset_ledger.save({"version": 1, "visual": {
        "pin:1": {"pin_id": "1", "last_used_at": "2026-10-29T00:00:00Z"}},
        "audio": {}, "reservations": {}})
    monkeypatch.setattr(asset_broker.pinterest, "search_videos",
                        lambda q, page_size=25: [_entry(id="1"), _entry(id="2")])
    monkeypatch.setattr(asset_broker.pinterest, "media_url", lambda e: "https://x/v.mp4")
    monkeypatch.setattr(asset_broker.pinterest, "download",
                        lambda url, dest, **k: (Path(dest).write_bytes(b"x" * 4096), True)[1])
    monkeypatch.setattr(asset_broker.asset_probe, "analyze",
                        lambda p, fmt: _measured())
    monkeypatch.setattr(asset_broker.render_gate, "probe",
                        lambda p: {"streams": [{"codec_type": "video", "width": 1080,
                                                "height": 1920}],
                                   "format": {"duration": "20.0"}})
    monkeypatch.setattr(asset_broker.template_renderer, "measure_luma_sat",
                        lambda p: {"yavg": 105.0, "sat": 14.2})
    rec = asset_broker.acquire(INTENT, "caption_list_title_v1", tmp_path, run_id="fresh",
                               verbose=False)
    assert rec["pin_id"] == "2"
    assert any("no-repeat" in r["why"] for r in rec["rejected"])


def test_broker_reports_the_lane_that_found_the_clip(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "l.json"))
    seen = []

    def search(q, page_size=25):
        seen.append(q)
        return [_entry(id="only-literal")] if "night bus" in q else []
    monkeypatch.setattr(asset_broker.pinterest, "search_videos", search)
    monkeypatch.setattr(asset_broker.pinterest, "media_url", lambda e: "https://x/v.mp4")
    monkeypatch.setattr(asset_broker.pinterest, "download",
                        lambda url, dest, **k: (Path(dest).write_bytes(b"x" * 4096), True)[1])
    monkeypatch.setattr(asset_broker.asset_probe, "analyze", lambda p, fmt: _measured())
    monkeypatch.setattr(asset_broker.render_gate, "probe",
                        lambda p: {"streams": [{"codec_type": "video", "width": 1080,
                                                "height": 1920}],
                                   "format": {"duration": "20.0"}})
    monkeypatch.setattr(asset_broker.template_renderer, "measure_luma_sat",
                        lambda p: {"yavg": 105.0, "sat": 14.2})
    rec = asset_broker.acquire(INTENT, "caption_list_title_v1", tmp_path, run_id="lanes",
                               verbose=False)
    assert rec["query_lane"] == "literal"
    assert rec["query"] in seen


def test_audio_expired_or_undecodable_link_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "l.json"))
    monkeypatch.setattr(asset_broker.music, "trending_rows",
                        lambda api, kw, limit=5: [{"id": "a", "play_link": "https://x/a",
                                                   "duration_seconds": 60}])
    monkeypatch.setattr(asset_broker, "_download_to_m4a",
                        lambda url, dest, timeout=300: False)
    with pytest.raises(asset_broker.AssetError) as exc:
        asset_broker.trending_audio("caption_list_title_v1", tmp_path,
                                    intent=INTENT, keyword="k")
    assert "first unused" in str(exc.value)


def test_run_preview_releases_reservations_on_a_failed_run(monkeypatch, tmp_path):
    """A run that dies after the assets were reserved must not burn them."""
    from src import run_preview
    ledger = tmp_path / "ledger.json"
    monkeypatch.setenv("BEAST_LEDGER", str(ledger))
    monkeypatch.setattr(run_preview.config, "load_config",
                        lambda: {"short_publish_enabled": False})
    monkeypatch.setattr(run_preview.config, "env",
                        lambda k, d=None: str(tmp_path))
    monkeypatch.setattr(run_preview.content_factory, "generate", lambda *a, **k: {
        "payload": {"topic_id": "t", "titleLines": ["a"], "caption": "",
                    "background_query": "night bus window"},
        "attempts": 1, "critic": {}, "ladder": [{"level": 3}],
        "audience_moment": {}, "card_compiled_by": "test", "card_degraded": False})
    monkeypatch.setattr(run_preview.asset_intent, "compile_intent",
                        lambda *a, **k: {"video_queries": ["night bus window"],
                                         "compiled_by": "test", "degraded": False})

    def fake_acquire(intent, fmt, scratch, run_id="adhoc", **k):
        asset_ledger.reserve(run_id, key="pin:5", kind="visual", pin_id="5")
        return {"reserve_key": "pin:5", "pin_id": "5", "path": "p",
                "width": 1080, "height": 1920, "score": {"total": 80},
                "motion": {}, "band_busy": 0.1, "candidates": [], "rejected": []}
    monkeypatch.setattr(run_preview.asset_broker, "acquire", fake_acquire)

    def boom(*a, **k):
        raise asset_broker.AssetError("trending unavailable")
    monkeypatch.setattr(run_preview.asset_broker, "trending_audio", boom)
    rc = run_preview.main(["--format", "A", "--no-deliver"])
    assert rc == 1
    data = json.loads(ledger.read_text())
    assert data["visual"]["pin:5"]["status"] == "released"
    assert data["visual"]["pin:5"].get("last_used_at") is None

def test_delivered_but_needs_review_run_exits_nonzero(monkeypatch, tmp_path):
    """A run whose footage missed the minimum must not report ok:true."""
    from src import run_preview, alerts
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(run_preview.config, "load_config",
                        lambda: {"short_publish_enabled": False})
    monkeypatch.setattr(run_preview.config, "env", lambda k, d=None: str(tmp_path))
    # The runner persists into ROOT/outputs and records in the real manifest; both
    # must point at the test's tmp dir or a test run contaminates production state.
    monkeypatch.setattr(run_preview, "ROOT", tmp_path)
    # run_manifest.MANIFEST is a module-level constant; patch it too or the test
    # still appends a fake entry to the real manifest.
    monkeypatch.setattr(run_preview.run_manifest, "MANIFEST",
                        tmp_path / "template_manifest.json")
    monkeypatch.setattr(run_preview.content_factory, "generate", lambda *a, **k: {
        "payload": {"topic_id": "t", "titleLines": ["a"], "caption": "",
                    "background_query": "night bus window"},
        "attempts": 1, "critic": {}, "ladder": [{"level": 3}],
        "audience_moment": {}, "card_compiled_by": "test", "card_degraded": False})
    monkeypatch.setattr(run_preview.asset_intent, "compile_intent",
                        lambda *a, **k: {"video_queries": ["night bus window"],
                                         "compiled_by": "test", "degraded": False})

    def fake_acquire(intent, fmt, scratch, run_id="adhoc", **k):
        asset_ledger.reserve(run_id, key="pin:7", kind="visual", pin_id="7")
        return {"reserve_key": "pin:7", "pin_id": "7", "path": "p",
                "width": 720, "height": 1280, "score": {"total": 61},
                "passes_minimum": False, "review_status": "needs_human_review",
                "motion": {}, "band_busy": 0.1, "candidates": [], "rejected": []}
    monkeypatch.setattr(run_preview.asset_broker, "acquire", fake_acquire)
    monkeypatch.setattr(run_preview.asset_broker, "trending_audio",
                        lambda *a, **k: {"path": "a", "stable_id": "s", "features": {},
                                         "score": {"total": 80}, "source": "test",
                                         "usage_clearance": "unknown", "distance": 0.3})
    def fake_render(fmt, payload, out, **k):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"x" * 2048)      # the runner copies a real artifact back
        return {"render": {"seconds": 1}, "attempts": 1, "gate_ok": True, "gate": {},
                "template_sha256": "x", "measured": {"yavg": 100.0}, "drift": {},
                "text_frame0": {"ok": True}}
    monkeypatch.setattr(run_preview.template_renderer, "render_preview", fake_render)
    monkeypatch.setattr(run_preview.template_renderer.TemplateRenderer, "qa",
                        lambda self, rec, payload: {"band_ok": True})
    monkeypatch.setattr(run_preview.render_gate, "probe",
                        lambda p: {"streams": [{"codec_type": "video"},
                                               {"codec_type": "audio"}]})
    from src import alerts as _alerts
    monkeypatch.setattr(_alerts, "telegram_video", lambda *a, **k: True)
    rc = run_preview.main(["--format", "A"])
    assert rc == 1, "needs_review must not exit 0"


def test_zero_to_one_hundred_scales_are_coerced_not_discarded():
    """Measured: a 0-100 arousal killed the whole brief and fell back to two lanes."""
    raw = {"video_queries": ["night bus window city lights", "bus window at night",
                             "city lights through glass"], **{k: v for k, v in INTENT.items()
                                                              if k != "video_queries"}}
    raw["arousal"] = 40
    raw["audio_intent"] = {**INTENT["audio_intent"], "energy": 70, "arousal": 35}
    from src.formats import content_factory
    out = asset_intent.compile_intent({"background_query": "night bus window",
                                       "topic_id": "t"}, "social_moment_loop_v1",
                                      chat=lambda p: (json.dumps(raw), "test"),
                                      verbose=False)
    assert out["degraded"] is False
    assert abs(float(out["arousal"]) - 0.4) < 1e-6
    assert abs(float(out["audio_intent"]["energy"]) - 0.7) < 1e-6
    assert out.get("normalised_fields")


def test_candidates_are_ranked_by_scene_overlap_before_download():
    entries = [
        {"id": "a", "title": "aesthetic wallpaper", "_lane": "literal"},
        {"id": "b", "title": "night bus window city lights", "_lane": "literal"},
        {"id": "c", "title": "bus window", "_lane": "emotional"},
    ]
    ranked = asset_broker._pre_rank(entries, INTENT)
    assert [e["id"] for e in ranked][0] == "b"
