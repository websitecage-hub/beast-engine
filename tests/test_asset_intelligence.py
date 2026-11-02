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
    intent = dict(INTENT, video_queries=["night bus window city lights",
                                         "NIGHT BUS WINDOW CITY LIGHTS",
                                         "bus window at night", "quiet bus ride home",
                                         "city lights through glass at night"])
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
def test_taste_score_passes_a_clean_tagged_clip():
    score = asset_scoring.taste_score(
        _entry(title="quiet night window aesthetic lifestyle"),
        intent=INTENT, lane="literal", measured=_measured(),
        ocr={"has_text": False, "has_logo": False, "by": "gemini"})
    assert score["total"] >= 60 and score["passes"]
    assert score["dimensions"]["no_on_screen_text"]["points"] == 30


def test_taste_score_forces_review_when_ocr_is_unknown():
    score = asset_scoring.taste_score(_entry(), intent=INTENT, lane="literal",
                                      measured=_measured(),
                                      ocr={"has_text": None, "by": "heuristic"})
    assert score["review_status"] == "needs_human_review"
    assert score["passes"] is False


def test_brightness_is_not_a_gate_any_more():
    """The owner's rule: pick by look and tags, not by a luminance target."""
    dark = _measured(metrics={"yavg": 12.0, "sat": 2.0})
    score = asset_scoring.taste_score(
        _entry(title="alone night window aesthetic quiet"),
        intent=INTENT, lane="literal", measured=dark,
        ocr={"has_text": False, "has_logo": False, "by": "gemini"})
    assert score["passes"] is True
    assert "luma" not in json.dumps(score).lower()


def test_taste_score_zeroes_a_clip_with_on_screen_text():
    score = asset_scoring.taste_score(
        _entry(), intent=INTENT, lane="literal", measured=_measured(),
        ocr={"has_text": True, "text_said": "subscribe", "by": "gemini"})
    assert score["dimensions"]["no_on_screen_text"]["points"] == 0
    assert score["passes"] is False


def test_pair_score_is_light_and_never_blocks_on_luma():
    video = {"measured": {"band_busy": 0.04}, "title": "night bus", "query_lane": "literal"}
    audio = {"score": {"total": 70}, "title": "trending bed"}
    pair = asset_scoring.score_pair(INTENT, video=video, audio=audio,
                                    format_id="social_moment_loop_v1",
                                    audit={"look_fit": {"points": 34}})
    assert pair["total"] > 50 and "rationale" in pair


def test_audio_score_prefers_the_more_used_track():
    prof = {"duration_s": 7.0}
    hot = asset_scoring.score_audio(INTENT, {"video_count": 900000},
                                    {"duration_s": 30.0}, prof)
    cold = asset_scoring.score_audio(INTENT, {"video_count": 1200},
                                     {"duration_s": 30.0}, prof)
    assert hot["total"] > cold["total"]
    assert hot["dimensions"]["trend"]["points"] > cold["dimensions"]["trend"]["points"]


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
def test_broker_rejects_a_clip_that_carries_its_own_text(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "l.json"))
    monkeypatch.setattr(asset_broker.pinterest, "search_videos",
                        lambda q, page_size=25: [_entry(id="captioned")])
    monkeypatch.setattr(asset_broker.pinterest, "media_url", lambda e: "https://x/v.mp4")
    monkeypatch.setattr(asset_broker.pinterest, "download",
                        lambda url, dest, **k: (Path(dest).write_bytes(b"x" * 4096), True)[1])
    monkeypatch.setattr(asset_broker.asset_probe, "analyze",
                        lambda p, fmt: _measured())
    monkeypatch.setattr(asset_broker.asset_probe, "ocr_check",
                        lambda p, **k: {"has_text": True, "has_logo": False,
                                        "text_said": "follow for more", "by": "test"})
    with pytest.raises(asset_broker.AssetError) as exc:
        asset_broker.acquire(INTENT, "caption_list_title_v1", tmp_path,
                             run_id="ocr", verbose=False)
    assert "on-screen text" in str(exc.value)


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
    monkeypatch.setattr(asset_broker.asset_probe, "ocr_check",
                        lambda p, **k: {"has_text": False, "has_logo": False, "by": "test"})
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
    monkeypatch.setattr(asset_broker.asset_probe, "ocr_check",
                        lambda p, **k: {"has_text": False, "has_logo": False, "by": "test"})
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


