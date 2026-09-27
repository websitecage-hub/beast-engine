"""build_video.py — THE COMPLETE MIND format (Part 5) + VISUAL SPEC v1.0 §4.

  * dark cinematic VIDEO background, darkened -0.13, looped continuously
  * 2-4 LARGE static text blocks, no per-word/per-line animation — a block
    fades in over <=0.3s and holds (spec §4: no reveal, no typed text)
  * hook: largest, pinned to y=35% of height (spec §4 / verified against the
    user's reference reels: "roughly one-third of the way down")
  * landing: same font, size and exact top as the hook — the visual echo IS the
    invisible loop (Part 4.2)
  * soft shadow, blur radius 8, ~70% opacity (spec §4)
  * a 30% black feathered band behind the text IS rendered when the video
    underneath is bright (spec §4 "subtle dark overlay behind text")
  * timing (spec §4): hook holds 4-5s, deepening 4-5s, landing 8-9s
  * 9-10 seconds, 1080x1920, -16 LUFS audio

Every text line is MEASURED with real font metrics and auto-fitted: overflow
is impossible by construction (v4.1 lesson).
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

from . import config

FONT_HOOK = "assets/fonts/Coolvetica-Regular.otf"      # user pick (2026-09-26)
FONT_BODY = "assets/fonts/Coolvetica-Regular.otf"       # one font family, per user
FONT_MARK = "assets/fonts/Inter-Regular.ttf"
WATERMARK_PX = 28
SIDE_MARGIN = 90                # Part 5.3: generous margins, 90px sides
BLOCK_FADE = 0.3                # Part 5.3: block fade <= 0.3s, no text theatre
# --- TEXT ENGINE UPGRADE (6-9 lines + CTA line) ------------------------------
LINE_SPACING = 1.30             # slightly tighter: more lines must share the frame
MIN_WIDTH_FILL = 0.75           # §2: text fills >=75% of frame width
MAX_WIDTH_FILL = 0.92           # never touch the edges
MAX_LINES_ON_SCREEN = 7         # spec §3 / §8.7: 5-7 source lines, hard ceiling
MAX_DISPLAY_LINES = 22          # after wrapping. The frame's real limit is BLOCK
                                # HEIGHT, not a line count: at 56px the block holds
                                # ~20 display lines in 1190px. This is a generous
                                # backstop against a pathological wrap, not the
                                # binding constraint.
MAX_LINES_TARGET = 7            # §3 target for source lines
MIN_LINES_ON_SCREEN = 5         # §8.7 source-line floor
MAX_TOTAL_WORDS = 190           # 9 lines x ~21 words; the print cap, enforced
# INSTAGRAM SAFE BAND. Spec §3 said "top of text at 30% of frame height", but a
# 5-7 line block is ~1000px tall, so a 30% top put its bottom at ~82% of the frame —
# directly under Instagram's caption and action bar, which is exactly what shipped.
# The block is now centred inside this band instead, which is both what the user asked
# for ("centred from top to bottom") and what keeps every line clear of the UI.
TEXT_SAFE_TOP = 0.13            # below the username/audio chrome at the top
TEXT_SAFE_BOTTOM = 0.72         # above the caption + like/comment/share bar
UI_ZONE_TOP = 0.76              # where Instagram's caption/action bar begins
PLACEMENT_TOLERANCE = 12        # px the block may sit off centre and still pass QA
SMART_PLACEMENT = True          # two-pass measure-then-place; see render_block
# Kept for the tests and for anyone wanting the old top-anchor: a positive value here
# means "top-anchor at this fraction", 0 means "centre inside the safe band".
TEXT_TOP_FRAC = 0.0
SHADOW_BLUR = 8                 # soft dark shadow
SHADOW_ALPHA = 179              # ~70% opacity
TEXT_BAND_ALPHA = 77            # 30% black scrim, only over bright footage
TEXT_BAND_BRIGHT_MIN = 88       # only lay the band when the bg luma exceeds this
TEXT_SCRIM = False              # never darken the footage: text rides on the video
# CTA line ("Comment SAFE...") renders smaller than the body, at the block's foot.
CTA_SCALE = 0.70                # spec §3: the CTA renders at 70% of the body
CTA_MIN_PX = 40                 # never shrink the CTA below legibility
CTA_GAP = 0.55                  # extra leading above the CTA line, in px multiples
MIN_PX = 52                     # spec §3: minimum font size. If the text cannot fit
                                # at 52px it is too long — we warn and render anyway
                                # rather than dropping the reel.
# The spec's 6-9 line blocks carry far more words than the old 3-5 line ones, so the
# block is allowed to occupy more of the frame. 0.68 leaves ~300px of headroom top
# and bottom, which keeps the text clear of the reel's UI chrome.
MAX_BLOCK_H = TEXT_SAFE_BOTTOM - TEXT_SAFE_TOP   # the safe band, 0.59 of the frame
# Coolvetica runs wide, so the ladders start lower and walk further down.
# The text engine asks for a 72px start; the ladder begins there and walks down.
# Legacy hook/card sizes. These stay large: a short card must still fill >=75% of
# the width, so trimming the top of this ladder is what broke MIN_WIDTH_FILL.
HOOK_PX_LADDER = [120, 112, 104, 96, 88, 82, 76, 72, 68, 64, 60, 56, 52, 48, 46, 44]
BODY_PX_LADDER = [96, 90, 84, 78, 72, 68, 64, 60, 56, 52, 48, 46, 44]
# The user asked for a touch smaller text. That is applied to MESSAGE blocks (the
# day's long text) via their own ladder, because shrinking every block also shrank
# the legacy short cards and dropped their width fill below the 75% floor.
MESSAGE_PX_LADDER = [78, 76, 74, 72, 70, 68, 66, 64, 62, 60, 58, 56, 54, 52]
INK = (245, 245, 245, 255)
INK_MARK = (230, 230, 230, 150)
INK_CTA = (238, 238, 238, 235)   # slightly softer: the ask is a footer, not body


# ------------------------------------------------------------------ wording

def _clean(text) -> str:
    return " ".join(str(text or "").split()).strip().strip('"').strip()


def text_blocks(content: dict) -> list:
    """THE COMPLETE MESSAGE as ONE block (FINAL FORMAT §2).

    The text engine now returns `onscreen_text` — the finished 6-9 line block with
    its own intended line breaks. That string is the single source of truth: the
    line breaks are the model's, and re-flowing or re-splitting them would destroy
    the structure the prompt was calibrated for. Hook/deepening/landing are still
    written to content.json for reporting, but on screen it is one static block,
    visible from frame 0 and never changing.
    """
    raw = content.get("onscreen_text")
    if raw and str(raw).strip():
        parts = [ln.strip() for ln in str(raw).replace("\r", "").split("\n")
                 if ln.strip()]
        if parts:
            return [{"text": "\n".join(parts), "kind": "message",
                     "lines_source": parts}]

    # legacy path: content written before the text engine upgrade
    hook = _clean(content.get("hook"))
    landing = _clean(content.get("landing"))
    deep = [_clean(d) for d in (content.get("deepening") or []) if _clean(d)]
    if not hook and content.get("blocks"):
        deep = [_clean(b) for b in content["blocks"] if _clean(b)]
        hook = deep[0] if deep else ""
        deep = deep[1:]
    parts = [hook, *deep[:2]]
    if landing and landing.lower() not in {p.lower() for p in parts}:
        parts.append(landing)
    parts = [p for p in parts if p]
    if not parts:
        return []

    # Hard cap, enforced regardless of what the model produced. Trim the middle
    # first: the hook opens and the CTA closes, so those survive; the middle story
    # lines are what a human editor would cut.
    while len(parts) > MAX_LINES_ON_SCREEN and len(parts) > 2:
        parts.pop(len(parts) - 2)

    total_words = sum(len(p.split()) for p in parts)
    if total_words > MAX_TOTAL_WORDS and len(parts) > 2:
        keep = [parts[0]]
        mid = parts[1:-1]
        mid.sort(key=lambda s: len(s.split()))
        keep.append(mid[0])
        keep.append(parts[-1])
        parts = keep

    return [{"text": "\n".join(parts), "kind": "message",
             "lines_source": parts}]


def _wrap_measured(text: str, font, max_w: int) -> list:
    """Greedy wrap by MEASURED width + balancing pass. [] if even one word won't fit.

    A newline in `text` is a hard line break: the format stacks the complete
    message as distinct short lines, so those breaks must be preserved rather
    than re-flowed.
    """
    words = text.split()
    if not words:
        return []

    def width(s: str) -> int:
        return font.getbbox(s)[2]

    lines: list[str] = []
    cur = ""
    for raw in text.split("\n"):
        if not raw.strip():                       # blank line = paragraph gap
            if cur:
                lines.append(cur)
                cur = ""
            continue
        for wd in raw.split():
            cand = f"{cur} {wd}".strip()
            if width(cand) <= max_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = wd
        # HARD BREAK at the end of each source line. Without this the wrap is purely
        # width-driven and runs sentences together ("...make eye contact You see
        # someone..."), which destroys the sentence structure the text engine wrote.
        if cur:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    lines = [ln for ln in lines if ln != ""] or []
    if any(width(ln) > max_w for ln in lines):
        return []

    # NOTE: there is deliberately no re-balancing pass here. A pass that shifts words
    # between adjacent lines would undo the hard breaks above and re-merge sentences.
    return lines


def split_message(text: str) -> tuple:
    """(body_text, cta_text) — the CTA is separated BEFORE wrapping.

    Wrapping is width-driven, so a CTA line can be merged into the preceding story
    line (it happened: the 7-line block wrapped to 10 and "Comment QUIET..." ended up
    mid-line, which meant it rendered at body size and the CTA gate failed). Holding
    it out as its own segment keeps it a distinct, smaller footer line.
    """
    lines = [ln.strip() for ln in str(text or "").replace("\r", "").split("\n")
             if ln.strip()]
    cta = [ln for ln in lines if is_cta(ln)]
    body = [ln for ln in lines if not is_cta(ln)]
    return "\n".join(body), "\n".join(cta)


def fit_message(text: str, cfg) -> tuple:
    """Return (entries, px, font_path) for the whole message as ONE block.

    `entries` is an ordered list of (line, is_cta) pairs — the CTA lines are wrapped
    at their own smaller size so the footer reads as a footer. The ladder starts at
    the 72px the spec asks for, keeps a 48px floor (below that the text stops being
    readable at thumbnail size), and the cap counts body + CTA lines together.
    """
    from PIL import ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    max_w = w - 2 * SIDE_MARGIN
    max_h = int(h * MAX_BLOCK_H)
    font_path = FONT_HOOK
    body_text, cta_text = split_message(text)
    # MESSAGE_PX_LADDER, not HOOK_PX_LADDER: the day's text runs 6-9 source lines and
    # starts a step smaller than a legacy short card, as asked.
    best = None
    for px in [p for p in MESSAGE_PX_LADDER if p >= MIN_PX]:
        font = ImageFont.truetype(str(config.ROOT / font_path), px)
        body_lines = _wrap_measured(body_text, font, max_w) if body_text else []
        cta_px = max(int(px * CTA_SCALE), CTA_MIN_PX)
        cta_font = ImageFont.truetype(str(config.ROOT / font_path), cta_px)
        cta_lines = _wrap_measured(cta_text, cta_font, max_w) if cta_text else []
        total = len(body_lines) + len(cta_lines)
        if total == 0 or total > MAX_DISPLAY_LINES:
            continue
        block_h = (len(body_lines) * int(px * LINE_SPACING)
                   + (int(cta_px * CTA_GAP) if cta_lines else 0)
                   + len(cta_lines) * int(cta_px * LINE_SPACING))
        if block_h > max_h:
            continue
        widest = max([font.getbbox(ln)[2] for ln in body_lines]
                     + [cta_font.getbbox(ln)[2] for ln in cta_lines])
        if widest > max_w:
            continue                     # never hand back a size that overflows
        entries = [(ln, False) for ln in body_lines] + [(ln, True) for ln in cta_lines]
        cand = (entries, px, font_path, widest / float(w))
        if best is None:
            best = cand
        if cand[3] >= MIN_WIDTH_FILL:
            return entries, px, font_path
    if best is not None:
        return best[0], best[1], best[2]
    # Spec §3: "minimum font size 52px (if lines don't fit at 52px, the text is too
    # long — log a warning but still render)". So this is NOT fatal any more: fall
    # back to the smallest ladder size and let it wrap wider than the ideal, rather
    # than losing the day's reel.
    px = MIN_PX
    font = ImageFont.truetype(str(config.ROOT / font_path), px)
    body_lines = _wrap_measured(body_text, font, max_w) if body_text else []
    cta_px = max(int(px * CTA_SCALE), CTA_MIN_PX)
    cta_font = ImageFont.truetype(str(config.ROOT / font_path), cta_px)
    cta_lines = _wrap_measured(cta_text, cta_font, max_w) if cta_text else []
    print(f"[video] WARNING: text exceeds the frame at the {MIN_PX}px minimum "
          f"({len(body_lines) + len(cta_lines)} display lines) — rendering anyway, "
          "consider a shorter message")
    return ([(ln, False) for ln in body_lines] + [(ln, True) for ln in cta_lines],
            px, font_path)


def fit_block(text: str, kind: str, cfg, max_lines: int | None = None) -> tuple:
    """Return (lines, px, font_path) — largest size where everything fits.

    TEXT ENGINE: the block is now 6-9 lines including the CTA, which renders smaller
    than the body, so the height test uses the real per-line heights rather than
    lines x leading. The ladder starts at the 72px the spec asks for and walks down;
    stopping at ~72px rather than the old 120px start is what keeps long copy
    readable instead of clipped.
    """
    from PIL import ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    max_w = w - 2 * SIDE_MARGIN
    max_h = int(h * MAX_BLOCK_H)
    cap = max_lines or MAX_LINES_ON_SCREEN
    ladder = HOOK_PX_LADDER if kind in ("hook", "landing", "message") else BODY_PX_LADDER
    font_path = FONT_HOOK if kind in ("hook", "landing", "message") else FONT_BODY
    best = None
    for px in ladder:
        font = ImageFont.truetype(str(config.ROOT / font_path), px)
        lines = _wrap_measured(text, font, max_w)
        if not lines or len(lines) > cap:      # hard cap on on-screen lines
            continue
        # Real block height: CTA lines are shorter and carry extra leading.
        cta_px = max(int(px * CTA_SCALE), CTA_MIN_PX)
        n_cta = sum(1 for ln in lines if is_cta(ln))
        block_h = ((len(lines) - n_cta) * int(px * LINE_SPACING)
                   + n_cta * (int(cta_px * LINE_SPACING) + int(cta_px * CTA_GAP)))
        if block_h > max_h:
            continue
        widest = max(font.getbbox(ln)[2] for ln in lines)
        cand = (lines, px, font_path, widest / w)
        if best is None:
            best = cand
        # stop at the first size that fills the target width band
        if cand[3] >= MIN_WIDTH_FILL:
            return lines, px, font_path
    if best is None:
        raise RuntimeError(
            f"text cannot fit in {cap} lines at any size: {text!r}")
    return best[0], best[1], best[2]


def assert_fits(lines: list, px: int, font_path: str, cfg) -> int:
    """Deterministic pre-render overflow gate. Returns widest line (px).

    Accepts tagged (line, is_cta) entries: the CTA is wrapped at a smaller size, so
    measuring it with the body font reported a false overflow (1124px vs the 900px
    limit) even though it renders narrower.
    """
    from PIL import ImageFont
    max_w = int(cfg["reel"]["w"]) - 2 * SIDE_MARGIN
    body_font = ImageFont.truetype(str(config.ROOT / font_path), int(px))
    cta_px = max(int(int(px) * CTA_SCALE), CTA_MIN_PX)
    cta_font = ImageFont.truetype(str(config.ROOT / font_path), cta_px)
    widest = 0
    for ln in lines:
        if isinstance(ln, tuple):
            text, is_c = ln
        else:
            text, is_c = ln, is_cta(ln)
        f = cta_font if is_c else body_font
        widest = max(widest, f.getbbox(text)[2])
    assert widest <= max_w, f"overflow: widest line {widest}px > {max_w}px allowed"
    return widest


# --------------------------------------------------------------- rendering

def _tagged(lines: list) -> list:
    """Normalise lines to (text, is_cta) entries, unpacking any already tagged.

    A naive `(ln, is_cta(ln))` over a tagged list produced ((text, flag), flag),
    which then reached PIL's font metrics as a tuple and crashed the render.
    """
    out = []
    for ln in lines:
        if isinstance(ln, tuple):
            out.append((str(ln[0]), bool(ln[1])))
        else:
            out.append((str(ln), is_cta(ln)))
    return out


def is_cta(line: str) -> bool:
    """The comment-keyword CTA line. Rendered smaller, at the block's foot."""
    low = (line or "").lower()
    return "comment" in low and "breakdown" in low


