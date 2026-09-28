"""background.py — VISUAL SPEC v1.0: Pinterest VIDEO clips, cinematic treatment.

Mandatory (spec §2):
  * source is ONLY the Pinterest video search endpoint (direct MP4s, real motion)
  * reject clips under 3s, reject non-video, dedup 30 days
  * fallback chain: another query -> Meta image animated (rare, last resort)

Cinematic treatment (spec §3):
  crop/scale 1080x1920 -> grade (brightness -0.10, saturation 0.85, contrast 1.15,
  blue shift) -> trim from the MIDDLE of the clip -> seamless loop.
"""
from __future__ import annotations

import random
import re
import subprocess
from pathlib import Path

from . import config, llm, pinterest

# ffmpeg is resolved centrally: PATH first, then imageio-ffmpeg's bundled
# binary. Hard-coding "ffmpeg" assumed a system install that CI does not have.
FFMPEG = config.resolve_ffmpeg()

# Background grade. The footage ships AS SHOT — no brightness/saturation crush and
# no colour balance shift. Empty by default; `bg_grade: true` would restore the
# cinematic chain below. Kept as a named constant so the ffmpeg chains keep a single
# source of truth and a re-enable is a one-line change.
GRADE = "null"   # ffmpeg pass-through: zero colour change
GRADE_CINEMATIC = ("eq=brightness=-0.10:saturation=0.85:contrast=1.15,"
                   "colorbalance=rs=-0.03:gs=0.0:bs=0.05")
MIN_CLIP_S = 3.0            # spec §2.3
DEDUP_DAYS = 30             # spec §2.3
# Frame-difference energy below which a Pinterest "video" is really a still image.
# Matches build_video's motion gate so a clip accepted here cannot fail there.
MOTION_MIN_DIFF = 0.8

# FINAL FORMAT §3 — the frame MUST contain a person, since the content is about a
# person's inner life. Rotated as extra queries when a cluster query finds nothing.
PERSON_QUERIES = [
    "man walking alone night video aesthetic",
    "silhouette man dark video aesthetic",
    "person standing alone night video",
    "lone figure walking city night video",
    "man looking out window rain video",
    "back of person walking night video",
    "person sitting alone dark aesthetic video",
    "silhouette person street light night video",
    "man walking rain night cinematic video",
    "person shadow dark aesthetic video",
    "lonely man night city video aesthetic",
    "man standing cliff dark video aesthetic",
    "person walking away night video aesthetic",
    "man face shadow dark video aesthetic",
    "person alone train night video aesthetic",
]
# §3 backup queries (used only if the person queries return nothing)
BACKUP_QUERIES = [
    "anime boy dark aesthetic video",
    "dark anime scene rain video aesthetic",
    "aesthetic dark night walk video",
    "cinematic night video aesthetic person",
]
# Words that indicate a person is present in the pin's title/description.
PERSON_WORDS = ("man", "person", "silhouette", "figure", "boy", "guy", "male",
                "walking", "walk", "standing", "sitting", "lonely", "alone",
                "human", "anime")


# ------------------------------------------------------------------ helpers

def _pin_aspect(entry: dict) -> float:
    for key in ("orig", "474x", "236x"):
        v = (entry.get("images") or {}).get(key)
        if isinstance(v, dict) and v.get("width") and v.get("height"):
            try:
                return float(v["width"]) / float(v["height"])
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return 0.75


def _duration_ms(entry: dict) -> int:
    """Duration straight from the search payload — no download needed (spec §2.3)."""
    best = 0
    for v in (entry.get("videos") or []):
        try:
            best = max(best, int(v.get("durationMs") or 0))
        except (TypeError, ValueError):
            continue
    return best


def _recent_pin_ids(memory) -> set:
    """Pin ids used within the dedup window (spec §2.3: 30 days)."""
    cutoff = config.today_utc() - config.timedelta(days=DEDUP_DAYS)
    used = set()
    for u in memory.get("used_pins", []):
        if isinstance(u, dict):
            raw = str(u.get("date") or "")[:10]
            try:
                if config.date.fromisoformat(raw) >= cutoff:
                    used.add(str(u.get("id")))
            except Exception:  # noqa: BLE001 — undated entries stay excluded
                used.add(str(u.get("id")))
        else:
            used.add(str(u))
    return used


def has_person(entry: dict) -> bool:
    """FINAL FORMAT §3 — does this pin plausibly contain a human figure?

    Pinterest gives us no vision, so this reads the query/title/description text
    and the alt text. It is a *preference signal*, not proof: pick_clip uses it to
    rank person-bearing clips above empty scenery, which is the strongest
    available guarantee short of watching the video.
    """
    blob = " ".join(str(entry.get(k) or "") for k in
                    ("title", "description", "alt", "grid_title", "seo_title",
                     "query", "source_query")).lower()
    return any(w in blob for w in PERSON_WORDS)


