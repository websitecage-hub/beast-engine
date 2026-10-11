"""proof_finish.py — finish the two scenes the concurrent run lost.

The main proof run built 3/5. Scene 5 was OOM-killed (two renders were running at
once) and scene 4 was rejected by the critic. This re-runs ONLY those two, solo,
reusing the accepted text for any scene already built so it does not re-spend
model calls on work that is done.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from src import (background, config, confession, critic_model, language,
                 render_gate, still)

OUT = config.ROOT / "outputs" / "proof"
TODO = sys.argv[1:] or ["cashier_question", "meeting_answer"]
INDEX = {"phone_ring_out": 1, "cancel_relief": 2, "aisle_turn": 3,
         "cashier_question": 4, "meeting_answer": 5}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = config.load_config()
    memory = config.load_memory()
    strategy = config.load_strategy()
    bank = {r["id"]: r for r in language.rows()}

    report = OUT / "proof_five.json"
    prior = {}
    if report.exists():
        try:
            d = json.loads(report.read_text())
            prior = {r["scene_id"]: r for r in d.get("results", [])}
        except Exception:  # noqa: BLE001
            prior = {}

    results = []
    for sid in TODO:
        row = bank[sid]
        print("-" * 70, flush=True)
        print(f"{sid}  referent={row.get('subject')!r}", flush=True)
        rec = {"scene_id": sid, "referent": row.get("subject"),
               "verdict": row["verdict"], "family": row["family"]}

        draft = confession.write(row, memory, strategy)
        rec["attempts"] = draft.get("attempt")
        rec["critic_verdict"] = str(draft.get("critic") or draft.get("error") or "")[:200]
        rec["critic_family"] = draft.get("critic_family")
        rec["writer_family"] = draft.get("writer_family")
        rec["text"] = draft.get("onscreen_text") or ""
        rec["soft_signals"] = draft.get("soft_signals")

        if not draft.get("ok"):
            print(f"   REJECTED at {draft.get('stage')}", flush=True)
            print(f"   ruling: {rec['critic_verdict'][:150]}", flush=True)
            if draft.get("onscreen_text"):
                for ln in confession.lines_of(draft["onscreen_text"]):
                    print(f"      {ln}", flush=True)
            rec["outcome"] = "rejected"
            results.append(rec)
            continue

        print(f"   critic: {rec['critic_verdict'][:80]}", flush=True)
        for ln in confession.lines_of(rec["text"]):
            print(f"      {ln}", flush=True)

        bg_scene = {"scene": row.get("subject") or row.get("object") or "",
                    "cluster": row.get("family"),
                    "bg_type": row.get("family")}
        lines = confession.lines_of(rec["text"])
        dur = still.duration_for(len(lines), cfg)
        try:
            bg_path, bg_source = background.build(cfg, bg_scene, memory, dur,
                                                  offline=False, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            print(f"   NO BACKGROUND: {str(exc)[:110]}", flush=True)
            rec["outcome"] = "no_background"
            results.append(rec)
            continue
        rec["bg_source"] = bg_source
        print(f"   background: {bg_source}", flush=True)

        mp4 = OUT / f"{INDEX.get(sid, 9)}_{sid}.mp4"
        t0 = time.time()
        try:
            still.build_reel(Path(bg_path), lines, None, mp4, cfg, dur)
        except Exception as exc:  # noqa: BLE001
            print(f"   RENDER FAILED: {str(exc)[:130]}", flush=True)
            rec["outcome"] = "render_failed"
            rec["error"] = str(exc)[:200]
            results.append(rec)
            continue
        rec["render_seconds"] = round(time.time() - t0)
        rec["mp4"] = str(mp4)

        ok, gate = render_gate.check(mp4, expect_audio=False)
        rec["gate"] = gate
        rec["gate_ok"] = ok
        print(f"   {render_gate.describe(gate)}", flush=True)
        rec["outcome"] = "built" if ok else "gate_failed"
        results.append(rec)

    # merge with the prior run so proof_five.json holds all five
    merged = []
    for sid in ["phone_ring_out", "cancel_relief", "aisle_turn",
                "cashier_question", "meeting_answer"]:
        new = next((r for r in results if r["scene_id"] == sid), None)
        merged.append(new or prior.get(sid) or {"scene_id": sid,
                                                "outcome": "missing"})

    print("=" * 70, flush=True)
    print(f"{'scene':18s} {'outcome':14s} {'attempt':8s} gate", flush=True)
    for r in merged:
        g = "PASS" if r.get("gate_ok") else ("FAIL" if r.get("gate") else "-")
        print(f"  {r['scene_id']:16s} {r.get('outcome',''):14s} "
              f"{str(r.get('attempts')):8s} {g}", flush=True)

    report.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry_run": True, "published": False,
        "critic_family": critic_model.family(),
        "results": merged,
    }, indent=2), encoding="utf-8")
    print(f"\nreport: {report}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())