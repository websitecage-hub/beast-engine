"""still.py — the image, the push-in, and the beat-timed lines (STRATEGY.md).

Three jobs, all of them places the old pipeline shipped something wrong:

1. PICK A STILL THAT CONTAINS THE OBJECT. The query is built from the scene's
   own object plus "night". The strategy bans "aesthetic", "moody" and
   "cinematic" as queries because they return the same foggy street every time,
   which is exactly how the grid became one card. A still that does not contain
   the object is rejected — words over the wrong picture is the signature of a
   template.

2. REJECT WHAT CANNOT SHIP. Baked-in text (a pin with a caption already on it),
   a logo, or a face larger than a third of the frame. The face check runs on
   the actual pixels via a skin-tone/face-box proxy and is deliberately
   conservative: the strategy says a face that could be read as a stolen
   creator is a trust and originality problem.

3. RENDER LINES ON THE BEAT. Line one visible by 1.2s, the last line held at
   least 2.5s, 16-20s total, slow push-in, text in the middle two-thirds, no
   watermark, no handle. This is the format REVERSAL: the previous format put
   every line on screen from frame 0 and the strategy has measured that this
   gives the test batch nothing to watch.

The timing model is fixed by the spec, not learned:
    line 1        at 0.35s  (must be visible by 1.2s)
    middle lines  spread across the body
    last line     arrives leaving >= 2.5s of hold before the end
"""
from __future__ import annotations

import json
import random
import re
import subprocess
from pathlib import Path

from . import config

FFMPEG = config.resolve_ffmpeg()

# Banned as search terms by the strategy. Kept as a constant so a future edit
# cannot quietly reintroduce them.
BANNED_QUERY_WORDS = ("aesthetic", "moody", "cinematic", "vibe", "a e s t h e t i c")

# Quality gates on the chosen pin.
MIN_STILL_PX = 900          # at least this on the short side (upscale guard)
MAX_FACE_FRACTION = 0.34    # spec: reject a face larger than a third of the frame
MIN_FACE_FRACTION = 0.0

FIRST_LINE_AT_S = 0.35      # must be visible by 1.2s
LAST_LINE_HOLD_S = 2.5      # spec: hold the rename for at least this long
PUSH_IN = 1.06              # slow push-in: 6% over the whole reel


# ------------------------------------------------------------------- the query

def _common_words(a: str, b: str) -> int:
    """How many significant words two strings share. Used as a weak same-object
    signal when the scene's own keywords are too colloquial to match a query."""
    stop = {"at", "in", "on", "the", "a", "an", "of", "to", "for", "with", "it"}
    wa = {w for w in re.findall(r"[a-z]{3,}", a.lower()) if w not in stop}
    wb = {w for w in re.findall(r"[a-z]{3,}", b.lower()) if w not in stop}
    return len(wa & wb)


def query_for(row: dict) -> str:
    """The object in line one, plus night. Nothing else.

    Deliberately NOT "aesthetic"/"moody"/"cinematic": those return the same
    foggy street and are how a grid of twelve cards all looked identical.
    """
    obj = str(row.get("object") or "phone").strip()
    obj = re.sub(r"[()'\"']", "", obj)
    words = [w for w in obj.lower().split() if w not in BANNED_QUERY_WORDS]
    q = " ".join(words) or "phone"
    return f"{q} at night no text"


def query_variants(row: dict) -> list:
    """Attempts in order. Two fetch attempts is the spec's allowance."""
    base = query_for(row)
    obj = " ".join(re.sub(r"[()'\"]", "", str(row.get("object") or "phone")).lower().split())
    return [
        base,
        f"{obj} dark room night",
    ]


# -------------------------------------------------------------- pin quality

