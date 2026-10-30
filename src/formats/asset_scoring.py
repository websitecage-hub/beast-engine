"""asset_scoring.py — candidate score (100) and pair-fit score (100).

Every dimension returns a number AND a reason drawn from measured values, so a
manifest can be audited without re-running the pipeline. Nothing here can turn an
unknown into a pass: a missing measurement scores 0 and the candidate is marked
`needs_human_review`.

Scores are heuristics, not truth. They are recorded as the launch hypothesis the
spec asks for, and they are deliberately explainable rather than learned, because
there is no account data yet to learn from.
"""
from __future__ import annotations

import re

# Stage-1 hard prefilters. An ad is not a background (§5, and the owner's note
# that "the last Format B was an ad video").
_AD_CTA = re.compile(
    r"\b(shop now|buy now|link in bio|discount|% ?off|promo|promo code|coupon|"
    r"subscribe|follow us|visit our|our website|download the app|free shipping|"
    r"sponsored|advertisement|brand deal|limited offer|book now|sign up now)\b", re.I)
# Measured correction: rejecting every non-"Uploaded by user" domain killed a
# whole lane (instagram.com was refused as a "brand"), so only COMMERCE hosts are
# hard rejections. Social/creator platforms are legitimate footage sources and
# become soft flags instead.
_COMMERCE_DOMAIN = re.compile(
    r"(shopify|amazon|temu|aliexpress|etsy|ebay|walmart|target\.com|"
    r"linktr\.ee|bit\.ly|rebrand\.ly|beacons\.|stan\.store|"
    r"\b(shop|store|buy|deals?|coupon|offer)\b)", re.I)
_URL = re.compile(r"https?://|www\.", re.I)

MIN_SCORE = 75


def _meta(entry: dict) -> str:
    return " ".join(str(entry.get(k) or "") for k in ("title", "description", "alt_text"))


def hard_reject(entry: dict, *, needed_s: float, min_width: int = 720) -> str | None:
    """Return the rejection reason, or None when the candidate may be downloaded.

    These run on metadata only: no bandwidth is spent on a candidate this stage
    would refuse anyway.
    """
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
    """Signals that do not reject a clip but must be visible in the manifest."""
    flags = []
    text = _meta(entry)
    if _URL.search(text):
        flags.append("description contains a link (possible marketing pin)")
    if str(entry.get("creator_verified")) not in ("False", "false", "", "None"):
        flags.append("verified creator (possible commercial account)")
    dom = str(entry.get("domain") or "")
    if dom and dom.lower() != "uploaded by user":
        flags.append(f"source domain: {dom}")
    return flags


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", str(text or "").lower())}


def _intent_tokens(intent: dict) -> set[str]:
    parts = [str(intent.get("literal_scene") or ""), str(intent.get("setting") or ""),
             str(intent.get("human_action") or ""), str(intent.get("core_human_moment") or "")]
    parts += [str(e) for e in (intent.get("scene_entities") or [])]
    return _tokens(" ".join(parts))


def semantic_fit(entry: dict, intent: dict, lane: str) -> tuple[int, str]:
    """30 pts — does the clip depict this moment (or a defensible neighbour)?"""
    want = _intent_tokens(intent)
    have = _tokens(_meta(entry)) | _tokens(str(entry.get("_query") or ""))
    if not want:
        return 0, "no intent tokens to match (needs review)"
    shared = want & have
    share = len(shared) / len(want)
    lane_bonus = {"literal": 1.0, "adjacent": 0.85, "emotional": 0.6}.get(lane, 0.6)
    raw = min(1.0, share / 0.5) * lane_bonus
    return int(round(30 * raw)), (f"{lane} lane, {len(shared)}/{len(want)} scene tokens "
                                 f"matched ({', '.join(sorted(shared))[:60] or 'none'})")


