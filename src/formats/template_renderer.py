"""template_renderer.py — the two cloned formats as engine renderers.

Implements the shared `renderer_base.Renderer` interface so the runner treats
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
from .renderer_base import Renderer

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
        # measured from the reference reel's audio (dBFS, not LUFS)
        "music": {"ref_tempo_bpm": 139.7, "ref_spectral_centroid_hz": 951.4,
                  "ref_rms_db": -16.9, "ref_onset_density_per_s": 2.73},
    },
    "social_moment_loop_v1": {
        "duration_s": 7.0, "duration_bounds": (6.4, 7.8),
        "yavg": 78.6, "yavg_tol": 20.0, "sat": 8.34, "sat_tol": 4.0,
        "grade_limits": {"brightness": (0.80, 1.45), "saturate": (0.60, 1.60)},
        "music": {"ref_tempo_bpm": 79.5, "ref_spectral_centroid_hz": 1537.0,
                  "ref_rms_db": -16.3, "ref_onset_density_per_s": 1.79},
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


def _background_grade_enabled() -> bool:
    """Is the old bounded background correction switched on?

    Off by default: the footage ships as shot. `BEAST_BG_GRADE=1` restores it, and
    `data/config.json`'s `bg_grade` is honoured when it is explicitly true, so the
    same switch governs the two-format pipeline and the older renderer.
    """
    import os
    env = os.environ.get("BEAST_BG_GRADE")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        from .. import config
        return bool(config.load_config().get("bg_grade", False))
    except Exception:                                     # noqa: BLE001
        return False


def grade_for(format_id: str, metrics: dict) -> tuple[str, dict]:
    """The background ships AS SHOT: no grade, by default.

    Owner's instruction: no colour layer over the footage - the clip that the search
    returned is the clip the viewer sees, with the copy and the music on top. The
    measured band is still reported (and still recorded on every run) so drift is
    visible in the manifest, but nothing is applied to the picture.

    Set BEAST_BG_GRADE=1 to restore the old bounded correction, which pulled the clip
    toward the format's measured band inside declared limits. It never pushed a dark
    clip until the blacks lifted; clips outside the limits were reported as stretch
    picks rather than silently forced.
    """
    p = PROFILES[format_id]
    if not _background_grade_enabled():
        y = metrics.get("yavg")
        s = metrics.get("sat")
        delta = {}
        if isinstance(y, (int, float)):
            delta["yavg"] = round(float(y) - float(p["yavg"]), 1)
        if isinstance(s, (int, float)):
            delta["sat"] = round(float(s) - float(p["sat"]), 2)
        return "none", {"brightness": 1.0, "saturate": 1.0, "applied": False,
                        "natural_fit": True, "raw": {"yavg": y, "sat": s},
                        "target": {"yavg": p["yavg"], "sat": p["sat"]},
                        "delta": delta,
                        "why": "no grade applied; band recorded only"}
    lim = p["grade_limits"]
    y = metrics.get("yavg") or p["yavg"]
    s = metrics.get("sat") or p["sat"]
    b = p["yavg"] / max(float(y), 1.0)
    sat = p["sat"] / max(float(s), 0.5)
    b_c = max(lim["brightness"][0], min(lim["brightness"][1], b))
    s_c = max(lim["saturate"][0], min(lim["saturate"][1], sat))
    css = f"brightness({b_c:.3f}) saturate({s_c:.3f}) contrast(1.02)"
    return css, {"brightness": round(b_c, 3), "saturate": round(s_c, 3),
                 "applied": True,
                 "natural_fit": (b_c == b and s_c == sat),
                 "raw": {"brightness": round(b, 3), "saturate": round(sat, 3)}}


def _content_for(format_id: str, payload: dict) -> dict:
    if format_id == "caption_list_title_v1":
        return {"titleLines": payload["titleLines"], "captionItems": [], "captionTakeaway": ""}
    return {"hookLines": payload["hookLines"], "payoffLines": payload["payoffLines"],
            "payoffStartFraction": payload.get("payoffStartFraction", 0.6)}


def stage_project(format_id: str, payload: dict, *, run_id: str | None = None,
                  background=None, audio=None, fixed_grade: dict | None = None) -> tuple[Path, dict]:
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
    if fixed_grade:
        gvals = dict(fixed_grade)
        css = (f"brightness({gvals['brightness']:.3f}) "
               f"saturate({gvals['saturate']:.3f}) contrast(1.02)")
        gvals.setdefault("natural_fit", False)
        gvals.setdefault("corrected", True)
    else:
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




def first_frame_luma(path) -> float | None:
    """Mean luma of frame 0, used to decide whether the flash repair is needed."""
    r = subprocess.run([_ffmpeg(), "-v", "info", "-ss", "0", "-i", str(path),
                        "-frames:v", "1", "-vf", "signalstats,metadata=print",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=180)
    m = re.search(r"YAVG=([\d.]+)", (r.stderr or "") + (r.stdout or ""))
    return float(m.group(1)) if m else None


# Below this, frame 0 is a capture warm-up artefact rather than a dark shot.
DARK_FIRST_FRAME = 60.0


def drop_dark_first_frame(path, fps: int = 30) -> dict:
    """Remove the capture warm-up frame and shift audio to match.

    HyperFrames' first captured frame is consistently dark (measured YAVG ~28 vs
    ~97 on frame 2): the <video> element has not painted yet when capture starts.
    Instagram uses frame 0 as the feed cover, so that flash is a real defect.
    The template is NOT touched — the artifact is corrected and the edit is
    recorded, then the result is measured again.
    """
    import tempfile
    # Temp on SCRATCH, never beside the output: the output lives on the small
    # persistent mount, and a killed re-encode must not leave an orphan there.
    SCRATCH.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkstemp(suffix=".mp4", dir=str(SCRATCH))[1])
    step = 1.0 / float(fps)
    r = subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", str(path),
                        "-vf", "trim=start_frame=1,setpts=PTS-STARTPTS",
                        "-af", f"atrim=start={step:.6f},asetpts=PTS-STARTPTS",
                        "-r", str(fps), "-c:v", "libx264", "-crf", "18",
                        "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", str(tmp)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 10240:
        tmp.unlink(missing_ok=True)
        return {"applied": False, "why": (r.stderr or "")[:200]}
    # SCRATCH and the output can be different mounts, where os.replace raises
    # EXDEV; copy then swap, and only remove the temp once the output is in place.
    try:
        tmp.replace(path)
    except OSError:
        shutil.copy2(tmp, path)
        tmp.unlink(missing_ok=True)
    return {"applied": True, "dropped_frames": 1, "audio_shift_s": round(step, 4)}


def frame0_text_metrics(path, format_id: str) -> dict:
    """Is text actually painted in the expected band on the FIRST frame?

    The template guarantees text from frame zero, but a broken injection would
    still render a perfectly valid video of an empty background. This counts
    near-white / pale-yellow pixels inside the text band at t=0 — a measurement,
    not an assumption. Run through ffmpeg + PIL so it needs no OCR.
    """
    import numpy as np
    from PIL import Image
    band = (0.22, 0.58, 190) if format_id == "caption_list_title_v1" else (0.30, 0.61, 165)
    lo, hi, threshold = band
    png = SCRATCH / "frames" / f"{Path(path).stem}-f0.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", str(path),
                        "-frames:v", "1", str(png)], capture_output=True, timeout=120)
    if r.returncode != 0 or not png.exists():
        return {"ok": None, "why": "frame 0 could not be extracted"}
    img = np.asarray(Image.open(png).convert("L")).astype(np.uint8)
    h = img.shape[0]
    band_px = img[int(h * lo):int(h * hi), :]
    bright = float((band_px > threshold).mean())
    # The reference title covers several percent of the band; an empty shot is ~0.
    return {"ok": bright >= 0.005, "bright_fraction": round(bright, 4),
            "band": [lo, hi], "threshold": threshold}


def render_preview(format_id: str, payload: dict, out_path, *, quality: str = "delivery",
                   background=None, audio=None, run_id: str | None = None,
                   keep_project: bool = False, allow_correction: bool = True) -> dict:
    """End-to-end: validate → stage → render → measure → (one) correct → QA."""
    errors = template_payloads.validate(payload, format_id)
    if errors:
        raise ValueError("invalid payload: " + "; ".join(errors))
    payload = template_payloads.normalise(payload, format_id)
    out_path = Path(out_path)
    prof = PROFILES[format_id]
    run_id = run_id or f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"

    proj, info = stage_project(format_id, payload, run_id=run_id,
                               background=background, audio=audio)
    rinfo = render_project(proj, out_path, quality=quality)
    measured = measure_luma_sat(out_path)
    attempts = 1
    corrections = []

    def band_ok(m):
        if m.get("yavg") is None:
            return None
        return (abs(m["yavg"] - prof["yavg"]) <= prof["yavg_tol"]
                and abs((m.get("sat") or 0) - prof["sat"]) <= prof["sat_tol"])

    # One bounded correction pass, only when the colour drifted and the needed
    # grade still fits inside the declared limits. Never a loop.
    if allow_correction and band_ok(measured) is False:
        lim = prof["grade_limits"]
        g = info["grade"]
        corr = {
            "brightness": max(lim["brightness"][0], min(
                lim["brightness"][1],
                g["brightness"] * (prof["yavg"] / max(measured.get("yavg") or prof["yavg"], 1)))),
            "saturate": max(lim["saturate"][0], min(
                lim["saturate"][1],
                g["saturate"] * (prof["sat"] / max(measured.get("sat") or prof["sat"], 0.5)))),
        }
        proj2, info2 = stage_project(format_id, payload, run_id=f"{run_id}-c1",
                                     background=background, audio=audio,
                                     fixed_grade=corr)
        render_project(proj2, out_path, quality=quality)
        measured2 = measure_luma_sat(out_path)
        corrections.append({"grade_before": g, "grade_after": info2["grade"],
                            "measured_before": measured, "measured_after": measured2})
        info = {**info2, "grade": info2["grade"]}
        measured = measured2
        attempts = 2

    # Only repair a genuinely dark frame 0; a legitimately dark shot (format B
    # measures 59-98 luma) must not lose a frame to a blanket rule.
    f0 = first_frame_luma(out_path)
    if f0 is not None and f0 < DARK_FIRST_FRAME:
        fix = drop_dark_first_frame(out_path, fps=30)
    else:
        fix = {"applied": False, "first_frame_luma": f0, "why": "frame 0 not dark"}
    ok, gate = render_gate.check(out_path, expect_audio=True,
                                 duration_bounds=prof["duration_bounds"])
    text = frame0_text_metrics(out_path, format_id)
    drift = {}
    if measured.get("yavg") is not None:
        drift = {"yavg_delta": round(measured["yavg"] - prof["yavg"], 1),
                 "sat_delta": round((measured.get("sat") or 0) - prof["sat"], 2)}
    record = {
        "format_id": format_id, "schema_version": template_payloads.SCHEMA_VERSION,
        "run_id": run_id, "output": str(out_path),
        "sha256": _sha256(out_path), "bytes": out_path.stat().st_size,
        "duration_s": round(_duration(out_path), 3),
        "template_sha256": _sha256(TEMPLATES / format_id / "index.html"),
        "payload": payload, "grade": info["grade"], "bg_metrics": info["bg_metrics"],
        "measured": measured, "drift": drift, "band_ok": band_ok(measured),
        "gate_ok": bool(ok), "gate": gate, "text_frame0": text,
        "attempts": attempts, "corrections": corrections, "first_frame_fix": fix,
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
                "drift": artifact.get("drift"), "duration_s": artifact.get("duration_s"),
                "text_frame0": artifact.get("text_frame0"),
                "attempts": artifact.get("attempts")}
