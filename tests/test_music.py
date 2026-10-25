"""The trending client must speak the service's real contract.

These tests exist because the client silently lost the trending signal for its
whole life: it called a path that 404s, read a `results` key that does not
exist, and filtered on `confidence`/`trend_score` fields the API never returns.
Every call failed, and every failure looked like "no trending data today".
"""
from __future__ import annotations

import json

from src import music


class _Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body

    def json(self):
        return self._body

    @property
    def headers(self):
        return {"content-type": "application/json"}


def test_polls_202_then_reads_ready_data(monkeypatch):
    """A cold keyword answers 202 + poll_with; the result lives under `data`."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "fresh=false" in url:
            return _Resp(200, {"status": "ready", "data": [{"title": "SLAY!", "artist": "E",
                                                           "video_count": 3800000,
                                                           "is_niche": True}]})
        return _Resp(202, {"status": "processing", "retry_after_seconds": 0,
                           "poll_with": "https://x.example/v1/trending?keyword=gym&fresh=false"})

    monkeypatch.setattr(music.requests, "get", fake_get)
    monkeypatch.setattr(music.time, "sleep", lambda *_: None)

    rows = music.trending_rows("https://x.example", "gym", 5)
    assert len(rows) == 1 and rows[0]["title"] == "SLAY!"
    assert len(calls) == 2 and "fresh=false" in calls[1], "must follow poll_with"


def test_never_polls_with_fresh_true(monkeypatch):
    """Polling with fresh=true restarts the scrape forever — the one real trap."""
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append((url, params))
        return _Resp(202, {"status": "processing", "retry_after_seconds": 0,
                           "poll_with": "https://x.example/v1/trending?fresh=false"})

    monkeypatch.setattr(music.requests, "get", fake_get)
    monkeypatch.setattr(music.time, "sleep", lambda *_: None)

    music.trending_rows("https://x.example", "gym", 5)
    polls = [u for (u, _p) in seen if "fresh=false" in u]
    assert polls, "polling stopped early"


def test_error_status_yields_no_rows(monkeypatch):
    monkeypatch.setattr(music.requests, "get",
                        lambda url, params=None, timeout=None: _Resp(
                            200, {"status": "error", "error": "scrape failed"}))
    assert music.trending_rows("https://x.example", "gym", 5) == []


def test_limit_is_clamped_to_the_api_max(monkeypatch):
    """limit=999 is a 422; the API documents a 1-50 range."""
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(params or {})
        return _Resp(200, {"status": "ready", "data": []})

    monkeypatch.setattr(music.requests, "get", fake_get)
    music.trending_rows("https://x.example", "gym", 999)
    assert seen["limit"] == 50


def test_fetch_trending_prefers_commercially_usable_rows(monkeypatch):
    """A brand account must not be steered onto unlicensed audio."""
    rows = [
        {"title": "viral but locked", "video_count": 9000000, "is_niche": True,
         "is_copyrighted": True, "is_commercial_use_allowed": False},
        {"title": "cleared bed", "video_count": 250000, "is_niche": True,
         "is_copyrighted": False, "is_commercial_use_allowed": True},
    ]
    monkeypatch.setattr(music, "trending_rows", lambda *a, **k: rows)
    monkeypatch.setattr(music, "wake", lambda *a, **k: None)

    ref = music.fetch_trending({"music": {"trending_api": "https://x.example",
                                          "trending_niche": "self-improvement",
                                          "phase2_score_band": [0.0, 1.0]}})
    assert ref["title"] == "cleared bed"
    assert ref["commercial_ok"] is True
    assert json.dumps(ref)  # must stay serialisable into memory
