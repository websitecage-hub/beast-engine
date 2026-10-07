"""proof_five.py — FIVE SCENES THROUGH THE REAL PATH, IN DRY RUN (requirement 6).

Every scene goes through the actual pipeline in order:

    scene pick -> write (real writer) -> code bans -> critic (real DeepSeek)
              -> still (real Pinterest) -> render (real ffmpeg) -> render gate

DRY RUN: nothing is published. No Instagram call is made at all — the run stops
at the gate and reports. Publishing requires the owner's approval.

It writes, for each scene:
    - the chosen scene and its named referent
    - the on-screen text
    - the critic's verdict and which model family gave it
    - the render gate's measurements
    - the mp4, ready to upload somewhere the owner can watch

and it deliberately includes at least one scene the critic REJECTS and the writer
then rewrites, so the retry path is exercised rather than assumed.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src import (config, confession, critic_model, language, provenance,
                 render_gate, state, still)

OUT = config.ROOT / "outputs" / "proof"
SCENES = ["phone_ring_out", "cancel_relief", "aisle_turn", "cashier_question",
          "meeting_answer"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = config.load_config()
    memory = config.load_memory()
    strategy = config.load_strategy()
    bank = {r["id"]: r for r in language.rows()}

    print("=" * 74)
    print("FIVE SCENES, REAL PATH, DRY RUN — nothing is published")
    print("=" * 74)
    print(f"writer family : meta:{__import__('src.llm', fromlist=['x']).MODEL}")
    print(f"critic family : {critic_model.family()}")
    print(f"image source  : {provenance.source_mode(cfg)}")
    print(f"publishing    : DISABLED (dry run)")
    print()

    results = []
    for i, sid in enumerate(SCENES, 1):
        row = bank.get(sid)
        if not row:
            print(f"{i}. {sid}: NOT IN THE BANK")
            continue
        print("-" * 74)
        print(f"{i}. {sid}   referent={row.get('subject')!r}  "
              f"verdict={row['verdict']!r}  family={row['family']}")

        rec: dict = {"scene_id": sid, "referent": row.get("subject"),
                     "verdict": row["verdict"], "family": row["family"]}

        # --- write (real writer + code bans + real critic), with one retry
        t0 = time.time()
        draft = confession.write(row, memory, strategy)
        rec["write_seconds"] = round(time.time() - t0, 1)
        rec["attempts"] = draft.get("attempt")
        rec["critic_verdict"] = str(draft.get("critic"))[:200]
        rec["critic_family"] = draft.get("critic_family")
        rec["writer_family"] = draft.get("writer_family")
        rec["text"] = draft.get("onscreen_text") or ""
        rec["soft_signals"] = draft.get("soft_signals")

        if not draft.get("ok"):
            print(f"   WRITER/CRITIC: REJECTED at {draft.get('stage')}")
            print(f"   ruling: {str(draft.get('critic') or draft.get('error'))[:150]}")
            if draft.get("onscreen_text"):
                print("   the rejected draft:")
                for ln in confession.lines_of(draft["onscreen_text"]):
                    print(f"      {ln}")
            rec["outcome"] = "rejected"
            results.append(rec)
            continue

        print(f"   critic: {rec['critic_verdict'][:90]}")
        for ln in confession.lines_of(rec["text"]):
            print(f"      {ln}")
        if rec["soft_signals"]:
            print(f"   soft signals: {rec['soft_signals']}")

        # --- still, real Pinterest
        shot = still.fetch_still(cfg, {}, row, memory, offline=False,
                                 out_dir=OUT / sid)
        rec["still_ok"] = shot.get("ok")
        rec["pin_id"] = shot.get("pin_id")
        rec["pin_query"] = shot.get("query")
        if shot.get("provenance"):
            rec["provenance"] = shot["provenance"]
        if not shot.get("ok"):
            print(f"   STILL: none usable ({shot.get('reason')})")
            rec["outcome"] = "no_still"
            results.append(rec)
            continue
        print(f"   still: pin {shot.get('pin_id')} via {shot.get('query')!r}")

        # --- render, real ffmpeg
        lines = confession.lines_of(rec["text"])
        dur = still.duration_for(len(lines), cfg)
        mp4 = OUT / f"{i}_{sid}.mp4"
        try:
            still.build_reel(Path(shot["path"]), lines, None, mp4, cfg, dur)
        except Exception as exc:  # noqa: BLE001
            print(f"   RENDER FAILED: {str(exc)[:120]}")
            rec["outcome"] = "render_failed"
            rec["error"] = str(exc)[:200]
            results.append(rec)
            continue
        rec["mp4"] = str(mp4)

        # --- the upload gate, on the finished file
        ok, gate = render_gate.check(mp4, expect_audio=False)
        rec["gate"] = gate
        rec["gate_ok"] = ok
        print(f"   {render_gate.describe(gate)}")
        rec["outcome"] = "built" if ok else "gate_failed"
        results.append(rec)
        print(f"   -> {mp4}")

    # ---------------------------------------------------------------- summary
    print()
    print("=" * 74)
    built = [r for r in results if r.get("outcome") == "built"]
    rejected = [r for r in results if r.get("outcome") == "rejected"]
    print(f"built {len(built)}/{len(SCENES)} | rejected {len(rejected)} | "
          f"other {len(results) - len(built) - len(rejected)}")
    print()
    for r in results:
        print(f"  {r['scene_id']:18s} {r['outcome']:12s} "
              f"attempt={r.get('attempts')} "
              f"{'gate=' + ('PASS' if r.get('gate_ok') else 'FAIL') if r.get('gate') else ''}")

    report = OUT / "proof_five.json"
    report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "published": False,
        "critic_family": critic_model.family(),
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\nreport: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())