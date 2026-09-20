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
               publish, state)


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


def scheduler_check(cfg, strategy, memory, offline=False, force=False) -> tuple:
    """Returns (should_post, reason).

    force=True (workflow_dispatch, or BEAST_FORCE_POST=1) skips the warmup off-day rule
    and the +/-35min window, but NEVER the one-post-per-day idempotency check.
    """
    today = config.today_utc()
    if offline:
        return True, "offline"
    if memory.get("last_post_date") == today.isoformat():
        return False, "already posted today (idempotency)"
    warmup_until = strategy.get("warmup_until")
    try:
        in_warmup = today <= config.date.fromisoformat(str(warmup_until))
    except Exception:  # noqa: BLE001
        in_warmup = False
    if in_warmup and not force:
        if today.toordinal() % 2 != 0:
            return False, f"warmup (until {warmup_until}) — off day"
    if force:
        return True, "forced (manual dispatch)"
    target = strategy.get("next_post_hour") or "15:00"
    try:
        hh, mm = (int(x) for x in target.split(":"))
    except Exception:  # noqa: BLE001
        hh, mm = 15, 0
    now = datetime.now(timezone.utc)
    delta = abs((now.hour * 60 + now.minute) - (hh * 60 + mm))
    if delta > 35:
        return False, f"outside the +/-35min window for {target} (delta {delta}m)"
    return True, f"in slot {target}"


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
        jitter = float(tone) if tone is not None else random.uniform(0, 1200)
        step("jitter_start", {"seconds": round(jitter, 1)})
        if jitter > 0:
            time.sleep(jitter)
        if trending_thread is not None:
            trending_thread.join(timeout=30)
        step("trending_joined", {"error": bool(box and box.error)})

        force = (config.env("GITHUB_EVENT_NAME") == "workflow_dispatch"
                 or config.env("BEAST_FORCE_POST") == "1")
        ok, reason = scheduler_check(cfg, strategy, memory, offline=offline, force=force)
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
        step("background", {"source": bg_source})

        ok_music, mmeta = music.acquire(cfg, content, strategy, memory,
                                        content.get("trending_ref"), duration, dry_run=dry_run)
        if not ok_music:
            raise RuntimeError("music provider chain exhausted (even drone failed)")
        content["music_source"] = mmeta.get("music_source", "")
        step("music", {"source": content["music_source"]})

        reel, qa = build_video.build(cfg, content, duration, R / "track.mp3", R / "track.wav",
                                     bg_mp4, R / "reel.mp4", offline=offline)
        step("built", {"qa": {k: v for k, v in qa.items() if k != "cards"}})

        config.save_content(content)

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
            alerts.success(memory.get("post_counter"), content.get("hook", ""), reel)
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


def _record_post(memory, content, result, strategy):
    now = datetime.now(timezone.utc)
    slot = strategy.get("next_post_hour")
    memory.setdefault("posts", []).append({
        "media_id": result["media_id"],
        "container_id": result.get("container_id"),
        "created_at": now.isoformat(),
        "hour_slot": slot,
        "hook": content.get("hook"),
        "caption": result.get("caption"),
        "hashtags": content.get("hashtags"),
        "dna": {
            "archetype": content.get("archetype"),
            "topic": content.get("topic"),
            "mood": content.get("mood"),
            "bg_type": content.get("bg_type"),
            "exploit": content.get("exploit"),
            "bg_source": content.get("bg_source"),
            "music_source": content.get("music_source"),
            "trending_ref": content.get("trending_ref"),
        },
        "metrics": {},
        "score": None,
        "harvested": False,
    })
    memory["last_post_date"] = config.today_utc().isoformat()
    memory["post_counter"] = int(memory.get("post_counter", 0)) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run, offline=args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
