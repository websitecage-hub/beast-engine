"""Helpers to build v3 backgrounds from Pinterest ONLY (owner directive:
"use pinterest as assets"). Used by build_short; keeps background.build's
video path as the first choice and adds a still push-in path for the A/B arm.
"""
from __future__ import annotations

from pathlib import Path

from . import background, config, pinterest


def still_clip(cfg, content, memory, duration_s: float,
               dry_run: bool = False) -> tuple:
    """A Pinterest STILL animated with a slow push-in.

    The owner's USAGE.md route: read `best_image`/`images.orig` from the search
    payload and fetch it through /download?url= (the proxy also covers opaque ids).
    Returns (path, "pinterest_still_animated") or (None, reason).
    """
    fps = int(cfg["reel"]["fps"])
    out = config.OUTPUTS / "bg.mp4"
    looped = config.OUTPUTS / "bg_still_looped.mp4"
    raw = config.OUTPUTS / "bg_still.jpg"
    used = background._recent_pin_ids(memory)

    queries = [str(q).strip() for q in (content.get("scene_queries") or []) if str(q).strip()]
    queries += [q for q in (content.get("scene") or "").split("|") if q.strip()]
    queries += ["aesthetic night window rain", "phone glow dark room aesthetic"]

    for q in queries[:8]:
        try:
            results = pinterest.search(q, page_size=25)
        except Exception:  # noqa: BLE001
            continue
        pool = [e for e in results
                if str(e.get("id")) not in used
                and (e.get("best_image") or (e.get("images") or {}).get("orig"))]
        if not pool:
            continue
        # prefer a pin with a person in frame (same signal background.py uses)
        pool.sort(key=lambda e: (background.has_person(e),
                                 len(str(e.get("title") or ""))), reverse=True)

        for entry in pool[:6]:
            img = (entry.get("best_image")
                   or (entry.get("images") or {}).get("orig"))
            if not img:
                continue
            if not pinterest.download(img, raw):
                continue
            try:
                if background.animate_still(raw, looped, duration_s, fps,
                                            darken=float(cfg.get("bg_darken", 0.0)),
                                            cfg=cfg):
                    if not background.clip_has_motion(looped, fps):
                        continue
                    out.write_bytes(looped.read_bytes())
                    background._record(memory, entry, dry_run)
                    return out, "pinterest_still_animated"
            except Exception:  # noqa: BLE001
                continue
    return None, "no pinterest still qualified"