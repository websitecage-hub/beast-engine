"""tests/_v3_sample.py — one v3 short reel end-to-end, saved for the owner.

Builds the reel for a FIXED brief (template/topic/arm), renders it, uploads it,
and sends it to Telegram. Also writes every draft (pass or reject) to
outputs/v3_drafts.jsonl — the rejected-draft persistence that was missing (the
owner asked to see a reel the critic rejected, and the words were gone).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from src import (build_short, config, generate_short, learn_short, lint_short,
                 state, upload_host)

DRAFTS = config.OUTPUTS / "v3_drafts.jsonl"


def load_telegram_env():
    """Local runs: the creds live in ~/.beast-secrets/telegram.env, but alerts.py
    reads os.environ. Load them here so delivery works outside Actions."""
    import os
    path = Path.home() / ".beast-secrets" / "telegram.env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def log_draft(entry: dict):
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    with DRAFTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    template_code = sys.argv[1] if len(sys.argv) > 1 else "T2"
    topic = sys.argv[2] if len(sys.argv) > 2 else "being fine alone, quiet in groups"
    arm = sys.argv[3] if len(sys.argv) > 3 else "C"

    templates = {t["code"]: t for t in generate_short.templates()["templates"]}
    template = templates[template_code]
    memory = config.load_memory()
    recent = ([p.get("hook") for p in (memory.get("posts") or [])[-8:]]
              + [p.get("payoff") for p in (memory.get("posts") or [])[-8:]])
    recent = [r for r in recent if r]

    t0 = time.time()
    res = generate_short.write_one(template, topic, arm, "quiet_ambient", "8-10",
                                   recent_lines=recent,
                                   recent_pov_count=0, max_rewrites=4)
    print(f"[sample] writer finished in {time.time()-t0:.0f}s: "
          f"ok={res['ok']} stage={res['stage']} attempt={res['attempt']}")

    reel = res.get("reel")
    if reel is None:
        draft = res.get("draft") or []
        log_draft({"ok": False, "stage": res["stage"], "template": template_code,
                   "topic": topic, "hook": draft[0] if draft else "",
                   "payoff": draft[1] if len(draft) > 1 else "",
                   "notes": res.get("notes", ""), "critic": res.get("critic", {})})
        print(f"[sample] REJECTED at {res['stage']}: {res.get('notes','')[:200]}")
        if not FORCE_DELIVER or len(draft) < 2:
            return 1
        # FORCE_DELIVER: the owner asked to SEE the reel. Render the last draft
        # anyway, clearly marked as critic-rejected. Nothing is published.
        reel = {"format": "short2", "template": template_code, "hook": draft[0],
                "payoff": draft[1], "topic": topic, "visual_arm": arm,
                "scene_queries": generate_short.scene_queries_for(topic, draft[0],
                                                                 draft[1]),
                "audio_type": "quiet_ambient", "length_bucket": "8-10",
                "caption": "(critic rejected — preview only)",
                "hashtags": []}
        rejected_only = True
    else:
        rejected_only = False

    log_draft({"ok": True, "template": template_code, "topic": topic,
               "hook": reel["hook"], "payoff": reel["payoff"],
               "caption": reel["caption"], "attempt": res["attempt"],
               "critic": res.get("critic", {})})

    built = build_short.build(reel, memory,
                              out_path=config.OUTPUTS / "reel_v3_sample.mp4",
                              duration_s=9.0)
    if not built.get("ok"):
        print(f"[sample] RENDER FAILED at {built.get('stage')}: "
              f"{str(built.get('why'))[:200]}")
        return 1
    gate = built["gate"]
    print(f"[sample] gate PASS {gate.get('duration_s')}s "
          f"{gate.get('w')}x{gate.get('h')} audio={gate.get('audio')}")

    video = Path(built["path"])
    url, host = upload_host.publicize(video)
    print(f"[sample] url: {url}")

    from src import alerts
    caption = (f"v3 {template_code} · {topic} · {built['duration_s']}s\n"
               f"{reel['hook']}\n{reel['payoff']}\n\n{url}")
    sent_text = alerts.telegram(caption)
    sent_vid = alerts.telegram_video(video, caption=f"{template_code} {topic}")
    print(f"[sample] telegram: text={sent_text} video={sent_vid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
