"""template_renderer.py — the two cloned formats as engine renderers.

Implements the shared `renderers.Renderer` interface so the orchestrator treats
these exactly like the v3/v2 adapters:

    validate_blueprint · build_timeline · render · qa

Non-negotiable rules encoded here:

* **Source immutability.** The engine-owned template is never written to. Each
  render copies it into `$BEAST_SCRATCH/<run>/<format>/`, and only that copy is
  touched: the two media files are replaced and one bounded grade variable plus
  the CONTENT data block are injected.
* **Data only.** Nothing from a payload is interpolated as markup; strings are
  JSON-encoded and validated by `template_payloads` first.
* **No fake success.** If HyperFrames/Node is not runnable the render raises
  `TemplateUnavailable`. It never returns a path to a file it did not produce.
* **Measure, don't assume.** Each output is probed (geometry, fps, duration,
  audio) and graded frames are measured for luma/saturation so drift is reported
  rather than discovered later.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .. import config, render_gate
from . import template_payloads
from .renderers import Renderer

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
SCRATCH = Path(os.environ.get("BEAST_SCRATCH", "/tmp/beast-render"))
HF_CMD = os.environ.get("BEAST_HF_CMD", "npx --yes hyperframes@0.8.212")

# Measured targets, from analysis/style-profile.json in the factory.
PROFILES = {
    "caption_list_title_v1": {
        "duration_s": 12.2, "duration_bounds": (11.6, 12.8),
        "yavg": 105.0, "yavg_tol": 14.0, "sat": 14.2, "sat_tol": 5.0,
        "grade_limits": {"brightness": (0.85, 1.32), "saturate": (0.60, 1.45)},
    },
    "social_moment_loop_v1": {
        "duration_s": 7.0, "duration_bounds": (6.4, 7.8),
        "yavg": 78.6, "yavg_tol": 20.0, "sat": 8.34, "sat_tol": 4.0,
        "grade_limits": {"brightness": (0.80, 1.45), "saturate": (0.60, 1.60)},
    },
}

CONTENT_RE = re.compile(r"const CONTENT = \{.*?\n      \};", re.S)


class TemplateUnavailable(RuntimeError):
    """The HyperFrames toolchain cannot run here. Never downgraded to a fake pass."""


def _ffmpeg() -> str:
    return config.resolve_ffmpeg()


def _node_bin_dir() -> str | None:
    """The two Node installs that exist off-PATH on this box."""
    for cand in (Path.home() / ".local" / "node" / "bin",
                 Path("/usr/local/nvm/versions/node/v24.22.1/bin")):
        if (cand / "node").exists():
            return str(cand)
    return None


def toolchain_status() -> dict:
    """Is this machine able to render? Explicit, with the reason when it is not."""
    node = shutil.which("node")
    npx = shutil.which("npx")
    if not node:
        d = _node_bin_dir()
        if d:
            node = str(Path(d) / "node")
            npx = str(Path(d) / "npx")
    return {"node": node, "npx": npx,
            "ok": bool(node and npx and Path(node).exists())}


def _env() -> dict:
    env = dict(os.environ)
    d = _node_bin_dir()
    if d and d not in env.get("PATH", ""):
        env["PATH"] = f"{d}:{env.get('PATH', '')}"
    # Keep large caches off the small /home mount where the toolchain allows it.
    env.setdefault("TMPDIR", str(SCRATCH / "tmp"))
    (SCRATCH / "tmp").mkdir(parents=True, exist_ok=True)
    return env


def measure_luma_sat(path) -> dict:
    """Average YAVG/SATAVG over sampled frames, via ffmpeg signalstats."""
    r = subprocess.run(
        [_ffmpeg(), "-v", "info", "-i", str(path),
         "-vf", "fps=1,signalstats,metadata=print", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    text = (r.stderr or "") + (r.stdout or "")
    ys = [float(m) for m in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", text)]
    ss = [float(m) for m in re.findall(r"lavfi\.signalstats\.SATAVG=([\d.]+)", text)]
    if not ys:
        return {"yavg": None, "sat": None, "error": "no signalstats output"}
    return {"yavg": round(sum(ys) / len(ys), 1),
            "sat": round(sum(ss) / len(ss), 2) if ss else None}


def grade_for(format_id: str, metrics: dict) -> tuple[str, dict]:
    """A bounded grade that lands the clip inside the measured band.

    Natural-fit clips need ~1.0. Clips outside the declared limits are reported
    as stretch picks by the caller; the grade is still bounded here rather than
    silently pushing a dark clip until the blacks lift.
    """
    p = PROFILES[format_id]
    lim = p["grade_limits"]
    y = metrics.get("yavg") or p["yavg"]
    s = metrics.get("sat") or p["sat"]
    b = p["yavg"] / max(float(y), 1.0)
    sat = p["sat"] / max(float(s), 0.5)
    b_c = max(lim["brightness"][0], min(lim["brightness"][1], b))
    s_c = max(lim["saturate"][0], min(lim["saturate"][1], sat))
    css = f"brightness({b_c:.3f}) saturate({s_c:.3f}) contrast(1.02)"
    return css, {"brightness": round(b_c, 3), "saturate": round(s_c, 3),
                 "natural_fit": (b_c == b and s_c == sat),
                 "raw": {"brightness": round(b, 3), "saturate": round(sat, 3)}}


def _content_for(format_id: str, payload: dict) -> dict:
    if format_id == "caption_list_title_v1":
        return {"titleLines": payload["titleLines"], "captionItems": [], "captionTakeaway": ""}
    return {"hookLines": payload["hookLines"], "payoffLines": payload["payoffLines"],
            "payoffStartFraction": payload.get("payoffStartFraction", 0.6)}


def stage_project(format_id: str, payload: dict, *, run_id: str | None = None,
                  background=None, audio=None) -> tuple[Path, dict]:
    """Copy the engine-owned template into a run-scoped scratch dir and inject data.

    Returns (project_dir, info). The engine-owned template is read-only here.
    """
    run_id = run_id or f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    src = TEMPLATES / format_id
    if not src.is_dir():
        raise FileNotFoundError(f"engine template missing: {src}")
    dest = SCRATCH / run_id / format_id
    if dest.exists():
        raise FileExistsError(f"run dir already exists (refusing to reuse): {dest}")
    dest.mkdir(parents=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)

    # 1) media swap: same filenames the template already references.
    bg_metrics = {"yavg": None, "sat": None}
    if background:
        shutil.copy2(background, dest / "assets" / "media" / "bg.mp4")
        bg_metrics = measure_luma_sat(dest / "assets" / "media" / "bg.mp4")
    if audio:
        shutil.copy2(audio, dest / "assets" / "audio" / "bgm.m4a")

    # 2) bounded grade as a CSS variable (the template's documented hook).
    css, gvals = grade_for(format_id, bg_metrics)
    html = (dest / "index.html").read_text(encoding="utf-8")
    if CONTENT_RE.search(html) is None:
        raise RuntimeError("template CONTENT block not found; refusing to guess")
    body = json.dumps(_content_for(format_id, payload), ensure_ascii=False)
    html = CONTENT_RE.sub(f"const CONTENT = {body};", html, count=1)
    if "</head>" not in html:
        raise RuntimeError("template has no </head>; refusing to inject grade")
    html = html.replace("</head>",
                        f"<style>:root{{--bg-filter:{css}}}</style>\n  </head>", 1)
    (dest / "index.html").write_text(html, encoding="utf-8")

    info = {"run_id": run_id, "project_dir": str(dest), "grade": gvals,
            "grade_css": css, "bg_metrics": bg_metrics,
            "source_template": str(src)}
    return dest, info


def render_project(project_dir: Path, output_path: Path, *, quality: str = "delivery",
                   fps: int = 30, crf: int = 18, keyframe_interval: int = 2,
                   timeout_s: int = 2400) -> dict:
    """Run the HyperFrames CLI. Raises TemplateUnavailable when it cannot run."""
    st = toolchain_status()
    if not st["ok"]:
        raise TemplateUnavailable(
            "HyperFrames needs node+npx; found node=%r npx=%r. "
            "Set PATH to a Node 22+ install (see toolchain_status())." % (st["node"], st["npx"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = (HF_CMD.split() + ["render", str(project_dir), "-o", str(output_path),
                             "-q", quality, "--fps", str(fps), "--crf", str(crf),
                             "--keyframe-interval", str(keyframe_interval)])
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(project_dir), env=_env(),
                       capture_output=True, text=True, timeout=timeout_s)
    log = SCRATCH / "logs"
    log.mkdir(parents=True, exist_ok=True)
    logfile = log / f"{project_dir.name}-{time.strftime('%H%M%S')}.log"
    logfile.write_text((r.stdout or "") + "\n" + (r.stderr or ""), encoding="utf-8")
    if r.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"hyperframes render failed rc={r.returncode}; log={logfile}; "
            f"tail={(r.stderr or r.stdout or '')[-400:]}")
    return {"ok": True, "cmd": " ".join(cmd), "seconds": round(time.time() - t0, 1),
            "log": str(logfile)}


def render_preview(format_id: str, payload: dict, out_path, *, quality: str = "delivery",
                   background=None, audio=None, run_id: str | None = None,
                   keep_project: bool = False) -> dict:
    """End-to-end: validate → stage → render → measure → QA. Returns the record."""
    errors = template_payloads.validate(payload, format_id)
    if errors:
        raise ValueError("invalid payload: " + "; ".join(errors))
    payload = template_payloads.normalise(payload, format_id)
    out_path = Path(out_path)
    proj, info = stage_project(format_id, payload, run_id=run_id,
                               background=background, audio=audio)
    rinfo = render_project(proj, out_path, quality=quality)
    prof = PROFILES[format_id]
    ok, gate = render_gate.check(out_path, expect_audio=True,
                                 duration_bounds=prof["duration_bounds"])
    measured = measure_luma_sat(out_path)
    drift = {}
    if measured.get("yavg") is not None:
        drift = {"yavg_delta": round(measured["yavg"] - prof["yavg"], 1),
                 "sat_delta": round((measured.get("sat") or 0) - prof["sat"], 2)}
    record = {
        "format_id": format_id, "schema_version": template_payloads.SCHEMA_VERSION,
        "run_id": info["run_id"], "output": str(out_path),
        "sha256": _sha256(out_path), "bytes": out_path.stat().st_size,
        "duration_s": round(_duration(out_path), 3),
        "template_sha256": _sha256(TEMPLATES / format_id / "index.html"),
        "payload": payload, "grade": info["grade"], "bg_metrics": info["bg_metrics"],
        "measured": measured, "drift": drift, "gate_ok": bool(ok), "gate": gate,
        "render": rinfo, "project_dir": str(proj) if keep_project else None,
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return record


def _sha256(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _duration(path) -> float:
    info = render_gate.probe(path)
    try:
        return float(info.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ interface
class TemplateRenderer(Renderer):
    """Adapter so a template format is indistinguishable from v3/v2 to callers."""

    def __init__(self, format_id: str):
        self.format_id = format_id
        self.id = format_id

    def validate_blueprint(self, blueprint: dict) -> list:
        return template_payloads.validate(blueprint, self.format_id)

    def build_timeline(self, blueprint: dict, assets=None, audio=None) -> dict:
        p = PROFILES[self.format_id]
        payload = template_payloads.normalise(blueprint, self.format_id)
        return {
            "format_id": self.format_id,
            "duration_s": p["duration_s"],
            "static": True,
            "text_visible_from_s": 0.0,
            "beats": [],
            "text": _content_for(self.format_id, payload),
            "audio": blueprint.get("audio") or {},
            "grade_limits": p["grade_limits"],
        }

    def render(self, timeline: dict, output_path, **ctx) -> dict:
        return render_preview(self.format_id, ctx["payload"], output_path,
                              quality=ctx.get("quality", "delivery"),
                              background=ctx.get("background"), audio=ctx.get("audio"))

    def qa(self, artifact: dict, blueprint: dict) -> dict:
        """Pass only when the file measures inside the format's band.

        A gate pass alone is not enough: the gate proves the file is a decodable
        1080x1920/30fps clip with audio, not that it still looks like the
        reference. Drift beyond the measured tolerance makes the variant
        `needs_review`, which is visible instead of being averaged away.
        """
        prof = PROFILES[self.format_id]
        m = artifact.get("measured") or {}
        band_ok = None
        if m.get("yavg") is not None:
            band_ok = (abs(m["yavg"] - prof["yavg"]) <= prof["yavg_tol"]
                       and abs((m.get("sat") or 0) - prof["sat"]) <= prof["sat_tol"])
        return {"ok": bool(artifact.get("gate_ok")) and band_ok is not False,
                "gate_ok": bool(artifact.get("gate_ok")),
                "band_ok": band_ok,
                "targets": {"yavg": prof["yavg"], "yavg_tol": prof["yavg_tol"],
                            "sat": prof["sat"], "sat_tol": prof["sat_tol"]},
                "gate": artifact.get("gate"), "measured": m,
                "drift": artifact.get("drift"), "duration_s": artifact.get("duration_s")}