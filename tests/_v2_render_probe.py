"""Render gold sample 1 in the v2 paced format over a real background clip."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from src import build_video, config, lint, paced, render_gate, writer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "src", "sample_reels.json"), encoding="utf-8") as f:
    samples = json.load(f)

cfg = config.load_config()
memory = config.load_memory()
reel = samples[0]
content = writer.to_content(reel, memory)
print("series:", content["series"], "| layout:", content["layout"])
print("hook:", content["hook"])
print("beats:", len(content["beats"]))
print("duration from schedule:", content["duration_s"], "s")

# A background clip: reuse the one background.build already produced, else offline.
bg = Path("outputs/bg.mp4")
if not bg.exists():
    print("no outputs/bg.mp4 — using the bundled fallback still")
    bg = config.ROOT / "assets" / "fallback" / "bg_default.jpg"
    content["bg_is_video"] = False
else:
    content["bg_is_video"] = True
print("background:", bg, "| video:", content["bg_is_video"])

out = Path("outputs/proof/v2_paced_sample1.mp4")
t0 = time.time()
info = build_video.build(cfg, content, content["duration_s"], None, None, bg, out)
print(f"\nrendered in {time.time()-t0:.0f}s -> {info['path']}")
print("starts:", info["starts"], "duration:", info["duration_s"])

ok, g = render_gate.check(out, expect_audio=False)
print("upload gate:", render_gate.describe(g))