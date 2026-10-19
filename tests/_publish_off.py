"""Flip v3 publishing OFF (one deliberate switch). Usage: python3 tests/_publish_off.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
p = Path(__file__).resolve().parent.parent / "data" / "config.json"
cfg = json.loads(p.read_text(encoding="utf-8"))
cfg["short_publish_enabled"] = False
p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("short_publish_enabled ->", cfg["short_publish_enabled"])