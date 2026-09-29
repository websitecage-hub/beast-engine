"""run_create.py — the daily CREATE orchestrator.

Flags:
  --dry-run  real services, real build, NO publish, NO state mutation, NO telegram
  --offline  canned content + bundled bg + drone music, fully deterministic

Order: PAUSE -> wake services -> trending fetch in a background thread -> jitter
sleep -> join -> scheduler check -> generate -> background -> music -> build -> publish.
"""
from __future__ import annotations

import argparse
import random
import threading
import time
import traceback
from datetime import datetime, timezone

from . import (alerts, background, build_video, config, generate, llm, music,
               publish, seo, state)


class _Box:
    value: object = None
    error: object = None


def _start_trending(cfg) -> tuple:
    """Kick off the slowest call (first trending fetch of the day) in a thread."""
    box = _Box()

    def worker():
        try:
            box.value = music.fetch_trending(cfg)
        except Exception as exc:  # noqa: BLE001 — trending failure is never fatal
            box.error = exc

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t, box


# The exact times the create workflow's crons fire (UTC). Kept here so the scheduler
# and the publish pipeline agree on what "today's slot" means.
#
# GitHub's cron scheduler delays, coalesces and sometimes drops scheduled runs —
# observed ~6h late on this repo, which lost whole slots. Waiting for an exact
# posting time is therefore not reliable. The workflow now WAKES every 4 hours and
# the scheduler decides whether a post is due; a delayed wake still lands inside the
# day and publishes instead of the slot being lost.
#
# 4 hours is deliberate: comfortably longer than one create run (build + publish),
# and the workflow's concurrency group is not cancel-in-progress, so a wake-up can
# never collide with the previous run still working.
RUN_HOURS = ("01:30", "05:30", "09:30", "13:30", "17:30", "21:30")

# The two moments a post becomes due. Everything before the first is a quiet run:
# `due` is counted from these, so the overnight wakes (01:30-09:30 UTC) never publish.
#
#   post 1  due 13:30 UTC (19:00 IST)
#   post 2  due 17:30 UTC (23:00 IST)
#
# A post that is due but still missing is published by whichever later run sees it,
# so a failed or delayed build is picked up instead of the day being lost. The daily
# cap alone stops a third post, however many times we wake.
DUE_HOURS = ("13:30", "17:30")
POSTS_PER_DAY = len(DUE_HOURS)

# Every wake-up time, in order. The acceptance suite pins this to the workflow crons.
SLOT_HOURS = RUN_HOURS


def posts_today(memory, today=None) -> int:
    """How many posts have already gone out today.

    Needs its own per-day counter: `post_counter` counts every post ever, and
    last_post_date can only express "posted at least once today" — neither can say
    "one of today's two is done".
    """
    today = today or config.today_utc()
    if str(memory.get("posts_today_date") or "") != today.isoformat():
        return 0
    try:
        return int(memory.get("posts_today_count") or 0)
    except (TypeError, ValueError):
        return 0


def scheduler_check(cfg, strategy, memory, offline=False, force=False,
                    dry_run=False) -> tuple:
    """Returns (should_post, reason).

    force=True (workflow_dispatch, or BEAST_FORCE_POST=1) skips the warmup off-day
    rule and the slot window, but NEVER the two-posts-per-day cap.

    dry_run=True bypasses the cap. It exists to stop extra PUBLISHES; a dry-run
    publishes nothing and mutates no state, but it used to return before the render
    started, so the render path was unexercisable on any day that had already posted.
    Every real run still gets the cap, including forced ones.

    The day's two posts each have a due time. A run publishes when a post is due and
    not yet done, which is what makes the later wakes retries: if the 13:30 build
    fails or GitHub fires that cron late, a later run publishes instead of skipping.
    """
    today = config.today_utc()
    if offline:
        return True, "offline"

    used = posts_today(memory, today)
    if used >= POSTS_PER_DAY:
        if dry_run:
            return True, "dry-run (daily cap not applicable)"
        return False, f"daily cap reached ({used}/{POSTS_PER_DAY})"

    warmup_until = strategy.get("warmup_until")
    try:
        in_warmup = today <= config.date.fromisoformat(str(warmup_until))
    except Exception:  # noqa: BLE001
        in_warmup = False
    if in_warmup and not force:
        if today.toordinal() % 2 != 0:
            return False, f"warmup (until {warmup_until}) — off day"
    if force:
        return True, f"forced (manual dispatch) — post {used + 1}/{POSTS_PER_DAY}"

    now = datetime.now(timezone.utc)
    now_min = now.hour * 60 + now.minute
    due = sum(1 for t in DUE_HOURS if now_min >= _slot_minutes(t))
    wanted = min(due, POSTS_PER_DAY)

    if used < wanted:
        return True, (f"post {used + 1}/{POSTS_PER_DAY} due "
                      f"(nominal {DUE_HOURS[used]})")
    # No write-off cutoff on purpose. GitHub fires scheduled crons minutes late
    # (observed: a 19:00 slot starting at 19:07), so a strict "past the last slot"
    # test would reject the very run that is post 2's retry. Publishing whenever a
    # post is due and still missing is strictly safer: the cap above is what prevents
    # a third post, and no cron fires after the last slot anyway.
    return False, (f"post {used + 1}/{POSTS_PER_DAY} waits for its due time "
                   f"{DUE_HOURS[used]} (now {now.strftime('%H:%M')} UTC)")


