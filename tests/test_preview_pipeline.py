"""Writer/router/broker contracts for the preview pipeline.

All model and network calls are mocked here: these tests pin the RULES (exact
list counts, fail-closed parsing, measured audio selection, no publishing),
not the services.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.formats import asset_broker, content_factory, run_manifest, template_renderer

GOOD_A = {
    "topic_id": "post-chat-replay",
    "headline": "4 things you do after a conversation",
    "titleLines": ["4 things you do", "after a", "conversation", "at night"],
    "list_items": ["replay the sentence", "decide they hate you",
                   "rehearse a reply", "check their story"],
    "caption": "4 things you do:\n1. replay the sentence\n2. decide they hate you\n"
               "3. rehearse a reply\n4. check their story",
    "closing": "none of it happened.",
    "background_query": "person walking forest trail morning light",
}
GOOD_B = {
    "topic_id": "hey-two-hours",
    "hookLines": ["you sent hey.", "two hours ago."],
    "payoffLines": ["they're online.", "you're not."],
    "loop_hypothesis": "the last line re-reads as the first",
    "caption": "the two hours after hey",
    "background_query": "night window rain bokeh",
}
PASS_VERDICT = {"specificity": 5, "sendability": 5, "tone_safety": 5, "originality": 4,
                "loop_ok": True, "claims_ok": True,
                "sendability_who": "the friend who over-explains"}


def _mock_writer(monkeypatch, payload):
    monkeypatch.setattr(content_factory.llm, "chat",
                        lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(content_factory.critic_model, "chat",
                        lambda *a, **k: json.dumps(PASS_VERDICT))


def test_a_generates_and_validates(monkeypatch):
    _mock_writer(monkeypatch, GOOD_A)
    out = content_factory.generate("caption_list_title_v1", None, verbose=False)
    assert out["attempts"] == 1
    p = out["payload"]
    assert len(p["list_items"]) == 4
    assert p["audio"]["rights"] == "preview_only"
    assert p["provenance"]["usage_clearance"] == "unknown"


def test_b_generates_and_validates(monkeypatch):
    _mock_writer(monkeypatch, GOOD_B)
    out = content_factory.generate("social_moment_loop_v1", None, verbose=False)
    assert out["payload"]["hookLines"] and out["payload"]["payoffLines"]


def test_list_count_mismatch_is_rejected_then_fails(monkeypatch):
    bad = dict(GOOD_A, list_items=GOOD_A["list_items"][:3])
    monkeypatch.setattr(content_factory.llm, "chat", lambda *a, **k: json.dumps(bad))
    monkeypatch.setattr(content_factory.critic_model, "chat",
                        lambda *a, **k: json.dumps(PASS_VERDICT))
    with pytest.raises(RuntimeError) as exc:
        content_factory.generate("caption_list_title_v1", None, verbose=False)
    assert "schema" in str(exc.value) or "failed after" in str(exc.value)


def test_unparseable_writer_fails_closed(monkeypatch):
    monkeypatch.setattr(content_factory.llm, "chat", lambda *a, **k: "not json at all")
    with pytest.raises(RuntimeError) as exc:
        content_factory.generate("social_moment_loop_v1", None, verbose=False)
    assert "failed after" in str(exc.value)


def test_unparseable_critic_fails_closed_not_blind_rewrite(monkeypatch):
    monkeypatch.setattr(content_factory.llm, "chat", lambda *a, **k: json.dumps(GOOD_B))
    monkeypatch.setattr(content_factory.critic_model, "chat", lambda *a, **k: "{} oops")
    with pytest.raises(RuntimeError) as exc:
        content_factory.generate("social_moment_loop_v1", None, verbose=False)
    assert "critic_unparseable" in str(exc.value)


def test_router_only_knows_the_two_pilot_formats():
    from src.formats import template_payloads
    for _ in range(5):
        assert content_factory.pick_format({}) in template_payloads.FORMATS


def test_run_preview_cannot_import_publish():
    src = Path("src/run_preview.py").read_text()
    assert "import publish" not in src and "from .publish" not in src
    assert "media_publish" not in src


def test_run_preview_refuses_when_publishing_is_on(monkeypatch):
    from src import run_preview
    monkeypatch.setattr(run_preview.config, "load_config",
                        lambda: {"short_publish_enabled": True})
    assert run_preview.main(["--format", "A", "--no-deliver"]) == 2


# ------------------------------------------------------------------ asset broker
def test_broker_returns_first_usable_clip(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_broker.pinterest, "search_videos",
                        lambda q, page_size=25: [{"id": "1", "pin_url": "u",
                                                  "creator": "c", "title": "t"}])
    monkeypatch.setattr(asset_broker.pinterest, "media_url", lambda e: "https://x/v.mp4")
    monkeypatch.setattr(asset_broker.pinterest, "download",
                        lambda url, dest: (Path(dest).write_bytes(b"x" * 4096), True)[1])
    monkeypatch.setattr(asset_broker.render_gate, "probe",
                        lambda p: {"streams": [{"codec_type": "video", "width": 1080,
                                                "height": 1920}],
                                   "format": {"duration": "12.0"}})
    monkeypatch.setattr(template_renderer, "measure_luma_sat",
                        lambda p: {"yavg": 105.0, "sat": 14.2})
    rec = asset_broker.pinterest_background("forest trail", "caption_list_title_v1",
                                            tmp_path)
    assert rec["pin_id"] == "1" and rec["usage_clearance"] == "unknown"
    assert rec["width"] == 1080 and rec["stretch_pick"] is False


def test_broker_fails_when_nothing_is_usable(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_broker.pinterest, "search_videos", lambda q, page_size=25: [])
    with pytest.raises(asset_broker.AssetError):
        asset_broker.pinterest_background("x", "social_moment_loop_v1", tmp_path)


def test_audio_picks_by_measured_distance(monkeypatch, tmp_path):
    rows = [{"id": "a", "play_link": "https://x/a", "duration_seconds": 60},
            {"id": "b", "play_link": "https://x/b", "duration_seconds": 60}]
    monkeypatch.setattr(asset_broker.music, "trending_rows",
                        lambda api, kw, limit=5: rows)
    monkeypatch.setattr(asset_broker, "_download_to_m4a",
                        lambda url, dest, timeout=300: (Path(dest).write_bytes(b"x" * 20480), True)[1])
    base = {"spectral_centroid_hz": 900.0, "rms_db": -16.0, "onset_density_per_s": 2.0}
    # The A reference is 139.7 BPM, so the 140 BPM track must win over the 100 one.
    monkeypatch.setattr(asset_broker.audio_features, "analyze",
                        lambda p: {**base, "tempo_bpm": 100.0 if "/track_a" in str(p) else 140.0})
    rec = asset_broker.trending_audio("caption_list_title_v1", tmp_path, keyword="k")
    assert rec["stable_id"] == "b" and rec["usage_clearance"] == "unknown"
    assert rec["rights"] == "preview_only"


def test_audio_refuses_unmeasurable_tracks(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_broker.music, "trending_rows",
                        lambda api, kw, limit=5: [{"id": "a", "play_link": "https://x/a",
                                                   "duration_seconds": 60}])
    monkeypatch.setattr(asset_broker, "_download_to_m4a",
                        lambda url, dest, timeout=300: (Path(dest).write_bytes(b"x" * 20480), True)[1])

    def boom(p):
        raise asset_broker.audio_features.AudioFeatureError("unmeasurable")
    monkeypatch.setattr(asset_broker.audio_features, "analyze", boom)
    with pytest.raises(asset_broker.AssetError) as exc:
        asset_broker.trending_audio("social_moment_loop_v1", tmp_path, keyword="k")
    assert "first unused" in str(exc.value)

# ---------------------------------------------------------------- content brain
def _bad_a(items, headline="8 things you do after a conversation"):
    return {"topic_id": "t", "headline": headline,
            "titleLines": ["8 things you do", "after a", "conversation", "when"],
            "list_items": items,
            "caption": headline + ":\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items)),
            "closing": "read the caption", "background_query": "person walking forest trail"}


def test_a_rejects_items_that_are_one_behaviour_reworded():
    items = ["replay the conversation in your head", "replay the conversation in your head at night",
             "check their story", "draft an apology", "ask a friend",
             "reread the chat", "plan a better reply", "go quiet"]
    errs = content_factory.creative_errors(_bad_a(items), "caption_list_title_v1")
    assert any("same behaviour reworded" in e for e in errs), errs


def test_a_rejects_clinical_labels():
    items = ["replay the last sentence", "check their story", "draft an apology",
             "ask a friend", "reread the chat", "plan a better reply",
             "go quiet", "leave them on read"]
    errs = content_factory.creative_errors(
        _bad_a(items, headline="8 signs of high-functioning anxiety"), "caption_list_title_v1")
    assert any("clinical" in e for e in errs), errs


def test_b_rejects_more_than_one_thought():
    payload = {"topic_id": "t",
               "hookLines": ["i rehearse the message " * 3, "and then never send it " * 3],
               "payoffLines": ["they think i forgot " * 3, "and i think about it at 2am " * 3],
               "loop_hypothesis": "the last line re-reads as the first",
               "caption": "", "background_query": "phone on a table"}
    errs = content_factory.creative_errors(payload, "social_moment_loop_v1")
    assert any("one thought" in e for e in errs), errs


def test_prompts_carry_the_mechanics():
    a = content_factory._prompt("caption_list_title_v1", None)
    b = content_factory._prompt("social_moment_loop_v1", None)
    assert "WRITE THE HUMAN MOMENT BEFORE THE HOOK" in a
    assert "no near-synonyms" in a and "title is a contract" in a
    assert "WRITE THE MOMENT, NOT THE TOPIC" in b
    assert "ONE thought per reel" in b and "concrete object" in b


def test_generate_retries_a_creative_rejection_then_fails(monkeypatch):
    dupes = ["replay the conversation in your head", "replay the conversation in your head again",
             "check their story", "draft an apology", "ask a friend",
             "reread the chat", "plan a better reply", "go quiet"]
    _mock_writer(monkeypatch, _bad_a(dupes))
    with pytest.raises(RuntimeError) as exc:
        content_factory.generate("caption_list_title_v1", "after a conversation")
    assert "creative" in str(exc.value), exc.value
