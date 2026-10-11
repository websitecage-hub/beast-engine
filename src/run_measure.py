"""run_measure.py — collect the v2 metric snapshots -> prune -> commit.

Runs 3x/day (measure.yml). This is what feeds the learner, so it is the step that
decides whether Phase 2 learns from reality or from noise.

WHY IT NO LONGER CALLS harvest.harvest()

harvest.py scores with the OLD hand-weighted formula
(6*sends + 3*saved + 2*watch + ...) and stores metrics flat on the post. v2's
reward is a different function (ENGINE_SPEC §2.1) and reads a 24h SNAPSHOT. Leaving
the old scorer in the live path would mean the learner trained on numbers the spec
never defined, while every test of the new reward passed in isolation — green
tests, wrong behaviour. That is the exact failure this rewrite removes.

So: snapshots at +2h/+24h/+72h/+7d, feature-detected metric names, and the score
written only on the 24h snapshot, by learner.reward.

harvest.harvest() is kept (and still callable) so the legacy path stays auditable.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from . import config, harvest, insights, state


def collect(dry_run: bool = False) -> dict:
    """Walk the posts, fetch each due snapshot, score the 24h one."""
    memory = config.load_memory()
    now = datetime.now(timezone.utc)
    updated, skipped, errors, unavailable_seen = 0, 0, [], set()

    for post in memory.get("posts", []):
        name = insights.due_snapshot(post)
        if not name:
            continue
        media_id = post.get("media_id") or post.get("ig_media_id")
        if not media_id:
            continue
        metrics, available = insights.fetch(str(media_id))
        if not metrics:
            # No data yet (a very young post) or the route refused. Either way it
            # is NOT a zero score — leave `score` untouched and try next run.
            skipped += 1
            continue
        for want in insights.WANTED:
            if want not in available:
                unavailable_seen.add(want)
        if not dry_run:
            insights.record_snapshot(post, metrics, name)
            post["last_harvested"] = now.isoformat()
        updated += 1

    if not dry_run:
        try:
            insights.fetch_followers()
        except Exception as exc:  # noqa: BLE001
            errors.append({"followers": str(exc)[:160]})
        prune = harvest.prune(memory)
        config.save_memory(memory)
    else:
        prune = {}

    return {"updated": updated, "skipped": skipped, "errors": errors,
            "metrics_unavailable": sorted(unavailable_seen),
            "total_posts": len(memory.get("posts", [])), "pruned": prune}


def run(dry_run: bool = False) -> int:
    started = time.time()
    log = {"workflow": "measure", "started": datetime.now(timezone.utc).isoformat(),
           "dry_run": dry_run, "version": "v2"}
    try:
        res = collect(dry_run=dry_run)
        pruned = state.prune_logs()
        log.update({"collect": res, "logs_pruned": pruned,
                    "seconds": round(time.time() - started, 2)})
        print(f"[measure] {res}")
        state.write_log("measure", log)
        state.commit_all("measure: v2 snapshots", dry_run=dry_run)
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
    ap.add_argument("--legacy", action="store_true",
                    help="run the pre-v2 harvester instead (audit only)")
    args = ap.parse_args()
    if args.legacy:
        res = harvest.harvest(dry_run=args.dry_run)
        print(f"[measure] legacy {res}")
        return 0
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())