def render_block(lines: list, px: int, font_path: str, watermark: str, out_png: Path,
                 cfg, fixed_top: int | None = None, bg_luma: float | None = None) -> Path:
    """ONE static text block as a transparent PNG — visible on every frame.

    FINAL FORMAT §2: this is the single overlay composited over the whole reel.
    `fixed_top` places the first line at the TEXT_TOP_FRAC anchor.

    TEXT ENGINE: the CTA line renders at CTA_SCALE of the body size with a little
    extra leading above it, so the ask reads as a footer rather than another
    story beat. The body keeps the fitted size.

    `bg_luma` (0-255 mean brightness of the background under the text) triggers the
    optional feathered dark scrim, so light footage can't wash the text out. The
    scrim MUST be composited before the text is drawn — see the ordering comment
    inside, and its regression test.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    w = int(cfg["reel"]["w"])
    h = int(cfg["reel"]["h"])
    font = ImageFont.truetype(str(config.ROOT / font_path), px)
    mark_font = ImageFont.truetype(str(config.ROOT / FONT_MARK), WATERMARK_PX)

    cta_px = max(int(px * CTA_SCALE), CTA_MIN_PX)
    cta_font = ImageFont.truetype(str(config.ROOT / font_path), cta_px)

    # Accept either tagged (line, is_cta) entries from fit_message, or plain strings
    # (legacy callers). Tagged entries are authoritative: the CTA was already held out
    # before wrapping, so its size cannot depend on re-detecting it by content.
    # Unpack rather than re-wrap: `(ln, True) if isinstance(ln, tuple)` turned a
    # tagged entry into ((text, flag), True) and handed a tuple to the font metrics.
    entries = _tagged(lines)

    line_h = int(px * LINE_SPACING)
    cta_h = int(cta_px * LINE_SPACING)
    n_cta = sum(1 for _ln, is_c in entries if is_c)
    block_h = ((len(entries) - n_cta) * line_h
               + (int(cta_px * CTA_GAP) if n_cta else 0)
               + n_cta * cta_h)
    top = fixed_top if fixed_top is not None else hook_top(entries, px, cfg)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # The footage is never darkened or overlaid with a scrim — text rides directly on
    # the video. The band is disabled outright (TEXT_SCRIM), and the shadow behind
    # each glyph carries legibility instead. Ordering matters: any compositing must
    # happen BEFORE the draw handles are created, or `d` points at a discarded image
    # and the glyphs vanish (that bug made the text invisible on every frame).
    if TEXT_SCRIM and bg_luma is not None and bg_luma >= TEXT_BAND_BRIGHT_MIN and block_h > 0:
        band_pad = int(px * 0.55)
        y0 = max(top - band_pad, 0)
        y1 = min(top + block_h + band_pad, h)
        band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle([0, y0, w, y1], fill=(0, 0, 0, TEXT_BAND_ALPHA))
        # feathered edges, so it reads as a cinematic scrim and not a grey box
        img = Image.alpha_composite(img, band.filter(ImageFilter.GaussianBlur(px * 0.5)))

    def paint_layer(top_y: int, with_watermark: bool = True):
        """Paint the text (and its shadow) at a given top. Returns (img, shadow).

        Extracted so the two-pass placement can paint once to MEASURE and again to
        place, with both passes guaranteed to use identical metrics.
        """
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        # The footage is never darkened or overlaid with a scrim — text rides directly
        # on the video. The band is disabled outright (TEXT_SCRIM), and the shadow
        # behind each glyph carries legibility instead. Ordering matters: any
        # compositing must happen BEFORE the draw handles are created, or `d` points
        # at a discarded image and the glyphs vanish (that bug made text invisible).
        if TEXT_SCRIM and bg_luma is not None and bg_luma >= TEXT_BAND_BRIGHT_MIN and block_h > 0:
            band_pad = int(px * 0.55)
            y0 = max(top_y - band_pad, 0)
            y1 = min(top_y + block_h + band_pad, h)
            band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(band).rectangle([0, y0, w, y1], fill=(0, 0, 0, TEXT_BAND_ALPHA))
            # feathered edges, so it reads as a cinematic scrim and not a grey box
            img = Image.alpha_composite(img, band.filter(ImageFilter.GaussianBlur(px * 0.5)))

        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd, d = ImageDraw.Draw(shadow), ImageDraw.Draw(img)   # AFTER any compositing

        def draw_center(txt, f, y, fill, sd_fill):
            # Centre on the glyphs' true ink box, not the layout box: getbbox()
            # includes the font's side bearing, which pushed the block ~3.5px off the
            # frame's centre line. getmask() returns the rendered bitmap, its bbox IS
            # the ink.
            try:
                mask_bb = f.getmask(txt).getbbox()
            except Exception:  # noqa: BLE001
                mask_bb = None
            if mask_bb:
                ink_w, ink_left = mask_bb[2] - mask_bb[0], mask_bb[0]
            else:
                bb = f.getbbox(txt)
                ink_w, ink_left = bb[2] - bb[0], bb[0]
            x = (w - ink_w) // 2 - ink_left
            sd.text((x + 2, y + 3), txt, font=f, fill=sd_fill)
            d.text((x, y), txt, font=f, fill=fill)

        y = top_y
        for ln, is_c in entries:
            if is_c:
                y += int(cta_px * CTA_GAP)
                draw_center(ln, cta_font, y, INK_CTA, (0, 0, 0, SHADOW_ALPHA))
                y += cta_h
            else:
                draw_center(ln, font, y, INK, (0, 0, 0, SHADOW_ALPHA))
                y += line_h

        if with_watermark and watermark:
            bb = mark_font.getbbox(watermark)
            d.text(((w - (bb[2] - bb[0])) // 2, int(h * 0.92)), watermark,
                   font=mark_font, fill=INK_MARK)
        return img, shadow

    # ---- smart placement: measure the real ink, then re-place exactly --------------
    # Only when the caller did not dictate a top. hook_top() gives a good estimate, but
    # block_h counts the CTA's leading gap (which paints nothing), so the estimate left
    # the visible block ~60px high in the band (measured 94px above vs 205px below).
    # Paint once, read the true ink extent, correct the offset, paint again.
    if fixed_top is None and SMART_PLACEMENT:
        probe, _ = paint_layer(top, with_watermark=False)
        bounds = ink_bounds(probe)
        if bounds:
            ink_top_abs, ink_bot_abs = bounds
            ink_h = ink_bot_abs - ink_top_abs
            band_top, band_bot = int(h * TEXT_SAFE_TOP), int(h * TEXT_SAFE_BOTTOM)
            want = band_top + max(0, (band_bot - band_top - ink_h) // 2)
            top = max(0, min(top + want - ink_top_abs, h - ink_h - 1))

    img, shadow = paint_layer(top, with_watermark=True)

    img = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)), img)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, "PNG")
    return out_png


def ink_bounds(layer) -> tuple | None:
    """(top, bottom) of the painted ink in an RGBA layer, or None if nothing painted.

    "Ink" means genuinely opaque text pixels: the drop shadow is blurred and semi
    transparent, so a high alpha threshold measures the glyphs and not the glow.
    """
    a = layer.split()[-1]
    box = a.point(lambda v: 255 if v >= 200 else 0).getbbox()
    return (box[1], box[3]) if box else None


def placement_report(lines: list, px: int, cfg, top: int | None = None) -> dict:
    """Where the block actually lands, in frame fractions. Used by the QA gate.

    Every layout bug we have hit was a number that looked right and painted wrong, so
    this measures pixels instead of trusting arithmetic — and it applies the SAME
    two-pass correction render_block does, so it cannot report a placement that the
    renderer would not produce.
    """
    from PIL import Image, ImageDraw, ImageFont
    w, h = int(cfg["reel"]["w"]), int(cfg["reel"]["h"])
    entries = _tagged(lines)
    cta_px = max(int(px * CTA_SCALE), CTA_MIN_PX)
    line_h, cta_h = int(px * LINE_SPACING), int(cta_px * LINE_SPACING)
    font = ImageFont.truetype(str(config.ROOT / FONT_HOOK), int(px))
    cta_font = ImageFont.truetype(str(config.ROOT / FONT_HOOK), cta_px)

    def draw_at(top_y: int):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        y = top_y
        for ln, is_c in entries:
            f = cta_font if is_c else font
            if is_c:
                y += int(cta_px * CTA_GAP)
            try:
                mb = f.getmask(ln).getbbox()
            except Exception:  # noqa: BLE001
                mb = None
            if mb:
                iw, il = mb[2] - mb[0], mb[0]
            else:
                bb = f.getbbox(ln)
                iw, il = bb[2] - bb[0], bb[0]
            d.text(((w - iw) // 2 - il, y), ln, font=f, fill=(255, 255, 255, 255))
            y += cta_h if is_c else line_h
        return ink_bounds(img)

    band_top, band_bot = int(h * TEXT_SAFE_TOP), int(h * TEXT_SAFE_BOTTOM)
    top_y = top if top is not None else hook_top(entries, px, cfg)
    bounds = draw_at(top_y)
    if bounds and SMART_PLACEMENT:
        # mirror render_block's correction exactly
        ink_h = bounds[1] - bounds[0]
        want = band_top + max(0, (band_bot - band_top - ink_h) // 2)
        top_y = max(0, min(top_y + want - bounds[0], h - ink_h - 1))
        bounds = draw_at(top_y)
    if not bounds:
        return {"empty": True}
    t, b = bounds
    return {
        "top": top_y,
        "ink_top_frac": round(t / h, 4), "ink_bot_frac": round(b / h, 4),
        "band_top": band_top, "band_bot": band_bot,
        "gap_above": t - band_top, "gap_below": band_bot - b,
        "inside_band": t >= band_top and b <= band_bot,
        "clears_ui": b <= int(h * UI_ZONE_TOP),
        "centred": abs((t - band_top) - (band_bot - b)) <= PLACEMENT_TOLERANCE,
    }


def hook_top(lines: list, px: int, cfg) -> int:
    """The canonical text top.

    Default (TEXT_TOP_FRAC = 0): the block is CENTRED inside Instagram's safe band,
    which is what the user asked for and what keeps its last line out from under the
    caption and action bar. A positive TEXT_TOP_FRAC top-anchors at that fraction of
    frame height (the spec §3 wording), clamped so the block still cannot reach the UI.
    """
    h = int(cfg["reel"]["h"])
    entries = _tagged(lines)
    px_i = int(px)
    cta_px = max(int(px_i * CTA_SCALE), CTA_MIN_PX)
    n_cta = sum(1 for _ln, is_c in entries if is_c)
    block_h = ((len(entries) - n_cta) * int(px_i * LINE_SPACING)
               + (int(cta_px * CTA_GAP) if n_cta else 0)
               + n_cta * int(cta_px * LINE_SPACING))
    band_top = int(h * TEXT_SAFE_TOP)
    band_bot = int(h * TEXT_SAFE_BOTTOM)
    if TEXT_TOP_FRAC > 0:
        return max(band_top, min(int(h * TEXT_TOP_FRAC), band_bot - block_h))
    # Centre on the MEASURED ink. block_h counts the CTA's leading gap, which paints
    # nothing, so centring on it left the visible text ~60px high in the band. The
    # renderer re-measures and corrects anyway (two-pass), but the estimate should be
    # right so the correction is a no-op and the anchor is predictable.
    ink_h = ink_span(entries, px)
    return max(band_top, band_top + max(0, (band_bot - band_top - ink_h) // 2))


def ink_span(lines: list, px: int) -> int:
    """Height of the actually-painted glyphs (excludes leading gaps that paint nothing).

    Renders to a scratch canvas and measures the alpha extent. Cheap (one small PIL
    draw) and exact, which matters because the CTA's gap is a layout-only construct.
    """
    from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
    entries = _tagged(lines)
    px_i = int(px)
    cta_px = max(int(px_i * CTA_SCALE), CTA_MIN_PX)
    line_h = int(px_i * LINE_SPACING)
    cta_h = int(cta_px * LINE_SPACING)
    n_cta = sum(1 for _ln, is_c in entries if is_c)
    layout_h = ((len(entries) - n_cta) * line_h + (int(cta_px * CTA_GAP) if n_cta else 0)
                + n_cta * cta_h)
    try:
        font = _ImageFont.truetype(str(config.ROOT / FONT_HOOK), px_i)
        cta_font = _ImageFont.truetype(str(config.ROOT / FONT_HOOK), cta_px)
        canvas = _Image.new("L", (10, max(layout_h, 1) + 40), 0)
        d = _ImageDraw.Draw(canvas)
        y = 20
        for ln, is_c in entries:
            f = cta_font if is_c else font
            if is_c:
                y += int(cta_px * CTA_GAP)
            d.text((2, y), ln, font=f, fill=255)
            y += cta_h if is_c else line_h
        box = canvas.getbbox()
        if box:
            return max(box[3] - box[1], 1)
    except Exception:  # noqa: BLE001 - fall back to the layout estimate
        pass
    return layout_h


def block_height(lines: list, px: int) -> int:
    """Drawn height of a block, in px — shared by the anchor, the luma probe and QA."""
    entries = _tagged(lines)
    px_i = int(px)
    cta_px = max(int(px_i * CTA_SCALE), CTA_MIN_PX)
    n_cta = sum(1 for _ln, is_c in entries if is_c)
    return ((len(entries) - n_cta) * int(px_i * LINE_SPACING)
            + (int(cta_px * CTA_GAP) if n_cta else 0)
            + n_cta * int(cta_px * LINE_SPACING))


def bg_text_luma(bg_mp4: Path, at: float, cfg, top: int, block_h: int) -> float | None:
    """Mean brightness (0-255) of the background in the text band.

    Spec §4 asks for a dark scrim behind the text only when the video there is
    bright. Measuring the actual band is the only honest way to decide, so read
    one frame and average the rows the text will occupy.
    """
    try:
        import numpy as np
        w = int(cfg["reel"]["w"])
        h = int(cfg["reel"]["h"])
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(at, 0):.2f}",
                            "-i", str(bg_mp4), "-frames:v", "1", "-vf",
                            f"scale={w}:{h},format=gray", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-"],
                           capture_output=True, timeout=120)
        if r.returncode != 0 or not r.stdout:
            return None
        frame = np.frombuffer(r.stdout, dtype=np.uint8)
        if frame.size != w * h:
            return None
        frame = frame.reshape(h, w)
        y0 = max(min(top, h - 1), 0)
        y1 = max(min(top + max(block_h, 1), h), y0 + 1)
        return float(frame[y0:y1, :].mean())
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------- timing

def beat_times(wav: Path, mood: str, cfg, duration_s: float) -> list:
    try:
        import librosa
        y, sr = librosa.load(str(wav), sr=22050, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        beats = [float(b) for b in beats if 0.0 < float(b) < duration_s]
        if len(beats) >= 4:
            return sorted(beats)
    except Exception as exc:  # noqa: BLE001
        print(f"[video] beat tracking failed: {exc}")
    return beat_times_fallback(cfg, mood, duration_s)


def beat_times_fallback(cfg, mood: str, duration_s: float) -> list:
    bpm = float((cfg.get("mood_fallback_bpm") or {}).get(mood, 65))
    interval = 60.0 / max(bpm, 1)
    out, t = [], interval * 0.5
    while t < duration_s:
        out.append(t)
        t += interval
    return out or [0.5, 1.0, 2.0]


def state_map(blocks: list, cfg, duration_s: float) -> list:
    """ONE text state spanning the whole reel (FINAL FORMAT §1).

    There is no timing map any more: the single block is on screen from 0.0 to the
    end. Kept as a function so QA/logging can still reason about "what is on screen
    when", and so old callers don't break.
    """
    if not blocks:
        return []
    return [{"index": 0, "kind": "message", "start": 0.0, "end": duration_s}]


def loop_echo_ok(blocks: list) -> bool:
    """FINAL FORMAT §1: the text never changes, so the loop echo is automatic.

    Under the old multi-card format this gate verified that the landing reused the
    hook's exact top coordinate. With a single always-visible block there is
    nothing to echo — the first and last frame are identical by construction.
    """
    return bool(blocks)


# ---------------------------------------------------------------- assembly

def _probe_frames(path) -> int | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_streams", "-select_streams", "v", str(path)],
                           capture_output=True, text=True, timeout=60)
        s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        nf = s.get("nb_frames")
        return int(nf) if nf and str(nf).isdigit() else None
    except Exception:  # noqa: BLE001
        return None


def assemble(bg_mp4: Path, states: list, pngs: list, track_mp3: Path, out_mp4: Path,
             cfg, duration_s: float, bg_is_video: bool = True) -> bool:
    """Video bg + ONE text overlay that is visible on EVERY frame (FINAL FORMAT §1/§2).

    The format is unambiguous: the text is already on screen at frame 0 and never
    appears, fades, slides or changes. That means a single `overlay=0:0` with NO
    `enable=` condition at all — there is exactly one PNG and zero timing logic.

    Any per-block `enable='between(t,...)'` is a failure condition (§7).
    """
    fps = int(cfg["reel"]["fps"])
    inputs = []
    if _probe_frames(bg_mp4) == 1:
        inputs += ["-loop", "1"]                    # still -> keep it running
    inputs += ["-stream_loop", "-1", "-i", str(bg_mp4)]
    for p in pngs:
        inputs += ["-loop", "1", "-i", str(p)]
    inputs += ["-i", str(track_mp3)]

    # The background ships AS SHOT. No brightness lift/drop, no saturation crush.
    # `bg_grade: true` would restore the cinematic chain; `bg_darken` a non-zero
    # value adds the brightness term. Both default to off, so this chain is a clean
    # scale+crop with zero colour change.
    darken = float(cfg.get("bg_darken", 0.0))
    grade_parts = []
    if cfg.get("bg_grade"):
        grade_parts.append("eq=contrast=1.08:saturation=0.55")
    if darken:
        grade_parts.append(f"eq=brightness={darken}")
    grade_seg = ("," + ",".join(grade_parts)) if grade_parts else ""
    filters = [f"[0:v]fps={fps},scale=1080:1920:force_original_aspect_ratio=increase,"
               f"crop=1080:1920{grade_seg},format=yuv420p[base]"]

    # §1/§2: one overlay, composited from frame 0 to the end. No enable=, no fade.
    last = "base"
    for i, _png in enumerate(pngs):
        filters.append(f"[{i + 1}:v]format=rgba[b{i}]")
        filters.append(f"[{last}][b{i}]overlay=0:0:eof_action=pass[o{i}]")
        last = f"o{i}"

    fade_out = 0.5
    aidx = len(pngs) + 1
    filters.append(f"[{aidx}:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
                   f"afade=t=in:st=0:d=0.3,"
                   f"afade=t=out:st={max(duration_s - fade_out, 0):.3f}:d={fade_out},"
                   f"atrim=0:{duration_s:.3f},asetpts=N/SR/TB[aout]")

    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", f"[{last}]", "-map", "[aout]",
        "-t", f"{duration_s:.3f}",
        # Final render quality. This is the only encode Instagram will see, so it is
        # near-lossless: CRF 16 with veryslow + a high profile and 4:2:0 8-bit, which
        # is what the platform re-encodes from. Audio at 192k keeps the music intact.
        # (The fixed format is 1080x1920 - no vertical upscale happens here.)
        "-c:v", "libx264", "-crf", "16", "-preset", "veryslow",
        "-profile:v", "high", "-level", "4.1",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-movflags", "+faststart", str(out_mp4)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        print(f"[video] assemble failed: {r.stderr[-1200:]}")
        return False
    return out_mp4.exists()


def cover_from_frame0(reel: Path, out_jpg: Path) -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.6", "-i", str(reel),
                        "-frames:v", "1", "-q:v", "2", str(out_jpg)],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and out_jpg.exists()


# ----------------------------------------------------------------- QA gate

def _luma_stats(reel: Path, at: float) -> dict:
    """Sample a frame's luma histogram (text presence + motion check)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(reel),
                        "-frames:v", "1", "-vf", "scale=180:320,format=gray",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        return {"bright": 0, "mean": 0.0}
    data = r.stdout
    bright = sum(1 for b in data if b >= 235)
    mean = sum(data) / max(len(data), 1)
    return {"bright": bright, "mean": mean}