def _variants(e: dict) -> list:
    """Usable MP4 variants for a pin, best resolution first.

    The API returns an HLS playlist (vHLSV4) and a direct MP4 (v720P), and
    `best_video` is always the 720p one. 720x1280 upscaled to 1080x1920 is a visible
    quality loss, so any real MP4 variant that meets or beats the target width is
    preferred. HLS is skipped: it is a playlist, not a file, and would need segment
    stitching for no gain here.
    """
    out = []
    for v in (e.get("videos") or []):
        if not isinstance(v, dict):
            continue
        url = str(v.get("url") or "")
        if not url or ".mp4" not in url.lower():
            continue
        try:
            w = int(v.get("width") or 0)
            h = int(v.get("height") or 0)
        except (TypeError, ValueError):
            continue
        out.append((w, h, url))
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return out


def best_source_url(e: dict, target_w: int = 1080) -> str:
    """The highest-quality MP4 for a pin, falling back to `best_video`.

    Prefers a variant at least as wide as the reel (no upscaling). If none reaches
    it, takes the widest available — still better than accepting a 720p default
    when a 906p or 1440p variant exists.
    """
    vs = _variants(e)
    for w, _h, url in vs:
        if w >= target_w:
            return url
    if vs:
        return vs[0][2]
    return str(e.get("best_video") or "")


def pick_clip(results: list, used: set):
    """Spec §2.3 selection: video with MP4, >=3s, croppable to 9:16, motion-leaning.

    FINAL FORMAT §3 additionally prefers clips that evidence a PERSON. Portrait
    shape and longer duration still count, but a human-figure signal outranks both.
    Returns the chosen entry or None.
    """
    pool = []
    for e in results:
        if str(e.get("id")) in used:
            continue
        if (e.get("type") or "image") != "video" or not e.get("best_video"):
            continue
        ms = _duration_ms(e)
        if ms and ms < MIN_CLIP_S * 1000:
            continue                                    # too short to loop
        ar = _pin_aspect(e)
        portrait = 1.0 if 0.4 <= ar <= 0.75 else (0.6 if 0.75 < ar <= 1.0 else 0.15)
        length = 1.0 + min(ms / 60000.0, 1.0)           # up to 2x for long clips
        # §3: person presence is the strongest signal available
        person = 2.5 if has_person(e) else 0.25
        pool.append((portrait * length * person, e))
    if not pool:
        return None
    total = sum(w for w, _ in pool)
    r = random.uniform(0, total)
    upto = 0.0
    for w, e in pool:
        upto += w
        if r <= upto:
            return e
    return pool[-1][1]


# ------------------------------------------------------------ processing