def _pin_text_present(img_path: Path) -> bool:
    """True when the pin appears to carry baked-in text.

    Algorithm: find BRIGHT PIXEL REGIONS (connected components on a downscaled
    brightness mask) and ask what fraction of the frame they cover. Text on a
    pin is a tight cluster of dense bright strokes; a dark cinematic scene has
    bright pixels too (a lamp, a window) but they are large soft areas, not many
    small dense ones.

    The obvious implementation — counting narrow bright horizontal RUNS — was
    tried and measured at ZERO on a synthetic pin with fourteen lines of large
    white caption text: each stroke run exceeds the run-length window, so the
    count never trips. Region coverage is what actually discriminates.
    """
    try:
        from PIL import Image
        import numpy as np
    except Exception:  # noqa: BLE001
        return False
    try:
        with Image.open(img_path) as im:
            a = np.array(im.convert("L").resize((192, 341)), dtype=np.uint8)
    except Exception:  # noqa: BLE001
        return False

    bright = a > 210
    n_bright = int(bright.sum())
    if n_bright < 40:
        return False

    # Connected components by flood fill over the mask, 8-connected.
    seen = np.zeros_like(bright, dtype=bool)
    h, w = bright.shape
    total = n_bright
    biggest = 0
    small_regions = 0
    stack = []
    for y0 in range(h):
        for x0 in range(w):
            if not bright[y0, x0] or seen[y0, x0]:
                continue
            stack.append((y0, x0))
            seen[y0, x0] = True
            size = 0
            minx = maxx = x0
            miny = maxy = y0
            while stack:
                y, x = stack.pop()
                size += 1
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and bright[ny, nx] \
                           and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            biggest = max(biggest, size)
            # a glyph cluster is bounded and dense relative to its box
            bw, bh = maxx - minx + 1, maxy - miny + 1
            if size >= 12 and size / float(bw * bh) > 0.35:
                small_regions += 1

    coverage = total / float(h * w)
    # TEXT: many dense glyph-ish regions covering a real share of the frame.
    if small_regions >= 25 and coverage > 0.008:
        return True
    # A single enormous bright blob (a poster, a bright wall) is not text, so
    # this deliberately does NOT trip on size alone.
    return False


def _face_fraction(img_path: Path) -> float:
    """Fraction of the frame occupied by the largest face-ish region.

    A proxy, not a detector: skin-tone pixels are clustered into row/column
    bands and the largest dense band is reported as a fraction of frame area.
    Conservative on purpose — the strategy only needs to reject a big obvious
    face, and a false positive costs a fetch retry, not a wrong post.
    """
    try:
        from PIL import Image
        im = Image.open(img_path).convert("RGB").resize((160, 284))
    except Exception:  # noqa: BLE001
        return 0.0
    px = im.load()
    w, h = im.size
    total = w * h
    hit = 0
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r > 95 and g > 40 and b > 20 and max(r, g, b) - min(r, g, b) > 15 \
               and abs(r - g) > 15 and r > g and r > b:
                hit += 1
    return hit / float(total)


def inspect_still(path: Path) -> tuple:
    """(ok, reason). Every rejection reason names what to do instead.

    ORDER MATTERS: the file is opened FIRST and the byte size checked after.
    Checking `st_size < 4096` before confirming it is an image reported a small
    but valid test image as "download empty", which is the wrong reason and hid
    the real rule being tested.
    """
    if not path.exists():
        return False, "file missing"
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            im.verify()
    except Exception as exc:  # noqa: BLE001
        return False, f"not a readable image: {exc}"
    if min(w, h) < MIN_STILL_PX:
        return False, f"too small ({w}x{h}, need short side >= {MIN_STILL_PX})"
    if path.stat().st_size < 4096:
        return False, "file suspiciously small (likely a truncated download)"
    if _pin_text_present(path):
        return False, "baked-in text on the pin"
    face = _face_fraction(path)
    if face > MAX_FACE_FRACTION:
        return False, f"face too large ({face:.0%} of frame, max {MAX_FACE_FRACTION:.0%})"
    return True, "ok"


# ------------------------------------------------------------------- timing

