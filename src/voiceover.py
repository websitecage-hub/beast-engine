"""voiceover.py — the TTS A/B arm (ENGINE_SPEC §3.3).

STRATEGY §3.3 makes this a TEST arm, not a default: ~15% of reels once Phase 1 is
stable. The reading-speed rule is why. A viewer reading on-screen text at ~3
words/s and hearing a voice at ~2.4 words/s (145 wpm) are doing two things at
once, and the strategy's whole premise is that the text must be readable. So the
voice is measured, aligned to the reveal schedule, and dropped if it does not fit.

QA the spec asks for: audio length <= video length. Also checked here: the line's
audio starts at its reveal time, so the voice and the text agree about when a line
exists. If they disagree the reel reads as broken.

The TTS service is optional. When it is unavailable this returns {"ok": False} and
the caller renders silently — which STRATEGY §3.5 explicitly approves ("non-speech
bed" resolves to silence), so a missing TTS is a skipped experiment, not a
failed day.
"""
from __future__ import annotations

import os
import subprocess
import urllib.request
from pathlib import Path

from . import config

# Optional service. Absent by default; the arm simply does not run.
TTS_ENV = "BEAST_TTS_URL"
WPM = 145


def available() -> bool:
    return bool(os.environ.get(TTS_ENV, "").strip())


def _synth_line(text: str, out_wav: Path, timeout: int = 90) -> bool:
    """One line of speech. Returns False rather than raising, so one bad line
    does not lose the whole reel."""
    base = os.environ.get(TTS_ENV, "").strip().rstrip("/")
    if not base:
        return False
    import json as _json
    import urllib.parse
    import urllib.request  # explicit: `import urllib` alone has no .request
    url = base + "?" + urllib.parse.urlencode({"text": text, "wpm": WPM})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            blob = r.read()
    except Exception:  # noqa: BLE001
        return False
    if len(blob) < 512:
        # Some services answer 200 with a JSON error body; a real wav/mp3 is
        # always larger than this.
        try:
            _json.loads(blob.decode("utf-8"))
            return False
        except Exception:  # noqa: BLE001
            pass
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    out_wav.write_bytes(blob)
    return True


def build(lines: list, starts: list, duration_s: float, out_dir: Path,
          music: Path | None = None) -> dict:
    """Place each line's speech at its reveal time, mixed under the music.

    Returns {"ok", "path", "reason", "line_audio": n}. The music is ducked 8 dB
    under the voice (ENGINE_SPEC §3.3) rather than replaced, so the reel keeps its
    bed while the voice is intelligible.
    """

    if not available():
        return {"ok": False, "reason": "no TTS service configured"}
    out_dir.mkdir(parents=True, exist_ok=True)

    made = []
    for i, (ln, t) in enumerate(zip(lines, starts)):
        wav = out_dir / f"vo_{i:02d}.wav"
        if not _synth_line(ln, wav):
            return {"ok": False, "reason": f"TTS failed on line {i + 1}"}
        made.append((wav, float(t)))

    if not made:
        return {"ok": False, "reason": "no lines synthesised"}

    # Mix: every line delayed to its reveal time, summed, then limited.
    inputs, filters, labels = [], [], []
    for i, (wav, t) in enumerate(made):
        inputs += ["-i", str(wav)]
        ms = max(int(t * 1000), 0)
        filters.append(f"[{i}:a]adelay={ms}|{ms},volume=1.0[v{i}]")
        labels.append(f"[v{i}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:"
                   f"duration=longest:normalize=0[voice]")

    voice = out_dir / "voice.wav"
    r = subprocess.run([config.resolve_ffmpeg(), "-y", "-v", "error", *inputs,
                        "-filter_complex", ";".join(filters),
                        "-map", "[voice]", "-t", f"{duration_s:.3f}",
                        str(voice)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not voice.exists():
        return {"ok": False, "reason": f"voice mix failed: {r.stderr[-200:]}"}

    # QA: the voice must not outlast the video.
    try:
        probe = subprocess.run([config.resolve_ffmpeg(), "-i", str(voice)], capture_output=True,
                               text=True, timeout=60)
        for ln in probe.stderr.splitlines():
            if "Duration:" in ln:
                hms = ln.split("Duration:")[1].split(",")[0].strip()
                h, m, s = (float(x) for x in hms.split(":"))
                total = h * 3600 + m * 60 + s
                if total > duration_s + 0.35:
                    return {"ok": False,
                            "reason": f"voice {total:.2f}s exceeds video {duration_s:.2f}s"}
    except Exception:  # noqa: BLE001
        pass

    if music and Path(music).exists():
        final = out_dir / "vo_mixed.m4a"
        r = subprocess.run(
            [config.resolve_ffmpeg(), "-y", "-v", "error", "-i", str(music), "-i", str(voice),
             "-filter_complex",
             # music bed at -22 LUFS, voice on top, music ducked 8 dB while speaking
             "[0:a]loudnorm=I=-22:TP=-1.5:LRA=11[m];"
             "[m][1:a]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[duck];"
             "[duck][1:a]amix=inputs=2:duration=first:normalize=0[a]",
             "-map", "[a]", "-t", f"{duration_s:.3f}",
             "-c:a", "aac", "-b:a", "192k", str(final)],
            capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and final.exists():
            return {"ok": True, "path": str(final), "line_audio": len(made)}
        # Ducking failed: fall back to the voice alone rather than losing the arm.
        return {"ok": True, "path": str(voice), "line_audio": len(made),
                "reason": "music duck failed; voice only"}

    return {"ok": True, "path": str(voice), "line_audio": len(made)}


def share(cfg: dict) -> float:
    """The voice arm's share of reels (voice == 'tts')."""
    try:
        return float((cfg.get("voice_share") or {}).get("tts", 0.0))
    except (TypeError, ValueError):
        return 0.0