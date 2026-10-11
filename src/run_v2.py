"""run_v2.py — the BEAST ENGINE v2 daily loop.

One run = one post. Everything the spec asks for happens here in order, and every
step that can fail fails CLOSED: a slot with no acceptable post is SKIPPED and
logged, never filled with a fallback.

    pause/rails -> slot due? -> select arms (learner) -> writer -> lint ->
    critic -> background (pinterest|pexels) -> render (paced) -> upload gate ->
    publish -> record -> memory

WHY THIS IS A NEW ENTRY POINT RATHER THAN run_create.run()

run_create.py is the proven plumbing and is left untouched: publish_reel, the
Telegram report, the state commit and the background/music acquisition all still
do exactly what they did. This module is the v2 CONTENT decision layer around
them. Rewriting run_create in place would put the working pipeline at risk for no
gain — the failure mode of that is a live account that stops posting.

Differences from the old loop that matter:
  * the arms come from the learner, not from strategy.json's weights
  * the format is the paced reveal, so duration comes from reveal_schedule
  * the reward, not a hand-weighted formula, decides what is good
  * hard constraints (POV cap, series, topic cooldown, CTA alternation) are
    applied BEFORE the draw, so a winning arm can never break a safety rule
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import (background, build_video, carousel, config, insights, learner,
               lint, music, paced, pexels, platform, provenance, publish,
               render_gate, state, strategy_v2, voiceover, writer)

PUBLISH_FMT = "paced_text"


def _step(log: list, name: str, payload: dict | None = None) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(), "step": name}
    if payload:
        entry.update(payload)
    log.append(entry)
    print(f"[v2] {name}: {payload if payload else ''}")


def _notify(text: str) -> None:
    """Telegram alert. Reads the same env/secrets the proven path uses.

    A failure here must never take the run down: the alert is how the owner finds
    out something went wrong, not the thing that makes it worse.
    """
    try:
        from . import notify_send
        import os
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (token and chat):
            env = Path.home() / ".beast-secrets" / "telegram.env"
            if env.exists():
                for line in env.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    # the file stores `export KEY=value` — a literal parse keeps
                    # "export " in the key and finds nothing, which is how the
                    # sample-reel send failed with 404 before this
                    if line.startswith("export "):
                        line = line[len("export "):]
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == "TELEGRAM_BOT_TOKEN":
                        token = token or v.strip().strip('"').strip("'")
                    elif k.strip() == "TELEGRAM_CHAT_ID":
                        chat = chat or v.strip().strip('"').strip("'")
        if token and chat:
            notify_send.send(token, chat, text)
        else:
            print(f"[v2] notify skipped (no telegram creds): {text[:120]}")
    except Exception as exc:  # noqa: BLE001
        print(f"[v2] notify failed: {exc}")


def slot_of(cfg: dict, now: datetime) -> str | None:
    """Which slot this run is for. A slot is due from its time to +3h.

    The +3h window is what makes GitHub's late scheduler survivable: a 00:30 wake
    that fires at 03:00 still lands inside its own window rather than being lost.
    """
    slots = cfg.get("slots_utc") or ["00:30"]
    minutes = now.hour * 60 + now.minute
    for s in slots:
        try:
            hh, mm = (int(x) for x in str(s).split(":"))
        except (ValueError, TypeError):
            continue
        start = hh * 60 + mm
        if start <= minutes <= start + 180:
            return str(s)
    return None


def _recent(memory: dict, n: int) -> list:
    return [p for p in (memory.get("posts") or [])][-n:]


def select_arms(cfg: dict, memory: dict, rails: dict) -> dict:
    """The learner's choice for every arm, with the hard constraints applied.

    Order matters: `allowed` is built first and `choose` only ever sees arms the
    constraints permit. Drawing first and filtering after would silently bias the
    explorer toward whichever arms happen to survive the filter.
    """
    posts = memory.get("posts") or []
    recent = _recent(memory, 12)
    recent_hooks = [p.get("hook") for p in posts if p.get("hook")]
    recent_series = [p.get("series") for p in recent if p.get("series")]
    recent_topics = [p.get("topic") for p in _recent(memory, 12) if p.get("topic")]
    last_cta = (recent[-1].get("cta_type") if recent else "") or ""

    arms, G = learner.arm_stats(memory)
    explore = float(rails.get("explore_rate") or cfg.get("explore_rate") or 0.30)

    seed = writer._seed()
    series_list = list(seed["series"].keys())

    # series: never three in a row
    series_options = [s for s in series_list if learner.series_ok(s, recent_series)] or series_list

    # HOOK TYPES ARE CONSTRAINED BY THE SERIES, not drawn independently.
    #
    # The seed bank pairs each hook type with the series it serves: `permission`
    # and `number_list` belong to small_steps, `outside_view` and
    # `for_someone_else` to show_them_this, and so on. Drawing them as independent
    # arms produced briefs like caught_in_the_act + permission — a permission hook
    # inside a recognition series, which forbids advice. No draft could satisfy
    # that, so the slot was skipped for a reason the writer could never fix.
    #
    # So the series is drawn first and the hook types are then restricted to the
    # ones that actually serve it. The learner still chooses within each, it just
    # cannot choose a combination the bank defines as incoherent.
    pair = {}
    for h in seed["hooks"]:
        pair.setdefault(str(h.get("series") or ""), set()).add(str(h.get("type") or ""))
    _series_first = learner.choose(
        arms, G, {"series": series_options}, explore)
    chosen_series = _series_first.get("series") or series_options[0]
    types_for_series = sorted(pair.get(chosen_series) or set())
    if not types_for_series:
        types_for_series = sorted({h["type"] for h in seed["hooks"]})
    # CTA: honour the policy mix, never two keyword CTAs in a row
    cta_shares = dict(cfg.get("cta_shares") or {"none": 0.5, "send": 0.25,
                                                "guide": 0.2, "book": 0.05})
    cta_options = [k for k in cta_shares if learner.keyword_cta_ok(k, last_cta)]
    if not cta_options:
        cta_options = ["none", "send"]
    # weighted CTA draw, then the learner's choice is honoured inside that
    cta_pick = random.choices(cta_options,
                              weights=[cta_shares.get(k, 0.01) for k in cta_options])[0]

    allowed = {
        "series": [chosen_series],
        "hook_type": types_for_series,
        "layout": ["stack", "single"],
        "voice": ["none"] + (["tts"] if voiceover.available() else []),
        "length_bucket": list(learner.LENGTH_BUCKETS),
        "scene_family": (list(cfg.get("bg_types") or {}) if isinstance(cfg.get("bg_types"), dict)
                             else []) or ["quiet_hope"],
        "slot": [slot_of(cfg, datetime.now(timezone.utc)) or (cfg.get("slots_utc") or ["00:30"])[0]],
        "bg_source": ["pinterest"] + (["pexels"] if pexels.available() else []),
        "cta_type": [cta_pick],
        "music_mood": (list(cfg.get("moods") or []) if isinstance(cfg.get("moods"), list)
                       else list((cfg.get("moods") or {}).keys())) or ["calm"],
    }

    # auto-reset overrides beat the draw (ENGINE_SPEC §2.7)
    if rails.get("force_bg_source"):
        allowed["bg_source"] = [rails["force_bg_source"]]
    if rails.get("length_bucket"):
        allowed["length_bucket"] = [rails["length_bucket"]]
    if rails.get("force_voice_tts") and voiceover.available():
        allowed["voice"] = ["tts"]

    choice = learner.choose(arms, G, allowed, explore)

    # cloning: a breakout pins the winning shape for the next 5 posts (§2.4)
    clone = None
    cloned = [p for p in posts if p.get("clone_of")]
    if not cloned or len(cloned) % 5 == 0:
        clone = strategy_v2.clone_target(_recent(memory, 20))
    if clone:
        for k in ("series", "hook_type", "scene_family", "layout"):
            if clone.get(k) and k in allowed and clone[k] in allowed[k]:
                choice[k] = clone[k]
        choice["clone_of"] = clone.get("clone_of")

    # topic cooldown, then a fresh corpus situation
    situation = strategy_v2.next_situation(memory)
    if not learner.topic_ok(situation, recent_topics) and situation:
        situation = ""
    choice["cta_type"] = cta_pick
    choice["source_situation"] = situation or "a moment where you performed being fine"
    choice["explore_rate"] = explore
    return choice


def run(dry_run: bool = False, offline: bool = False, force: bool = False,
        carousel_mode: bool = False, no_publish: bool = False) -> int:
    cfg = config.load_config()
    memory = config.load_memory()
    log: list = []
    now = datetime.now(timezone.utc)

    if config.paused():
        _step(log, "paused", {"file": "data/PAUSE"})
        return 0

    # safety rails, before anything is spent
    triggers = strategy_v2.reset_triggers(memory)
    rails = strategy_v2.apply_rails(triggers, cfg)
    if triggers.get("flat_reach") or triggers.get("low_hold") or triggers.get("publish_errors"):
        _step(log, "rails", triggers)
        if rails.get("alert"):
            _notify(f"BEAST v2 rails: {rails['alert']}")
    pause, why = strategy_v2.should_pause(memory)
    if pause and not force:
        _step(log, "paused_by_rails", {"reason": why})
        return 0

    slot = slot_of(cfg, now)
    if not slot and not force:
        _step(log, "not_due", {"at": now.isoformat(),
                               "slots": cfg.get("slots_utc")})
        return 0
    slot = slot or (cfg.get("slots_utc") or ["00:30"])[0]

    todays = [p for p in (memory.get("posts") or [])
              if str(p.get("published_at_utc") or p.get("created_at") or "")[:10]
              == now.date().isoformat()]
    cap = int(cfg.get("daily_cap") or 1)
    if len(todays) >= cap and not force:
        _step(log, "daily_cap_reached", {"posted_today": len(todays), "cap": cap})
        return 0

    # 1. what to make
    choice = select_arms(cfg, memory, rails)
    _step(log, "selected", {k: v for k, v in choice.items() if k != "source_situation"})

    # 2. write it
    res = writer.write(choice["series"], choice["hook_type"],
                       choice["source_situation"], choice["length_bucket"],
                       choice["layout"], choice["cta_type"], choice["voice"],
                       choice.get("scene_family", ""),
                       recent_hooks=[p.get("hook") for p in
                                     (memory.get("posts") or []) if p.get("hook")])
    if not res.get("ok"):
        _step(log, "skipped", {"stage": res.get("stage"), "notes": str(res.get("notes"))[:300]})
        state.write_log("confess_v2", {"log": log, "skipped": res.get("stage")})
        return 0

    reel = res["reel"]
    content = writer.to_content(reel, memory)
    content["slot"] = slot
    if choice.get("clone_of"):
        content["clone_of"] = choice["clone_of"]
    _step(log, "drafted", {"hook": reel["hook"], "lines": len(content["beats"]) + 1,
                           "duration_s": content["duration_s"], "attempt": res["attempt"]})

    # 3. the visual
    # THE SEARCH QUERY IS A VISUAL, NOT THE SCRIPT.
    # The first dry run searched Pinterest for the HOOK SENTENCE ("You're allowed
    # to need ten minutes alone first.") and the pins that came back were junk —
    # quote cards with burned-in text, exactly what the QA gate is supposed to
    # reject. A sentence is not a search query. The visual identity is the
    # scene FAMILY (buried_anger -> person clenching jaw in a car, etc.) plus the
    # writer's own scene_queries when it produced them; the hook words are not
    # part of that.
    bg_scene = {"scene": "", "cluster": choice.get("scene_family"),
                "bg_type": choice.get("scene_family"),
                "scene_queries": reel["scene_queries"]}
    bg_path, bg_source = None, ""
    use_pexels = pexels.should_use_pexels(cfg, memory.get("post_counter") or 0,
                                          forced=(choice.get("bg_source") == "pexels"))
    if use_pexels and pexels.available():
        got = pexels.fetch((reel["scene_queries"] or ["calm night"])[0],
                           config.OUTPUTS / "bg_pexels.mp4")
        if got.get("ok"):
            bg_path, bg_source = Path(got["path"]), "pexels"
            content["bg_source_meta"] = {"url": got.get("url"),
                                         "licence": got.get("licence"),
                                         "credit": got.get("credit")}
    if not bg_path:
        try:
            bg_path, bg_source = background.build(cfg, bg_scene, memory,
                                                  content["duration_s"],
                                                  offline=offline, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            _step(log, "skipped", {"stage": "background", "error": str(exc)[:200]})
            state.write_log("confess_v2", {"log": log})
            return 0
    content["bg_source"] = bg_source
    # The background's TYPE comes from the FILE we actually have, not from a
    # default set earlier in the dict: trusting the stale flag fed a video clip
    # down the still path (-loop 1) and the render failed.
    content["bg_is_video"] = str(bg_path).lower().endswith((".mp4", ".mov", ".webm", ".mkv"))
    _step(log, "background", {"source": bg_source, "path": str(bg_path)})
    try:
        content["provenance"] = provenance.record(
            bg_path, source=("pexels" if bg_source == "pexels" else "pinterest"),
            query=reel["hook"], cfg=cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[v2] provenance not recorded: {exc}")

    # 4. the bed: music, or the voice arm, or silence
    track_mp3 = None
    try:
        ok_music, mmeta = music.acquire(cfg, content, config.load_strategy(), memory,
                                        None, content["duration_s"],
                                        dry_run=dry_run, offline=offline)
        if ok_music:
            # music.acquire writes to a FIXED path (outputs/track.mp3) and returns
            # only the source name — there is no "path"/"mp3" key in the meta. The
            # old lookup read the meta dict, found nothing, and every reel rendered
            # SILENT while a perfectly good track sat on disk.
            cand = config.OUTPUTS / "track.mp3"
            track_mp3 = cand if (cand.is_file() and cand.stat().st_size > 4096) else None
            if track_mp3 is None:
                print(f"[v2] music reported ok but produced no usable track; "
                      f"rendering silent")
        _step(log, "music", {"ok": ok_music, "source": (mmeta or {}).get("music_source"),
                             "file": str(track_mp3) if track_mp3 else None})
    except Exception as exc:  # noqa: BLE001
        _step(log, "music", {"error": str(exc)[:160]})

    if content.get("voice") == "tts" and voiceover.available():
        vo = voiceover.build([reel["hook"]] + reel["beats"],
                             content["line_starts"], content["duration_s"],
                             config.OUTPUTS, music=track_mp3)
        _step(log, "voiceover", vo)
        if vo.get("ok"):
            track_mp3 = Path(vo["path"])

    # 5. render
    out = config.OUTPUTS / "reel.mp4"
    try:
        build_video.build(cfg, content, content["duration_s"], track_mp3, None,
                          bg_path, out)
    except Exception as exc:  # noqa: BLE001
        _step(log, "skipped", {"stage": "render", "error": str(exc)[:200]})
        state.write_log("confess_v2", {"log": log})
        return 0
    _step(log, "rendered", {"path": str(out), "bytes": out.stat().st_size})

    # 6. the upload gate, on the finished file
    ok, gate = render_gate.check(out, expect_audio=bool(track_mp3))
    _step(log, "upload_gate", {"ok": ok, "failures": gate.get("failures")})
    if not ok:
        _notify(f"BEAST v2 skipped the slot: upload gate failed "
                f"({'; '.join(gate.get('failures') or [])})")
        state.write_log("confess_v2", {"log": log, "gate": gate})
        return 0

    # 7. carousel variant
    if carousel_mode:
        built = carousel.build(reel, config.OUTPUTS / "carousel", cfg, bg=bg_path)
        _step(log, "carousel", {"ok": built["ok"], "slides": len(built["slides"]),
                                "problems": built["problems"]})

    # 8. publish
    if dry_run or no_publish:
        _step(log, "dry_run", {"would_publish": reel["hook"],
                               "cta": content["cta_type"]})
        state.write_log("confess_v2", {"log": log, "dry_run": True,
                                       "content": {k: content.get(k) for k in (
                                           "hook", "beats", "caption", "cta_type",
                                           "series", "hook_type", "layout",
                                           "duration_s", "words")}})
        return 0

    account = publish.resolve_account()
    ig_id = str(account["id"])
    limit = carousel.publish_limit(ig_id)
    quota = limit.get("quota_usage")
    if quota is not None:
        try:
            if int(quota) >= int((limit.get("config") or {}).get("quota_total", 50)):
                _step(log, "quota_exhausted", limit)
                return 0
        except (TypeError, ValueError):
            pass

    followers = insights.latest_followers()
    trial = platform.trial_params(cfg) if platform.trial_eligible(followers, cfg) else None
    result = publish.publish_reel(out, content, cfg, dry_run=False)
    if result.get("error"):
        memory.setdefault("posts", []).append(
            {"created_at": now.isoformat(), "publish_error": str(result["error"])[:200]})
        config.save_memory(memory)
        _notify(f"BEAST v2 publish FAILED: {str(result['error'])[:180]}")
        _step(log, "publish_failed", {"error": str(result["error"])[:200]})
        state.write_log("confess_v2", {"log": log})
        return 1

    # 9. record — with the API's REAL publish time, never the preferred slot
    real_ts = str(result.get("published_at") or result.get("timestamp") or "")
    post = {
        "id": result.get("media_id"),
        "ig_media_id": result.get("media_id"),
        "media_id": result.get("media_id"),
        "container_id": result.get("container_id"),
        "created_at": now.isoformat(),
        "published_at_utc": real_ts or now.isoformat(),
        "slot": slot,
        "series": content["series"],
        "hook_type": content["hook_type"],
        "hook": content["hook"],
        "topic": content.get("topic_label") or content.get("topic"),
        "source_situation": content.get("source_situation"),
        "scene_family": content.get("scene_family"),
        "bg_source": bg_source,
        "pin_id": str(content.get("provenance", {}).get("file") or ""),
        "layout": content["layout"],
        "voice": content.get("voice") or "none",
        "cta_type": content["cta_type"],
        "duration_s": content["duration_s"],
        "words": content["words"],
        "format": "carousel" if carousel_mode else "reel",
        "trial": bool(trial),
        "clone_of": content.get("clone_of"),
        "caption": content["caption"],
        "hashtags": content["hashtags"],
        "metrics": {},
        "score": None,
    }
    memory.setdefault("posts", []).append(post)
    memory["post_counter"] = int(memory.get("post_counter") or 0) + 1
    memory["last_post_date"] = now.date().isoformat()
    memory.setdefault("used_hooks", []).append({"hook": content["hook"],
                                                "date": now.date().isoformat()})
    memory["used_hooks"] = memory["used_hooks"][-400:]
    config.save_memory(memory)
    _step(log, "published", {"media_id": post["media_id"], "slot": slot,
                             "trial": post["trial"]})

    if triggers.get("flat_reach"):
        _notify(f"BEAST v2 posted (flat-reach mode): {reel['hook']}")
    state.write_log("confess_v2", {"log": log, "post": post})
    try:
        state.commit_all("confess v2: state")
    except Exception as exc:  # noqa: BLE001
        print(f"[v2] state commit failed: {exc}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--carousel", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args()
    return run(dry_run=a.dry_run, offline=a.offline, force=a.force,
               carousel_mode=a.carousel, no_publish=a.no_publish)


if __name__ == "__main__":
    sys.exit(main())