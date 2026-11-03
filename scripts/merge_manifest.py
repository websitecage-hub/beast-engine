"""CLI shim for the git merge driver (see src/formats/manifest_merge.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.formats.manifest_merge import main   # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
