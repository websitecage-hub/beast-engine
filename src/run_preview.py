"""run_preview.py — one orchestrated, preview-only pass for the two pilot formats.

    python3 -m src.run_preview --format auto [--topic "..."] [--no-deliver]

Stages, each recorded as ok/failed with a reason:

  1 content generation + schema   5 audio acquisition + fingerprint match
  2 critic + deterministic gates  6 HyperFrames render
  3 format selection (A|B only)   7 structural QA (render gate)
  4 Pinterest acquisition         8 visual band + one bounded correction
                                  9 audio validation
                                 10 manifest persistence
                                 11 Telegram preview delivery

Publishing is unreachable from here by construction: this module never imports
`publish`, and it refuses to run if a publish switch is on. Preview delivery is
the Telegram file send, recorded truthfully (True/False), never implied.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

from . import config, render_gate
from .formats import (asset_broker, content_factory, run_manifest,
                      template_renderer, template_payloads)

ALIAS = {"A": "caption_list_title_v1", "B": "social_moment_loop_v1"}
ROOT = Path(__file__).resolve().parents[1]


def _stage(stages: dict, name: str, ok, **detail) -> None:
    stages[name] = {"ok": bool(ok), **detail}


def _pick_format(requested: str) -> str:
    if requested in ALIAS:
        return ALIAS[requested]
    if requested in template_payloads.FORMATS:
        return requested
    # auto: fewest successful previews wins, A on a tie
    counts = {f: 0 for f in template_payloads.FORMATS}
    for e in run_manifest.load():
        if e.get("status") in run_manifest.CONSUMED and e.get("format_id") in counts:
            counts[e["format_id"]] += 1
    return min(counts, key=lambda f: (counts[f], f != "caption_list_title_v1"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="preview-only two-format pass")
    ap.add_argument("--format", default="auto", help="A, B, auto or a format id")
    ap.add_argument("--topic", default=None)
    ap.add_argument("--keyword", default=None, help="audio trending keyword")
    ap.add_argument("--quality", default="delivery", choices=["draft", "looks", "delivery"])
    ap.add_argument("--no-deliver", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    if cfg.get("short_publish_enabled"):
        print("refusing to run: short_publish_enabled is true; the preview runner "
              "must not be able to publish", file=sys.stderr)
        return 2

    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    fmt = _pick_format(args.format)
    scratch = Path(config.env("BEAST_SCRATCH", "/tmp/beast-render") or "/tmp/beast-render") / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    stages: dict = {}
    t0 = time.time()
    print(f"[preview] run={run_id} format={fmt} scratch={scratch}", flush=True)

    # 1-3: content, gates, routing (routing already resolved above)
    try:
        gen = content_factory.generate(fmt, args.topic)
    except Exception as exc:  # noqa: BLE001
        _stage(stages, "content", False, error=str(exc)[:300])
        print(json.dumps({"ok": False, "stages": stages}, indent=1))
        return 1
    payload = gen["payload"]
    _stage(stages, "content", True, attempts=gen["attempts"],
           topic_id=payload.get("topic_id"),
           critic={k: gen["critic"].get(k) for k in
                   ("specificity", "sendability", "tone_safety", "originality",
                    "loop_ok", "claims_ok")})
    _stage(stages, "format_selection", True, format_id=fmt, requested=args.format)

    # 4: Pinterest
    try:
        bg = asset_broker.pinterest_background(payload["background_query"], fmt,
                                               scratch / "assets")
        _stage(stages, "pinterest", True, pin_id=bg.get("pin_id"),
               size=f"{bg['width']}x{bg['height']}", hls=bg.get("hls"),
               stretch_pick=bg.get("stretch_pick"), metrics=bg.get("metrics"))
    except Exception as exc:  # noqa: BLE001
        _stage(stages, "pinterest", False, error=str(exc)[:300])
        print(json.dumps({"ok": False, "stages": stages}, indent=1))
        return 1

    # 5: audio
    keyword = args.keyword or (payload.get("topic_id") or "self-improvement").replace("-", " ")
    try:
        au = asset_broker.trending_audio(fmt, scratch / "assets", keyword=keyword, limit=5)
        _stage(stages, "audio", True, stable_id=au.get("stable_id"),
               title=au.get("title"), distance=au.get("distance"),
               clearance=au.get("usage_clearance"))
    except Exception as exc:  # noqa: BLE001
        _stage(stages, "audio", False, error=str(exc)[:300])
        print(json.dumps({"ok": False, "stages": stages}, indent=1))
        return 1

    payload["audio"] = {**payload.get("audio", {}), "source": au["source"],
                        "rights": "preview_only", "stable_id": au.get("stable_id"),
                        "title": au.get("title")}
    payload["provenance"] = {"source": "pinterest", "pin_id": bg.get("pin_id"),
                             "creator": bg.get("creator"),
                             "pin_url": bg.get("pin_url"),
                             "usage_clearance": "unknown"}

    # 6-9: render + QA (the renderer owns the one correction pass)
    # Render on the roomy scratch mount: HyperFrames refuses to render when the
    # OUTPUT filesystem is low, and /home is the small persistent one.
    out = scratch / f"{fmt}-live-{run_id}.mp4"
    try:
        rec = template_renderer.render_preview(fmt, payload, out, quality=args.quality,
                                               background=bg["path"], audio=au["path"])
    except Exception as exc:  # noqa: BLE001
        _stage(stages, "render", False, error=str(exc)[:300])
        print(json.dumps({"ok": False, "stages": stages}, indent=1))
        return 1
    renderer = template_renderer.TemplateRenderer(fmt)
    qa = renderer.qa(rec, payload)
    _stage(stages, "render", True, seconds=rec["render"].get("seconds"),
           attempts=rec.get("attempts"), corrections=bool(rec.get("corrections")))
    _stage(stages, "structural_qa", rec["gate_ok"], gate=rec["gate"])
    _stage(stages, "visual_qa", qa.get("band_ok") is not False,
           measured=rec.get("measured"), drift=rec.get("drift"),
           text_frame0=rec.get("text_frame0"))
    info = render_gate.probe(out)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    _stage(stages, "audio_validation", has_audio and au.get("features") is not None,
           has_audio=has_audio, track_distance=au.get("distance"))

    # Persist the final file on the home mount only when there is real headroom.
    persist = ROOT / "outputs" / "previews" / out.name
    persisted = False
    if shutil.disk_usage(ROOT).free > 300 * 1024 * 1024:
        persist.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, persist)
        persisted = True
    final_path = persist if persisted else out

    # 10: manifest
    entry = run_manifest.make_entry(
        run_id=run_id, format_id=fmt,
        template_dir=f"templates/{fmt}", template_sha256=rec["template_sha256"],
        payload=payload,
        background=run_manifest.asset_ref(bg["path"], source="pinterest",
                                          stable_id=bg.get("pin_id"),
                                          creator=bg.get("creator"),
                                          pin_url=bg.get("pin_url"),
                                          usage_clearance="unknown"),
        audio=run_manifest.asset_ref(au["path"], source=au["source"],
                                     stable_id=au.get("stable_id"),
                                     usage_clearance="unknown"),
        render={**rec, "quality": args.quality},
        qa={"ok": all(s["ok"] for s in stages.values()), **qa}, output=final_path)
    entry["content"] = {"topic_id": payload.get("topic_id"),
                        "critic": gen["critic"], "attempts": gen["attempts"]}
    entry["audio_features"] = au.get("features")
    entry["stages"] = stages
    entry["status"] = "rendered" if entry["qa_ok"] else "needs_review"

    # 11: preview delivery (never publishing)
    delivered = None
    if not args.no_deliver:
        try:
            from . import alerts
            caption = (f"{fmt} · {payload.get('topic_id')}\n"
                       f"{' · '.join((payload.get('titleLines') or
                                      (payload.get('hookLines') or []) + (payload.get('payoffLines') or []))[:2])}\n"
                       f"preview only · rights unknown")
            delivered = bool(alerts.telegram_video(final_path, caption))
        except Exception:  # noqa: BLE001
            delivered = False
    entry["delivery"] = {"channel": "telegram" if not args.no_deliver else None,
                         "ok": delivered, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                              time.gmtime())}
    _stage(stages, "manifest", True, path=str(run_manifest.MANIFEST),
           persisted=persisted, final_path=str(final_path))
    _stage(stages, "delivery", delivered is not False, delivered=delivered)
    run_manifest.record(entry)

    report = {"ok": all(s["ok"] for s in stages.values()), "run_id": run_id,
              "format_id": fmt, "output": str(final_path), "scratch_output": str(out), "sha256": entry["output"]["sha256"],
              "measured": rec.get("measured"), "band_ok": qa.get("band_ok"),
              "text_frame0": rec.get("text_frame0"), "attempts": rec.get("attempts"),
              "stages": stages, "delivered": delivered,
              "seconds": round(time.time() - t0, 1)}
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())