def _probe_duration(path: Path) -> float:
    """Duration via ffprobe, falling back to parsing `ffmpeg -i` (ffprobe may be absent)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True, timeout=60)
        val = float((r.stdout or "0").strip() or 0)
        if val > 0:
            return val
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True,
                           text=True, timeout=60)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                      (r.stderr or "") + (r.stdout or ""))
        if not m:
            return 0.0
        h, mi, s = m.groups()
        return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:  # noqa: BLE001
        return 0.0



def _grade_segments(cfg, darken: float) -> str:
    """ffmpeg colour chain for the background — EMPTY by default.

    The user wants the original footage untouched: no brightness lift/drop, no
    saturation crush, no colour-balance shift. So the default chain is a pure
    pass-through. Setting config `bg_grade: true` restores GRADE_CINEMATIC, and a
    non-zero `bg_darken` adds the extra brightness term. Returning "" (no filter
    at all) is what guarantees zero pixel change, so we never emit a no-op eq.
    """
    parts = []
    if cfg.get("bg_grade"):
        parts.append(GRADE_CINEMATIC)
    if darken:
        parts.append(f"eq=brightness={darken}")
    return ",".join(parts)


def process_clip(src: Path, out: Path, duration_s: float, fps: int,
                 cfg: dict | None = None) -> bool:
    """Spec §3: crop/scale -> grade -> trim from the MIDDLE -> seamless loop."""
    out.parent.mkdir(parents=True, exist_ok=True)
    src_dur = _probe_duration(src)
    if src_dur <= 0:
        print("[background] cannot probe source duration")
        return False
    # Spec §3.3 — start in the middle where the motion is best.
    start = max((src_dur - duration_s) / 2.0, 0.0)
    # Spec §3.1 + §3.2 + §3.5 in one pass.
    vf = (f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          f"fps={fps},{_grade_segments(cfg, 0.0)},format=yuv420p".replace(",,", ","))
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{duration_s:.3f}", "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "slower", "-crf", "16", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        print(f"[background] process failed: {r.stderr[-400:]}")
        return False
    return True


def loop_clip(src: Path, out: Path, duration_s: float, fps: int,
              fade: float = 0.5) -> bool:
    """Spec §3.4 — cross-fade the tail into the head so the video loops cleanly.

    For continuous-motion footage (rain/smoke/fog/water) the cut is already
    seamless; the xfade guarantees it for everything else.
    """
    if duration_s <= fade * 2:
        return False
    offset = max(duration_s - fade * 2, 0.0)
    filt = (f"[0:v]split[a][b];"
            f"[a]trim=0:{offset:.3f},setpts=PTS-STARTPTS[ha];"
            f"[b]trim={offset:.3f}:{duration_s:.3f},setpts=PTS-STARTPTS[tb];"
            f"[ha][tb]xfade=transition=fade:duration={fade:.3f}:"
            f"offset={max(offset - fade, 0):.3f},format=yuv420p[v]")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src),
           "-filter_complex", filt, "-map", "[v]", "-an",
           "-c:v", "libx264", "-preset", "slower", "-crf", "16", "-r", str(fps), str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        print(f"[background] loop xfade failed (using straight cut): {r.stderr[-300:]}")
        return False
    return True


def animate_still(src: Path, out: Path, duration_s: float, fps: int,
                  darken: float = 0.0, cfg: dict | None = None) -> bool:
    """Spec §2.4 step 3 — LAST RESORT: animate a still with zoompan + blur drift."""
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = max(int(round(duration_s * fps)), 2)
    ow, oh = int(1080 * 1.2), int(1920 * 1.2)
    vf = (f"scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
          f"zoompan=z='1.0+0.10*on/{frames - 1}':"
          f"x='iw/2-(iw/zoom/2)+8*sin(on/12)':y='ih/2-(ih/zoom/2)+6*cos(on/15)':"
          f"d={frames}:s=1080x1920:fps={fps},"
          f"gblur=sigma=0.6,{_grade_segments(cfg, darken)},format=yuv420p".replace(",,", ","))
    cmd = [FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", str(src),
           "-t", f"{duration_s:.3f}", "-vf", vf,
           "-c:v", "libx264", "-preset", "slower", "-crf", "16", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    return r.returncode == 0 and out.exists()


def clip_has_motion(path: Path, fps: int = 30) -> bool:
    """True when the clip actually moves. Guards the Part 5.2 "video not static" rule.

    Pinterest returns some pins that are a still image re-encoded as a short video, and
    those pass every other check while producing a reel that fails the final motion QA
    gate. Testing here means a dead pin is skipped and the query loop simply moves on,
    instead of a fully built reel being thrown away at the end. Sampling is done on a
    downscaled gray copy; the cost is milliseconds.
    """
    try:
        import numpy as np
        frames = []
        for at in (1.0, 1.6):
            r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{at:.2f}", "-i",
                                str(path), "-frames:v", "1", "-vf",
                                "scale=160:284,format=gray", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"],
                               capture_output=True, timeout=120)
            if r.returncode != 0 or not r.stdout:
                return True          # can't measure -> don't block the pipeline
            frames.append(np.frombuffer(r.stdout, dtype=np.uint8).astype(np.int16))
        if len(frames) != 2 or frames[0].size != frames[1].size:
            return True
        return float(np.abs(frames[0] - frames[1]).mean()) >= MOTION_MIN_DIFF
    except Exception:  # noqa: BLE001
        return True


def _record(memory, entry, dry_run: bool):
    if dry_run:
        return
    memory.setdefault("used_pins", []).append({
        "id": str(entry.get("id")), "date": config.today_utc().isoformat(),
        "pin_url": entry.get("pin_url"),
    })


# ------------------------------------------------------------------- build

def build(cfg, content, memory, duration_s: float, offline: bool = False,
          dry_run: bool = False):
    """Returns (bg_mp4_path, bg_source). Raises only if every provider fails."""
    fps = int(cfg["reel"]["fps"])
    out_norm = config.OUTPUTS / "bg.mp4"
    looped = config.OUTPUTS / "bg_looped.mp4"
    raw = config.OUTPUTS / "bg_raw.mp4"

    if offline:
        bundled = config.ASSETS / "fallback" / "bg_default.jpg"
        if not bundled.exists():
            make_default_bg(bundled)
        # Offline is hermetic: animate the bundled still so the format still has
        # motion, but flag it as non-Pinterest so the QA gate can see the truth.
        if bundled.exists() and animate_still(bundled, looped, duration_s, fps,
                                              darken=float(cfg.get("bg_darken", 0.0)),
                                              cfg=cfg):
            return looped, "bundled_animated"
        raise RuntimeError("offline background failed")

    used = _recent_pin_ids(memory)
    scene = (content.get("scene") or "").strip()
    bg_type = content.get("bg_type")
    cluster = content.get("cluster") or content.get("archetype")
    mapped = (cfg.get("archetype_bg_map") or {}).get(cluster)
    synonyms = (cfg["bg_types"].get(bg_type)
                or cfg["bg_types"].get(mapped) or []
                or ["dark aesthetic video night city"])

    # FINAL FORMAT §3 — rotate the query list, LLM scene first when we have one.
    # Every query must yield a PERSON in frame, so the cluster synonyms are
    # backed by the explicit person-bearing query list, then the §3 backups.
    queries = []
    for q in [scene, *synonyms]:
        if q and q not in queries:
            queries.append(q)
    random.shuffle(synonyms)
    queries += [q for q in synonyms if q not in queries]
    # §3: if the cluster queries don't return a usable clip, fall back to
    # explicit person queries (rotated) before ever considering a still.
    person_pool = PERSON_QUERIES[:]
    random.shuffle(person_pool)
    queries += [q for q in person_pool if q not in queries]

    # Spec §2.4 steps 1-2 — Pinterest video, then more queries.
    for q in queries[:10]:
        for attempt in range(2):
            try:
                results = pinterest.search_videos(q)
                entry = pick_clip(results, used)
                if not entry:
                    break
                url = best_source_url(entry, target_w=int(cfg["reel"]["w"]))
                if url and pinterest.download(url, raw):
                    if process_clip(raw, looped, duration_s, fps, cfg):
                        # Part 5.2: a still-image pin re-encoded as video would pass
                        # here and then fail the final QA gate, discarding a whole
                        # build. Reject it now so the loop just tries the next query.
                        if not clip_has_motion(looped, fps):
                            print(f"[background] {q!r}: static clip, skipping")
                            continue
                        if not loop_clip(looped, out_norm, duration_s, fps):
                            # straight cut is acceptable for continuous motion
                            out_norm.write_bytes(looped.read_bytes())
                        _record(memory, entry, dry_run)
                        return out_norm, "pinterest_video"
            except Exception as exc:  # noqa: BLE001
                print(f"[background] {q!r} attempt {attempt + 1} failed: {exc}")

    # §3 backups (anime / generic cinematic person footage)
    for q in BACKUP_QUERIES:
        try:
            results = pinterest.search_videos(q)
            entry = pick_clip(results, used)
            if not entry:
                continue
            url = entry.get("best_video")
            if url and pinterest.download(url, raw) and process_clip(raw, looped, duration_s, fps, cfg):
                if not loop_clip(looped, out_norm, duration_s, fps):
                    out_norm.write_bytes(looped.read_bytes())
                _record(memory, entry, dry_run)
                return out_norm, "pinterest_video"
        except Exception as exc:  # noqa: BLE001
            print(f"[background] backup {q!r} failed: {exc}")

    # Spec §2.4 step 3 — LAST RESORT: AI image, animated.
    print("[background] WARNING: no Pinterest clip qualified; animating an AI still")
    prompt = (f"cinematic night photograph, {scene or 'lone man silhouette in rain'}, "
              f"extremely dark low-key, crushed blacks, single light source, "
              f"vertical composition, no text, no watermark")
    gen = config.OUTPUTS / "bg_ai.jpg"
    if llm.image(prompt, gen) and animate_still(
            gen, looped, duration_s, fps, darken=float(cfg.get("bg_darken", 0.0))):
        return looped, "meta_image_animated"

    bundled = config.ASSETS / "fallback" / "bg_default.jpg"
    if not bundled.exists():
        make_default_bg(bundled)
    if animate_still(bundled, looped, duration_s, fps,
                     darken=float(cfg.get("bg_darken", 0.0))):
        return looped, "bundled_animated"
    raise RuntimeError("all background providers failed")


def make_default_bg(path) -> bool:
    """1080x1920 near-black with a smooth dark-red radial glow (dither-noised)."""
    from PIL import Image
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 1080, 1920
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2.0, h * 0.42
    max_d = float((w ** 2 + h ** 2) ** 0.5 / 2)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max_d
    t = np.clip(1.0 - d, 0.0, 1.0) ** 2.2
    r = 10.0 + 62.0 * t
    g = 10.0 + 8.0 * t
    b = 12.0 + 14.0 * t
    rng = np.random.default_rng(11)
    n = rng.uniform(-0.9, 0.9, size=(h, w)).astype(np.float32)
    arr = np.stack([r + n, g + n, b + n], axis=-1)
    Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB").save(path, "JPEG",
                                                                     quality=92)
    return path.exists()