def line_times(n_lines: int, duration_s: float, cfg: dict | None = None) -> list:
    """When each line appears, in seconds.

    Fixed by the spec rather than learned: line one by 1.2s, the last line held
    for at least LAST_LINE_HOLD_S, and the middle spaced evenly between them so
    each line gets its own beat of attention.
    """
    first_at, hold_s = FIRST_LINE_AT_S, LAST_LINE_HOLD_S
    try:
        c = ((cfg or {}).get("confession") or {})
        first_at = float(c.get("first_line_at_s", first_at))
        hold_s = float(c.get("last_line_hold_s", hold_s))
    except Exception:  # noqa: BLE001
        pass
    n = max(int(n_lines), 1)
    if n == 1:
        return [first_at]
    last = max(duration_s - hold_s, first_at + 0.4)
    if n == 2:
        return [first_at, last]
    span = last - first_at
    step = span / (n - 1)
    return [round(first_at + i * step, 3) for i in range(n)]


def timing_ok(times: list, duration_s: float, cfg: dict | None = None) -> tuple:
    """(ok, reason). The spec's three timing invariants, checked on numbers."""
    line_one_by, hold_need = 1.2, LAST_LINE_HOLD_S
    try:
        c = ((cfg or {}).get("confession") or {})
        line_one_by = float(c.get("line_one_by_s", line_one_by))
        hold_need = float(c.get("last_line_hold_s", hold_need))
    except Exception:  # noqa: BLE001
        pass
    if not times:
        return False, "no line times"
    if min(times) > line_one_by:
        return False, f"line one arrives at {min(times)}s (must be <= {line_one_by}s)"
    if times != sorted(times):
        return False, "lines arrive out of order"
    hold = duration_s - times[-1]
    if hold < hold_need - 0.01:
        return False, f"last line held {hold:.2f}s (need >= {hold_need}s)"
    if times[-1] <= times[0]:
        return False, "last line and first line compete for the same beat"
    return True, "ok"


def duration_for(line_count: int, cfg: dict | None = None) -> float:
    """16-20s, chosen from the line count so the pause is real.

    Read from config so the spec value is a tunable, not a constant buried in
    code — the strategy treats length as a first-class variable.
    """
    lo, hi = 16.0, 20.0
    try:
        c = ((cfg or config.load_config()).get("confession") or {})
        lo = float(c.get("min_s", lo))
        hi = float(c.get("max_s", hi))
    except Exception:  # noqa: BLE001
        pass
    extra = min(max(int(line_count) - 5, 0), 2)
    return min(lo + extra * (hi - lo) / 2.0, hi)


# ------------------------------------------------------------------ the still

