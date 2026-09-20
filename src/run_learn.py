"""run_learn.py — weekly analysis -> strategy + REPORT.md -> commit."""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from . import analyze, config, state


def run(dry_run: bool = False) -> int:
    started = time.time()
    log = {"workflow": "learn", "started": datetime.now(timezone.utc).isoformat(),
           "dry_run": dry_run}
    try:
        res = analyze.analyze(dry_run=dry_run)
        state.prune_logs()
        log.update({"analyze": res, "seconds": round(time.time() - started, 2)})
        print(f"[learn] {res}")
        state.write_log("learn", log)
        state.commit_all("learn: strategy update + report", dry_run=dry_run)
        return 0
    except Exception as exc:  # noqa: BLE001
        log["error"] = str(exc)
        print(f"[learn] failed: {exc}")
        state.write_log("learn", log)
        state.commit_all("learn: failure log", dry_run=dry_run)
        return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
