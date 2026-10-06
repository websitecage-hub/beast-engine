"""bed.py — the quiet non-speech bed, or silence (STRATEGY.md, ENGINE_SPEC.md).

The strategy is blunt about this: "original, and not a voice. This audience
hears performance instantly. An AI narrator is another mask." So the audio API
is used for a quiet bed ONLY — room tone, rain, a vibration, a turn signal —
unique per reel so the audio counts as original.

MEASURED CAPABILITY OF THIS STACK (the honest limit):

  The audio service this stack owns is a TRENDING/MUSIC SCRAPER, not a
  generator. Verified from its own root document:

      {"endpoints": {"download": "POST /v1/download {url, ...}",
                     "file":     "GET /v1/file/<niche>/<filename>",
                     "library":  "GET /v1/library/<niche>",
                     "niches":   "GET /v1/niches",
                     "trending": "GET /v1/trending/<niche>"}}

  There is no text-to-audio endpoint. It cannot synthesise room tone, rain or a
  buzz, and it cannot speak either — so the spec's conditional ("if the API can
  only generate speech, skip audio") resolves to the other branch: the service
  cannot produce a spoken bed, and it cannot produce the non-speech bed the
  strategy prefers.

  What it CAN do is fetch a source track, which is a different thing from the
  beds the strategy names. The strategy's own rule is that a voice is worse than
  silence and that the words are the payload. So the default here is SILENCE
  (a bed of last resort that is still legal), and a fetched music track is only
  used when the config explicitly opts in — because shipping a music track the
  strategy asked the system not to use is worse than shipping silence it did.

  If a future audio service exposes synthesis, set bed.mode = "synth" and
  implement `_synth_bed`; the rest of the pipeline is already wired for it.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from . import config, music

FFMPEG = config.resolve_ffmpeg()

# The beds the strategy names. Used to build BOTH a synth prompt (if synthesis
# ever exists) and the ffmpeg fallback tone for a given bed type.
BED_KINDS = (
    ("room_tone", "quiet room tone, distant street, low hum"),
    ("rain", "steady rain on a window, no thunder"),
    ("vibration", "a single phone vibration on a table, then quiet"),
    ("turn_signal", "a car turn signal ticking, quiet cabin"),
)

# A synthesized bed is strictly a LAST resort: the strategy prefers silence over
# anything performed, and a sine drone is performed. Kept behind a flag.
SYNTH_ALLOWED = False


def _has_audio_stream(path: Path) -> bool:
    try:
        return music.has_audio_stream(path)
    except Exception:  # noqa: BLE001
        return False


def _synth_bed(kind: str, out_mp3: Path, seconds: float) -> bool:
    """Generate a quiet non-speech bed with ffmpeg's own sources.

    Off by default (SYNTH_ALLOWED). Present so the spec's "audio API used for a
    quiet bed only" has a code path the moment a deploy wants one, and so the
    rejection reasons are testable.
    """
    if not SYNTH_ALLOWED:
        return False
    src = {
        "room_tone": "anoisesrc=c=brown:r=44100:a=0.012,lowpass=f=700",
        "rain": "anoisesrc=c=pink:r=44100:a=0.020,highpass=f=900,lowpass=f=6000",
        "vibration": "sine=f=90:r=44100:d=0.25",
        "turn_signal": "sine=f=1200:r=44100:d=0.06",
    }.get(kind, "anoisesrc=c=brown:r=44100:a=0.012,lowpass=f=700")
    # Impulse beds are one short sound by definition ("a single phone
    # vibration", "a car turn signal ticking"), so they are padded to the full
    # length rather than looped — a looped click would read as a metronome and a
    # short file would leave the tail of the reel silent by accident.
    impulse = kind in ("vibration", "turn_signal")
    audio_filter = "loudnorm=I=-30,afade=t=in:st=0:d=0.4," \
                   f"afade=t=out:st={max(seconds - 0.6, 0):.2f}:d=0.6"
    if impulse:
        audio_filter += f",apad=whole_dur={seconds:.2f}"
    try:
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", src, "-t", f"{seconds:.2f}",
             "-af", audio_filter,
             "-c:a", "libmp3lame", "-q:a", "6", str(out_mp3)],
            check=True, capture_output=True, timeout=180)
        return out_mp3.exists() and out_mp3.stat().st_size > 2048
    except Exception as exc:  # noqa: BLE001
        print(f"[bed] synth failed: {exc}")
        return False


def choose(cfg, content: dict, memory: dict, seconds: float,
           out_dir: Path | None = None, offline: bool = False) -> dict:
    """Return {ok, path, source, kind, reason}. `ok` False means SHIP SILENT.

    Silence is a valid, strategy-approved outcome, not a failure. The caller
    must not treat it as an error and must not substitute music the spec
    disallowed.
    """
    out_dir = out_dir or (config.ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = str((cfg.get("bed") or {}).get("mode") or "silence").lower()
    kind = random.choice([k for k, _ in BED_KINDS])

    if mode == "silence" or offline:
        return {"ok": False, "path": None, "source": "silence", "kind": kind,
                "reason": "bed.mode=silence — the strategy's default; words are the payload"}

    if mode == "synth":
        out_mp3 = out_dir / "bed.mp3"
        if _synth_bed(kind, out_mp3, seconds) and _has_audio_stream(out_mp3):
            return {"ok": True, "path": out_mp3, "source": "synth", "kind": kind,
                    "reason": "ok"}
        return {"ok": False, "path": None, "source": "silence", "kind": kind,
                "reason": "synth produced nothing usable — shipping silent"}

    if mode in ("music", "fetch"):
        # Explicit opt-in only. The strategy does not want a music track, so a
        # caller has to ask for one by name.
        got, meta = music.acquire(cfg, content, {}, memory, None, seconds,
                                  offline=offline)
        path = (meta or {}).get("path") if got else None
        if path and _has_audio_stream(Path(path)):
            return {"ok": True, "path": Path(path),
                    "source": (meta or {}).get("music_source", "music"), "kind": "music",
                    "reason": "ok (opt-in)"}
        return {"ok": False, "path": None, "source": "silence", "kind": kind,
                "reason": "music fetch returned nothing — shipping silent"}

    return {"ok": False, "path": None, "source": "silence", "kind": kind,
            "reason": f"unknown bed.mode {mode!r} — shipping silent"}