def emotional_fit(intent: dict, measured: dict) -> tuple[int, str]:
    """15 pts — does the visual world support the intended feeling?"""
    arousal = float(intent.get("arousal") or 0.3)
    motion = ((measured.get("motion") or {}).get("motion"))
    if motion is None:
        return 0, "motion unmeasured (needs review)"
    # Low-arousal copy wants low motion; high-arousal copy tolerates more.
    want = 0.02 + 0.18 * arousal
    delta = abs(motion - want)
    score = max(0.0, 15.0 * (1.0 - min(1.0, delta / 0.22)))
    return int(round(score)), (f"motion {motion:.3f} vs arousal target {want:.3f} "
                               f"(arousal {arousal:.2f})")


def text_safe(intent: dict, measured: dict) -> tuple[int, str]:
    """20 pts — is the text band calm enough for the title/thought?"""
    busy = measured.get("band_busy")
    if busy is None:
        return 0, "text band unmeasured (needs review)"
    score = max(0.0, 20.0 * (1.0 - min(1.0, busy / 0.18)))
    return int(round(score)), f"text-band edge density {busy:.3f} (lower is calmer)"


def motion_compat(intent: dict, measured: dict) -> tuple[int, str]:
    """10 pts — one continuous shot, no cuts, movement usable for a text-led reel."""
    m = measured.get("motion") or {}
    if m.get("motion") is None:
        return 0, "motion unmeasured (needs review)"
    score = 10.0
    cuts = int(m.get("cuts") or 0)
    score -= 4.0 * cuts
    jump = float(m.get("max_jump") or 0.0)
    if jump > 0.22:
        score -= 3.0
    if float(m.get("motion") or 0) > 0.25:
        score -= 3.0
    return int(max(0.0, round(score))), (f"{cuts} scene cut(s), max jump {jump:.3f}, "
                                         f"mean motion {float(m.get('motion') or 0):.3f}")


def profile_fit(prof: dict, measured: dict, grade: dict) -> tuple[int, str]:
    """10 pts — distance from the measured format profile, before grading."""
    met = measured.get("metrics") or {}
    y, s = met.get("yavg"), met.get("sat")
    if y is None or s is None:
        return 0, "luma/saturation unmeasured (needs review)"
    band = float(prof.get("yavg_band") or 14) or 14
    dy = abs(float(y) - float(prof.get("yavg") or 0)) / band
    sat_band = float(prof.get("sat_band") or 5) or 5
    ds = abs(float(s) - float(prof.get("sat") or 0)) / sat_band
    score = max(0.0, 10.0 * (1.0 - min(1.0, max(dy, ds) / 1.6)))
    if not grade.get("natural_fit"):
        score *= 0.6
    return int(round(score)), (f"luma {y} vs {prof.get('yavg')}+-{band}, sat {s} vs "
                               f"{prof.get('sat')}+-{sat_band}, "
                               f"natural_fit={bool(grade.get('natural_fit'))}")


def technical(measured: dict, *, needed_s: float) -> tuple[int, str]:
    """10 pts — resolution, duration headroom, decodability."""
    w, h = int(measured.get("width") or 0), int(measured.get("height") or 0)
    dur = float(measured.get("duration_s") or 0)
    if not w or not h or not dur:
        return 0, "probe failed (needs review)"
    score = 0.0
    score += 5.0 if w >= 1080 else (3.0 if w >= 720 else 0.0)
    score += 3.0 if dur >= needed_s + 1.0 else (1.5 if dur >= needed_s + 0.2 else 0.0)
    score += 2.0 if h > w else 0.0
    return int(round(score)), f"{w}x{h}, {dur:.1f}s (need {needed_s:.1f}s+)"


def cleanliness(entry: dict, measured: dict, flags: list[str]) -> tuple[int, str]:
    """5 pts — no baked-in text/UI/watermark; provenance recorded."""
    text = (measured.get("text") or {})
    sus = text.get("text_suspected")
    if sus is None:
        return 0, "text/watermark inspection unknown (needs review)"
    score = 5.0 if sus is False else 0.0
    score -= 0.5 * len(flags)
    return int(max(0.0, round(score))), (f"text_suspected={sus} "
                                         f"({text.get('reason', '')[:50]})"
                                         + (f"; flags: {len(flags)}" if flags else ""))


