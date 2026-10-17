"""normalize.py — one schema for every research artefact on disk.

The master architecture (Section 5.3) asks for a record that preserves the raw
evidence and separates observed facts from interpretation. This module produces
that record shape for whatever is actually present locally, and records what is
MISSING rather than filling it with a zero or a guess.

Supported local sources, all optional:
  * Reddit Atom/RSS feeds  (data/research/*.xml)
  * a pre-extracted Reddit corpus (data/research/corpus.json)
  * external reel records, if any exist (videos.json / Apify / vidIQ exports),
    normalised through ``normalize_reel_record`` so a future paid scrape would
    land in the same schema — but nothing here calls a network or a paid API.

The reel schema is populated only for fields the source genuinely supplies. A
metric the source does not carry is listed in ``missing`` and left ``None``;
this is what stops a missing view count from later being scored as zero.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path

# Fields a fully-populated performance record would carry. Absence of any of
# these is recorded per-record in `missing`, never invented.
REEL_FIELDS = [
    "platform", "canonical_url", "creator_handle", "content_id", "posted_at",
    "captured_at", "views", "likes", "comments", "shares", "saves", "reach",
    "followers", "creator_baseline_views", "outlier_multiple", "duration_s",
    "language", "niche", "hook_text", "format_family",
]

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_NONASCII = re.compile(r"[^\x00-\x7f]")
# A rough English signal. Research audit only; not a language classifier.
_EN_WORDS = {"the", "you", "and", "that", "have", "people", "when", "with",
             "this", "your", "they", "just", "like", "what"}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _stable_id(*parts) -> str:
    blob = "|".join(str(p) for p in parts if p is not None)
    return hashlib.sha1(blob.encode("utf-8", "ignore")).hexdigest()[:16]


def _clean(text: str) -> str:
    """Strip Reddit HTML wrappers and collapse whitespace."""
    if not text:
        return ""
    t = _HTML_TAG.sub(" ", str(text))
    t = (t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return _WS.sub(" ", t).strip()


def looks_english(text: str) -> str:
    """'en' | 'unknown'. Deliberately conservative: only labels English when it
    can see common function words. Everything else stays 'unknown' rather than
    being silently treated as English."""
    words = set(re.findall(r"[a-z']+", (text or "").lower()))
    if not words:
        return "unknown"
    hits = len(words & _EN_WORDS)
    if hits >= 3 or (hits >= 1 and len(words) <= 8):
        return "en"
    # non-ascii heavy and no function words -> not confidently English
    return "en" if hits >= 1 and not _NONASCII.search(text or "") else "unknown"


def _record(source: str, **kw) -> dict:
    # record_id and captured_at are popped up front; captured_at is re-seeded so
    # the REEL_FIELDS loop below (which includes it) can fill it without clobber.
    record_id = kw.pop("record_id", None) or _stable_id(
        source, kw.get("content_id"), kw.get("title"), kw.get("text"))
    kw.setdefault("captured_at", _now_iso())
    rec = {"record_id": record_id, "source": source}
    for f in REEL_FIELDS:
        rec[f] = kw.pop(f, None)
    rec.update(kw)  # remaining bespoke fields (subreddit, title, text, ...)
    missing = [f for f in REEL_FIELDS if rec.get(f) in (None, "")]
    rec["missing"] = missing
    return rec


# ------------------------------------------------------------------ reddit xml

def parse_reddit_xml(path: Path) -> list:
    """Parse an Atom feed of Reddit posts. Returns [] for an empty/broken file."""
    import xml.etree.ElementTree as ET
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    ns = "{http://www.w3.org/2005/Atom}"
    out = []
    for e in root.findall(ns + "entry"):
        title = _clean(e.findtext(ns + "title"))
        content = _clean(e.findtext(ns + "content"))
        link = e.find(ns + "link")
        url = link.get("href") if link is not None else None
        published = e.findtext(ns + "published") or e.findtext(ns + "updated")
        cat = e.find(ns + "category")
        sub = (cat.get("term") if cat is not None else None)
        cid = e.findtext(ns + "id")
        mtime = _dt.datetime.fromtimestamp(
            path.stat().st_mtime, _dt.timezone.utc).isoformat()
        if not title and not content:
            continue
        out.append(_record(
            "reddit_xml", platform="reddit", content_id=cid,
            canonical_url=url, posted_at=published, captured_at=mtime,
            subreddit=sub, title=title, text=content,
            language=looks_english(title + " " + content),
            niche=sub, source_file=path.name))
    return out


# --------------------------------------------------------------- reddit corpus

def load_corpus(path: Path) -> list:
    """The pre-extracted {sub,title,text} corpus, normalised to records."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("posts") or data.get("items") or []
    mtime = _dt.datetime.fromtimestamp(
        path.stat().st_mtime, _dt.timezone.utc).isoformat()
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        sub = item.get("sub") or item.get("subreddit")
        title = _clean(item.get("title") or "")
        text = _clean(item.get("text") or item.get("selftext") or "")
        out.append(_record(
            "reddit_corpus", platform="reddit",
            content_id=item.get("id") or _stable_id(sub, title, text),
            canonical_url=item.get("url") or item.get("permalink"),
            posted_at=item.get("created") or item.get("created_utc"),
            captured_at=mtime, subreddit=sub, title=title, text=text,
            language=looks_english(title + " " + text), niche=sub,
            source_file=path.name))
    return out


