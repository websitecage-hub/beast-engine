"""music.py — provider chain: TrendingFree -> Library -> Drone. First success wins, never fatal.

TrendingFreeProvider:
  a. Trending fetch from the user's audio service (slow on first call of the day).
  b. Search query = mood_search[mood] + genre_hint[genre] + random trending_style.
  c. Resolve free direct-MP3 URLs (Pixabay, then Internet Archive).
  d. Acquire through the audio service: POST /v1/download, then GET /v1/file/reels/<name>.
  e. QA with ffprobe; loop if shorter than the reel.
"""
from __future__ import annotations

import random
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

from . import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0 Safari/537.36")
PIXABAY_MP3_RE = re.compile(r"https://cdn\.pixabay\.com/audio/[^\"'\s]+\.mp3")
_woken = False


# ---------------------------------------------------------------- helpers

def wake(api: str, retries: int = 6, wait: float = 15.0) -> bool:
    global _woken
    if _woken:
        return True
    for attempt in range(retries):
        try:
            if requests.get(f"{api}/health", timeout=30).status_code == 200:
                _woken = True
                return True
        except requests.RequestException:
            pass
        if attempt < retries - 1:
            time.sleep(wait)
    return False


def ffprobe_duration(path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return 0.0


def has_audio_stream(path) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60)
        return "audio" in out.stdout
    except Exception:  # noqa: BLE001
        return False


def to_mp3(src, dst) -> bool:
    """Transcode/remux any audio (webm/m4a/ogg...) to real mp3."""
    dst = Path(dst)
    if dst.exists() and ffprobe_duration(dst) > 0:
        return True
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                        "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(dst)],
                       capture_output=True, text=True, timeout=300)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 1024


def to_wav(src, dst, sr: int = 22050) -> bool:
    """Mono wav for librosa beat analysis."""
    dst = Path(dst)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                        "-vn", "-ac", "1", "-ar", str(sr), "-f", "wav", str(dst)],
                       capture_output=True, text=True, timeout=180)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 1024


def _unused_marker():
    pass


# ------------------------------------------------------- trending fetching

def fetch_trending(cfg) -> dict | None:
    """Fetch today's trending reference. Primary niche, then fallback niche.

    Returns {title, artist, genre, trend_score} or None. Never raises.
    """
    music = cfg["music"]
    api = music["trending_api"]
    wake(api)
    for niche in (music["trending_niche"], music.get("trending_fallback_niche")):
        if not niche:
            continue
        try:
            r = requests.get(f"{api}/v1/trending/{niche}", params={"limit": 10},
                             timeout=150)
            if r.status_code != 200:
                continue
            rows = (r.json() or {}).get("results") or []
            good = [x for x in rows
                    if float(x.get("confidence") or 0) >= float(music.get("min_confidence", 0.5))]
            if not good:
                continue
            good.sort(key=lambda x: (x.get("trend_score") or 0), reverse=True)
            top = good[0]
            category = str(top.get("category") or "")
            genre = category.split(":", 1)[1].strip() if ":" in category else ""
            return {
                "title": str(top.get("title") or ""),
                "artist": str(top.get("artist") or ""),
                "genre": genre,
                "trend_score": top.get("trend_score"),
            }
        except Exception:  # noqa: BLE001 — trending failure is never fatal
            continue
    return None


# --------------------------------------------------- free MP3 URL sourcing

def pixabay_urls(query: str, limit: int = 12) -> list:
    """Primary source: scrape cdn.pixabay.com/audio/*.mp3 links from a search page."""
    try:
        url = "https://pixabay.com/music/search/" + quote(query, safe="") + "/"
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        if r.status_code != 200:
            return []
        return PIXABAY_MP3_RE.findall(r.text)[:limit]
    except Exception:  # noqa: BLE001
        return []


