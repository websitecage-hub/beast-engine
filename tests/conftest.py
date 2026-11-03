"""Test isolation: no test may write production state.

Measured: a test that called the real runner without patching the manifest path
appended into data/template_manifest.json; a concurrent writer then interleaved and
the next reader quarantined the whole manifest as corrupt, which silently stopped
every run from reading its own history. This fixture is autouse, so it holds for
every test that forgets.
"""
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def never_touch_production_state(tmp_path, monkeypatch):
    """Point every writer at a per-test tmp dir before the test body runs."""
    from src import run_preview
    from src.formats import run_manifest

    monkeypatch.setattr(run_manifest, "MANIFEST", tmp_path / "template_manifest.json")
    monkeypatch.setattr(run_preview, "ROOT", tmp_path / "root")
    monkeypatch.setenv("BEAST_LEDGER", str(tmp_path / "asset_ledger.json"))
    monkeypatch.setenv("BEAST_MEMORY", str(tmp_path / "memory.json"))
    # Anything that still resolves a data/ path should land in tmp, not the repo.
    monkeypatch.setenv("BEAST_DATA_DIR", str(tmp_path / "data"))
    return tmp_path
