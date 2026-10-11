"""Render the stage-1 reel WITH the music track, then gate it (audio expected)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from src import build_video, config, render_gate  # noqa: E402

st = json.loads(Path("outputs/v2_stage1.json").read_text())
cfg = config.load_config()
content = st["content"]
bg = Path(st["bg"])
content["bg_is_video"] = str(bg).lower().endswith((".mp4", ".mov", ".webm", ".mkv"))
content["bg_source"] = st["bg_source"]
audio = Path("outputs/track.mp3")
assert audio.is_file() and audio.stat().st_size > 4096, f"no usable track: {audio}"
dur = float(content["duration_s"])
out = Path("outputs/reel_v2_audio.mp4")

t0 = time.time()
build_video.build(cfg, content, dur, audio, None, bg, out)
print(f"rendered {time.time() - t0:.0f}s -> {out} ({out.stat().st_size/1048576:.1f} MB)")

ok, g = render_gate.check(out, expect_audio=True)
print(render_gate.describe(g))
print("audio stream present:", g.get("audio"))