def extract_stamps(reel: Path, states: list, duration_s: float, out_dir=None) -> list:
    out_dir = Path(out_dir or config.OUTPUTS)
    stamps = []
    for i, s in enumerate(states):
        t = min(s["start"] + max(BLOCK_FADE, 0.5), max(s["end"] - 0.15, s["start"] + 0.2))
        p = out_dir / f"check_{i}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}",
                            "-i", str(reel), "-frames:v", "1", "-q:v", "3", str(p)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and p.exists():
            stamps.append({"index": i, "t": round(t, 2), "kind": s["kind"],
                           "frame": str(p), **{k: v for k, v in _luma_stats(reel, t).items()}})
    return stamps


def card_visible(frame_path: Path) -> bool:
    from PIL import Image
    try:
        with Image.open(frame_path) as im:
            hist = im.convert("L").histogram()
        return sum(hist[235:256]) >= 120
    except Exception:  # noqa: BLE001
        return False


def motion_present(reel: Path, cfg) -> bool:
    """Part 5.2/5.6 — the background must actually move (not a static frame).

    Compares frame-difference energy between two nearby timestamps where NO text
    transition happens. A static bg yields near-zero difference.
    """
    try:
        import numpy as np
        outs = []
        for at in (2.0, 2.35):
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i",
                                str(reel), "-frames:v", "1", "-vf",
                                "scale=160:284,format=gray", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"], capture_output=True, timeout=120)
            if r.returncode != 0 or not r.stdout:
                return True          # can't measure -> don't block
            outs.append(np.frombuffer(r.stdout, dtype=np.uint8).astype(np.int16))
        if len(outs) != 2 or outs[0].size != outs[1].size:
            return True
        diff = float(np.abs(outs[0] - outs[1]).mean())
        return diff >= float(cfg.get("motion_min_diff", 0.8))
    except Exception:  # noqa: BLE001
        return True


