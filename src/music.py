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
    """Fetch today's trending reference — Part 4.5 Phase 2 window.

    Sounds in days 4-8 of their rise are the high-value adoption window: the
    algorithm is actively distributing reels using them. We approximate that by
    preferring mid-band trend_score momentum over the already-peaked top of the
    chart. Returns {title, artist, genre, trend_score, phase} or None. Never raises.
    """
    music = cfg["music"]
    api = music["trending_api"]
    wake(api)
    lo, hi = (music.get("phase2_score_band") or [0.35, 0.85])
    for niche in (music["trending_niche"], music.get("trending_fallback_niche")):
        if not niche:
            continue
        try:
            r = requests.get(f"{api}/v1/trending/{niche}", params={"limit": 25},
                             timeout=150)
            if r.status_code != 200:
                continue
            rows = (r.json() or {}).get("results") or []
            good = [x for x in rows
                    if float(x.get("confidence") or 0) >= float(music.get("min_confidence", 0.5))]
            if not good:
                continue
            scores = [float(x.get("trend_score") or 0) for x in good]
            smin, smax = min(scores), max(scores)
            span = max(smax - smin, 1.0)

            def norm(x):
                return (float(x.get("trend_score") or 0) - smin) / span

            # Phase 2 = the rising middle, not the peak and not the floor
            phase2 = [x for x in good if lo <= norm(x) <= hi]
            pool = phase2 or good
            pool.sort(key=lambda x: float(x.get("trend_score") or 0), reverse=True)
            top = pool[0]
            category = str(top.get("category") or "")
            genre = category.split(":", 1)[1].strip() if ":" in category else ""
            return {
                "title": str(top.get("title") or ""),
                "artist": str(top.get("artist") or ""),
                "genre": genre,
                "trend_score": top.get("trend_score"),
                "phase": "phase2" if phase2 else "fallback",
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


MOOD_TEMPO_BANDS = {
    # mood -> (min_bpm, max_bpm) — a quiet_devastating reel must not get a 140bpm
    # banger; a restrained_anger reel must not get a lullaby. Measured, not guessed.
    "quiet_devastating": (50, 95),
    "heavy_shadow": (40, 80),
    "muffled_world": (55, 95),
    "restrained_anger": (60, 105),
    "gentle_hope": (45, 85),
}
MOOD_RMS_FLOOR = 0.02       # near-silence is a broken download, not a mood match


def tempo_of(path) -> float | None:
    """Measured tempo (bpm) via librosa. None when analysis fails."""
    try:
        import librosa
        y, sr = librosa.load(str(path), sr=22050, mono=True)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        return float(tempo)
    except Exception:  # noqa: BLE001
        return None


def rms_of(path) -> float | None:
    try:
        import librosa
        y, sr = librosa.load(str(path), sr=22050, mono=True)
        return float(librosa.feature.rms(y=y).mean())
    except Exception:  # noqa: BLE001
        return None


def mood_matches(mood: str, track_path) -> bool:
    """Audio-verified mood gate: tempo within band + non-silent."""
    band = MOOD_TEMPO_BANDS.get(mood)
    if band is None:
        return True
    t = tempo_of(track_path)
    r = rms_of(track_path)
    if t is None:
        return True            # analysis failed -> don't block the chain
    lo, hi = band
    if not (lo <= t <= hi):
        print(f"[music] mood gate: {t:.0f}bpm outside {mood} band {lo}-{hi} — rejecting")
        return False
    if r is not None and r < MOOD_RMS_FLOOR:
        print(f"[music] mood gate: near-silent (rms {r:.4f}) — rejecting")
        return False
    return True


def _spec5_violates(text: str, cfg) -> bool:
    """VISUAL SPEC v1.0 §5 gate: piano / lofi / cheerful must never be searched.

    The config vocabulary is already clean, but a stale data/trending_styles.json or
    a genre hint could reintroduce a banned term, so enforce it at query time too.
    """
    low = (text or "").lower()
    banned = (cfg.get("music") or {}).get("banned_music_terms") or []
    return any(term in low for term in banned)


def _spec5_safe_query(cfg, mood: str) -> str:
    """A spec-compliant query, guaranteed free of banned terms."""
    music = cfg.get("music") or {}
    base = (music.get("mood_search") or {}).get(mood) or "dark ambient drone"
    return "dark ambient drone sub bass" if _spec5_violates(base, cfg) else base


def song_provider(cfg, content, memory, track_mp3, track_wav, duration_s: float):
    """Resolve by SONG NAME through the audio service's /v1/song (Instagram-first).

    The service resolves a title/artist across Instagram -> SoundCloud -> YouTube
    and returns {"ok": true, "path": "/data/reels/src-<hash>.mp3"} — there is no
    URL field, so the bytes come back via the same two-step as /v1/download:
    read `path`, then GET /v1/file/reels/<filename>.

    Spec §4.5 note: an outdoor-concert pop track is *not* the right bed for these
    reels, whose audio is the one element the strategy treats as fixed. The
    trending reference is used for its *mood* signal (genre/tempo), never as a
    literal song request, so the sound is always inside the §5 palette.
    """
    import requests

    music = cfg.get("music") or {}
    api = music.get("trending_api")
    if not api:
        return False, {}
    mood = content.get("mood") or "heavy_shadow"
    if not content.get("mood"):
        cluster = content.get("cluster") or content.get("archetype")
        mood = (cfg.get("cluster_mood_map") or {}).get(cluster, mood)

    # spec §5 palette, informed by the trending genre/mood signal
    ref = content.get("_trending_ref") or {}
    genre = (ref.get("genre") or "").strip().lower()
    query = _spec5_safe_query(cfg, mood)
    hint = (music.get("genre_hint") or {}).get(genre, "")
    if hint and not _spec5_violates(query + hint, cfg):
        query = f"{query}{hint}".strip()
    if _spec5_violates(query, cfg):
        query = _spec5_safe_query(cfg, mood)

    music["_song_title"] = query
    title = query

    try:
        wake(api)
        raw = _song_by_title(api, title)
        if not raw and title != _spec5_safe_query(cfg, mood):
            # the mood-specific phrasing found nothing; fall back to the base
            # palette query before giving up on this tier entirely
            title = _spec5_safe_query(cfg, mood)
            raw = _song_by_title(api, title)
        if not raw:
            return False, {}
    except Exception as exc:  # noqa: BLE001
        print(f"[music] /v1/song failed: {exc}")
        return False, {}

    tmp = config.OUTPUTS / "song_dl.bin"
    tmp.write_bytes(raw)
    if not any(c.exists() and to_mp3(c, track_mp3)
               for c in (tmp, config.OUTPUTS / "song_dl.webm")):
        return False, {}

    if not has_audio_stream(track_mp3) or ffprobe_duration(track_mp3) < 5:
        return False, {}
    if not mood_matches(mood, track_mp3):
        return False, {}
    to_wav(track_mp3, track_wav)
    return True, {"music_source": "song", "music_query": title,
                  "music_mood": mood, "duration_s": ffprobe_duration(track_mp3)}


def _song_by_title(api: str, title: str) -> bytes | None:
    """POST /v1/song with a NAME, then fetch the file it produced.

    Response shape (verified live): {"ok":true, "path":"/data/reels/src-<hash>.mp3",
    "duration_s":...} — no URL, so the bytes come from /v1/file/reels/<filename>.
    """
    try:
        r = requests.post(f"{api}/v1/song",
                          json={"title": title, "artist": "", "niche": "reels"},
                          timeout=300)
        ct = r.headers.get("content-type", "")
        if r.status_code != 200 or not ct.startswith("application/json"):
            return None
        data = r.json() or {}
        if not data.get("ok"):
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
    ourselves rather than losing the track entirely. Every acquired track must pass the
    measured mood gate (tempo band + RMS) before it is accepted.
    """
    music = cfg["music"]
    api = music["trending_api"]
    mood = content.get("mood") or "heavy_shadow"
    # Part 7.1: the cluster's own preferred sound family wins when the model
    # didn't pick one (keeps audio mood-matched to the content cluster).
    if not content.get("mood"):
        cluster = content.get("cluster") or content.get("archetype")
        mood = (cfg.get("cluster_mood_map") or {}).get(cluster, mood)
    genre = (trending_ref or {}).get("genre") or ""
    styles = config.load_trending_styles()
    mood_search = music["mood_search"].get(mood, "dark ambient")
    style = random.choice(styles) if styles else ""
    if _spec5_violates(style, cfg):
        style = ""                       # spec §5: never let a stale style through
    query_parts = [mood_search]
    query_parts.append(music.get("genre_hint", {}).get(genre, ""))
    if style:
        query_parts.append(style)
    full_query = " ".join(p.strip() for p in query_parts if p and p.strip())
    if _spec5_violates(full_query, cfg):
        full_query = _spec5_safe_query(cfg, mood)

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

    for url, source in candidates[:6]:
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
        if not mood_matches(mood, track_mp3):
            continue                      # tempo/silence mismatch — try the next one
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
        # /v1/song resolves a NAME (Instagram-first) — the trending-audio path
        content.setdefault("_trending_ref", trending_ref or {})
        ok, meta = song_provider(cfg, content, memory, track_mp3, track_wav, duration_s)
        if ok:
            return True, meta
        ok, meta = library_provider(cfg, content, memory, track_mp3, track_wav, duration_s)
        if ok:
            return True, meta
    ok, meta = drone_provider(cfg, content, track_mp3, track_wav, duration_s)
    if ok:
        return True, meta
    return False, {}
