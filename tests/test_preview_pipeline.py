"""Writer/router/broker contracts for the preview pipeline.

All model and network calls are mocked here: these tests pin the RULES (exact
list counts, fail-closed parsing, measured audio selection, no publishing),
not the services.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.formats import (asset_broker, asset_intent, asset_ledger, content_factory,
                         run_manifest, template_renderer)

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
    "hookLines": ["you typed a reply", "and deleted it"],
    "payoffLines": ["sent haha instead", "then checked the chat twice"],
    "loop_hypothesis": "the last line re-reads as the first",
    "caption": "the two hours after hey",
    "background_query": "night window rain bokeh",
}
PASS_VERDICT = {
    "scene_specificity": 5, "recognition_authenticity": 5, "natural_voice": 5,
    "emotional_truth_dignity": 5, "format_fit": 5, "distinctiveness": 4,
    "shareability": 4, "caption_value": 4, "product_trust": 5,
    "hard_fail": None, "evidence": "the draft names the exact moment",
    "sendability_who": "the friend who over-explains",
    "rewrite_notes": "",
}
CARD = {
    "audience_moment": {"situation": "the bus home after a party",
                        "observable_behaviour": "re-reading the goodbye",
                        "private_worry": "that leaving early was noticed",
                        "emotional_tension": "relief and guilt at once",
                        "audience_cluster": "parties and social events"},
    "concepts": [{"concept_id": "a", "situation": "the bus home after a party",
                  "behaviour": "replaying the goodbye", "private_meaning": "they noticed",
                  "turn": "nobody did", "format": "caption_list_title_v1"},
                 {"concept_id": "b", "situation": "the group chat at midnight",
                  "behaviour": "drafting a reply", "private_meaning": "sounding needy",
                  "turn": "sending haha", "format": "social_moment_loop_v1"},
                 {"concept_id": "c", "situation": "the kitchen at a party",
                  "behaviour": "hovering near the snacks", "private_meaning": "not belonging",
                  "turn": "nobody noticed", "format": "social_moment_loop_v1"}],
    "chosen": 0, "why": "strongest recognition", "topic_id": "post-chat-replay",
    "compiled_by": "llm:test", "degraded": False,
}


def _mock_writer(monkeypatch, payload, card=None):
    """The writer is also the card compiler here: answer by prompt content."""
    def chat(*a, **k):
        content = a[0][0]["content"] if a and a[0] else k.get("content", "")
        if isinstance(content, str) and "audience moment card" in content:
            return json.dumps(card or CARD)
        return json.dumps(payload)
    monkeypatch.setattr(content_factory.llm, "chat", chat)
    monkeypatch.setattr(content_factory.critic_model, "chat",
                        lambda *a, **k: json.dumps(PASS_VERDICT))


@pytest.fixture(autouse=True)
def _no_recent(monkeypatch):
    """Recent-content dedupe reads the real manifest; keep tests hermetic."""
    monkeypatch.setattr(content_factory, "recent_context",
                        lambda limit=8: {"topics": [], "lines": []})


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
GOOD_MEASURED = {
    "width": 1080, "height": 1920, "duration_s": 20.0, "has_audio": False,
    "metrics": {"yavg": 105.0, "sat": 14.2},
    "motion": {"ok": True, "motion": 0.03, "max_jump": 0.05, "cuts": 0, "samples": 8},
    "text": {"text_suspected": False, "reason": "no persistent text band",
             "bands": [], "uniform": []},
    "band_busy": 0.04,
}


def _mock_pinterest(monkeypatch, entries):
    monkeypatch.setattr(asset_broker.pinterest, "search_videos",
                        lambda q, page_size=25: entries)
    monkeypatch.setattr(asset_broker.pinterest, "media_url", lambda e: "https://x/v.mp4")
    monkeypatch.setattr(asset_broker.pinterest, "download",
                        lambda url, dest, **k: (Path(dest).write_bytes(b"x" * 4096), True)[1])
    monkeypatch.setattr(asset_broker.render_gate, "probe",
                        lambda p: {"streams": [{"codec_type": "video", "width": 1080,
                                                "height": 1920}],
                                   "format": {"duration": "20.0"}})
    monkeypatch.setattr(template_renderer, "measure_luma_sat",
                        lambda p: {"yavg": 105.0, "sat": 14.2})
    monkeypatch.setattr(asset_broker.asset_probe, "analyze",
                        lambda p, fmt: dict(GOOD_MEASURED))
    monkeypatch.setattr(asset_broker.asset_probe, "ocr_check",
                        lambda p, **k: {"has_text": False, "has_logo": False,
                                        "by": "test"})


def test_broker_selects_a_scored_clip_and_reserves_it(monkeypatch, tmp_path):
    _mock_pinterest(monkeypatch, [{"id": "1", "pin_url": "u", "creator": "c",
                                   "title": "night bus window city lights",
                                   "domain": "Uploaded by user"}])
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "ledger.json"))
    rec = asset_broker.acquire(
        asset_intent.fallback({"background_query": "night bus window city lights",
                               "topic_id": "bus"}, "caption_list_title_v1"),
        "caption_list_title_v1", tmp_path, run_id="t1")
    assert rec["pin_id"] == "1" and rec["usage_clearance"] == "unknown"
    assert rec["width"] == 1080 and rec["stretch_pick"] is False
    assert rec["score"]["total"] >= 75 and rec["passes_minimum"] is True
    assert rec["query_lane"] == "literal"
    assert asset_ledger.load()["reservations"]["t1"]["visual"] == "pin:1"


def test_broker_rejects_ads_before_downloading(monkeypatch, tmp_path):
    _mock_pinterest(monkeypatch, [
        {"id": "ad", "title": "FOCUS | shop now", "is_promoted": True},
        {"id": "brand", "title": "night city poster", "domain": "shopify.com"},
        {"id": "ig", "title": "night bus window city lights", "domain": "instagram.com"},
        {"id": "ok", "title": "night bus window city lights", "domain": "Uploaded by user"}])
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "ledger.json"))
    rec = asset_broker.acquire(
        asset_intent.fallback({"background_query": "night bus window city lights",
                               "topic_id": "bus"}, "caption_list_title_v1"),
        "caption_list_title_v1", tmp_path, run_id="t2")
    assert rec["pin_id"] in ("ok", "ig")          # social domains are usable footage
    why = {r["id"]: r["why"] for r in rec["rejected"]}
    assert "ad" in why and "promoted" in why["ad"]
    assert "brand" in why and "commerce" in why["brand"]


def test_broker_fails_when_nothing_is_usable(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_broker.pinterest, "search_videos", lambda q, page_size=25: [])
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "ledger.json"))
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
