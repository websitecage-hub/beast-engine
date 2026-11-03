"""Repair data/*.json files carrying conflict markers (CLI shim).

Committed conflict markers in state files are what made the run manifest unreadable
and silently stopped later runs. The preview workflow calls this before committing.

Usage: python3 -m scripts.repair_data_json [dir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.formats.manifest_merge import strip_markers    # noqa: E402


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "data")
    repaired = dropped = 0
    for path in sorted(root.glob("*.json")):
        try:
            text = path.read_text()
        except OSError as exc:
            print(f"[repair] unreadable {path}: {exc}")
            continue
        if "<<<<<<<" not in text and ">>>>>>>" not in text:
            continue
        try:
            entries = json.loads(strip_markers(text))
        except Exception as exc:                     # noqa: BLE001
            print(f"[repair] cannot recover {path} ({exc}); dropping it from the commit")
            path.unlink()
            dropped += 1
            continue
        path.write_text(json.dumps(entries, indent=1, sort_keys=True))
        print(f"[repair] repaired {path}")
        repaired += 1
    print(f"[repair] repaired={repaired} dropped={dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
