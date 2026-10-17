"""run_experiment.py — dry-run-default orchestrator (Section 13, Step 3).

It can drive the v3 two-beat renderer (format `two_beat_v3`) end to end through
the shared interface: pick an eligible arm -> select a real corpus situation ->
write a candidate -> rule prefilter -> independent critic -> render -> QA/gate.
For the v2 control arm (`paced_v2`) it delegates to the existing `run_v2` path so
no v2 logic is duplicated.

HARD SAFETY:
  * `data/PAUSE` is checked first and aborts the run.
  * This module NEVER imports or calls the publishing path — `dry_run` is the
    only mode. There is no `media_publish`, no `publish_reel`, no schedule change.
  * Model/asset steps fail closed; a failure is reported, not papered over.
  * Dry-run assignments are written to the run log, NOT appended to the
    experiment assignment ledger (a dry run is not a valid published sample).

Outputs (preview mp4, blueprint, timeline, gate report, logs, checksum) land
under `outputs/experiments/<run-id>/` or `--out-dir`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import blueprint as bp_mod
from . import config, generate_short
from .formats import registry

# The publishing path is intentionally absent. This constant documents the guard
# the test suite enforces: run_experiment must not reference these symbols.
FORBIDDEN_SYMBOLS = ("media_publish", "publish_reel", "publish.publish")


def _log(entries: list, step: str, payload=None) -> None:
    e = {"at": datetime.now(timezone.utc).isoformat(), "step": step}
    if payload:
        e.update(payload)
    entries.append(e)
    print(f"[exp] {step}: {payload if payload else ''}", flush=True)


def _corpus_evidence(topic: str, research_dir=None) -> list:
    """Return [{record_id, source, url, excerpt}] for the most relevant record."""
    from .research import normalize as rnorm
    rdir = research_dir or (config.ROOT / "data" / "research")
    res = rnorm.normalize_all(rdir)
    recs = res["records"]
    key = (topic or "").lower().split()[0] if topic else ""
    for r in recs:
        blob = (str(r.get("title") or "") + " " + str(r.get("text") or "")).lower()
        if key and key in blob:
            return [{"record_id": r["record_id"], "source": r["source"],
                     "url": r.get("canonical_url"),
                     "excerpt": (str(r.get("title") or "") or
                                 str(r.get("text") or ""))[:160]}]
    if recs:
        r = recs[0]
        return [{"record_id": r["record_id"], "source": r["source"],
                 "url": r.get("canonical_url"),
                 "excerpt": (str(r.get("title") or "") or str(r.get("text") or ""))[:160]}]
    return []


def _offline_reel() -> dict:
    """A deterministic, lint-clean two-beat pair so the render path is testable
    with no network. Wording is the lint_short selftest fixture (known clean)."""
    return {
        "template": "T3", "topic": "saying I'm fine",
        "hook": "Send this to the friend who types 'haha' and means 'I'm fine'.",
        "payoff": "She typed 'haha' and sat there shaking.",
        "visual_arm": "C", "scene_queries": ["empty room night window rain"],
        "audio_type": "quiet_ambient", "length_bucket": "8-10",
        "caption": "the haha that isn't fine", "hashtags": ["#relatable"],
    }


def blueprint_from_reel(reel: dict, format_id: str, evidence: list,
                        experiment_id=None, arm=None) -> dict:
    """Adapt a v3 reel dict to the shared ContentBlueprint contract."""
    hook = str(reel.get("hook") or "")
    payoff = str(reel.get("payoff") or "")
    return bp_mod.new(
        format_id, experiment_id=experiment_id,
        series_id=reel.get("template"), topic_id=reel.get("topic"),
        audience_hypothesis=reel.get("topic"),
        viewer_moment=hook, hook_mechanic=reel.get("template"),
        beats=[{"kind": "hook", "text": hook, "start_ratio": 0.0,
                "visual_intent": (reel.get("scene_queries") or [None])[0]},
               {"kind": "payoff", "text": payoff, "start_ratio": 0.60,
                "visual_intent": "reframe"}],
        caption=reel.get("caption"), cta_variant="none_or_caption_only",
        scene_query=(reel.get("scene_queries") or [None])[0],
        audio_plan={"mode": _audio_mode(reel.get("audio_type")), "track_id": None},
        evidence_refs=[e["record_id"] for e in evidence],
        hypothesis="A specific, sendable two-beat short earns a higher share/send "
                   "rate per reach than the paced-reveal control.",
        rights_requirements=["visual license", "audio license"],
        risk_flags=[], critic={},
        visual_arm=reel.get("visual_arm") or "C",
        length_bucket=reel.get("length_bucket") or "8-10",
    )


def _audio_mode(audio_type) -> str:
    # v3 audio types are all "licensed ambient/track" families; silent arm is
    # handled inside build_short (a quiet room tone), never truly silent.
    return "licensed_ambient" if audio_type else "silent"


def _map_critic(verdict: dict) -> dict:
    """Map the v3 critic keys onto the shared rubric where names differ."""
    if not isinstance(verdict, dict):
        return {}
    return {
        "specificity": verdict.get("specificity"),
        "payoff": verdict.get("sendability"),      # sendability is the v3 payoff bar
        "sendability": verdict.get("sendability"),
        "novelty": verdict.get("originality"),
        "truthfulness": 5 if verdict.get("claims_ok") else 0,
        "brand_fit": verdict.get("tone_safety"),
    }


def _candidate_v3(offline: bool, entries: list, evidence: list,
                  experiment_id: str, seed=None, arm=None) -> dict:
    """One v3 candidate: model-backed writer+critic, or the offline fixture."""
    import random
    rng = random.Random(seed)
    topics = list(generate_short.SCENE_NOUNS.keys())
    topic = rng.choice(topics)
    arm = arm or rng.choice(["A", "B", "C"])
    audio = rng.choice(generate_short.AUDIO_TYPES)
    if offline:
        reel = _offline_reel()
        reel["visual_arm"] = arm          # honour a forced arm even offline
        _log(entries, "candidate_offline", {"topic": topic, "arm": arm})
        return {"reel": reel, "critic": {}, "notes": "offline fixture",
                "model_backed": False}
    template = generate_short.pick_template([])
    res = generate_short.write_one(template, topic, arm, audio,
                                   length_bucket="8-10", recent_lines=[],
                                   verbose=True)
    return {"reel": res.get("reel"), "critic": _map_critic(res.get("critic") or {}),
            "notes": res.get("notes"), "model_backed": True,
            "ok": bool(res.get("ok")), "stage": res.get("stage"),
            "raw_critic": res.get("critic") or {}}


def run(format_id: str = "auto", offline: bool = False, count: int = 1,
        out_dir=None, cards: int = 3, seed=None, arm=None) -> int:
    cfg = config.load_config()
    memory = config.load_memory()
    entries: list = []
    now = datetime.now(timezone.utc)
    run_id = f"exp-{now:%Y%m%d-%H%M%S}"

    if config.paused():
        _log(entries, "paused", {"file": "data/PAUSE"})
        return 0

    elig = registry.eligible()
    if format_id == "auto":
        from . import experiment
        exp = experiment.load(config.ROOT / "data" / "experiments.json")
        recent = [a.get("arm") for a in (exp.get("assignments") or [])][-4:]
        rec = experiment.recommend_arm(exp, elig, recent_arms=recent)
        format_id = rec["arm"]
        _log(entries, "arm_selected", rec)
    elif format_id not in elig:
        _log(entries, "format_not_eligible", {"format_id": format_id,
                                              "eligible": elig})
        return 2

    _log(entries, "start", {"run_id": run_id, "format_id": format_id,
                            "offline": offline, "count": count, "dry_run": True})

    # The v2 control arm is delegated to the existing pipeline (no duplication).
    if format_id == "paced_v2":
        _log(entries, "delegate_v2", {"note": "using run_v2 dry-run path"})
        from . import run_v2
        code = run_v2.run(dry_run=True, offline=offline, force=True,
                          no_publish=True)
        _log(entries, "v2_done", {"exit": code})
        _write_run_bundle(out_dir, run_id, entries, None, None, None, None)
        return code

    # v3: blueprint -> timeline -> render -> qa, via the shared interface.
    evidence = _corpus_evidence("")
    _log(entries, "evidence", {"refs": [e["record_id"] for e in evidence],
                               "url": evidence[0]["url"] if evidence else None})
    renderer = registry.renderer_for("two_beat_v3", memory=memory, offline=offline)

    chosen = None
    for i in range(max(1, cards)):
        cand = _candidate_v3(offline, entries, evidence, run_id, seed=seed, arm=arm)
        if not cand.get("ok", True) and cand.get("reel") is None:
            _log(entries, "candidate_rejected", {"i": i, "stage": cand.get("stage"),
                                                 "notes": str(cand.get("notes"))[:200]})
            continue
        bp = blueprint_from_reel(cand["reel"], "two_beat_v3", evidence,
                                 experiment_id=run_id)
        bp["critic"] = cand.get("critic") or {}
        errs = renderer.validate_blueprint(bp)
        if errs:
            _log(entries, "candidate_rejected", {"i": i, "errors": errs})
            continue
        if cand.get("model_backed"):
            from . import blueprint as _b
            ok, reasons = _b.critic_passed(bp["critic"])
            if not ok:
                _log(entries, "critic_failed", {"i": i, "reasons": reasons})
                continue
        _log(entries, "candidate_accepted", {"i": i,
                                             "model_backed": cand.get("model_backed")})
        chosen = bp
        break

    if chosen is None:
        _log(entries, "no_candidate", {"note": "no candidate passed prefilter/critic;"
                                               " nothing rendered"})
        _write_run_bundle(out_dir, run_id, entries, None, None, None, None)
        return 1

    timeline = renderer.build_timeline(chosen)
    _log(entries, "timeline", {"duration_s": timeline.get("duration_s"),
                               "beats": [b["kind"] for b in timeline["beats"]]})

    out_base = Path(out_dir) if out_dir else (config.OUTPUTS / "experiments" / run_id)
    out_base.mkdir(parents=True, exist_ok=True)
    out_path = out_base / f"{format_id}.mp4"
    artifact = renderer.render(timeline, out_path)
    if not artifact.get("ok"):
        _log(entries, "render_failed", {"stage": artifact.get("stage"),
                                        "why": str(artifact.get("why"))[:300]})
        _write_run_bundle(out_base, run_id, entries, chosen, timeline, None, artifact)
        return 1
    _log(entries, "rendered", {"path": str(out_path),
                               "bytes": out_path.stat().st_size})

    qa = renderer.qa(artifact, chosen)
    _log(entries, "qa", {"ok": qa.get("ok"), "why": qa.get("why"),
                         "failures": (qa.get("gate") or {}).get("failures")})

    checksum = _sha256(out_path)
    _log(entries, "checksum", {"sha256": checksum})
    _log(entries, "dry_run", {"would_publish": chosen["beats"][0]["text"],
                              "note": "no publishing API called"})
    _write_run_bundle(out_base, run_id, entries, chosen, timeline, out_path, qa)
    return 0 if qa.get("ok") else 1


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def _write_run_bundle(out_dir, run_id, entries, blueprint, timeline, preview, qa):
    base = Path(out_dir) if out_dir else (config.OUTPUTS / "experiments" / run_id)
    base.mkdir(parents=True, exist_ok=True)
    (base / "run.json").write_text(
        json.dumps({"run_id": run_id, "dry_run": True, "log": entries,
                    "qa": qa}, indent=2, ensure_ascii=False), encoding="utf-8")
    if blueprint is not None:
        (base / "blueprint.json").write_text(
            json.dumps(blueprint, indent=2, ensure_ascii=False), encoding="utf-8")
    if timeline is not None:
        (base / "timeline.json").write_text(
            json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
    if preview is not None and Path(preview).exists():
        (base / "checksum.sha256").write_text(_sha256(preview) + "\n",
                                              encoding="utf-8")
    print(f"[exp] bundle -> {base}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="dry-run experiment orchestrator")
    ap.add_argument("--format", default="auto",
                    help="two_beat_v3 | paced_v2 | auto (default: auto)")
    ap.add_argument("--offline", action="store_true",
                    help="no network; deterministic fixture for CI")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--cards", type=int, default=3,
                    help="max candidate blueprints to try")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--arm", default=None, choices=["A", "B", "C"],
                    help="force the v3 visual arm (A pexels, B pinterest, C text)")
    a = ap.parse_args()
    return run(format_id=a.format, offline=a.offline, count=a.count,
               out_dir=a.out_dir, cards=a.cards, seed=a.seed, arm=a.arm)


if __name__ == "__main__":
    sys.exit(main())