def test_text_colour_follows_the_footage_brightness():
    from src.formats import text_style
    dark = text_style.choose(55)
    mid = text_style.choose(105)
    bright = text_style.choose(160)
    assert dark["fill"] == "#E0D29B" and dark["stroke_px"] == 2
    assert bright["fill"] == "#FFFFFF"
    assert mid["fill"] not in (dark["fill"], bright["fill"])
    assert text_style.for_background({"yavg": 55.7}, {"brightness": 1.32})["fill"] != "#FFFFFF"


def test_ocr_without_any_engine_is_unknown_not_clean(monkeypatch):
    """No OCR engine and no key must never report a clip as clean."""
    from src.formats import asset_probe
    monkeypatch.setattr(asset_probe, "_grab", lambda *a, **k: [])
    out = asset_probe.ocr_check("x.mp4")
    assert out["has_text"] is None


def test_percent_strings_in_an_intent_are_rescaled():
    raw = dict(INTENT)
    raw["arousal"] = "40%"
    raw["audio_intent"] = {**INTENT["audio_intent"], "energy": "70%"}
    out = asset_intent.compile_intent({"background_query": "night bus", "topic_id": "t"},
                                      "social_moment_loop_v1",
                                      chat=lambda p: (json.dumps(raw), "test"),
                                      verbose=False)
    assert out["degraded"] is False
    assert abs(float(out["arousal"]) - 0.4) < 1e-6
    assert abs(float(out["audio_intent"]["energy"]) - 0.7) < 1e-6


def test_object_fragments_are_not_searchable():
    for bad in ("plate held in", "panel two people"):
        ok, why = asset_intent.viable_query(bad)
        assert ok is False and "fragment" in why
    for good in ("night bus window city lights", "someone waiting in line",
                 "hands typing at desk indoors"):
        assert asset_intent.viable_query(good)[0] is True


def test_lanes_drop_fragments_and_keep_only_viable_queries():
    intent = dict(INTENT, video_queries=["plate held in", "panel two people",
                                         "night bus window city lights"])
    plan = asset_intent.lanes(intent)
    assert [p["query"] for p in plan] == ["night bus window city lights"]


def test_publish_content_uses_the_approved_caption_verbatim():
    from src import run_publish
    payload = {"caption": "8 things you do after leaving early",
               "closing": "the replay is the real event.",
               "titleLines": ["you leave at nine"], "topic_id": "t",
               "background_query": "night bus"}
    c = run_publish.content_for(payload)
    assert c["caption"].startswith("8 things you do after leaving early")
    assert "the replay is the real event." in c["caption"]
    assert c["hook"] == "you leave at nine"
    assert c["topic"] == "t"


def test_metaphor_query_leads_the_search(monkeypatch, tmp_path):
    """The director's metaphor is searched first; scene lanes stay as fallback."""
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "l.json"))
    seen = []

    def search(q, page_size=25):
        seen.append(q)
        return [_entry(id="m1", title="fluorescent light flicker")] if "fluorescent" in q else []

    monkeypatch.setattr(asset_broker.pinterest, "search_videos", search)
    monkeypatch.setattr(asset_broker.pinterest, "media_url", lambda e: "https://x/v.mp4")
    monkeypatch.setattr(asset_broker.pinterest, "download",
                        lambda url, dest, **k: (Path(dest).write_bytes(b"x" * 4096), True)[1])
    monkeypatch.setattr(asset_broker.asset_probe, "analyze", lambda p, fmt: _measured())
    monkeypatch.setattr(asset_broker.asset_probe, "ocr_check",
                        lambda p, **k: {"has_text": False, "has_logo": False, "by": "test"})
    monkeypatch.setattr(asset_broker.render_gate, "probe",
                        lambda p: {"streams": [{"codec_type": "video", "width": 1080,
                                                "height": 1920}],
                                   "format": {"duration": "20.0"}})
    monkeypatch.setattr(asset_broker.template_renderer, "measure_luma_sat",
                        lambda p: {"yavg": 105.0, "sat": 14.2})
    rec = asset_broker.acquire(INTENT, "caption_list_title_v1", tmp_path, run_id="mq",
                               metaphor_query="fluorescent light flicker", verbose=False)
    assert seen[0] == "fluorescent light flicker"
    assert rec["query_lane"] == "metaphor"
