"""text_style.py — pick the text colour from the footage, not from a constant.

The owner's rule: the text must sit ON the background, so the colour has to follow
the background's brightness — yellow when the shot is dark, white when it is
bright, with a warm in-between. This is measured from the chosen clip's luma
(post-grade), so every render gets the colour that reads best on THAT footage.

Reference evidence (measured on the outliers):
  rock.s.w 5.7M views  -> #E0D29B on a dark street
  lifeliftman          -> #FFFFFF on a bright outdoor shot
  colleen 3.4M         -> #F8E7B1 (warm pale) on a mid-dark interior
"""
from __future__ import annotations

WARM = "#E0D29B"      # dark footage: the measured viral yellow
PALE = "#F7F0D2"      # mid footage: warm off-white
WHITE = "#FFFFFF"     # bright footage: maximum contrast

DARK_BELOW = 92.0
BRIGHT_ABOVE = 122.0


def choose(luma: float | None) -> dict:
    """Return {fill, stroke_px, why} for a measured background luma (0-255)."""
    if luma is None:
        # Unknown footage: the safe default is white with a real outline.
        return {"fill": WHITE, "stroke_px": 3, "why": "luma unknown; white is safest"}
    y = float(luma)
    if y < DARK_BELOW:
        return {"fill": WARM, "stroke_px": 2,
                "why": f"dark footage (luma {y:.0f} < {DARK_BELOW:.0f}): warm yellow"}
    if y > BRIGHT_ABOVE:
        return {"fill": WHITE, "stroke_px": 3,
                "why": f"bright footage (luma {y:.0f} > {BRIGHT_ABOVE:.0f}): white"}
    return {"fill": PALE, "stroke_px": 2,
            "why": f"mid footage (luma {y:.0f}): warm off-white"}


def for_background(measured: dict, grade: dict | None = None) -> dict:
    """Luma the viewer will actually see: natural luma x the bounded grade."""
    y = (measured or {}).get("yavg")
    if y is None:
        return choose(None)
    b = float((grade or {}).get("brightness") or 1.0)
    return choose(float(y) * b)