# ------------------------------------------------------------- external reels

# Map many possible supplier field names onto the one schema. A supplier that
# names a field something else still normalises; nothing is guessed.
_ALIASES = {
    "canonical_url": ("canonical_url", "url", "link", "reel_url", "post_url"),
    "creator_handle": ("creator_handle", "ownerUsername", "username",
                       "handle", "creator", "account"),
    "content_id": ("content_id", "id", "shortCode", "shortcode", "pk"),
    "posted_at": ("posted_at", "timestamp", "taken_at", "created_at", "date"),
    "views": ("views", "view_count", "play_count", "plays", "videoViewCount",
              "videoPlayCount"),
    "likes": ("likes", "like_count", "likesCount"),
    "comments": ("comments", "comments_count", "commentsCount"),
    "shares": ("shares", "share_count", "sharesCount"),
    "saves": ("saves", "save_count", "savesCount"),
    "followers": ("followers", "follower_count", "followersCount"),
    "creator_baseline_views": ("creator_baseline_views", "baseline_views",
                               "median_views"),
    "outlier_multiple": ("outlier_multiple", "outlier", "multiple"),
    "duration_s": ("duration_s", "duration", "videoDuration"),
    "hook_text": ("hook_text", "caption", "description", "text", "title"),
}


def _first(d: dict, keys) -> object:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def normalize_reel_record(item: dict, source: str = "external") -> dict:
    """Map one supplier record into the shared reel schema."""
    if not isinstance(item, dict):
        raise TypeError("reel record must be a dict")
    kw = {}
    for field, aliases in _ALIASES.items():
        kw[field] = _first(item, aliases)
    platform = item.get("platform") or "instagram"
    lang = item.get("language") or looks_english(str(kw.get("hook_text") or ""))
    rec = _record(source, platform=platform, language=lang, niche=item.get("niche"),
                  raw=item, **kw)
    # keep any explicit analyst fields the supplier already provided
    for f in ("format_family", "rights_notes", "observations", "hypotheses",
              "confidence", "analyst_version"):
        if item.get(f) is not None:
            rec[f] = item[f]
    return rec


def discover_external(dir_or_file) -> list:
    """Load any local external reel dataset (videos.json / Apify / vidIQ export)
    and normalise it. Returns [] when nothing is present. Never hits a network."""
    p = Path(dir_or_file)
    candidates = []
    if p.is_file() and p.suffix == ".json":
        candidates = [p]
    elif p.is_dir():
        candidates = sorted(p.glob("*.json"))
    out = []
    for f in candidates:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("items", "posts", "reels", "data", "results", "videos"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
            if not items and data:
                items = [data]
        for it in items:
            if isinstance(it, dict):
                out.append(normalize_reel_record(it, source=f.stem))
    return out


def normalize_all(research_dir) -> dict:
    """Normalise every local source under ``research_dir``.

    Returns {"records": [...], "by_source": {source: count}, "files": [...]}.
    """
    d = Path(research_dir)
    records = []
    files = []
    for f in sorted(d.glob("*.xml")) if d.is_dir() else []:
        got = parse_reddit_xml(f)
        records += got
        files.append((f.name, len(got)))
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        if f.name == "corpus.json" or f.name.endswith("_corpus.json"):
            got = load_corpus(f)
        else:
            got = discover_external(f)
        records += got
        files.append((f.name, len(got)))
    by_source: dict = {}
    for r in records:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    return {"records": records, "by_source": by_source, "files": files}


if __name__ == "__main__":
    import argparse
    from .. import config
    ap = argparse.ArgumentParser(description="Normalize local research artefacts")
    ap.add_argument("--dir", default=str(config.ROOT / "data" / "research"))
    a = ap.parse_args()
    res = normalize_all(a.dir)
    print(json.dumps({"by_source": res["by_source"], "files": res["files"],
                      "total": len(res["records"])}, indent=2))