def fetch_still(cfg, content: dict, row: dict, memory: dict, offline: bool = False,
                out_dir: Path | None = None) -> dict:
    """Find, download and vet one still. Returns {ok, path, pin_id, query, reason}."""
    from . import pinterest

    out_dir = out_dir or (config.ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "still.jpg"
    used = {str(x) for x in (memory.get("used_pins") or [])}

    if offline:
        fb = config.ROOT / "assets" / "fallback" / "bg_default.jpg"
        return {"ok": fb.exists(), "path": fb if fb.exists() else None,
                "pin_id": "offline", "query": "offline",
                "reason": "offline fallback" if fb.exists() else "no fallback image"}

    pinterest.wake()
    for attempt in query_variants(row):
        try:
            results = pinterest.search(attempt)
        except Exception as exc:  # noqa: BLE001
            print(f"[still] search failed for {attempt!r}: {exc}")
            continue
        random.shuffle(results)
        for entry in results:
            pid = str(entry.get("id") or "")
            if pid and pid in used:
                continue
            url = (entry.get("best_image") or (entry.get("images") or {}).get("736x")
                   or (entry.get("images") or {}).get("orig"))
            if not url:
                continue
            try:
                got = pinterest.download(url, target)
                if not got:
                    continue
            except Exception as exc:  # noqa: BLE001
                print(f"[still] download failed: {exc}")
                continue
            ok, why = inspect_still(target)
            print(f"[still] {pid} -> {why}")
            if ok:
                # PROVENANCE (requirement 5): record the source URL, the licence
                # and the author for every image that reaches a reel. Written
                # next to the artefact and appended to data/provenance/images.jsonl.
                try:
                    from . import provenance
                    prov = provenance.record(
                        target, source=provenance.source_mode(cfg),
                        source_url=url, pin_id=pid, query=attempt,
                        author=str(entry.get("author") or
                                   entry.get("pinner") or ""),
                        extra={"alt_text": entry.get("alt_text") or "",
                               "image_source_mode": provenance.source_mode(cfg)})
                    print(f"[still] provenance {prov['source']} "
                          f"licence={prov['licence']}")
                except Exception as exc:  # noqa: BLE001
                    # A provenance failure must not stop a reel, but it MUST be
                    # visible — silently losing the record is the thing this
                    # requirement exists to prevent.
                    prov = {"error": str(exc)}
                    print(f"[still] PROVENANCE FAILED: {exc}")
                return {"ok": True, "path": target, "pin_id": pid, "query": attempt,
                        "reason": "ok", "alt": entry.get("alt_text") or "",
                        "source_url": url, "provenance": prov}
    return {"ok": False, "path": None, "pin_id": None, "query": query_for(row),
            "reason": "no pin passed the quality gates"}


# ------------------------------------------------------------------- render

def build_reel(still: Path, lines: list, audio: Path | None, out_mp4: Path,
               cfg: dict, duration_s: float, font_px: int = 64) -> Path:
    """Compose the beat-timed confession: still + slow push-in + lines on cue.

    Text is drawn onto a transparent PNG per line and each is overlaid with its
    own enable= window, so a line appears once and stays. That is the format the
    strategy asks for ("lines arriving on the beat") and it is the opposite of
    the previous build's single all-visible block.

    `-loop 1` on the still and on every text layer: a one-frame input ends at
    t=0, so every enable= past the first frame silently renders nothing and the
    encode still "succeeds" with the text only on frame 0.
    """
    from . import build_video

    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    fps = int(cfg["reel"]["fps"])
    # cfg is passed so the RENDERER and the GATE compute the same times. The
    # gate calls line_times(n, dur, cfg) and build_reel called the default-arg
    # form, which is the "gate measures a different path than the renderer"
    # failure — a config change to first_line_at_s or last_line_hold_s would
    # have been honoured by the gate and ignored by the actual build.
    times = line_times(len(lines), duration_s, cfg)

    # Text layers, centred inside the safe band.
    #
    # POSITIONS ARE EVENLY SPREAD ACROSS THE BAND, not clustered: the first
    # attempt used 0.30 + 0.075*i which put line 5 at 0.60 and the rename at
    # 0.62 — two lines nearly on top of each other once the earlier ones
    # persisted. With accumulating lines each slot must be its own row.
    #
    # ONE COMMON FONT SIZE FOR EVERY LINE. Fitting each line independently made
    # the short line render at 64px while the long one dropped to ~48px, so the
    # block shipped visibly mixed sizes — which reads as broken, not as design.
    # The size is chosen once, for the longest line, and applied to all.
    band_top, band_bot = 0.15, 0.70
    n = max(len(lines), 1)
    common_px = build_video.fit_common_px(lines, cfg, start_px=font_px,
                                         y_band=(band_top, band_bot))
    text_pngs = []
    for i, ln in enumerate(lines):
        png = out_mp4.parent / f"line_{i}.png"
        centre = band_top + (i + 0.5) * (band_bot - band_top) / n
        # The rename lands at the foot of the band: it is the last thing read
        # and the thing a viewer screenshots.
        if i == n - 1:
            centre = band_bot - (band_bot - band_top) / (2 * n)
        build_video.render_single_line(ln, png, cfg, px=common_px, y_frac=centre)
        text_pngs.append(png)

    # ONE frame from the still, NOT a looped input.
    #
    # zoompan's d= counts OUTPUT frames PER INPUT FRAME. The still was fed as
    # "-loop 1 -t 18" (540 input frames) and zoompan was told d=540, so it
    # produced 540 x 540 = 291,600 frames instead of 540 and the encode ran
    # until the 900s timeout (verified: subprocess.TimeoutExpired, and the
    # partial file was unplayable — "moov atom not found"). One input frame
    # with d=540 yields exactly the 540 frames the reel needs.
    #
    # The text PNGs stay looped: an overlay stream must not END during its
    # enable= window, and the graph terminates on the bg's 540 frames.
    inputs = ["-i", str(still)]
    for png in text_pngs:
        inputs += ["-loop", "1", "-i", str(png)]
    if audio and Path(audio).exists():
        inputs += ["-i", str(audio)]

    # Slow push-in, and FORCE SQUARE PIXELS.
    #
    # zoompan emitted non-square pixels (measured: SAR 555:416, so the reel
    # displayed as 3:4 DAR 0.75 instead of 9:16 DAR 0.5625) — Instagram would
    # letterbox or stretch it. Two causes had to be fixed together:
    #   1. the still was not 9:16 to begin with, and scale to a mismatched
    #      aspect carries the difference into the pixel aspect ratio;
    #   2. zoompan's output inherits that ratio unless it is normalized.
    # `setsar=1` after zoompan is what actually pins it, and the crop below
    # means the picture is never distorted to get there.
    filters = [
        # pre-crop to the target aspect from the centre (no distortion), then
        # scale up for the push-in headroom
        f"[0:v]crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})',"
        f"scale={int(w * PUSH_IN)}:{int(h * PUSH_IN)},setsar=1,"
        f"zoompan=z='min(zoom+0.0004,{PUSH_IN})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration_s * fps)}:"
        f"s={w}x{h}:fps={fps},setsar=1[bg]"
    ]
    prev = "bg"
    for i, t in enumerate(times):
        label = f"t{i}"
        # OPEN-ENDED window: a line arrives at its beat and STAYS to the end.
        # A between(t, t_i, t_{i+1}) window made each line replace the previous
        # one, so the frame at t=4 showed LESS text than at t=0.5 — measured,
        # and the opposite of what the strategy asks for. The confession has to
        # build: the rename only lands if the scene it renames is still on
        # screen behind it, and the final frame is what a viewer screenshots or
        # forwards.
        filters.append(
            f"[{prev}][{i + 1}:v]overlay=0:0:enable='gte(t,{t})'[{label}]")
        prev = label
    filters.append(f"[{prev}]format=yuv420p[vout]")

    cmd = [FFMPEG, "-y", *inputs, "-filter_complex", ";".join(filters),
           "-map", "[vout]"]
    if audio and Path(audio).exists():
        cmd += ["-map", f"{len(text_pngs) + 1}:a", "-af", "loudnorm=I=-16",
                "-shortest"]
    cmd += ["-r", str(fps), "-c:v", "libx264", "-preset", "slow", "-crf", "16",
            "-movflags", "+faststart", "-t", f"{duration_s:.3f}", str(out_mp4)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    return out_mp4


def qa(result: dict) -> tuple:
    """The spec's render invariants, on the numbers that produced the build."""
    problems = []
    if not result.get("ok"):
        return False, {"error": result.get("reason", "no still")}
    dur = float(result.get("duration") or 0)
    times = result.get("times") or []
    ok, why = timing_ok(times, dur)
    if not ok:
        problems.append(why)
    if not duration_ok(dur):
        problems.append(f"duration {dur:.1f}s outside 16-20s")
    return (not problems), {"problems": problems, "duration": dur, "times": times}


def duration_ok(seconds: float) -> bool:
    return 16.0 - 0.01 <= float(seconds) <= 20.0 + 0.01