"""Dim check, measured on TEXT pixels only.

The first version compared max brightness in the hook's row and reported 100%
retained. That was the metric's fault, not the renderer's: max() returns the
brightest pixel in the region, which on a moving clip is a highlight in the
FOOTAGE, not the text. Both samples returned 255 because both had a bright
background pixel in that row.

This isolates the text the same way the accumulation check does — bright in the
reel AND dark in the same background — and then asks how bright those text
pixels still are once the next line has arrived. A 55% dim moves white (~255)
down to roughly 140, which is the difference between "still full alpha" and
"dimmed".
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
from src.still import FFMPEG  # noqa: E402

REEL = Path("outputs/proof/v2_paced_sample1.mp4")
BG = Path("outputs/bg.mp4")
W, H = 1080, 1920
BAND_TOP, BAND_H = int(H * 0.10), int(H * 0.66)
SLOT_H = BAND_H // 7
# The hook's own row (line 0 of 7 in stack layout).
ROW_TOP = BAND_TOP + SLOT_H

NEWEST = 2.0      # hook is the newest line here
AFTER_NEXT = 5.0  # line 2 arrived at 3.06, so the hook should be dimmed


def row(src: Path, t: float) -> bytes:
    r = subprocess.run(
        [FFMPEG, "-ss", f"{t}", "-i", str(src), "-vframes", "1",
         "-vf", f"crop={W}:{SLOT_H}:0:{ROW_TOP},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    return r.stdout


a_reel, a_bg = row(REEL, NEWEST), row(BG, NEWEST)
b_reel, b_bg = row(REEL, AFTER_NEXT), row(BG, AFTER_NEXT)
if not (a_reel and b_reel) or len(a_reel) != len(a_bg) or len(b_reel) != len(b_bg):
    print("could not sample the hook row")
    raise SystemExit(1)

def text_stats(reel: bytes, bg: bytes, thresh: int) -> tuple:
    vals = [r for r, g in zip(reel, bg) if r > thresh and g < 110]
    if not vals:
        return 0, 0
    return len(vals), sum(vals) // len(vals)

n_new, mean_new = text_stats(a_reel, a_bg, 180)
n_old, mean_old = text_stats(b_reel, b_bg, 180)
n_old_lo, _ = text_stats(b_reel, b_bg, 100)

print("=== hook row, text pixels only (bright in reel, dark in background) ===")
print(f"  while newest   (t={NEWEST}):  {n_new:6d} px above 180, mean brightness {mean_new}")
print(f"  after next line(t={AFTER_NEXT}):  {n_old:6d} px above 180, mean brightness {mean_old}")
print(f"  after next line(t={AFTER_NEXT}):  {n_old_lo:6d} px above 100 (the dimmed text)")
print()
if mean_new == 0:
    print("no text found in the hook row — cannot judge")
else:
    ratio = mean_old / mean_new if mean_new else 0
    print(f"  mean brightness retained: {100*ratio:.0f}%  (55% dim -> ~50-60% of 255)")
    dropped = n_old < n_new * 0.6 or mean_old < mean_new * 0.8
    print("  VERDICT:", "PASS - the older line dims when the next arrives" if dropped
          else "FAIL - older line is still at full alpha")