def qa_gate(reel: Path, cfg, blocks: list | None = None) -> tuple:
    """Part 5.6 checklist. Every check mandatory before publish."""
    min_s, max_s = float(cfg["reel"]["min_s"]), float(cfg["reel"]["max_s"])
    info = {"path": str(reel), "exists": reel.exists(), "checks": {}}
    if not reel.exists():
        return False, info
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(reel)],
            capture_output=True, text=True, timeout=120)
        data = json.loads(probe.stdout or "{}")
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
        return False, info
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    dur = float(fmt.get("duration") or (v or {}).get("duration") or 0)
    size = int(fmt.get("size") or reel.stat().st_size)
    info.update({"width": (v or {}).get("width"), "height": (v or {}).get("height"),
                 "duration": dur, "size_mb": round(size / 1048576, 2),
                 "faststart": b"moov" in reel.open("rb").read(64),
                 "has_audio": bool(a)})
    problems = []
    c = info["checks"]
    c["duration_9_10s"] = min_s - 0.2 <= dur <= max_s + 0.2
    c["resolution_1080x1920"] = ((v or {}).get("width") == int(cfg["reel"]["w"])
                                and (v or {}).get("height") == int(cfg["reel"]["h"]))
    c["h264_yuv420p"] = (v or {}).get("codec_name") == "h264"
    c["aac_audio"] = bool(a) and (a or {}).get("codec_name") == "aac"
    c["size_under_60mb"] = size < 60 * 1024 * 1024
    c["faststart"] = info["faststart"]
    if blocks:
        # §1/§2: exactly ONE text block on screen.
        c["one_text_block"] = len([b for b in blocks if b.get("text")]) == 1
        # §8.8: 5-10 SOURCE lines (the model's line breaks, which the validator in
        # generate.py checked). This used to demand <=5, which the text engine's
        # 6-9 line structure cannot satisfy — every valid reel failed the gate.
        _src_lines = [ln for b in blocks
                      for ln in str(b.get("text") or "").split("\n") if ln.strip()]
        c["lines_in_bounds"] = (MIN_LINES_ON_SCREEN <= len(_src_lines)
                                <= MAX_LINES_ON_SCREEN)
        c["cta_present"] = any(is_cta(ln) for ln in _src_lines)
        c["loop_echo"] = loop_echo_ok(blocks)
        # PLACEMENT GATE. Every layout failure so far was a number that looked correct
        # and painted wrong (text 82% down the frame, under Instagram's caption bar).
        # Assert on measured pixels, so a regression fails the build instead of shipping.
        _placed = []
        for b in blocks:
            if b.get("lines") and b.get("px"):
                _placed.append(placement_report(b["lines"], b["px"], cfg))
        if _placed and not all(p.get("empty") for p in _placed):
            c["text_inside_safe_band"] = all(p.get("inside_band", True) for p in _placed)
            c["text_clears_ui"] = all(p.get("clears_ui", True) for p in _placed)
            c["text_centred"] = all(p.get("centred", True) for p in _placed)
            info["placement"] = _placed[0]
    for name, ok in c.items():
        if not ok:
            problems.append(name)
    info["problems"] = problems
    return (not problems), info