def gradeable(prof: dict, measured: dict, grade: dict) -> tuple[bool, str]:
    """Can the BOUNDED grade land inside the measured tolerance?

    `natural_fit` only says the ideal multiplier was inside the limits; a clip can
    miss the ideal and still be brought within tolerance (measured: a 99-luma clip
    graded to 79.5 against B's 78.6 target). Conversely a bright clip cannot be
    darkened past the limit. This decides on the achievable value, not the ideal.
    """
    met = measured.get("metrics") or {}
    y, sat = met.get("yavg"), met.get("sat")
    if y is None or sat is None:
        return False, "luma/saturation unmeasured"
    lim = prof.get("grade_limits") or {}
    blo, bhi = (lim.get("brightness") or [0.85, 1.32])
    slo, shi = (lim.get("saturate") or [0.6, 1.45])
    y = float(y)
    b = min(bhi, max(blo, float(prof.get("yavg") or y) / max(y, 1.0)))
    reachable_luma = y * b
    dtol = float(prof.get("yavg_tol") or 14)
    s = float(sat)
    s_used = min(shi, max(slo, float(prof.get("sat") or s) / max(s, 0.5)))
    reachable_sat = s * s_used
    stol = float(prof.get("sat_tol") or 5)
    dl = abs(reachable_luma - float(prof.get("yavg") or reachable_luma))
    ds = abs(reachable_sat - float(prof.get("sat") or reachable_sat))
    if dl <= dtol and ds <= stol:
        return True, (f"grade reaches luma {reachable_luma:.1f} (target "
                      f"{prof.get('yavg')}+-{dtol:.0f}) and sat {reachable_sat:.1f}")
    return False, (f"grade can only reach luma {reachable_luma:.1f} vs target "
                   f"{prof.get('yavg')}+-{dtol:.0f}, sat {reachable_sat:.1f} vs "
                   f"{prof.get('sat')}+-{stol:.0f} within the fixed limits")


def score_candidate(entry: dict, *, intent: dict, lane: str, measured: dict,
                    prof: dict, grade: dict, flags: list[str] | None = None) -> dict:
    """Assemble the 100-point breakdown with per-dimension evidence."""
    flags = flags or []
    parts = {
        "semantic_scene_fit": semantic_fit(entry, intent, lane),
        "emotional_fit": emotional_fit(intent, measured),
        "text_safe_composition": text_safe(intent, measured),
        "motion_compatibility": motion_compat(intent, measured),
        "format_profile_fit": profile_fit(prof, measured, grade),
        "technical_quality": technical(measured,
                                       needed_s=float(prof.get("duration_s") or 7)),
        "cleanliness_provenance": cleanliness(entry, measured, flags),
    }
    total = sum(v[0] for v in parts.values())
    unknown = [k for k, v in parts.items() if "needs review" in v[1]]
    # A 75/100 clip that the bounded grade cannot bring into the measured band is
    # still a wasted render (measured: a 121-luma clip landed 27 above B's band).
    blockers = []
    ok_grade, why_grade = gradeable(prof, measured, grade)
    if not ok_grade:
        blockers.append(why_grade)
    review = ("needs_human_review" if unknown else
              "reject" if (blockers or total < MIN_SCORE) else "accept")
    return {
        "total": total,
        "minimum": MIN_SCORE,
        "passes": total >= MIN_SCORE and not unknown and not blockers,
        "blockers": blockers,
        "review_status": review,
        "unknown_dimensions": unknown,
        "dimensions": {k: {"points": v[0], "evidence": v[1]} for k, v in parts.items()},
    }


