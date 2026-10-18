"""Diagnose why arm B (Pinterest) fell back to arm C in the sample run."""
import sys
sys.path.insert(0, ".")
from src import config, pinterest

cfg = config.load_config()
queries = ["awake in bed staring ceiling 3am", "phone glow face in dark bed",
           "person content alone window rain"]
for q in queries[:2]:
    try:
        results = pinterest.search_videos(q)
        print(f"query {q!r}: {len(results or [])} results")
        for e in (results or [])[:3]:
            print("   keys:", sorted(e.keys())[:8])
            print("   best_video:", str(e.get("best_video"))[:60])
            break
    except Exception as exc:
        print(f"query {q!r}: EXCEPTION {exc}")
