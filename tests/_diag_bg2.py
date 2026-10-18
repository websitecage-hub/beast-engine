"""Run the FULL background.build path for a v3 reel to find the real failure."""
import sys, time
sys.path.insert(0, ".")
from src import config, background

cfg = config.load_config()
memory = config.load_memory()
content = {"scene_queries": ["phone glow face in dark bed", "awake in bed staring ceiling 3am"],
           "scene": "", "bg_type": "short", "cluster": "short",
           "archetype": "short", "topic": "short", "topic_label": "short"}
t0 = time.time()
try:
    path, source = background.build(cfg, content, memory, 9.0)
    print(f"OK in {time.time()-t0:.0f}s: {path} ({source})")
except Exception as exc:
    print(f"FAILED in {time.time()-t0:.0f}s: {exc}")
