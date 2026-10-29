"""audio_features.py — the measurable audio features, with no silent failure.

The factory that produced the two new templates expected a module at
`analysis/audio_features.py`. It was never shipped, and the import was swallowed:
`audio_fingerprint()` returned `{}`, so `music_score()` returned the same neutral
`0.0` for every track and "closest matching track" silently degraded to "first
unused track". Nothing looked broken.

This module implements the same measurement, against the schema that the factory
actually recorded in `analysis/audio/reference_audio_features.json`:

    duration_s · rms_db · peak_db · loudness_curve_db[3] · tempo_bpm
    onset_density_per_s · spectral_centroid_hz · band_energy_pct{bass,mid,high}
    zero_crossing_rate · key_estimate

Units matter and are not interchangeable:

    rms_db / peak_db / loudness_curve_db are **dBFS** (relative to digital full
    scale, 20*log10). They are NOT LUFS. The acquisition path normalises delivered
    audio with ffmpeg `loudnorm` to about -20 LUFS, which is a different scale
    entirely; comparing the two is meaningless. Reference values here are about
    -16.9 dBFS RMS / -2.7 dBFS peak for format A.

Every function either returns a real measurement or raises `AudioFeatureError`.
There is no `{}` and no neutral zero: a missing measurement must be visible
upstream, because a scored pick and an unscored pick must never look alike.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import config

SR = 22050                       # analysis rate; matches the factory's decode step
BASS_HZ = 250.0                  # band split, power-summed
MID_HZ = 4000.0

# The fields a caller may rely on. `key_estimate` is advisory only.
REQUIRED_FEATURES = (
    "duration_s", "rms_db", "peak_db", "tempo_bpm",
    "onset_density_per_s", "spectral_centroid_hz", "zero_crossing_rate",
)


class AudioFeatureError(RuntimeError):
    """Audio could not be decoded or measured. Never downgraded to a default."""


def _ffmpeg() -> str:
    """The engine's ffmpeg (imageio-ffmpeg bundle or PATH)."""
    try:
        path = config.resolve_ffmpeg()
    except Exception:  # noqa: BLE001 — fall back to PATH lookup, then fail loudly
        path = shutil.which("ffmpeg") or ""
    if not path:
        raise AudioFeatureError("ffmpeg not found (imageio-ffmpeg or PATH)")
    return path


def decode_mono(path, sr: int = SR) -> tuple[np.ndarray, int]:
    """Decode any audio to mono float32 at `sr`. Raises AudioFeatureError."""
    src = Path(path)
    if not src.exists():
        raise AudioFeatureError(f"audio file not found: {src}")
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "decode.wav"
        r = subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-i", str(src), "-vn",
             "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not wav.exists():
            raise AudioFeatureError(
                f"decode failed for {src.name}: {(r.stderr or '').strip()[:200]}")
        try:
            import soundfile as sf
            y, got_sr = sf.read(str(wav), dtype="float32", always_2d=False)
        except Exception as exc:  # noqa: BLE001
            raise AudioFeatureError(f"wav read failed for {src.name}: {exc}") from exc
    y = np.asarray(y, dtype=np.float32).reshape(-1)
    if y.size == 0:
        raise AudioFeatureError(f"decoded zero samples: {src.name}")
    return y, got_sr


def _db(x: float) -> float:
    """Linear amplitude -> dBFS, floored so digital silence stays finite."""
    return 20.0 * math.log10(max(float(x), 1e-9))


def _band_energy_pct(y: np.ndarray, sr: int) -> dict:
    spec = np.abs(np.fft.rfft(y * np.hanning(y.size))) ** 2
    freqs = np.fft.rfftfreq(y.size, 1.0 / sr)
    total = float(spec.sum()) or 1.0
    bass = float(spec[freqs < BASS_HZ].sum())
    mid = float(spec[(freqs >= BASS_HZ) & (freqs < MID_HZ)].sum())
    high = float(spec[freqs >= MID_HZ].sum())
    return {"bass": round(100 * bass / total, 1),
            "mid": round(100 * mid / total, 1),
            "high": round(100 * high / total, 1)}


def _loudness_curve_db(y: np.ndarray, parts: int = 3) -> list:
    """RMS (dBFS) across equal segments — the factory recorded 3 values."""
    chunks = np.array_split(y, parts)
    return [round(_db(float(np.sqrt(np.mean(c ** 2)))), 1) for c in chunks if c.size]


