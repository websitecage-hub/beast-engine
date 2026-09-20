"""run_measure.py — harvest insights -> prune memory -> commit."""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from . import config, harvest, state


def run(dry_run: bool = False) -> int:
    started = time.time()
    log = {"workflow": "measure", "started": datetime.now(timezone.utc).isoformat(),
           "dry_run": dry_run}
    try:
        res = harvest.harvest(dry_run=dry_run)
        pruned = state.prune_logs()
        log.update({"harvest": res, "logs_pruned": pruned,
                    "seconds": round(time.time() - started, 2)})
        print(f"[measure] {res}")
        state.write_log("measure", log)
        state.commit_all("measure: harvest insights", dry_run=dry_run)
        return 0
    except Exception as exc:  # noqa: BLE001
        log["error"] = str(exc)
        print(f"[measure] failed: {exc}")
        state.write_log("measure", log)
        state.commit_all("measure: failure log", dry_run=dry_run)
        return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
