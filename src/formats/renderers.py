"""renderers.py — the shared renderer interface + adapters (Section 7.2).

Every renderer implements exactly four methods so the orchestrator can treat a
two-beat short and a paced confession the same way:

    validate_blueprint(blueprint) -> errors
    build_timeline(blueprint, assets, audio) -> timeline
    render(timeline, output_path) -> artifact
    qa(artifact, blueprint) -> report

This module adds NO new visual technique. It puts the already-working v3
(`generate_short`/`lint_short`/`build_short`) and v2 (`paced`/`build_video`) code
behind one interface. A timeline is explicit: beat start/end seconds, the text
safe band, the audio plan and the intended motion. `qa` reuses the real
`render_gate` (no mocks) so a rendered file is judged by the same gate the
production path uses.

The cinematic renderer is intentionally absent (Section 16, decision 3); the
registry marks `cinematic_v1` not_eligible instead.
"""
from __future__ import annotations

from pathlib import Path

from .. import blueprint as bp_mod
from . import registry

FPS = 30
SIZE = "1080x1920"
TEXT_BAND = [150, 930]          # v3 §4 / v2 text-safe band

# v3 length buckets → (lo, hi) seconds. Mirrors build_short.build()'s own map.
_V3_BUCKETS = {"6-8": (6.0, 8.0), "8-10": (8.0, 10.0), "10-12": (10.0, 12.0)}
_V3_WORDS_PER_SEC = 2.3         # two-beat on-screen reading pace


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

    # -- shared helpers -------------------------------------------------------
    def _gate(self, path, expect_audio, duration_bounds) -> dict:
        from .. import render_gate
        ok, report = render_gate.check(path, expect_audio=expect_audio,
                                       duration_bounds=tuple(duration_bounds))
        return {"ok": ok, "gate": report, "why": None if ok else
                render_gate.describe(report)}


class TwoBeatV3Renderer(Renderer):
    """Adapter over the existing v3 modules. No v3 logic is duplicated."""

    id = "two_beat_v3"

    def __init__(self, memory=None, offline: bool = False, verbose: bool = True):
        self.memory = memory if memory is not None else {}
        self.offline = offline
        self.verbose = verbose

    def validate_blueprint(self, blueprint: dict) -> list:
        errors = list(registry.validate_for_format(blueprint))
        beats = [b for b in blueprint.get("beats", []) if isinstance(b, dict)]
        kinds = [b.get("kind") for b in beats]
        if kinds != ["hook", "payoff"]:
            errors.append("two_beat_v3 needs exactly beats [hook, payoff]")
        arm = blueprint.get("visual_arm") or "C"
        rr = blueprint.get("rights_requirements") or []
        # Arm C is a generated plain background and needs no third-party rights;
        # arms A/B require declared visual + audio rights.
        if arm in ("A", "B"):
            joined = " ".join(str(r).lower() for r in rr)
            if "visual" not in joined or "audio" not in joined:
                errors.append(f"arm {arm} requires visual+audio rights declared")
        return errors

    def build_timeline(self, blueprint: dict, assets=None, audio=None) -> dict:
        duration = _v3_duration(blueprint)
        ratios = bp_mod.timeline_ratios(blueprint)
        beats = []
        for i, b in enumerate(blueprint.get("beats", [])):
            start = round(float(ratios[i]) * duration, 2)
            end = (round(float(ratios[i + 1]) * duration, 2) if i + 1 < len(ratios)
                   else duration)
            beats.append({"kind": b.get("kind"), "text": str(b.get("text") or ""),
                          "start_s": start, "end_s": end,
                          "visual_intent": b.get("visual_intent")})
        return {
            "format_id": "two_beat_v3", "renderer": self.id, "fps": FPS,
            "size": SIZE, "duration_s": duration, "beats": beats,
            "text_band": list(TEXT_BAND), "loop_seamless": True,
            "motion_intent": "hook holds, payoff reframes at 60%",
            "audio": audio or blueprint.get("audio_plan"),
            "visual_arm": blueprint.get("visual_arm") or "C",
            "scene_queries": _scene_queries(blueprint, assets),
        }

    def render(self, timeline: dict, output_path) -> dict:
        from .. import build_short
        reel = {
            "hook": _beat_text(timeline, "hook"),
            "payoff": _beat_text(timeline, "payoff"),
            "visual_arm": timeline.get("visual_arm") or "C",
            "scene_queries": timeline.get("scene_queries") or [],
            "length_bucket": timeline.get("length_bucket") or "8-10",
            "audio_type": (timeline.get("audio") or {}).get("mode"),
        }
        res = build_short.build(reel, self.memory, Path(output_path),
                                offline=self.offline,
                                duration_s=timeline.get("duration_s"),
                                verbose=self.verbose)
        return res

    def qa(self, artifact: dict, blueprint: dict) -> dict:
        if not artifact.get("ok"):
            return {"ok": False, "why": artifact.get("why") or artifact.get("stage"),
                    "stage": artifact.get("stage")}
        bounds = registry.get("two_beat_v3")["duration_bounds"]
        rep = self._gate(artifact["path"], expect_audio=True, duration_bounds=bounds)
        return {"ok": rep["ok"], "gate": rep["gate"], "why": rep["why"],
                "bounds": bounds, "path": artifact["path"]}


