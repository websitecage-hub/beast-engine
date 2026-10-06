"""run_confess.py — the daily confession (ENGINE_SPEC.md "Daily loop").

One run per day, in time to publish at 23:30 UTC or to skip cleanly. The run
either produces a reel or it skips, and **a skipped day is a success** — the
spec is explicit that a filler post is how the account stays at 100 reach.

The loop, in the spec's order, with the skip conditions it demands:

  1. load weights, the bank, the last 40 posts, the last 14 scorecards
  2. pick a scene (weighted, not used in 40, object not more than twice in 10)
  3. writer -> one draft
  4. critic -> one retry -> pass or SKIP THE SLOT
  5. Pinterest -> reject baked-in text, logo, big face, missing object;
     two attempts -> then SKIP
  6. render: lines on the beat, line one by 1.2s, last line held >= 2.5s,
     16-20s, slow push-in, text in the middle two-thirds, no watermark
  7. attach the bed, or silence
  8. publish at 23:30 UTC, caption == the last line
  9. store the row
 10. any failure -> skip the day, log the reason, do NOT fall back to a generic reel

WHY THIS IS A SEPARATE RUNNER AND NOT A REWRITE OF run_create: the old
pipeline is 2-posts-a-day at two due times with a comment-keyword CTA and a
video background. The strategy reverses all four. Keeping the old runner intact
means the account can be switched back by changing which workflow is enabled,
and the old gates/tests stay meaningful instead of being deleted.
"""
from __future__ import annotations

import argparse
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import bed as bed_mod
from . import build_video, confession, config, language, publish, state, still

PUBLISH_AT = "23:30"          # UTC, fixed by the strategy
DUE_MINUTES = 23 * 60 + 30
# The recovery window. GitHub's crons on this repo are measured running hours
# late and sometimes dropping, so a single 23:30 wake loses the day when it
# misfires. These wakes are the retry: if the 23:35 run fails, the 01:30 run
# publishes the SAME post.
#
# A posting day therefore SPANS MIDNIGHT: day D's post is due from 23:30 on D
# until RECOVERY_END on D+1. With a plain calendar-date check the 01:30 wake
# would look at a brand-new date, see "not due until 23:30", and do nothing —
# silent no-op that looks like a healthy run. That is the failure class where the
# write-off rule rejects the very run meant to be the retry.
RECOVERY_END_MINUTES = 4 * 60 + 30      # 04:30


def posting_day(now: datetime) -> str:
    """Which day's post this moment belongs to (ISO date).

    Up to and INCLUDING the last recovery wake, the current moment still belongs
    to YESTERDAY's post. The boundary is inclusive on purpose: the workflow's
    final recovery cron fires at exactly 04:30, and an exclusive test made that
    very wake decide the new day had not come due yet and publish nothing.
    """
    if now.hour * 60 + now.minute <= RECOVERY_END_MINUTES:
        return (now.date() - timedelta(days=1)).isoformat()
    return now.date().isoformat()


def _slot_minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def should_run(now: datetime, cfg: dict, memory: dict, force: bool = False,
               dry_run: bool = False) -> tuple:
    """(ok, reason). One post a day maximum, at 23:30 UTC.

    Never catches up a missed day with two posts: a missed day stays missed,
    because bursting looks automated and gives the model two chances to
    downrank the account in one session. The cap is per POSTING DAY, so the
    overnight recovery wakes cannot publish a second reel.
    """
    day = posting_day(now)
    posted = int(memory.get("posts_today_count") or 0) if \
        str(memory.get("posts_today_date")) == day else 0
    if posted >= 1:
        if dry_run:
            return True, "dry-run (daily cap not applicable)"
        return False, f"already posted for {day} ({posted}/1)"
    if force or dry_run:
        return True, "forced" if force else "dry-run"

    now_min = now.hour * 60 + now.minute
    in_recovery = now_min <= RECOVERY_END_MINUTES
    if now_min < DUE_MINUTES and not in_recovery:
        return False, f"waits for {PUBLISH_AT} UTC (now {now.strftime('%H:%M')} UTC)"
    if in_recovery:
        return True, (f"recovery window for {day} (now "
                      f"{now.strftime('%H:%M')} UTC, before {RECOVERY_END_MINUTES // 60}:"
                      f"{RECOVERY_END_MINUTES % 60:02d})")
    return True, f"due ({PUBLISH_AT} UTC)"