def _slot_minutes(hhmm: str) -> int:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return hh * 60 + mm


def run(dry_run=False, offline=False) -> int:
    config.ensure_data_files()
    started = time.time()
    log = {"workflow": "create", "started": datetime.now(timezone.utc).isoformat(),
           "dry_run": dry_run, "offline": offline, "steps": [], "errors": []}

    def step(name, extra=None):
        entry = {"step": name, "t": round(time.time() - started, 2)}
        if extra:
            entry.update(extra)
        log["steps"].append(entry)
        print(f"[create] {name} {extra or ''}")

    if config.paused():
        step("paused")
        state.write_log("create", log)
        return 0

    cfg = config.load_config()
    strategy = config.load_strategy()
    memory = config.load_memory()
    R = config.OUTPUTS
    R.mkdir(parents=True, exist_ok=True)

    try:
        if not offline:
            llm.wake()
            from . import pinterest
            pinterest.wake()
            music.wake(cfg["music"]["trending_api"])
        step("services_awake")

        trending_thread, box = (None, None)
        tone = config.env("BEAST_JITTER_SECONDS")
        if not offline:
            trending_thread, box = _start_trending(cfg)
        jitter = 0.0 if offline else (float(tone) if tone is not None
                                      else random.uniform(0, 1200))

        force = (config.env("GITHUB_EVENT_NAME") == "workflow_dispatch"
                 or config.env("BEAST_FORCE_POST") == "1")

        # Pre-check BEFORE sleeping. The jitter exists to look human, but it was
        # running first, so a run that was always going to skip still burned up to
        # 20 minutes of CI (observed: jitter_start 1064.5s, then "already posted
        # today") against a 30-minute job timeout. Deciding first costs nothing and
        # keeps the human-looking delay on the runs that actually post.
        #
        # dry_run bypasses the one-post-per-day guard. That guard exists to stop a
        # second PUBLISH, and a dry-run publishes nothing and mutates no state — but
        # it was returning "already posted today" before the render ever ran, so the
        # render path could not be exercised on any day that had already posted
        # (which is most days, since the day's first success sets it). The guard is
        # untouched for every real run.
        ok, reason = scheduler_check(cfg, strategy, memory,
                                     offline=offline, force=force, dry_run=dry_run)
        if not ok:
            step("scheduler", {"post": False, "reason": reason, "pre_jitter": True})
            log["result"] = "skipped"
            state.write_log("create", log)
            state.commit_all(f"create: skip ({reason})", dry_run=dry_run)
            return 0

        step("jitter_start", {"seconds": round(jitter, 1)})
        if jitter > 0:
            time.sleep(jitter)
        if trending_thread is not None:
            trending_thread.join(timeout=30)
        step("trending_joined", {"error": bool(box and box.error)})

        # Re-check after the delay: the sleep can be long enough for another run to
        # have posted, and the one-post-per-day cap must still hold. dry_run must be
        # passed here too — this second call is the one that actually gates the build,
        # so omitting it made the dry-run skip exactly like a real run.
        ok, reason = scheduler_check(cfg, strategy, memory, offline=offline,
                                     force=force, dry_run=dry_run)
        step("scheduler", {"post": ok, "reason": reason})
        if not ok:
            log["result"] = "skipped"
            state.write_log("create", log)
            state.commit_all(f"create: skip ({reason})", dry_run=dry_run)
            return 0

        duration = build_video.pick_duration(cfg)
        step("duration", {"seconds": duration})

        content = generate.generate(dry_run=dry_run, offline=offline)
        if box and box.value:
            content["trending_ref"] = box.value
        step("generated", {"hook": content.get("hook"), "exploit": content.get("exploit")})

        bg_mp4, bg_source = background.build(cfg, content, memory, duration,
                                             offline=offline, dry_run=dry_run)
        content["bg_source"] = bg_source
        content["bg_is_video"] = bg_source in ("pinterest_video",)
        # SEO §6: the alt text describes the VISUAL, so it can only be finalised once
        # the background scene is actually chosen. Refresh it here, before the reel is
        # written to content.json, so publish always ships a visual description.
        from . import seo as _seo
        content["alt_text"] = _seo.build_alt_text(
            scene=content.get("scene", ""), topic=content.get("topic", ""),
            hook=content.get("hook", ""), bg_source=bg_source,
            on_screen_text=content.get("onscreen_text", "") or content.get("on_screen_text", ""))
        step("background", {"source": bg_source, "is_video": content["bg_is_video"],
                            "alt_text": content["alt_text"][:70] + "..."})

        ok_music, mmeta = music.acquire(cfg, content, strategy, memory,
                                        content.get("trending_ref"), duration, dry_run=dry_run,
                                        offline=offline)
        if not ok_music:
            raise RuntimeError("music provider chain exhausted (even drone failed)")
        content["music_source"] = mmeta.get("music_source", "")
        step("music", {"source": content["music_source"]})

        reel, qa = build_video.build(cfg, content, duration, R / "track.mp3", R / "track.wav",
                                     bg_mp4, R / "reel.mp4", offline=offline)
        step("built", {"qa": {k: v for k, v in qa.items() if k != "cards"}})

        # Part 5.6 — "Video bg: motion present (not static)" is mandatory when we
        # are actually going to publish. Offline mode knowingly uses the bundled
        # still for hermetic tests, so the motion check only gates real runs.
        if not offline and not qa.get("motion", True):
            raise RuntimeError(
                f"QA gate failed: background has no motion (source={content.get('bg_source')}) "
                "— Part 5.2 requires dark cinematic VIDEO")
        if not offline and bg_source != "pinterest_video":
            print(f"[create] WARNING: bg_source={bg_source} is not video "
                  "(Part 5.2 format requires video)")

        config.save_content(content)

        # SEO §9: the checklist is asserted before publishing, not just reported. A reel
        # that ships without its keyword in the first caption line or without alt text is
        # unreachable by search, and that failure is silent — so it gates the publish.
        seo_check = seo.checklist(content)
        step("seo", seo_check)
        failed = [k for k, v in seo_check.items() if not v]
        if failed:
            print(f"[create] SEO CHECKLIST: {len(failed)} unmet -> {failed}")
        else:
            print("[create] SEO checklist: all green")

        if not dry_run and not offline:
            _record_track(memory, mmeta)
            config.save_memory(memory)

        result = publish.publish_reel(reel, content, cfg, dry_run=(dry_run or offline))
        step("published", result)
        log["result"] = result

        if not dry_run and not offline and result.get("media_id"):
            _record_post(memory, content, result, strategy)
            config.save_memory(memory)
            state.write_log("create", log)
            state.commit_all(f"post: {result['media_id']}")
            alerts.success(memory.get("post_counter"), content.get("hook", ""), reel,
                           manual_steps=result.get("manual_steps"))
            return 0

        state.write_log("create", log)
        if dry_run or offline:
            state.commit_all("create: dry-run log", dry_run=True)
        print(f"[create] finished in {time.time() - started:.1f}s")
        return 0

    except PermissionError as exc:
        step("token_dead", {"error": str(exc)})
        log["errors"].append(str(exc))
        if not dry_run:
            alerts.fail("TOKEN DEAD — regenerate in the developer dashboard.", str(exc))
        state.write_log("create", log)
        state.commit_all("create: token dead", dry_run=dry_run)
        return 1
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        step("failed", {"error": str(exc)})
        log["errors"].append(str(exc))
        print(tb)
        if not dry_run:
            alerts.fail(str(exc), tb)
        state.write_log("create", log)
        state.commit_all("create: failure log", dry_run=dry_run)
        return 1


