"""The audio adapter may only speak routes the deployed service actually serves.

Fixture taken from the live OpenAPI on 2026-10-29 (audi0-scraper v2.3.0):
    /v1/trending · /v1/health · /v1/trending/hashtags · /v1/trending/countries
It has no /v1/song, /v1/download, /v1/file/..., /v1/library, /v1/niches.

This test exists because the old code was written against a previous generation
of the service (still present in ~/trending-audio-scraper) and every call to the
removed routes failed silently behind a broad except.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from src import music

LIVE_PATHS = {
    "/v1/trending",
    "/v1/health",
    "/v1/trending/hashtags",
    "/v1/trending/countries",
}

OBSOLETE = ("/v1/song", "/v1/download", "/v1/file/", "/v1/library", "/v1/niches")


def _source() -> str:
    return Path(music.__file__).read_text(encoding="utf-8")


def test_no_obsolete_route_is_referenced_anywhere_in_the_adapter():
    src = _source()
    hits = [r for r in OBSOLETE if r in src]
    assert not hits, f"music.py still references removed routes: {hits}"


def test_literal_routes_are_a_subset_of_the_live_contract():
    src = _source()
    literals = set(re.findall(r'"/v1/[a-z0-9/<>_-]+"', src))
    literals = {l.strip('"') for l in literals}
    # Routes are also built with f-strings ({api}/v1/trending); normalise those.
    built = {r for r in LIVE_PATHS if f"v1/{r.split('/v1/')[1]}" in src}
    unknown = {l for l in literals if l not in LIVE_PATHS and not l.startswith("/v1/trending")}
    assert not unknown, f"adapter references undocumented routes: {unknown}"
    assert built, "the live trending route is not built anywhere in the adapter"


def test_trending_client_is_status_driven():
    src = inspect.getsource(music._trending_payload)
    assert "processing" in src and "poll_with" in src
    assert "retry_after_seconds" in src
    rows_src = inspect.getsource(music.trending_rows)
    assert '"data"' in rows_src, "the ready payload carries rows under data, not results"


def test_acquire_has_no_dead_tiers():
    src = inspect.getsource(music.acquire)
    for name in ("song_provider", "library_provider", "_acquire"):
        assert name not in src, f"acquire still calls {name}"


def test_mood_gate_never_treats_unmeasured_as_pass():
    src = inspect.getsource(music.mood_matches)
    assert "return None" in src, "unmeasured tempo must be reported, not treated as a pass"