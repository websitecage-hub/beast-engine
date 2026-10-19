"""Fast path: render one v3 reel with a Pinterest background and send it to
Telegram. No publishing (short_publish_enabled is false and this file never
calls publish).

Usage: python3 tests/_v3_quick.py [TEMPLATE] [TOPIC] [ARM]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import (alerts, build_short, config, generate_short, music,  # noqa: E402
                 upload_host)


def load_env():
    for name in ("telegram.env",):
        p = Path.home() / ".beast-secrets" / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_env()
    template_code = sys.argv[1] if len(sys.argv) > 1 else "T4"
    topic = sys.argv[2] if len(sys.argv) > 2 else "phone calls"
    arm = sys.argv[3] if len(sys.argv) > 3 else "B"      # B = Pinterest

    templates = {t["code"]: t for t in generate_short.templates()["templates"]}
    template = templates[template_code]
    memory = config.load_memory()
    recent = [x for x in ([p.get("hook") for p in (memory.get("posts") or [])[-8:]]
                          + [p.get("payoff") for p in (memory.get("posts") or [])[-8:]])
              if x]

    t0 = time.time()
    res = generate_short.write_one(template, topic, arm, "quiet_ambient", "8-10",
                                   recent_lines=recent, recent_pov_count=0,
                                   max_rewrites=4)
    print(f"[quick] writer {time.time()-t0:.0f}s ok={res['ok']} "
          f"stage={res['stage']} attempt={res['attempt']}", flush=True)

    reel = res.get("reel")
    marked = False
    if reel is None:
        draft = res.get("draft") or []
        if len(draft) < 2:
            print(f"[quick] no draft to render: {res.get('notes','')[:160]}")
            return 1
        print(f"[quick] critic rejected all attempts — preview render anyway")
        shown = list(reel_drafts(res)) if False else draft
        reel = {"format": "short2", "template": template_code, "hook": shown[0],
                "payoff": shown[1], "topic": topic, "visual_arm": arm,
                "scene_queries": generate_short.scene_queries_for(topic, shown[0],
                                                                 shown[1]),
                "audio_type": "quiet_ambient", "length_bucket": "8-10",
                "caption": "(preview — critic rejected this draft)",
                "hashtags": []}
        marked = True

    ok_music, _meta = music.acquire(config.load_config(),
                                    {"mood": "quiet_ambient",
                                     "music_mood": "quiet_ambient"},
                                    config.load_strategy(), memory, None, 9.0,
                                    dry_run=True)
    track = config.OUTPUTS / "track.mp3"
    reel["_track_path"] = str(track) if (ok_music and track.exists()) else ""
    print(f"[quick] music: {bool(reel['_track_path'])}", flush=True)

    built = build_short.build(reel, memory,
                              out_path=config.OUTPUTS / "reel_v3_now.mp4",
                              duration_s=9.0)
    if not built.get("ok"):
        print(f"[quick] RENDER FAILED at {built.get('stage')}: "
              f"{str(built.get('why'))[:240]}")
        return 1
    video = Path(built["path"])
    g = built["gate"]
    print(f"[quick] gate PASS {g.get('duration_s')}s {g.get('w')}x{g.get('h')} "
          f"audio={g.get('audio')}", flush=True)

    url, _host = upload_host.publicize(video)
    print(f"[quick] url: {url}", flush=True)
    cap = (f"v3 {template_code} · {topic} · {arm} · {built['duration_s']}s\n"
           f"{'PREVIEW (critic rejected) — ' if marked else ''}"
           f"{reel['hook']} / {reel['payoff']}\n{url}")
    print("[quick] telegram:", alerts.telegram(cap),
          alerts.telegram_video(video, caption=f"v3 {template_code} {topic}"),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())