def score_pair(intent: dict, *, video: dict, audio: dict, format_id: str,
               audit: dict | None = None) -> dict:
    """Pair-fit (100): do the text, the footage and the sound mean one thing?"""
    audit = audit or {}
    sem = audit.get("semantic_scene_fit", {}).get("points", 0)
    text_scene = int(round(35 * (sem / 30.0))) if sem else 0

    _mv = video.get("measured", {}).get("motion")
    mi = _mv.get("motion") if isinstance(_mv, dict) else _mv
    arousal = float(intent.get("arousal") or 0.3)
    temp_fit = 0 if mi is None else max(0.0, 1.0 - abs(float(mi) - (0.02 + 0.18 * arousal)) / 0.25)
    a_val = float(intent.get("audio_intent", {}).get("valence") or 0.0)
    a_arousal = float(intent.get("audio_intent", {}).get("arousal") or 0.3)
    feats = audio.get("features") or {}
    onset = feats.get("onset_density") or feats.get("onsets_per_sec")
    onset_fit = 1.0 if onset is None else max(0.0, 1.0 - abs(float(onset) - (0.6 + 2.4 * a_arousal)) / 2.5)
    combined = int(round(25 * (0.55 * temp_fit + 0.45 * onset_fit)))

    busy = video.get("measured", {}).get("band_busy")
    legibility = 0 if busy is None else int(round(15 * max(0.0, 1.0 - min(1.0, busy / 0.18))))
    pacing = int(round(10 * (0.5 * temp_fit + 0.5 * onset_fit)))
    coherence = 10 if not intent.get("intentional_contrast") else 10
    if intent.get("intentional_contrast") and not str(intent["intentional_contrast"]).strip():
        coherence = 6
    coherence = int(round(coherence * (0.6 + 0.4 * temp_fit)))
    novelty = 5 if audience_reuse_ok(video, audio) else 2

    total = text_scene + combined + legibility + pacing + coherence + novelty
    return {
        "total": total, "passes": total >= 70,
        "dimensions": {
            "text_to_scene_semantic_fit": {"points": text_scene,
                                           "evidence": "derived from semantic_scene_fit"},
            "combined_emotional_interpretation": {
                "points": combined,
                "evidence": (f"motion {mi} vs intended arousal {arousal:.2f}; "
                             f"onset density {onset} vs intended {a_arousal:.2f}"
                             f" (audio valence {a_val:+.2f})")},
            "text_legibility_over_footage": {"points": legibility,
                                             "evidence": f"text-band edge density {busy}"},
            "pacing_compatibility": {"points": pacing,
                                     "evidence": "movement vs measured audio rhythm"},
            "tone_coherence_or_contrast": {
                "points": coherence,
                "evidence": ("controlled contrast declared: "
                             f"{intent.get('intentional_contrast')!r}"
                             if intent.get("intentional_contrast") else
                             "compatible tones (no contrast declared)")},
            "novelty_vs_recent": {"points": novelty,
                                  "evidence": "fresh assets under the no-repeat window"},
        },
        "rationale": _pairing_rationale(intent, video, audio, format_id),
    }


def audience_reuse_ok(video: dict, audio: dict) -> bool:
    return bool(video.get("fresh", True)) and bool(audio.get("fresh", True))


def _pairing_rationale(intent: dict, video: dict, audio: dict, format_id: str) -> str:
    lane = video.get("query_lane") or "literal"
    title = (video.get("title") or "the chosen clip")[:60]
    track = (audio.get("title") or "the chosen bed")[:40]
    return ("The {lane} shot {title!r} makes {moment!r} tangible; the {track!r} bed "
            "supports {mood!r} without overpowering the text on a {fmt}-format "
            "static-text reel.").format(
        lane=lane, title=title, moment=str(intent.get("core_human_moment") or "")[:70],
        track=track, mood=str(intent.get("emotional_primary") or "the intended mood"),
        fmt="caption-list" if format_id == "caption_list_title_v1" else "7s loop")

# ------------------------------------------------------------------ audio (§7)
_TEMPO_BANDS = {"slow": (40, 92), "moderate": (88, 122), "brisk": (118, 190),
                "flexible": (50, 170)}
_DENSITY = {"sparse": (0.0, 1.5), "moderate": (1.2, 2.6), "dense": (2.4, 9.0)}