def _record_track(memory, mmeta):
    if mmeta.get("url"):
        memory.setdefault("used_track_urls", []).append(
            {"url": mmeta["url"], "date": config.today_utc().isoformat()})
    if mmeta.get("track"):
        memory.setdefault("used_tracks", []).append(
            {"track": mmeta["track"], "date": config.today_utc().isoformat()})
    # The spectral PROFILE is what actually prevents repeats. URL-only memory missed
    # the identical track arriving under a different name, and a raw-PCM hash missed
    # it too because the decode path differed — which is how two consecutive reels
    # shipped the same audio.
    prof = mmeta.get("profile")
    if prof is not None:
        memory.setdefault("used_track_profiles", []).append(
            {"profile": list(prof), "date": config.today_utc().isoformat(),
             "source": mmeta.get("music_source") or "",
             "query": mmeta.get("music_query") or mmeta.get("query") or ""})

    # Remember the QUERY too. The audio service resolves a query to one track
    # deterministically, so never repeating a query is the primary way to guarantee
    # unique music on every post; the spectral profile above is the backstop for the
    # case where two different phrases resolve to the same file.
    q = (mmeta.get("music_query") or mmeta.get("query") or "").strip()
    if q:
        used_q = memory.setdefault(music.USED_QUERIES_KEY, [])
        if q.lower() not in {str(x).strip().lower() for x in used_q}:
            used_q.append(q)

    # Backfill: runs that predate the query memory recorded their query only inside the
    # used_track_profiles entry. Without this, those older queries stay in the fresh
    # pool and can be handed out again — the very repetition this is meant to stop.
    known = {str(x).strip().lower() for x in (memory.get(music.USED_QUERIES_KEY) or [])}
    for e in (memory.get("used_track_profiles") or []):
        prev = str((e or {}).get("query") or "").strip()
        if prev and prev.lower() not in known:
            memory.setdefault(music.USED_QUERIES_KEY, []).append(prev)
            known.add(prev.lower())


