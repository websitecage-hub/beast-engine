"""render_gate.py — THE UPLOAD GATE (requirement: never publish a bad file).

Every check here runs against the FINISHED MP4 with real ffprobe and a real
full-file decode. Nothing is inferred from the render call's exit code.

WHY THIS MODULE EXISTS. The first reel this pipeline produced was reported as a
success by every layer above it and was in fact unplayable: ffmpeg was killed by
its own 900s timeout mid-encode and left a file with no moov atom. The exit-code
path said nothing was wrong. Two checks in this file would have caught it —
`moov present` and `full decode` — and that is exactly why they are here and why
"the render function returned without raising" is not accepted as evidence.

FAILURE POLICY: any failure returns ok=False with the reason. The caller skips
the day and logs it. It must NEVER retry into the same bad file, and it must
never publish on a partial result.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# Instagram's documented Reels limits. The size cap is the documented 1GB for
# video, but this account's route is an Instagram-Login container created from a
# public video_url, and the anonymous file host used in §publishing has a much
# smaller practical ceiling — 60MB is this pipeline's own working limit from the
# superseded build and is kept because it is the number already proven to upload.
MAX_BYTES = 60 * 1024 * 1024
MIN_S, MAX_S = 16.0, 20.0
W, H = 1080, 1920
FPS = 30


def _ffmpeg():
    from . import config
    return config.resolve_ffmpeg()


def _ffprobe() -> str | None:
    """ffprobe next to ffmpeg, or the system one. None when absent."""
    from . import config
    ff = Path(config.resolve_ffmpeg())
    cand = ff.with_name(ff.name.replace("ffmpeg", "ffprobe"))
    if cand.exists():
        return str(cand)
    import shutil
    return shutil.which("ffprobe")


# ffmpeg's own stderr carries every fact the gate needs, in a stable format.
# PARSED, NOT GUESSED, because this environment has NO ffprobe: the ffmpeg that
# ships with imageio_ffmpeg is a single binary with no sibling probe tool, and
# the first version of this gate raised FileNotFoundError on a valid reel.
# CI images usually DO have ffprobe, so ffprobe stays the preferred path and this
# is the guaranteed-present fallback.
_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_RE_VIDEO = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+)")
_RE_HDIM = re.compile(r"(\d{2,5})x(\d{2,5})")
_RE_SAR = re.compile(r"\[SAR (\d+:\d+)")
_RE_FPS = re.compile(r"([\d.]+) fps")
_RE_PIX = re.compile(r"Video: \w+[^,]*, ([a-z0-9]+(?:\([a-z]+\))?)")
_RE_AUDIO = re.compile(r"Stream #\d+:\d+.*?: Audio: (\w+)")


def probe_via_ffmpeg(path) -> dict:
    """The same dict shape as ffprobe, read from `ffmpeg -i` stderr."""
    out = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True, timeout=120)
    err = out.stderr or ""
    streams = []

    m = _RE_VIDEO.search(err)
    if m:
        line = [ln for ln in err.splitlines() if "Video:" in ln]
        line = line[0] if line else ""
        dim = _RE_HDIM.search(line)
        sar = _RE_SAR.search(line)
        fps = _RE_FPS.search(line)
        pix = _RE_PIX.search(line)
        streams.append({
            "codec_type": "video", "codec_name": m.group(1),
            "width": int(dim.group(1)) if dim else None,
            "height": int(dim.group(2)) if dim else None,
            "sample_aspect_ratio": sar.group(1) if sar else None,
            "r_frame_rate": f"{fps.group(1)}/1" if fps else None,
            "pix_fmt": pix.group(1) if pix else None,
        })

    a = _RE_AUDIO.search(err)
    if a:
        streams.append({"codec_type": "audio", "codec_name": a.group(1)})

    fmt: dict = {}
    d = _RE_DURATION.search(err)
    if d:
        fmt["duration"] = str(int(d.group(1)) * 3600 + int(d.group(2)) * 60
                              + float(d.group(3)))

    if not streams and not fmt:
        return {"error": (err.strip().splitlines() or ["ffmpeg could not read "
                        "the file"])[-1][:300]}
    return {"streams": streams, "format": fmt, "probed_via": "ffmpeg"}


def probe(path) -> dict:
    """ffprobe when present, else ffmpeg stderr. Same dict shape either way."""
    probe_bin = _ffprobe()
    if probe_bin:
        out = subprocess.run(
            [probe_bin, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=120)
        if out.returncode == 0:
            try:
                d = json.loads(out.stdout)
                d["probed_via"] = "ffprobe"
                return d
            except Exception:  # noqa: BLE001
                pass
    return probe_via_ffmpeg(path)


def _sar_is_square(sar: str) -> bool:
    """'1:1' -> True. '555:416' -> False. Missing -> assumed square by ffmpeg."""
    if not sar or sar in ("N/A", "0:1"):
        return True
    m = re.match(r"^(\d+):(\d+)$", str(sar).strip())
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    return b != 0 and a == b


def check(path, expect_audio: bool | None = None) -> tuple:
    """(ok, report). The full upload gate.

    expect_audio: True = an audio stream is required, False = it must be ABSENT,
    None = either is acceptable. The silence case is approved by STRATEGY.md, so
    a reel with no audio is valid — but it must be ABSENT ON PURPOSE, which means
    the caller states which it expects rather than the gate guessing.
    """
    p = Path(path)
    report: dict = {"file": str(p)}

    # 1. exists and is non-trivial
    if not p.exists():
        return False, {**report, "failures": ["file does not exist"]}
    size = p.stat().st_size
    report["size_bytes"] = size
    if size == 0:
        return False, {**report, "failures": ["file is empty"]}

    # 2. moov atom present — a truncated encode is unplayable. Checked on the
    #    BYTES, because that is the only place the failure is visible.
    head = p.open("rb").read(4096)
    report["moov_head"] = b"moov" in head
    has_moov = b"moov" in head
    if not has_moov:
        # moov can legitimately sit at the end when faststart did not run
        with p.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            tail = fh.read()
        has_moov = b"moov" in tail
        report["moov_tail"] = has_moov

    # 3. ffprobe
    pr = probe(p)
    report["probe_error"] = pr.get("error")
    streams = pr.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = pr.get("format") or {}

    failures = []
    if not has_moov:
        failures.append("moov atom not found — the encode was truncated, the "
                        "file is unplayable")
    if not video:
        failures.append("no video stream")

    if video:
        report["width"] = video.get("width")
        report["height"] = video.get("height")
        report["sar"] = video.get("sample_aspect_ratio")
        report["fps_raw"] = video.get("r_frame_rate")
        report["codec"] = video.get("codec_name")
        report["pix_fmt"] = video.get("pix_fmt")
        if (video.get("width"), video.get("height")) != (W, H):
            failures.append(f"resolution {video.get('width')}x{video.get('height')} "
                            f"(need {W}x{H})")
        if not _sar_is_square(video.get("sample_aspect_ratio")):
            failures.append(f"non-square pixels SAR "
                            f"{video.get('sample_aspect_ratio')} — the reel would "
                            f"be letterboxed or stretched")
        try:
            num, den = str(video.get("r_frame_rate", "0/1")).split("/")
            fps = float(num) / float(den or 1)
            report["fps"] = round(fps, 3)
            if abs(fps - FPS) > 0.05:
                failures.append(f"framerate {fps:.2f} (need {FPS})")
        except Exception:  # noqa: BLE001
            failures.append(f"unreadable framerate {video.get('r_frame_rate')!r}")
        if video.get("codec_name") != "h264":
            failures.append(f"codec {video.get('codec_name')} (need h264)")

    dur = None
    try:
        dur = float(fmt.get("duration"))
    except (TypeError, ValueError):
        pass
    report["duration_s"] = dur
    if dur is None:
        failures.append("no duration in container")
    elif not (MIN_S - 0.05 <= dur <= MAX_S + 0.05):
        failures.append(f"duration {dur:.2f}s outside {MIN_S:.0f}-{MAX_S:.0f}s")

    # 4. audio: present only if it was meant to be
    report["audio"] = bool(audio)
    if audio:
        report["audio_codec"] = audio.get("codec_name")
        report["audio_channels"] = audio.get("channels")
    if expect_audio is True and not audio:
        failures.append("no audio stream (an audio bed was expected)")
    if expect_audio is False and audio:
        failures.append("unexpected audio stream (this reel is a silent one)")

    # 5. size limit
    if size > MAX_BYTES:
        failures.append(f"{size / 1048576:.1f}MB over the {MAX_BYTES / 1048576:.0f}MB "
                        f"upload limit")

    # 6. FULL DECODE. The check that catches a file whose header lies. A truncated
    #    or corrupt stream fails here even when the header is intact.
    dec = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", str(p), "-f", "null", "-"],
        capture_output=True, text=True, timeout=600)
    err = (dec.stderr or "").strip()
    report["decode_ok"] = dec.returncode == 0 and not err
    report["decode_stderr"] = err[:400]
    if not report["decode_ok"]:
        failures.append(f"full decode failed: {err[:200] or 'ffmpeg error'}")

    report["failures"] = failures
    report["ok"] = not failures
    return (not failures), report


def describe(report: dict) -> str:
    """One log line, for the skip-day log."""
    if report.get("ok"):
        return (f"gate PASS {report.get('width')}x{report.get('height')} "
                f"SAR {report.get('sar')} {report.get('duration_s')}s "
                f"{report.get('fps')}fps "
                f"{report.get('size_bytes', 0) / 1048576:.1f}MB "
                f"audio={'yes' if report.get('audio') else 'none'} codec="
                f"{report.get('codec')} decode=ok")
    why = "; ".join(report.get("failures") or [report.get("probe_error") or "unknown"])
    return f"gate FAIL {Path(str(report.get('file'))).name}: {why}"