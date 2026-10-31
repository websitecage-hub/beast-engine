"""asset_scoring.py — the simple selection layer the owner asked for.

What decides a background now:

1. it is not an ad, a promoted pin or a shop link;
2. it has **no on-screen text, logo or watermark** (OCR, not a luminance guess);
3. its tags/metadata feel like the moment — lifestyle / aesthetic / alone / quiet /
   night / window / street / rain / dusk, plus overlap with the scene words;
4. it is technically usable (vertical, big enough, long enough, decodes);
5. it has not been used inside the no-repeat window.

That is it. There is no luma target, no grading model and no 100-point rubric
pretending to measure "quality": the owner was explicit that the elaborate scoring
was complexity that hurt the result. The renderer still applies its normal visual
treatment; if the finished frame is unusable, QA says so after the fact.
"""
from __future__ import annotations

import math
import re

_AD_CTA = re.compile(
    r"\b(shop now|buy now|link in bio|discount|% ?off|promo|promo code|coupon|"
    r"subscribe|follow us|visit our|our website|download the app|free shipping|"
    r"sponsored|advertisement|brand deal|limited offer|book now|sign up now)\b", re.I)
_COMMERCE_DOMAIN = re.compile(
    r"(shopify|amazon|temu|aliexpress|etsy|ebay|walmart|target\.com|"
    r"linktr\.ee|bit\.ly|rebrand\.ly|beacons\.|stan\.store|"
    r"\b(shop|store|buy|deals?|coupon|offer)\b)", re.I)
_URL = re.compile(r"https?://|www\.", re.I)

# The look the format is going for. These are selection words, not a query ban:
# a clip titled "quiet night window aesthetic" is exactly what we want.
_LOOK = re.compile(
    r"\b(lifestyle|aesthetic|aesthetics|alone|solitude|quiet|calm|cozy|cosy|"
    r"night|evening|dusk|sunset|blue hour|window|rain|rainy|street|city|"
    r"walk|walking|home|bedroom|kitchen|cafe|coffee|bus|train|car|drive|"
    r"mood|moody|soft|slow|cinematic|film|filmphoto|35mm|vintage|indoor|"
    r"lights|bokeh|shadow|shadows|minimal|simple|pov)\b", re.I)

MIN_SCORE = 60


def _meta(entry: dict) -> str:
    return " ".join(str(entry.get(k) or "") for k in
                    ("title", "description", "alt_text", "board"))


def hard_reject(entry: dict, *, needed_s: float, min_width: int = 720) -> str | None:
    """Only the things that are definitely not footage: ads, shops, wrong shape."""
    if not entry:
        return "empty entry"
    if str(entry.get("type") or "").lower() not in ("video", "gif", ""):
        return f"not video ({entry.get('type')})"
    if entry.get("is_promoted"):
        return "promoted pin (advertising)"
    text = _meta(entry)
    hit = _AD_CTA.search(text)
    if hit:
        return f"advertising call-to-action: {hit.group(0)!r}"
    dom = str(entry.get("domain") or "")
    if dom and dom.lower() != "uploaded by user" and _COMMERCE_DOMAIN.search(dom):
        return f"commerce/affiliate domain: {dom!r}"
    return None


def soft_flags(entry: dict) -> list[str]:
    flags = []
    text = _meta(entry)
    if _URL.search(text):
        flags.append("description contains a link")
    if str(entry.get("creator_verified")) not in ("False", "false", "", "None"):
        flags.append("verified creator (possible commercial account)")
    dom = str(entry.get("domain") or "")
    if dom and dom.lower() != "uploaded by user":
        flags.append(f"source domain: {dom}")
    return flags


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", str(text or "").lower())}


def look_fit(entry: dict, intent: dict) -> tuple[int, str]:
    """40 pts — does the clip look like the world of this moment?"""
    meta = _meta(entry)
    looks = {m.group(0).lower() for m in _LOOK.finditer(meta)}
    scene = _tokens(" ".join([str(intent.get("literal_scene") or ""),
                              str(intent.get("setting") or ""),
                              str(intent.get("human_action") or "")] +
                             [str(e) for e in (intent.get("scene_entities") or [])]))
    shared = scene & _tokens(meta)
    score = min(26, len(looks) * 5) + min(14, len(shared) * 4)
    return score, (f"look words {sorted(looks)[:5]} (+{min(26, len(looks)*5)}); "
                   f"scene words {sorted(shared)[:4]} (+{min(14, len(shared)*4)})")


