"""run_short.py — the v3 daily loop (§6, §9 step 5).

3 reels/day at 12:30 / 17:30 / 01:00 UTC. Per run:
  learner picks arms -> writer -> lint -> critic -> background per arm -> render
  -> render_gate -> [publish] -> record (REAL publish time) -> Telegram alert.

Keeps the proven plumbing untouched: publish.publish_reel, upload_host,
notify_send, state.commit_all. Publishing is OFF until the operator flips the
same two switches as before (workflow schedule + dry_run default).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import (alerts, build_short, config, generate_short, learn_short,
               lint_short, music, publish, render_gate, state, upload_host)

SLOTS_UTC = ["12:30", "17:30", "01:00"]


def _recent(memory: dict, key: str, n: int = 6) -> list:
    out = []
    for p in (memory.get("posts") or [])[-n:]:
        v = p.get(key)
        if isinstance(v, list):
            out.extend(str(x) for x in v)
        elif v:
            out.append(str(v))
    return out


def _recent_pov_count(memory: dict, n: int = 4) -> int:
    return sum(1 for p in (memory.get("posts") or [])[-n:]
               if p.get("template") == "T10" or "pov" in str(p.get("hook", "")).lower())


def build_one(memory: dict, dry_run: bool = True, verbose: bool = True,
              offline: bool = False) -> dict:
    """One full reel through the v3 pipeline. Returns the post record."""
    now = datetime.now(timezone.utc)
    slot = f"{now.hour}:{now.minute:02d}"

    # 0. kill-rule arms
    retired = learn_short.retired_arms(memory)
    if verbose and retired:
        print(f"[v3] retired arms (kill rule): {retired}", flush=True)

    # 1. the learner picks
    picks = learn_short.choose_v3(
        memory,
        recent_topics=[p.get("topic") for p in (memory.get("posts") or [])],
        recent_templates=[p.get("template") for p in (memory.get("posts") or [])],
        explore_rate=learn_short.exploration_rate(memory),
        recent_pov_count=_recent_pov_count(memory))
    template_code = picks.get("template", "T4")
    templates = {t["code"]: t for t in generate_short.templates()["templates"]}
    template = templates.get(template_code) or templates["T4"]
    topic = picks.get("topic") or generate_short.templates()["topics"][0]
    visual_arm = picks.get("visual_arm", "C")
    if visual_arm in retired.get("visual_arm", []):
        visual_arm = "C"
    audio_type = picks.get("audio_type", "soft_instrumental")
    length_bucket = picks.get("length_bucket", "8-10")
    if verbose:
        print(f"[v3] picked: {template_code} {topic} arm{visual_arm} "
              f"{audio_type} {length_bucket}", flush=True)

    # 2. write -> lint -> critic. A 3/day deployment can afford 5 drafts per slot:
    #    the critic's originality bar rejects ~2 of every 3 drafts, and a slot that
    #    dies after 3 attempts loses the whole posting window.
    res = generate_short.write_one(
        template, topic, visual_arm, audio_type, length_bucket,
        recent_lines=_recent(memory, "hook", 12) + _recent(memory, "payoff", 12),
        recent_pov_count=_recent_pov_count(memory), max_rewrites=4, verbose=verbose)
    if not res.get("ok"):
        print(f"[v3] REJECTED at {res.get('stage')}: {res.get('notes', '')[:140]}",
              flush=True)
        return {"ok": False, "stage": res.get("stage"), "slot": slot,
                "template": template_code, "topic": topic, "visual_arm": visual_arm}
    reel = res["reel"]
    if verbose:
        print(f"[v3] drafted (attempt {res['attempt']}): {reel['hook']!r} / "
              f"{reel['payoff']!r}", flush=True)

    # 3. audio (music.acquire writes outputs/track.mp3 and returns only the source
    #    name — the path is the FIXED contract, learned in the v2 session)
    ok_music, mmeta = music.acquire(config.load_config(),
                                    {"mood": audio_type, "music_mood": audio_type},
                                    config.load_strategy(), memory, None,
                                    float(reel.get("_dur") or 9.0), dry_run=dry_run)
    track = config.OUTPUTS / "track.mp3"
    if not (ok_music and track.exists()):
        track = None
    reel["_track_path"] = str(track) if track else ""

    # 4. render + gate
    built = build_short.build(reel, memory,
                              out_path=config.OUTPUTS / "reel_short.mp4",
                              offline=offline, verbose=verbose)
    if not built.get("ok"):
        print(f"[v3] RENDER FAILED at {built.get('stage')}: "
              f"{str(built.get('why'))[:200]}", flush=True)
        return {"ok": False, "stage": built.get("stage"), "slot": slot,
                "template": reel.get("template"), "topic": reel.get("topic"),
                "why": str(built.get("why"))[:200]}
    video = Path(built["path"])
    if verbose:
        print(f"[v3] gate PASS: {built['gate'].get('duration_s')}s "
              f"{built['gate'].get('w')}x{built['gate'].get('h')}", flush=True)

    # 5. record the post BEFORE publish claims (crash-safe ordering) — then publish
    record = {
        "ok": True, "format": "short2", "template": reel["template"],
        "topic": reel["topic"], "visual_arm": built.get("arm") or reel["visual_arm"],
        "audio_type": reel["audio_type"], "length_bucket": reel["length_bucket"],
        "slot": slot, "hook": reel["hook"], "payoff": reel["payoff"],
        "caption": reel["caption"], "hashtags": reel["hashtags"],
        "duration_s": built.get("duration_s"),
        "scene_queries": reel.get("scene_queries"),
        "created_at": now.isoformat(),
        "published_at_utc": None, "media_id": None,
        "critic": res.get("critic"), "attempt": res.get("attempt"),
        "bg": built.get("bg"),
    }
    return record


def publish_one(record: dict, video: Path, dry_run: bool = True) -> dict:
    """The proven publish path, unchanged. Returns the publish result."""
    cfg = config.load_config()
    # caption_for() joins content["hashtags"] with spaces itself, so pass the
    # list straight through — no pre-joined line needed.
    content = {
        "caption": record["caption"],
        "hashtags": record["hashtags"],
        "cta_type": "none",
        "cta_text": "",
        "keyword": "",
        "on_screen_text": f"{record['hook']} {record['payoff']}",
        "title": f"{record['hook']} {record['payoff']}",
    }
    result = publish.publish_reel(video, content, cfg, dry_run=dry_run)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--force", action="store_true",
                    help="ignore the PAUSE file and the daily cap (still dry unless publishing on)")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="render with no asset network calls (bundled background); "
                         "the writer/critic still need their endpoints")
    args = ap.parse_args(argv)

    if config.paused() and not args.force:
        print("[v3] paused (data/PAUSE exists)")
        return 0
    cfg = config.load_config()
    dry = args.dry_run or args.no_publish or cfg.get("short_publish_enabled", False) is False

    memory = config.load_memory()
    cap = int(cfg.get("short_daily_cap", 3))
    today = config.today_utc().isoformat()
    if not args.force:
        if str(memory.get("short_posts_today_date")) == today and \
                int(memory.get("short_posts_today_count") or 0) >= cap:
            print(f"[v3] daily cap {cap} reached")
            return 0

    record = build_one(memory, dry_run=dry, offline=args.offline)
    if not record.get("ok"):
        alerts.telegram(f"[v3] slot skipped at {record.get('stage')}: "
                    f"{str(record.get('why') or record.get('notes') or '')[:120]}")
        state.commit_all("v3: slot skipped")
        return 1

    video = config.OUTPUTS / "reel_short.mp4"
    result = {}
    if not dry:
        result = publish_one(record, video, dry_run=False)
        if result.get("media_id"):
            record["media_id"] = result["media_id"]
            record["published_at_utc"] = (result.get("published_at")
                                          or datetime.now(timezone.utc).isoformat())
        else:
            record["publish_error"] = str(result)[:300]
            alerts.telegram(f"[v3] publish failed: {str(result)[:160]}")
    else:
        record["dry_run"] = True

    # memory bookkeeping (v3 keys are separate from v2's)
    memory.setdefault("short_posts", [])
    memory["short_posts"].append(record)
    memory["posts"].append({**record,
                            "score": None})   # filled by the measure pass
    memory["short_posts_today_date"] = today
    memory["short_posts_today_count"] = int(
        memory.get("short_posts_today_count") or 0) + 1
    if str(memory.get("short_posts_today_date")) != today:
        memory["short_posts_today_count"] = 1
    config.save_memory(memory)
    state.commit_all(f"v3: {'dry ' if dry else ''}run — {record['template']} "
                     f"{record['topic']}")

    # the deliverable, to Telegram: the reel itself
    try:
        url, host = upload_host.publicize(video)
        record["public_url"] = url
        config.save_memory(memory)
        sent = alerts.telegram_video(video, caption=(f"v3 {record['template']} · "
                                          f"{record['topic']} · "
                                          f"{record['duration_s']}s\n"
                                          f"{record['hook']} / {record['payoff']}\n{url}"))
        # never silent: a False here used to look exactly like a success
        print(f"[v3] telegram delivery: {sent} ({url})", flush=True)
        record["telegram_delivered"] = bool(sent)
        config.save_memory(memory)
    except Exception as exc:  # noqa: BLE001
        print(f"[v3] telegram delivery failed: {exc}", flush=True)
    print(f"[v3] DONE ok=True url={record.get('public_url', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