_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _key_estimate(y: np.ndarray, sr: int) -> str:
    """Cheap chroma-vs-Krumhansl key guess. Advisory: never gates anything."""
    try:
        n = 1 << 14
        if y.size < n:
            return ""
        frames = [np.abs(np.fft.rfft(y[i:i + n] * np.hanning(n))) ** 2
                  for i in range(0, y.size - n, n)]
        if not frames:
            return ""
        spec = np.mean(frames, axis=0)
        freqs = np.fft.rfftfreq(n, 1.0 / sr)
        chroma = np.zeros(12)
        for f, mag in zip(freqs, spec):
            if f < 55 or f > 2000:
                continue
            midi = int(round(69 + 12 * math.log2(f / 440.0)))
            chroma[midi % 12] += mag
        if chroma.sum() <= 0:
            return ""
        chroma = chroma / chroma.sum()
        best = ("", -9e9)
        for shift in range(12):
            for profile, mode in ((_MAJOR, "major"), (_MINOR, "minor")):
                p = np.roll(profile, shift)
                corr = float(np.corrcoef(chroma, p / p.sum())[0, 1])
                if corr > best[1]:
                    best = (f"{_PITCH_NAMES[shift]} {mode}", corr)
        return best[0]
    except Exception:  # noqa: BLE001 — advisory only, but never a fake value
        return ""


def analyze(path) -> dict:
    """Full measured feature set for one audio file.

    Raises AudioFeatureError rather than returning a partial/empty dict, so a
    caller can never mistake "not measured" for "measured as neutral".
    """
    y, sr = decode_mono(path)
    duration = y.size / float(sr)
    if duration <= 0.05:
        raise AudioFeatureError(f"audio too short to measure: {Path(path).name}")

    rms = float(np.sqrt(np.mean(y ** 2)))
    peak = float(np.max(np.abs(y)))

    # librosa is the measured path for tempo/onsets/centroid; if it is unavailable
    # we fail, because a tempo of 0.0 would silently mis-rank every track.
    try:
        import librosa
    except Exception as exc:  # noqa: BLE001
        raise AudioFeatureError(f"librosa unavailable, cannot measure tempo: {exc}") from exc
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo_bpm = float(np.atleast_1d(tempo)[0])
        onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
        centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        zcr = float(np.mean(librosa.feature.zero_crossing_rate(y=y)))
    except Exception as exc:  # noqa: BLE001
        raise AudioFeatureError(f"feature extraction failed for {Path(path).name}: {exc}") from exc

    feats = {
        "file": Path(path).name,
        "duration_s": round(duration, 2),
        "rms_db": round(_db(rms), 1),
        "peak_db": round(_db(peak), 1),
        "loudness_curve_db": _loudness_curve_db(y),
        "tempo_bpm": round(tempo_bpm, 1),
        "onset_density_per_s": round(len(onsets) / duration, 2),
        "spectral_centroid_hz": round(centroid, 1),
        "band_energy_pct": _band_energy_pct(y, sr),
        "zero_crossing_rate": round(zcr, 4),
        "key_estimate": _key_estimate(y, sr),
    }
    missing = [k for k in REQUIRED_FEATURES if feats.get(k) in (None, "")]
    if missing:
        raise AudioFeatureError(f"incomplete measurement for {Path(path).name}: {missing}")
    return feats


# ------------------------------------------------------------------ comparison
# Tolerances used to turn a difference into a 0..1 closeness. They are the bands
# the factory used when scoring a track against a reference fingerprint.
DEFAULT_TOLERANCES = {
    "tempo_bpm": 60.0,
    "spectral_centroid_hz": 1200.0,
    "rms_db": 10.0,
    "onset_density_per_s": 2.5,
}


def distance(ref: dict, feats: dict, tolerances: dict | None = None) -> float:
    """Weighted distance in 0..1 between a reference and a candidate (0 = identical).

    Raises AudioFeatureError if the candidate has not actually been measured, so
    an unmeasured track cannot quietly collect the neutral score.
    """
    if not feats:
        raise AudioFeatureError("candidate has no measured features")
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    weights = {"tempo_bpm": 1.0, "spectral_centroid_hz": 0.6,
               "rms_db": 0.5, "onset_density_per_s": 0.4}
    # Two reference shapes exist on disk and both are legitimate: the style
    # profile's `music` block uses ref_* keys (ref_tempo_bpm, ...), while
    # analysis/audio/reference_audio_features.json uses the plain measured keys
    # (tempo_bpm, ...). Accept either rather than forcing one file to be rewritten.
    def ref_value(key: str):
        for probe in (f"ref_{key}", key):
            if ref.get(probe) is not None:
                return ref[probe]
        return None

    pairs = {
        "tempo_bpm": (ref_value("tempo_bpm"), feats.get("tempo_bpm")),
        "spectral_centroid_hz": (ref_value("spectral_centroid_hz"),
                                 feats.get("spectral_centroid_hz")),
        "rms_db": (ref_value("rms_db"), feats.get("rms_db")),
        "onset_density_per_s": (ref_value("onset_density_per_s"),
                                feats.get("onset_density_per_s")),
    }
    total_w = 0.0
    acc = 0.0
    for key, (a, b) in pairs.items():
        if a is None or b is None:
            raise AudioFeatureError(f"cannot compare {key}: reference or candidate value missing")
        acc += weights[key] * min(1.0, abs(float(a) - float(b)) / tol[key])
        total_w += weights[key]
    return round(acc / total_w, 4)


def closeness(ref: dict, feats: dict, tolerances: dict | None = None) -> float:
    """1.0 = a dead match, 0.0 = maximally far. Higher is better."""
    return round(1.0 - distance(ref, feats, tolerances), 4)
