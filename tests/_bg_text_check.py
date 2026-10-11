"""Burned-in text check on the background clip itself (NOT the reel).

The user rejected the last sample because the background was a quote-card with
text baked in. The reel frame always contains OUR text, so this checks the
BACKGROUND FILE the way the QA gate would: sample frames, run a crude edge-density
luma analysis that flags typical caption-card layouts (a bright block of small
high-contrast glyphs against a flat field).

This is intentionally simpler than tesseract (not installed here); it is the same
signal the pinterest pin gate uses — quote cards have extreme high-frequency
bright-on-flat structure that real footage almost never has.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
from src.config import resolve_ffmpeg  # noqa: E402

FFMPEG = resolve_ffmpeg()

BG = Path(sys.argv[1] if len(sys.argv) > 1 else "outputs/bg.mp4")
W, H = 640, 360  # downscale: captions survive downscale, noise does not


def frame(t: float) -> bytes:
    r = subprocess.run(
        [FFMPEG, "-ss", f"{t}", "-i", str(BG), "-vframes", "1",
         "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    return r.stdout


def text_score(px: bytes) -> tuple:
    """(bright_fraction, edge_fraction). Caption cards score high on both."""
    if not px or len(px) != W * H:
        return 0.0, 0.0
    bright = sum(1 for v in px if v > 210) / len(px)
    edges = 0
    for y in range(1, H - 1):
        row = y * W
        for x in range(1, W - 1):
            i = row + x
            if abs(px[i] - px[i + 1]) > 90 and abs(px[i] - px[i - 1]) > 90:
                edges += 1
    return bright, edges / len(px)


print(f"background: {BG}")
print(f"{'t':>6}  {'bright':>8}  {'edges':>8}  verdict")
suspects = 0
for t in (1.0, 2.0, 2.9, 4.0, 5.8, 6.5):
    px = frame(t)
    b, e = text_score(px)
    # Real footage: bright<0.05 mostly, edges<0.004. A quote card: a solid block
    # of glyphs -> bright spike AND extreme local contrast.
    bad = b > 0.08 and e > 0.006
    suspects += bad
    print(f"{t:>6}  {b:8.4f}  {e:8.4f}  {'TEXT-LIKE' if bad else 'clean'}")
print()
print("VERDICT:", "FAIL — caption-card structure found"
      if suspects >= 2 else
      "PASS — no burned-in text structure in the sampled frames")