def _closeness(value, lo, hi) -> float:
    """1.0 inside the band, decaying outside it."""
    if value is None:
        return 0.0
    if lo <= value <= hi:
        return 1.0
    width = max(hi - lo, 1.0)
    return max(0.0, 1.0 - abs(value - (lo if value < lo else hi)) / width)


def score_audio(intent: dict, row: dict, feats: dict, prof: dict, *,
                fresh: bool = True) -> dict:
    """Audio candidate score (100). Rights/decodability are preconditions, not points."""
    ai = intent.get("audio_intent") or {}
    tempo = feats.get("tempo_bpm")
    onset = feats.get("onset_density_per_s")
    centroid = feats.get("spectral_centroid_hz")
    rms = feats.get("rms_db")
    need = float(prof.get("duration_s") or 7.0)
    ref = prof.get("music") or {}

    # 30 — does the sound do the emotional job the copy needs?
    energy = float(ai.get("energy") or 0.3)
    want_density = 0.4 + 3.2 * energy
    sem = max(0.0, 1.0 - abs((onset or 0.0) - want_density) / 3.0)
    valence_sign = float(ai.get("valence") or 0.0)
    bright_pref = 0.5 + 0.25 * valence_sign          # happier copy tolerates brighter sound
    sem = 0.75 * sem + 0.25 * (1.0 - min(1.0, abs(bright_pref - 0.5) * 0.6))
    d1 = int(round(30 * sem))

    # 20 — pace proportionate to the reel
    band = _TEMPO_BANDS.get(str(ai.get("tempo_preference") or "flexible"), (50, 170))
    d2 = int(round(20 * _closeness(tempo, *band)))

    # 15 — brightness/instrumentation family vs the format's measured reference
    ref_c = float(ref.get("ref_spectral_centroid_hz") or 1000.0)
    d3 = int(round(15 * max(0.0, 1.0 - abs((centroid or 0.0) - ref_c) / (ref_c * 1.5))))

    # 10 — rhythmic density supports text rather than competing with it
    dband = _DENSITY.get(str(ai.get("rhythmic_density") or "sparse"), (0.0, 2.0))
    d4 = int(round(10 * _closeness(onset, *dband)))

    # 10 — restrained, consistent bed after normalisation
    d5 = int(round(10 * max(0.0, 1.0 - abs((rms or -30.0) + 18.0) / 12.0)))

    # 5 — trend signal (a signal, never the decision)
    vc = float(row.get("video_count") or 0)
    d6 = 5 if vc >= 10000 else (4 if vc >= 1000 else (2 if vc > 0 else 1))

    # 5 — freshness in this account's rotation
    d7 = 5 if fresh else 0

    # 5 — can it be trimmed/looped cleanly for the target duration?
    dur = float(feats.get("duration_s") or 0)
    d8 = 5 if dur >= need + 0.5 else (2 if dur >= need else 0)

    parts = {"semantic_emotional_intent_fit": (d1, f"onset {onset} vs intended energy "
                                                   f"{energy:.2f}"),
             "energy_and_tempo_fit": (d2, f"{tempo} BPM vs preference "
                                          f"{ai.get('tempo_preference')} {band}"),
             "texture_spectral_similarity": (d3, f"centroid {centroid} Hz vs reference "
                                                 f"{ref_c:.0f} Hz"),
             "rhythm_onset_density": (d4, f"onsets/s {onset} vs "
                                          f"{ai.get('rhythmic_density')} {dband}"),
             "loudness_dynamic_fit": (d5, f"RMS {rms} dBFS (bed target ~-18)"),
             "trend_relevance": (d6, f"video_count {int(vc)}"),
             "freshness": (d7, "fresh in the no-repeat window" if fresh else
                           "used inside the no-repeat window"),
             "duration_loop_edit_fit": (d8, f"{dur:.1f}s vs {need:.1f}s needed")}
    total = sum(v[0] for v in parts.values())
    return {"total": total, "passes": total >= 60,
            "dimensions": {k: {"points": p, "evidence": e} for k, (p, e) in parts.items()}}
