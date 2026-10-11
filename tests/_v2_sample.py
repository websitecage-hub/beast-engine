"""Build one v2 reel in two stages so each fits a command window.

Stage 1 (fast): select arms -> write -> critic -> pick the background.
Writes the state to outputs/v2_stage1.json. NO rendering.

Stage 2 (slow): render from the stage-1 state -> upload gate.

WHY TWO STAGES: the writer loop (up to 4 model calls, ~85s each) plus a 22s
render (~240s) exceeds a single 420s command window, and a timeout kill mid-ffmpeg
wastes the whole model spend. Splitting means a timeout only ever costs one stage.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from src import (background, config, insights, learner, lint, music, pexels,
                 platform, render_gate, run_v2, voiceover, writer)
from src import build_video

STATE = Path("outputs/v2_stage1.json")


def stage1() -> int:
    cfg = config.load_config()
    memory = config.load_memory()
    res = run_v2.select_arms(cfg, memory, {})
    print(f"[stage1] selected: {json.dumps({k: v for k, v in res.items()
                                           if k != 'source_situation'})}", flush=True)

    w = writer.write(res["series"], res["hook_type"], res["source_situation"],
                     res["length_bucket"], res["layout"], res["cta_type"],
                     res["voice"], res.get("scene_family", ""),
                     recent_hooks=[p.get("hook") for p in
                                   (memory.get("posts") or []) if p.get("hook")])
    if not w.get("ok"):
        print(f"[stage1] writer failed at {w.get('stage')}: "
              f"{str(w.get('notes'))[:260]}", flush=True)
        return 1
    reel = w["reel"]
    print(f"[stage1] drafted (attempt {w['attempt']}): {reel['hook']}", flush=True)

    content = writer.to_content(reel, memory)
    # 3. background — the writer's scene_queries LEAD (they are visual queries,
    #    not prose); a full sentence as a Pinterest query returns quote cards
    #    with burned-in text, which is the failure we are fixing.
    bg_scene = {"scene": "", "cluster": res.get("scene_family"),
                "bg_type": res.get("scene_family"),
                "scene_queries": reel["scene_queries"]}
    bg_path, bg_source = None, ""
    if pexels.available() and res.get("bg_source") == "pexels":
        got = pexels.fetch((reel["scene_queries"] or ["calm night"])[0],
                           config.OUTPUTS / "bg_pexels.mp4")
        if got.get("ok"):
            bg_path, bg_source = Path(got["path"]), "pexels"
    if not bg_path:
        try:
            bg_path, bg_source = background.build(cfg, bg_scene, memory,
                                                  content["duration_s"],
                                                  offline=False, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[stage1] background failed: {exc}", flush=True)
            return 1
    print(f"[stage1] background: {bg_source} -> {bg_path}", flush=True)

    # 4. audio (may legitimately end as silence)
    track = None
    try:
        ok_m, meta = music.acquire(cfg, content, config.load_strategy(), memory,
                                   None, content["duration_s"],
                                   dry_run=True, offline=False)
        # music.acquire writes the track to a FIXED path (outputs/track.mp3, with
        # track.wav alongside) and returns only the SOURCE NAME in meta — there is
        # no "path"/"mp3" key. Reading the meta dict therefore found nothing and
        # every reel rendered SILENT even when a track was sitting on disk. The
        # contract is: outputs/track.mp3 exists and has an audio stream.
        cand = config.OUTPUTS / "track.mp3"
        track = cand if (ok_m and cand.is_file() and cand.stat().st_size > 4096) else None
        print(f"[stage1] music: ok={ok_m} source={(meta or {}).get('music_source')} "
              f"file={track}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[stage1] music error: {str(exc)[:140]}", flush=True)

    STATE.write_text(json.dumps({
        "content": content, "bg": str(bg_path), "bg_source": bg_source,
        "audio": str(track) if track else "",
    }, indent=1), encoding="utf-8")
    print(f"[stage1] state -> {STATE}", flush=True)
    return 0


def stage2() -> int:
    st = json.loads(STATE.read_text(encoding="utf-8"))
    cfg = config.load_config()
    content = st["content"]
    bg = Path(st["bg"])
    audio = Path(st["audio"]) if st["audio"] else None
    dur = float(content["duration_s"])
    out = Path("outputs/reel_v2.mp4")

    # The background's TYPE is decided by the FILE, not by whatever the content
    # dict claimed at draft time: stage 1 saved bg_is_video=False (the default
    # from to_content) while stage 1's actual pick was an mp4 clip. Trusting the
    # stale flag fed a video down the still path (-loop 1), which fails.
    content["bg_is_video"] = str(bg).lower().endswith((".mp4", ".mov", ".webm", ".mkv"))
    content["bg_source"] = st["bg_source"]

    t0 = time.time()
    build_video.build(cfg, content, dur, audio, None, bg, out)
    print(f"[stage2] rendered in {time.time() - t0:.0f}s -> {out}", flush=True)

    ok, gate = render_gate.check(out, expect_audio=bool(audio))
    print(f"[stage2] {render_gate.describe(gate)}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(stage1() if (len(sys.argv) < 2 or sys.argv[1] == "1") else stage2())