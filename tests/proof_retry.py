"""proof_retry.py — retry ONE scene until it builds, then stop.

The critic is stochastic and (correctly) rejects a state-shaped last line, so a
scene can need more than the pipeline's 2 attempts. This loops the real path for
a single scene, capped, and stops the moment one draft passes AND renders.

Usage: python3 -m tests.proof_retry <scene_id> [max_tries]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from src import background, config, confession, render_gate, language, still

OUT = config.ROOT / "outputs" / "proof"
INDEX = {"phone_ring_out": 1, "cancel_relief": 2, "aisle_turn": 3,
         "cashier_question": 4, "meeting_answer": 5}


def main() -> int:
    sid = sys.argv[1]
    tries = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    cfg = config.load_config()
    memory = config.load_memory()
    strategy = config.load_strategy()
    row = {r["id"]: r for r in language.rows()}[sid]

    for n in range(1, tries + 1):
        print(f"--- try {n}/{tries}: {sid}", flush=True)
        d = confession.write(row, memory, strategy)
        why = str(d.get("critic") or d.get("error") or "")[:110]
        print(f"    {d.get('stage') or 'ok'}: {why}", flush=True)
        if not d.get("ok"):
            for ln in confession.lines_of(d.get("onscreen_text") or ""):
                print(f"       {ln}", flush=True)
            continue

        print(f"    critic: PASS", flush=True)
        for ln in confession.lines_of(d["onscreen_text"]):
            print(f"       {ln}", flush=True)

        lines = confession.lines_of(d["onscreen_text"])
        dur = still.duration_for(len(lines), cfg)
        # The backdrop comes from background.build() — the SAME module the
        # pipeline uses — so this retry exercises the real path, not a
        # still-only shortcut the runner no longer takes.
        bg_scene = {"scene": row.get("subject") or row.get("object") or "",
                    "cluster": row.get("family"),
                    "bg_type": row.get("family")}
        try:
            bg_path, bg_source = background.build(cfg, bg_scene, memory, dur,
                                                  offline=False, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            print(f"    NO BACKGROUND: {str(exc)[:110]}", flush=True)
            continue
        print(f"    background: {bg_source}", flush=True)
        mp4 = OUT / f"{INDEX[sid]}_{sid}.mp4"
        try:
            still.build_reel(Path(bg_path), lines, None, mp4, cfg, dur)
        except Exception as exc:  # noqa: BLE001
            print(f"    RENDER FAILED: {str(exc)[:110]}", flush=True)
            continue
        ok, gate = render_gate.check(mp4, expect_audio=False)
        print(f"    {render_gate.describe(gate)}", flush=True)

        if ok:
            rep = OUT / "proof_five.json"
            data: dict = json.loads(rep.read_text()) if rep.exists() else {"results": []}
            data.setdefault("results", [])
            rec = {"scene_id": sid, "referent": row.get("subject"),
                   "verdict": row["verdict"], "family": row["family"],
                   "outcome": "built", "attempts": n,
                   "critic_verdict": "PASS", "text": d["onscreen_text"],
                   "bg_source": bg_source,
                   "gate": gate, "gate_ok": True,
                   "render_seconds": None, "mp4": str(mp4)}
            data["results"] = [r for r in data["results"]
                               if r.get("scene_id") != sid] + [rec]
            order = ["phone_ring_out", "cancel_relief", "aisle_turn",
                     "cashier_question", "meeting_answer"]
            data["results"].sort(key=lambda r: order.index(r["scene_id"])
                                 if r.get("scene_id") in order else 9)
            data["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime())
            data["dry_run"] = True
            data["published"] = False
            rep.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"    BUILT -> {mp4}", flush=True)
            return 0
    print(f"    gave up after {tries} tries", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())