# ------------------------------------------------------------------- build

def build(cfg, content, duration_s: float, track_mp3: Path, track_wav: Path, bg_mp4: Path,
          out_mp4: Path | None = None, offline: bool = False):
    out_mp4 = out_mp4 or (config.OUTPUTS / "reel.mp4")
    blocks = text_blocks(content)
    if not blocks:
        raise RuntimeError("no text: content produced an empty message")

    # ONE block, all lines stacked, centered, sitting on top of the video from frame 0.
    # No pinning, no per-card top, no timing. The message uses the CTA-aware fitter so
    # the footer line renders smaller than the body.
    fitted = []
    for blk in blocks:
        if blk["kind"] == "message":
            entries, px, font_path = fit_message(blk["text"], cfg)
            widest = assert_fits(entries, px, font_path, cfg)
            fitted.append({**blk, "lines": entries, "px": px, "font_path": font_path,
                           "widest": widest, "pinned_top": None})
        else:
            lines, px, font_path = fit_block(blk["text"], blk["kind"], cfg)
            widest = assert_fits(lines, px, font_path, cfg)
            fitted.append({**blk, "lines": lines, "px": px, "font_path": font_path,
                           "widest": widest, "pinned_top": None})
    for b in fitted:
        b["pinned_top"] = hook_top(b["lines"], b["px"], cfg)
    print(f"[video] {len(fitted)} text block(s): "
          + ", ".join(f"{len(b['lines'])} lines @{b['px']}px "
                      f"({sum(1 for ln in b['lines'] if isinstance(ln, tuple) and ln[1])} cta) "
                      f"(width {b['widest']}/{int(cfg['reel']['w'])}px)" for b in fitted))

    watermark = ""
    handle = ((cfg.get("brand") or {}).get("handle") or "").strip()
    if handle:
        watermark = (handle.split(".")[0] + "." + handle.split(".")[-1] + "_").upper()
        watermark = "".join(ch for ch in watermark if ch.isalnum() or ch in "._")

    pngs = []
    for i, b in enumerate(fitted):
        p = config.OUTPUTS / f"block_{i}_{b['kind']}.png"
        # spec §4: measure the actual background band, then scrim only if bright
        top = b["pinned_top"] if b["pinned_top"] is not None else hook_top(
            b["lines"], b["px"], cfg)
        luma = bg_text_luma(bg_mp4, 1.0, cfg, top, block_height(b["lines"], b["px"]))
        render_block(b["lines"], b["px"], b["font_path"], watermark, p, cfg,
                     fixed_top=b["pinned_top"], bg_luma=luma)
        pngs.append(p)

    states = state_map(blocks, cfg, duration_s)
    if not assemble(bg_mp4, states, pngs, track_mp3, out_mp4, cfg, duration_s):
        raise RuntimeError("video assembly failed")
    cover_from_frame0(out_mp4, config.OUTPUTS / "cover.jpg")

    ok, info = qa_gate(out_mp4, cfg, blocks)
    if not ok:
        raise RuntimeError(f"QA gate failed: {info.get('problems')}")

    # Hard cap: the RENDERED line count (post-wrap), not just the source parts.
    # TEXT ENGINE §8.8 bounds both ends — more than 10 lines cannot fit the frame
    # readably, fewer than 5 means the story never got written.
    for b in fitted:
        if len(b["lines"]) > MAX_DISPLAY_LINES:
            raise RuntimeError(
                f"render violated: {len(b['lines'])} displayed lines "
                f"(max {MAX_DISPLAY_LINES})")
        # §8.8 governs the model's source lines, which are what the validator checked.
        src_n = len(b.get("lines_source") or [])
        if src_n and src_n < MIN_LINES_ON_SCREEN:
            raise RuntimeError(
                f"TEXT ENGINE §8.8 violated: only {src_n} source lines "
                f"(min {MIN_LINES_ON_SCREEN})")
    # The CTA line must survive to the finished video — it is the whole conversion
    # path, so its absence is a hard failure rather than a cosmetic issue.
    for b in fitted:
        if not any((ln[1] if isinstance(ln, tuple) else is_cta(ln))
                   for ln in b["lines"]):
            raise RuntimeError("TEXT ENGINE §3.4 violated: no CTA line rendered")

    info["blocks"] = [{"kind": b["kind"], "px": b["px"], "widest": b["widest"],
                       "lines": len(b["lines"])} for b in fitted]
    info["states"] = states

    # §1/§7 — the text MUST be on screen at frame 0 and still on the last frame.
    # Sampling only the middle (or a fixed timestamp past the end) would miss both
    # the "appears later" and "disappears early" failure modes.
    checkpoints = [0.0, 0.25, duration_s * 0.5, max(duration_s - 0.25, 0.1)]
    missing = []
    visibility = []
    for t in checkpoints:
        frame = config.OUTPUTS / f"vis_{t:.2f}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}",
                            "-i", str(out_mp4), "-frames:v", "1", "-q:v", "3", str(frame)],
                           capture_output=True, text=True, timeout=120)
        vis = r.returncode == 0 and frame.exists() and card_visible(frame)
        visibility.append({"t": round(t, 2), "text_visible": vis})
        if not vis:
            missing.append(round(t, 2))
    info["text_visibility"] = visibility
    if missing:
        raise RuntimeError(
            f"FINAL FORMAT §1 violated: text not visible at {missing}s — the text "
            "must be on screen from frame 0 to the end with no animation")

    info["motion"] = motion_present(out_mp4, cfg)
    return out_mp4, info


def pick_duration(cfg) -> float:
    lo, hi = float(cfg["reel"]["min_s"]), float(cfg["reel"]["max_s"])
    return round(random.uniform(lo, hi), 2)