def internet_archive_urls(query: str, collection: str | None = None, limit: int = 3) -> list:
    """Fallback source: official Internet Archive API -> first .mp3 per identifier.

    `collection` is appended to the query so an instrument/genre hint (e.g. `phonk`)
    can be ANDed with a mood phrase. Results are the search's own ordering (relevance),
    which is what we want — do not sort them.
    """
    out = []
    full = f"({query}) AND mediatype:audio"
    if collection:
        full = f"({query}) AND ({collection}) AND mediatype:audio"
    try:
        r = requests.get("https://archive.org/advancedsearch.php",
                         params={"q": full, "fl[]": "identifier", "rows": 20,
                                 "output": "json"},
                         headers={"User-Agent": UA}, timeout=60)
        if r.status_code != 200:
            return []
        docs = (((r.json() or {}).get("response") or {}).get("docs")) or []
        for doc in docs:
            ident = doc.get("identifier")
            if not ident:
                continue
            try:
                meta = requests.get(f"https://archive.org/metadata/{ident}",
                                    headers={"User-Agent": UA}, timeout=60).json()
            except Exception:  # noqa: BLE001
                continue
            for f in (meta.get("files") or []):
                name = f.get("name") or ""
                if name.lower().endswith(".mp3"):
                    out.append(f"https://archive.org/download/{ident}/{name}")
                    break
            if len(out) >= limit:
                break
    except Exception:  # noqa: BLE001
        return out
    return out


INSTRUMENT_HINTS = (
    "piano", "phonk", "trap", "drone", "strings", "cinematic", "lofi", "beat", "guitar",
)


def instrument_of(mood_search: str) -> str:
    """Pull a genre/instrument hint out of the mood phrase ('sad emotional piano')."""
    low = (mood_search or "").lower()
    for hint in INSTRUMENT_HINTS:
        if hint in low:
            return hint
    return ""


