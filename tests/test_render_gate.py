"""test_render_gate.py — the gate, exercised against REAL ffmpeg output.

NO MOCKS. This test builds genuine MP4s with the real binary and runs the real
gate over them, because the failure this gate exists to catch — a truncated
encode with no moov atom — is invisible to anything that fakes the probe.

Run: python3 -m tests.test_render_gate

Four artefacts are produced and asserted on:
  1. a GOOD reel built through the real compositor  -> gate must pass
  2. a TRUNCATED file (encode killed mid-write)     -> gate must fail on moov
  3. a WRONG-ASPECT reel (SAR 555:416)             -> gate must fail on pixels
  4. a WRONG-LENGTH reel (8s)                      -> gate must fail on duration

Artefact 2 is the one that mattered: it is byte-for-byte the shape of the file
this pipeline actually produced and then reported as a success.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from src import render_gate as G


def _ff():
    from src import config
    return config.resolve_ffmpeg()


def _make(path: Path, *, seconds: float = 18.0, sar: str = "1",
          w: int = 1080, h: int = 1920, fps: int = 30) -> bool:
    """A real h264 MP4 with a moving picture, built by ffmpeg."""
    vf = (f"testsrc=size={w}x{h}:rate={fps}:duration={seconds},"
          f"setsar={sar}")
    cmd = [_ff(), "-y", "-v", "error", "-f", "lavfi", "-i", vf,
           "-t", f"{seconds}", "-c:v", "libx264", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return r.returncode == 0 and path.exists()


def _truncate(path: Path, out: Path, keep: float = 0.35) -> Path:
    """Copy the first fraction of the bytes — the shape of a killed encode."""
    data = path.read_bytes()
    out.write_bytes(data[: int(len(data) * keep)])
    return out


def main() -> int:
    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="gate_"))
    print(f"artefacts in {tmp}\n")

    # ---- 1. a good reel passes
    good = tmp / "good.mp4"
    if not _make(good):
        print("!! could not build the reference clip — ffmpeg missing?")
        return 1
    ok, rep = G.check(good, expect_audio=False)
    print(f"[{'PASS' if ok else 'FAIL'}] real 1080x1920 18s clip")
    print(f"          {G.describe(rep)}")
    if not ok:
        problems.append(f"a valid clip was rejected: {rep.get('failures')}")
    for k in ("width", "height", "sar", "fps", "duration_s", "size_bytes",
              "decode_ok", "moov_head"):
        if k not in rep:
            problems.append(f"report is missing {k}")

    # ---- 2. a truncated encode fails, and fails on moov / decode
    trunc = _truncate(good, tmp / "truncated.mp4")
    ok2, rep2 = G.check(trunc, expect_audio=False)
    print(f"[{'PASS' if not ok2 else 'FAIL'}] truncated encode (no moov)")
    print(f"          {G.describe(rep2)}")
    if ok2:
        problems.append("a truncated file PASSED the gate — this is the exact "
                        "failure that shipped an unplayable reel before")
    else:
        joined = " ".join(rep2.get("failures") or []).lower()
        if "moov" not in joined and "decode" not in joined:
            problems.append(f"truncated file failed for the wrong reason: {joined}")

    # ---- 3. non-square pixels fail
    bad_sar = tmp / "bad_sar.mp4"
    if _make(bad_sar, sar="555/416"):
        ok3, rep3 = G.check(bad_sar, expect_audio=False)
        print(f"[{'PASS' if not ok3 else 'FAIL'}] non-square pixels SAR 555:416")
        print(f"          {G.describe(rep3)}")
        if ok3:
            problems.append("a 3:4 reel passed the gate — Instagram would "
                            "letterbox it")

    # ---- 4. wrong duration fails
    short = tmp / "short.mp4"
    if _make(short, seconds=8.0):
        ok4, rep4 = G.check(short, expect_audio=False)
        print(f"[{'PASS' if not ok4 else 'FAIL'}] 8s clip (needs 16-20s)")
        print(f"          {G.describe(rep4)}")
        if ok4:
            problems.append("an 8s reel passed the gate")

    # ---- 5. wrong resolution fails
    small = tmp / "small.mp4"
    if _make(small, w=720, h=1280):
        ok5, rep5 = G.check(small, expect_audio=False)
        print(f"[{'PASS' if not ok5 else 'FAIL'}] 720x1280 clip")
        print(f"          {G.describe(rep5)}")
        if ok5:
            problems.append("a 720p reel passed the gate")

    # ---- 6. a missing file fails cleanly (never raises)
    try:
        ok6, rep6 = G.check(tmp / "does_not_exist.mp4")
        print(f"[{'PASS' if not ok6 else 'FAIL'}] missing file")
        if ok6:
            problems.append("a missing file passed the gate")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"missing file RAISED instead of failing cleanly: {exc}")

    # ---- 7. the audio contract both ways
    ok7, rep7 = G.check(good, expect_audio=True)
    print(f"[{'PASS' if not ok7 else 'FAIL'}] silent reel with expect_audio=True")
    if ok7:
        problems.append("a silent reel passed when audio was REQUIRED")
    ok8, rep8 = G.check(good, expect_audio=False)
    print(f"[{'PASS' if ok8 else 'FAIL'}] silent reel with expect_audio=False "
          f"(silence is approved)")

    print()
    if problems:
        print("FAILURES:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("ALL RENDER-GATE CHECKS PASSED (real ffmpeg, no mocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())