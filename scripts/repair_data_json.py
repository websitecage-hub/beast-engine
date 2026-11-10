"""Repair (or restore) JSON state files that carry conflict markers.

Committed conflict markers in state files are what made the run manifest unreadable
and silently stopped later runs. The preview workflow calls this before committing.

Shape matters: the manifest is a LIST of run entries and can be unioned, but the asset
ledger is a DICT and must never be rewritten as a list. Measured: a blanket repair
turned data/asset_ledger.json into `[]` and every run then died with
"'list' object has no attribute 'setdefault'". Only known list-shaped files are unioned;
anything else is restored from git (or dropped) so the next run starts from a valid file.

Usage: python3 -m scripts.repair_data_json [dir]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.formats.manifest_merge import strip_markers    # noqa: E402

LIST_SHAPED = {"template_manifest.json"}


def _restore(path: Path) -> bool:
    """Bring a tracked file back to its committed version."""
    try:
        r = subprocess.run(["git", "checkout", "--", str(path)],
                           capture_output=True, text=True, timeout=60)
    except Exception:                                    # noqa: BLE001
        return False
    return r.returncode == 0


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "data")
    repaired = dropped = restored = 0
    for path in sorted(root.glob("*.json")):
        try:
            text = path.read_text()
        except OSError as exc:
            print(f"[repair] unreadable {path}: {exc}")
            continue
        if "<<<<<<<" not in text and ">>>>>>>" not in text:
            continue
        if path.name in LIST_SHAPED:
            try:
                entries = json.loads(strip_markers(text))
            except Exception as exc:                     # noqa: BLE001
                print(f"[repair] cannot recover {path} ({exc}); dropping it")
                path.unlink()
                dropped += 1
                continue
            path.write_text(json.dumps(entries, indent=1, sort_keys=True))
            print(f"[repair] unioned {path}")
            repaired += 1
            continue
        # A dict-shaped state file cannot be unioned; a textual repair would corrupt it.
        if _restore(path):
            print(f"[repair] restored {path} from git")
            restored += 1
        else:
            print(f"[repair] dropping untracked conflicted file {path}")
            path.unlink()
            dropped += 1
    print(f"[repair] repaired={repaired} restored={restored} dropped={dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))