def _download_direct(url: str, out_path) -> bool:
    """Fetch an already-public media URL ourselves (no third-party service needed)."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=180, stream=True)
        if r.status_code != 200:
            return False
        with Path(out_path).open("wb") as fh:
            for chunk in r.iter_content(65536):
                fh.write(chunk)
        return Path(out_path).stat().st_size > 10240
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------- provider chain

def _acquire(api: str, url: str, title: str, artist: str) -> bytes | None:
    """POST /v1/download then GET /v1/file/reels/<filename>. Returns bytes or None."""
    try:
        r = requests.post(f"{api}/v1/download",
                          json={"url": url, "niche": "reels", "title": title, "artist": artist},
                          timeout=300)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not data.get("ok"):
            return None
        path = str(data.get("path") or "")
        fname = path.rsplit("/", 1)[-1]
        if not fname:
            return None
        fr = requests.get(f"{api}/v1/file/reels/{fname}", timeout=180)
        if fr.status_code == 200 and len(fr.content) > 10240:
            return fr.content
    except Exception:  # noqa: BLE001
        return None
    return None


def trending_free_provider(cfg, content, strategy, memory, trending_ref, track_mp3, track_wav,
                           duration_s: float):
    """Returns (True, meta) on success. Never raises.

    Candidates are tried in order. Each one is first pushed through the user's audio
    service (POST /v1/download) and fetched from /v1/file/reels/<name>; if that service
    cannot take a URL we already have a direct public URL, so we fall back to fetching it
    ourselves rather than losing the track entirely.
    """
    music = cfg["music"]
    api = music["trending_api"]
    mood = content.get("mood") or "dark_ambient"
    genre = (trending_ref or {}).get("genre") or ""
    styles = config.load_trending_styles()
    mood_search = music["mood_search"].get(mood, "dark ambient")
    query_parts = [mood_search]
    query_parts.append(music.get("genre_hint", {}).get(genre, ""))
    if styles:
        query_parts.append(random.choice(styles))
    full_query = " ".join(p.strip() for p in query_parts if p and p.strip())

    used_urls = {u.get("url") if isinstance(u, dict) else str(u)
                 for u in memory.get("used_track_urls", [])}

    candidates = [(u, "pixabay") for u in pixabay_urls(full_query)]
    if not candidates:
        hint = instrument_of(mood_search)
        term = f'"{hint}"' if hint else mood_search
        candidates += [(u, "archive") for u in internet_archive_urls(term, hint or None)]
        if not candidates and hint:
            candidates += [(u, "archive") for u in internet_archive_urls(mood_search)]
    candidates = [(u, s) for (u, s) in candidates if u not in used_urls]
    random.shuffle(candidates)

    title = (trending_ref or {}).get("title") or full_query
    artist = (trending_ref or {}).get("artist") or "free-license"

    for url, source in candidates[:4]:
        got = None
        raw = _acquire(api, url, title, artist)
        if raw:
            tmp = config.OUTPUTS / "track_dl.bin"
            tmp.write_bytes(raw)
            for cand in (tmp, config.OUTPUTS / "track_dl.webm"):
                if cand.exists() and to_mp3(cand, track_mp3):
                    got = "service"
                    break
        if not got:
            tmp = config.OUTPUTS / "track_direct.bin"
            if _download_direct(url, tmp) and to_mp3(tmp, track_mp3):
                got = "direct"
        if not got:
            continue
        if not has_audio_stream(track_mp3) or ffprobe_duration(track_mp3) < 5:
            continue
        if not to_wav(track_mp3, track_wav):
            continue
        return True, {"music_source": "trending_free", "url": url, "source_site": source,
                      "acquired_via": got, "query": full_query, "trending_ref": trending_ref}
    return False, {}


def library_provider(cfg, content, memory, track_mp3, track_wav, duration_s: float):
    """assets/audio/manifest.json fallback. Empty library -> skip."""
    try:
        manifest = config.load_json(config.AUDIO_MANIFEST_PATH, [])
    except Exception:  # noqa: BLE001
        return False, {}
    if not manifest:
        return False, {}
    mood = content.get("mood")
    used = set(memory.get("used_tracks", []))
    pool = [m for m in manifest if mood in (m.get("moods") or []) and m.get("file") not in used]
    if not pool:
        pool = [m for m in manifest if m.get("file") not in used]
    if not pool:
        return False, {}
    pick = random.choice(pool)
    src = config.ASSETS / "audio" / pick["file"]
    if not src.exists():
        return False, {}
    fade = 0.8
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(src),
         "-t", f"{duration_s:.3f}", "-af", f"afade=t=out:st={max(duration_s - fade, 0):.3f}:d={fade}",
         "-ar", "44100", "-ac", "2", str(track_mp3)],
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not has_audio_stream(track_mp3):
        return False, {}
    to_wav(track_mp3, track_wav)
    return True, {"music_source": "library", "track": pick.get("file")}


def drone_provider(cfg, content, track_mp3, track_wav, duration_s: float):
    """Last resort: synthesize dark ambient. Never fails."""
    d = max(duration_s, 8.0)
    fade_in, fade_out = 1.0, 1.5
    filt = (f"[0:a]volume=0.30[a0];[1:a]volume=0.25[a1];[2:a]lowpass=f=300,volume=0.5[a2];"
            f"[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0,"
            f"afade=t=in:st=0:d={fade_in},afade=t=out:st={max(d - fade_out, 0):.3f}:d={fade_out},"
            f"loudnorm=I=-16:TP=-1.5:LRA=11[out]")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"sine=frequency=55:duration={d:.3f}",
           "-f", "lavfi", "-i", f"sine=frequency=110:duration={d:.3f}",
           "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={d:.3f}:amplitude=0.6",
           "-filter_complex", filt, "-map", "[out]", "-t", f"{d:.3f}",
           "-ar", "44100", "-ac", "2", str(track_mp3)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not has_audio_stream(track_mp3):
        return False, {}
    to_wav(track_mp3, track_wav)
    return True, {"music_source": "drone"}


def acquire(cfg, content, strategy, memory, trending_ref, duration_s: float,
            dry_run: bool = False, offline: bool = False):
    """Run the chain. Always returns (True, meta) unless even the drone fails.

    offline=True skips the network tiers entirely (deterministic drone only), so
    `--offline` never touches the audio service — that path is not hermetic.
    """
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    track_mp3 = config.OUTPUTS / "track.mp3"
    track_wav = config.OUTPUTS / "track.wav"
    for f in (track_mp3, track_wav):
        if f.exists():
            f.unlink()

    if not offline:
        ok, meta = trending_free_provider(cfg, content, strategy, memory, trending_ref,
                                          track_mp3, track_wav, duration_s)
        if ok:
            return True, meta
        ok, meta = library_provider(cfg, content, memory, track_mp3, track_wav, duration_s)
        if ok:
            return True, meta
    ok, meta = drone_provider(cfg, content, track_mp3, track_wav, duration_s)
    if ok:
        return True, meta
    return False, {}