def taste_score(entry: dict, *, intent: dict, lane: str, measured: dict,
                ocr: dict, fresh: bool = True) -> dict:
    """0-100: look 40, text-free 30, technical 20, freshness 10."""
    look, look_why = look_fit(entry, intent)

    has_text = ocr.get("has_text")
    if has_text is False:
        text_pts, text_why = 30, "OCR: no on-screen text or logo"
    elif has_text is None:
        text_pts, text_why = 10, "OCR unknown (needs a human look)"
    else:
        text_pts, text_why = 0, f"OCR found text: {str(ocr.get('text_said'))[:50]!r}"

    w = int(measured.get("width") or 0)
    h = int(measured.get("height") or 0)
    dur = float(measured.get("duration_s") or 0)
    need = float((measured.get("needed_s") or 0) or 7)
    tech = (10 if w >= 1080 else 6 if w >= 720 else 0) + \
           (5 if dur >= need + 1 else 2 if dur >= need else 0) + \
           (5 if h > w else 0)
    fresh_pts = 10 if fresh else 0

    total = look + text_pts + tech + fresh_pts
    unknown = has_text is None
    # Text/logo is a hard stop, not a deduction: a clip with its own caption can
    # never be a clean background however well its tags match.
    blockers = (["carries on-screen text or a logo"]
                if (ocr.get("has_text") or ocr.get("has_logo")) else [])
    return {
        "total": total, "minimum": MIN_SCORE, "blockers": blockers,
        "passes": total >= MIN_SCORE and not unknown and not blockers,
        "review_status": ("needs_human_review" if unknown else
                          "reject" if (blockers or total < MIN_SCORE) else "accept"),
        "unknown_dimensions": ["ocr"] if unknown else [],
        "dimensions": {
            "look_fit": {"points": look, "evidence": look_why},
            "no_on_screen_text": {"points": text_pts, "evidence": text_why},
            "technical": {"points": tech,
                          "evidence": f"{w}x{h}, {dur:.1f}s (need {need:.1f}s)"},
            "freshness": {"points": fresh_pts,
                          "evidence": "fresh" if fresh else "used inside the window"},
        },
    }


# ------------------------------------------------------------------------ audio
def score_audio(intent: dict, row: dict, feats: dict, prof: dict, *,
                fresh: bool = True) -> dict:
    """60 pts trend, 20 duration/loop, 20 freshness. Cool beats clever."""
    vc = float(row.get("video_count") or 0)
    trend = 60 if vc >= 500_000 else 52 if vc >= 100_000 else 44 if vc >= 20_000 \
        else 34 if vc >= 5_000 else 24 if vc > 0 else 10
    need = float(prof.get("duration_s") or 7)
    dur = float(feats.get("duration_s") or 0)
    loop = 20 if dur >= need + 1 else 12 if dur >= need else 0
    fresh_pts = 20 if fresh else 0
    total = trend + loop + fresh_pts
    return {"total": total, "passes": total >= 50,
            "dimensions": {
                "trend": {"points": trend, "evidence": f"video_count {int(vc)}"},
                "loopable": {"points": loop, "evidence": f"{dur:.1f}s vs {need:.1f}s"},
                "freshness": {"points": fresh_pts,
                              "evidence": "fresh" if fresh else "used recently"}}}


# ------------------------------------------------------------------- pair (light)
def score_pair(intent: dict, *, video: dict, audio: dict, format_id: str,
               audit: dict | None = None) -> dict:
    """50 text-to-scene, 30 sound, 20 legibility. Recorded, never a hard gate."""
    sem = (audit or {}).get("look_fit", {}).get("points", 0)
    scene = min(50, int(sem * 50 / 40)) if sem else 0
    trend = (audio.get("score") or {}).get("total", 0) if isinstance(audio.get("score"), dict) else 0
    sound = min(30, int(trend * 30 / 80)) if trend else 15
    busy = (video.get("band_busy") if isinstance(video, dict) else None)
    leg = 20 if busy is None else max(0, min(20, int(20 * (1 - min(1.0, busy / 0.2)))))
    total = scene + sound + leg
    return {"total": total, "passes": total >= 55,
            "dimensions": {"text_to_scene": {"points": scene,
                                             "evidence": "look_fit from metadata"},
                           "sound": {"points": sound, "evidence": "trend score"},
                           "legibility": {"points": leg,
                                          "evidence": f"text-band busyness {busy}"}},
            "rationale": (f"{str(video.get('query_lane') or 'literal')} lane shot "
                          f"'{str(video.get('title') or '')[:50]}' paired with "
                          f"'{str(audio.get('title') or 'the selected bed')[:40]}' "
                          f"for {format_id}.")}