"""renderer_base.py — the four-method renderer interface.

The live pipeline has one renderer per format (`template_renderer`). This base is all
that interface needs: the four methods plus the shared structural gate. It used to live
in `formats/renderers.py` next to the retired v3 renderers, which meant importing the
interface pulled the whole legacy tree (build_video, build_short, blueprint, registry,
pexels, voiceover) into every run.
"""
from __future__ import annotations


class Renderer:
    """Base interface. Subclasses set ``id`` and implement the four methods."""

    id = "base"

    def validate_blueprint(self, blueprint: dict) -> list:
        raise NotImplementedError

    def build_timeline(self, blueprint: dict, assets=None, audio=None) -> dict:
        raise NotImplementedError

    def render(self, timeline: dict, output_path) -> dict:
        raise NotImplementedError

    def qa(self, artifact: dict, blueprint: dict) -> dict:
        raise NotImplementedError

    # -- shared helper --------------------------------------------------------
    def _gate(self, path, expect_audio, duration_bounds) -> dict:
        from .. import render_gate
        ok, report = render_gate.check(path, expect_audio=expect_audio,
                                      duration_bounds=tuple(duration_bounds))
        return {"ok": ok, "gate": report, "why": None if ok else
                render_gate.describe(report)}