class PacedV2Renderer(Renderer):
    """Adapter over the existing v2 render path.

    validate/build_timeline are fully implemented and deterministic. render()
    delegates to the real `build_video.build`, so it requires the orchestrated v2
    production inputs (background mp4 + optional track) that run_v2 already
    assembles; when they are absent it fails closed with a clear reason rather
    than inventing a background here.
    """

    id = "paced_v2"

    def __init__(self, cfg=None, memory=None, offline: bool = False):
        self.cfg = cfg
        self.memory = memory if memory is not None else {}
        self.offline = offline

    def validate_blueprint(self, blueprint: dict) -> list:
        return list(registry.validate_for_format(blueprint))

    def build_timeline(self, blueprint: dict, assets=None, audio=None) -> dict:
        from .. import lint
        lines = [str(b.get("text") or "") for b in blueprint.get("beats", [])]
        starts, duration = lint.reveal_schedule(lines)
        beats = []
        for i, text in enumerate(lines):
            start = starts[i] if i < len(starts) else duration
            end = starts[i + 1] if i + 1 < len(starts) else duration
            beats.append({"kind": (blueprint["beats"][i].get("kind")
                                   if i < len(blueprint["beats"]) else "line"),
                          "text": text, "start_s": start, "end_s": end})
        return {
            "format_id": "paced_v2", "renderer": self.id, "fps": FPS, "size": SIZE,
            "duration_s": duration, "beats": beats, "text_band": list(TEXT_BAND),
            "pacing": registry.get("paced_v2")["pacing"],
            "audio": audio or blueprint.get("audio_plan"),
        }

    def render(self, timeline: dict, output_path) -> dict:
        content = timeline.get("content")
        bg_mp4 = timeline.get("bg_mp4")
        if not content or not bg_mp4:
            return {"ok": False, "stage": "precondition",
                    "why": "paced_v2 render needs orchestrated `content` and "
                           "`bg_mp4` in the timeline; use run_v2 for the full path"}
        try:
            from .. import build_video
            track = timeline.get("track")
            build_video.build(self.cfg, content, timeline["duration_s"], track,
                              None, Path(bg_mp4), Path(output_path))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "render", "why": str(exc)[:300]}
        return {"ok": True, "path": str(output_path)}

    def qa(self, artifact: dict, blueprint: dict) -> dict:
        if not artifact.get("ok"):
            return {"ok": False, "why": artifact.get("why") or artifact.get("stage"),
                    "stage": artifact.get("stage")}
        bounds = registry.get("paced_v2")["duration_bounds"]
        rep = self._gate(artifact["path"], expect_audio=True, duration_bounds=bounds)
        return {"ok": rep["ok"], "gate": rep["gate"], "why": rep["why"],
                "bounds": bounds, "path": artifact["path"]}


# ------------------------------------------------------------------- helpers

def _v3_duration(blueprint: dict) -> float:
    bucket = blueprint.get("length_bucket")
    if bucket in _V3_BUCKETS:
        lo, hi = _V3_BUCKETS[bucket]
        return round((lo + hi) / 2, 2)
    words = sum(len(str(b.get("text") or "").split())
                for b in blueprint.get("beats", []))
    return max(6.0, min(12.0, round(words / _V3_WORDS_PER_SEC, 2)))


def _beat_text(timeline: dict, kind: str) -> str:
    for b in timeline.get("beats", []):
        if b.get("kind") == kind:
            return str(b.get("text") or "")
    return ""


def _scene_queries(blueprint: dict, assets) -> list:
    if blueprint.get("scene_query"):
        return [str(blueprint["scene_query"])]
    if isinstance(assets, dict):
        return list(assets.get("scene_queries") or [])
    return []


_REGISTRY = {"two_beat_v3": TwoBeatV3Renderer, "paced_v2": PacedV2Renderer}


def build(name: str, **kw) -> Renderer:
    """Factory used by registry.renderer_for."""
    if name not in _REGISTRY:
        raise KeyError(f"no renderer adapter for {name!r}")
    return _REGISTRY[name](**kw)