def run(dry_run: bool = False, offline: bool = False, force: bool = False) -> int:
    started = time.time()
    log = {"workflow": "confess", "started": datetime.now(timezone.utc).isoformat(),
           "dry_run": dry_run, "offline": offline, "steps": [], "errors": []}

    def step(name, extra=None):
        entry = {"step": name, "t": round(time.time() - started, 2)}
        if extra:
            entry.update(extra)
        log["steps"].append(entry)
        print(f"[confess] {name} {extra or ''}")

    def skip(reason, **extra):
        step("skipped", {"reason": reason, **extra})
        log["result"] = {"skipped": True, "reason": reason}
        state.write_log("confess", log)
        state.commit_all(f"confess: skip ({reason})", dry_run=dry_run)
        print(f"[confess] SKIPPED: {reason}")
        return 0     # a skipped day is a SUCCESS, not an error

    cfg = config.load_config()
    memory = config.load_memory()
    strategy = config.load_strategy()
    now = datetime.now(timezone.utc)

    ok, reason = should_run(now, cfg, memory, force=force, dry_run=dry_run)
    step("scheduler", {"post": ok, "reason": reason, "now": now.strftime("%H:%M")})
    if not ok:
        return skip(reason)

    # 2. pick a scene
    row = language.pick(memory, strategy)
    if not row:
        return skip("bank exhausted — no eligible scene (wait, do not loosen the test)")
    step("scene", {"id": row["id"], "family": row["family"], "object": row["object"]})

    # 3-4. writer -> bans -> critic -> one retry
    draft = confession.write(row, memory, strategy, dry_run=dry_run)
    if not draft.get("ok"):
        return skip(f"draft rejected ({draft.get('stage')}): {draft.get('error')}",
                    scene=row["id"])
    lines = confession.lines_of(draft["onscreen_text"])
    duration = still.duration_for(len(lines))
    step("drafted", {"lines": len(lines), "attempt": draft.get("attempt"),
                     "duration": duration, "last_line": lines[-1][:80]})

    # 5. the still — two attempts, then skip. Never words over the wrong picture.
    shot = still.fetch_still(cfg, draft, row, memory, offline=offline)
    if not shot.get("ok"):
        return skip(f"no acceptable still: {shot.get('reason')}", scene=row["id"])
    step("still", {"pin_id": shot["pin_id"], "query": shot["query"]})

    # 6-7. render lines on the beat, then attach the bed or go silent
    out_dir = config.OUTPUTS
    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = out_dir / "confession.mp4"
    beds = bed_mod.choose(cfg, draft, memory, duration, out_dir=out_dir, offline=offline)
    step("bed", {"source": beds.get("source"), "kind": beds.get("kind"),
                 "reason": beds.get("reason")})
    times = still.line_times(len(lines), duration)
    try:
        still.build_reel(Path(shot["path"]), lines, beds.get("path"), out_mp4,
                         cfg, duration)
    except Exception as exc:  # noqa: BLE001
        return skip(f"render failed: {exc}", scene=row["id"])

    qa_ok, qa = still.qa({"ok": True, "duration": duration, "times": times})
    step("rendered", {"qa": qa, "path": str(out_mp4)})
    if not qa_ok:
        return skip(f"render gate failed: {qa.get('problems')}", scene=row["id"])

    # 8. publish, caption == the last line
    if dry_run:
        step("dry_run", {"would_publish": draft["caption"][:120]})
        log["result"] = {"dry_run": True, "scene": row["id"], "lines": lines}
        state.write_log("confess", log)
        print("[confess] dry-run complete")
        return 0

    try:
        res = publish.publish_reel(out_mp4, draft["caption"], cfg)
        media_id = res.get("media_id") or res.get("id")
        if not media_id:
            raise RuntimeError(f"publish returned no media id: {res}")
    except Exception as exc:  # noqa: BLE001
        step("publish_failed", {"error": str(exc)})
        log["errors"].append(str(exc))
        log["result"] = {"published": False, "error": str(exc)}
        state.write_log("confess", log)
        state.commit_all("confess: publish failed", dry_run=dry_run)
        return 1
    step("published", {"media_id": media_id})

    # 9. store the row
    post = {
        "media_id": media_id,
        "created_at": now.isoformat(),
        "hook": lines[0],
        "hook_text": lines[0],
        "caption": draft["caption"],
        "hashtags": [],
        "topic": row["object"],
        "keyword": "",
        "hour_slot": PUBLISH_AT,
        "metrics": {},
        "score": None,
        "harvested": False,
        "dna": {
            **language.record_use(memory, row),
            "onscreen_text": draft["onscreen_text"],
            "line_count": len(lines),
            "last_line": lines[-1],
            "last_line_shape": draft.get("last_line_shape"),
            "line_times": times,
            "duration_s": duration,
            "audio_source": beds.get("source"),
            "audio_kind": beds.get("kind"),
            "pin_id": shot.get("pin_id"),
            "pin_query": shot.get("query"),
            "format": "confession_v1",
            "critic": draft.get("critic"),
        },
    }
    memory.setdefault("posts", []).append(post)
    memory["used_pins"] = list({*(memory.get("used_pins") or []), str(shot.get("pin_id"))})
    memory["post_counter"] = int(memory.get("post_counter") or 0) + 1
    memory["last_post_date"] = now.date().isoformat()
    # Stored under the POSTING day, not the calendar day: the recovery wakes run
    # after midnight and must see this post as already made for that day.
    memory["posts_today_date"] = posting_day(now)
    memory["posts_today_count"] = 1
    memory.pop("last_keyword", None)      # no comment-keyword layer any more
    config.save_memory(memory)

    log["result"] = {"published": True, "media_id": media_id, "scene": row["id"],
                     "seconds": round(time.time() - started, 1)}
    state.write_log("confess", log)
    state.commit_all(f"confess: post {media_id}", dry_run=dry_run)
    print(f"[confess] published {media_id}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    return run(dry_run=a.dry_run, offline=a.offline, force=a.force)


if __name__ == "__main__":
    raise SystemExit(main())