def _record_post(memory, content, result, strategy):
    """Part 7.1 — store the reel's full genetic code."""
    now = datetime.now(timezone.utc)
    slot = strategy.get("next_post_hour")
    hook = content.get("hook") or ""
    memory.setdefault("posts", []).append({
        "media_id": result["media_id"],
        "container_id": result.get("container_id"),
        "created_at": now.isoformat(),
        "hour_slot": slot,
        "hook_text": hook,
        "hook": hook,
        "keyword": content.get("keyword") or "",
        # Spec §2: the topic is tracked on the POST (not only in the DNA) because
        # topic rotation reads it back to exclude the last three topics.
        "topic": content.get("topic_label") or content.get("topic"),
        "caption": result.get("caption"),
        "hashtags": content.get("hashtags"),
        "dna": {
            # Part 7.1 genetic code
            "hook_text": hook,
            "cluster": content.get("cluster") or content.get("archetype"),
            "topic": content.get("topic"),
            "hook_length_chars": len(hook),
            "num_text_blocks": len(content.get("blocks") or []),
            "landing_text": content.get("landing") or "",
            "bg_type": content.get("bg_type"),
            "bg_is_video": bool(content.get("bg_is_video")),
            "audio_mood": content.get("mood"),
            "audio_from_trending": content.get("music_source") == "trending_free",
            "trending_ref": content.get("trending_ref"),
            "loop_technique": content.get("loop_technique"),
            # SEO §8: recorded per post so the weekly report can measure whether the
            # search fields were actually shipped, instead of trusting that they were.
            "alt_text": content.get("alt_text") or result.get("alt_text") or "",
            "onscreen_text": content.get("onscreen_text") or "",
            "keyword": content.get("keyword") or "",
            "hashtags": content.get("hashtags") or [],
            "posted_hour": slot,
            "include_cta": bool(content.get("include_whisper")),
            "exploit": content.get("exploit"),
            # legacy aliases kept so older analysis paths keep working
            "archetype": content.get("cluster") or content.get("archetype"),
            "mood": content.get("mood"),
            "bg_source": content.get("bg_source"),
            "music_source": content.get("music_source"),
        },
        "metrics": {},
        "score": None,
        "harvested": False,
    })
    memory["last_post_date"] = config.today_utc().isoformat()
    memory["post_counter"] = int(memory.get("post_counter", 0)) + 1
    # Per-day cap counter: reset when the date rolls over, then increment. This is what
    # lets a second post through on the same day while still refusing a third.
    today = config.today_utc().isoformat()
    if str(memory.get("posts_today_date") or "") != today:
        memory["posts_today_date"] = today
        memory["posts_today_count"] = 0
    memory["posts_today_count"] = int(memory.get("posts_today_count") or 0) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run, offline=args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
