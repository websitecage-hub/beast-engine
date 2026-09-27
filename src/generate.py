"""generate.py — the text engine driver.

ONE call to the Meta LLM with mind.SYSTEM_PROMPT produces the reel's entire
payload: onscreen_text, caption, keyword, topic. That is the whole content
pipeline now — there is no candidate/judge/dedup tournament and no caption
assembly step, because the model writes the final caption including hashtags.

What this module still enforces in CODE (never trusting the model):
  * on-screen line count within the spec's 6-9 target (5-10 absolute bounds)
  * no "I"/"we" in on-screen text; no emojis; no hashtags on screen
  * the CTA line exists and carries the chosen keyword
  * the keyword is one of the DM-automation keywords and never repeats
    consecutively (the account's automation only fires on those words)
  * hashtags: 5-7, from the sanctioned pool
  * cluster / bg_type / mood / scene are derived from the keyword so the footage
    matches the story

Everything the downstream video pipeline needs is stored in content.json.
"""
from __future__ import annotations

import json
import re

from . import config, llm, mind, seo

SYSTEM_PROMPT = mind.SYSTEM_PROMPT

# The caption must close with hashtags drawn from this pool (spec §4). The last three
# appear in the spec's own examples, so they are legal too.
HASHTAG_POOL = [
    "socialanxiety", "overthinking", "socialanxietystruggles", "anxietyproblems",
    "introvertstruggles", "latenightthoughts", "mentalhealthmatters", "socialskills",
    "anxietysupport", "quietpeople", "overthinkers", "socialanxietyproblems",
    "deepthinkers", "phoneanxiety", "2amthoughts", "introvertproblems",
]
HASHTAGS_MIN, HASHTAGS_MAX = 5, 7

MIN_LINES, MAX_LINES = 5, 10      # spec §8.8 absolute bounds
TARGET_LINES = (6, 9)             # spec §3 target

BANNED_THERAPY = ("journey", "healing", "trauma", "toxic")
BANNED_CTA = ("link in bio", "save this", "share this", "comment below")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")

OFFLINE_CONTENT = {
    "onscreen_text": (
        "POV: you answered the phone instead of letting it ring out.\n"
        "Your voice came out too fast and too high.\n"
        "You said sorry twice for nothing at all.\n"
        "Then you talked over them and went quiet.\n"
        "You spent the rest of the day re-reading it.\n"
        "You're not broken. Your body just never learned that a phone was safe.\n"
        "Comment QUIET and I'll send you the full breakdown."
    ),
    "caption": (
        "You didn't do anything wrong on that call.\n\n"
        "The speed, the pitch, the two apologies — that's a nervous system "
        "responding to a threat it invented. Not a personality flaw.\n\n"
        "If this is you, you're not alone.\n\n"
        "Comment QUIET and I'll send you the full breakdown.\n\n"
        "#socialanxiety #phoneanxiety #overthinking #socialanxietystruggles #quietpeople"
    ),
    "keyword": "QUIET",
    "topic": "phone call avoidance",
}


# ------------------------------------------------------------------ validation

def _clean(text) -> str:
    return " ".join(str(text or "").replace("\r", "").split())


def _lines(onscreen: str) -> list:
    """On-screen lines, preserving the model's intended breaks."""
    raw = str(onscreen or "").replace("\r", "").split("\n")
    return [ln.strip() for ln in raw if ln.strip()]


def onscreen_problems(onscreen: str, keyword: str) -> list:
    """Every reason the on-screen text would be rejected. Empty list == good."""
    problems: list = []
    lines = _lines(onscreen)
    if not lines:
        return ["on-screen text is empty"]
    if len(lines) < MIN_LINES:
        problems.append(f"{len(lines)} lines (need >= {MIN_LINES})")
    if len(lines) > MAX_LINES:
        problems.append(f"{len(lines)} lines (need <= {MAX_LINES})")
    body = " ".join(lines)
    low = body.lower()
    # spec §8.1 — second person only. A bare "I " / "we " is a violation; "I'll" is
    # the mandated CTA wording and is explicitly required by §3.4, so it is allowed.
    if re.search(r"\b(i|we|our|my|us)\b(?!')", low):
        problems.append("first person in on-screen text")
    if EMOJI.search(body):
        problems.append("emoji in on-screen text")
    if "#" in body:
        problems.append("hashtag in on-screen text")
    for phrase in BANNED_THERAPY:
        if phrase in low:
            problems.append(f"therapy-speak: {phrase}")
    if "!" in body:
        problems.append("exclamation mark")
    for phrase in ("link in bio", "save this"):
        if phrase in low:
            problems.append(f"banned phrase: {phrase}")
    # §3.4 — the CTA line must exist and carry the keyword the automation listens for.
    cta = [ln for ln in lines if "comment" in ln.lower() and "breakdown" in ln.lower()]
    if not cta:
        problems.append("no CTA line")
    elif keyword.lower() not in cta[-1].lower():
        problems.append(f"CTA line does not contain keyword {keyword!r}")
    return problems


def caption_problems(caption: str, keyword: str) -> list:
    problems: list = []
    if not caption.strip():
        return ["caption is empty"]
    low = caption.lower()
    if EMOJI.search(caption):
        problems.append("emoji in caption")
    for phrase in BANNED_CTA:
        if phrase in low:
            problems.append(f"banned phrase: {phrase}")
    tags = extract_hashtags(caption)
    if not (HASHTAGS_MIN <= len(tags) <= HASHTAGS_MAX):
        problems.append(f"{len(tags)} hashtags (need {HASHTAGS_MIN}-{HASHTAGS_MAX})")
    for t in tags:
        if t.lstrip("#") not in HASHTAG_POOL:
            problems.append(f"hashtag off-pool: {t}")
    paras = [p for p in caption.split("\n\n") if p.strip()]
    if len(paras) > 5:                      # 4 short paragraphs + the tag line
        problems.append(f"{len(paras)} caption paragraphs (max 4 + tags)")
    if keyword and keyword.lower() not in low:
        problems.append(f"caption missing keyword {keyword!r}")
    return problems


def extract_hashtags(caption: str) -> list:
    return re.findall(r"#[A-Za-z0-9_]+", caption or "")


def pick_keyword(memory: dict, preferred: str = "") -> str:
    """The keyword for today. Never the same as the last reel's.

    Only keywords wired to the DM automation may be used — a keyword nobody has a
    rule for means the commenter never gets the breakdown.
    """
    last = str((memory.get("last_keyword") or "")).strip().upper()
    pref = (preferred or "").strip().upper()
    if pref in mind.KEYWORDS and pref != last:
        return pref
    pool = [k for k in mind.KEYWORDS if k != last]
    # Prefer keywords we have used least, so all of them stay in rotation.
    counts = memory.get("keyword_counts") or {}
    pool.sort(key=lambda k: (counts.get(k, 0), mind.KEYWORDS.index(k)))
    return pool[0] if pool else mind.KEYWORDS[0]


# ------------------------------------------------------------------ assembly

def _ensure_hashtags(caption: str) -> str:
    """Pad the tag line to the 5-7 range from the sanctioned pool.

    The model sometimes writes four (its own EXAMPLE 4 does). The spec's range is a
    hard requirement, so the shortfall is topped up in code rather than re-rolling
    the whole reel.
    """
    tags = extract_hashtags(caption)
    if len(tags) >= HASHTAGS_MIN:
        return caption
    have = {t.lstrip("#") for t in tags}
    extra = [f"#{t}" for t in HASHTAG_POOL if t not in have][:HASHTAGS_MIN - len(tags)]
    if not extra:
        return caption
    if tags:
        return caption.rstrip() + " " + " ".join(extra)
    return caption.rstrip() + "\n\n" + " ".join(extra)


def _fix_line_count(onscreen: str) -> str:
    """Trim toward the 6-9 line target without touching the CTA.

    The model is asked for 6-9 lines and usually complies; when it over-writes the
    hard bound is 10, and the safest cut is a middle story line — the CTA and the
    compassion pivot carry the reel's function.
    """
    lines = _lines(onscreen)
    if len(lines) <= MAX_LINES:
        return "\n".join(lines)
    cta = [i for i, ln in enumerate(lines)
           if "comment" in ln.lower() and "breakdown" in ln.lower()]
    keep_cta = cta[-1] if cta else None
    while len(lines) > MAX_LINES:
        cut = None
        for i in range(1, len(lines) - 1):
            if i == keep_cta:
                continue
            cut = i
            break
        if cut is None:
            break
        lines.pop(cut)
        if keep_cta is not None and cut < keep_cta:
            keep_cta -= 1
    return "\n".join(lines)


def build_user_prompt(directives: str = "", last_keyword: str = "") -> str:
    """The user message: a plain daily order, plus the account's live keyword list.

    The keyword list belongs HERE, not in the system prompt: the spec's §5 examples
    include words (FREEZE) that are not wired to this account's DM automation, and
    the spec prompt must stay verbatim. Naming the enabled words in the daily order
    keeps the CTA pointing at a rule that actually fires.
    """
    parts = ["Generate one reel for today."]
    parts.append(
        "For this account the CTA keyword MUST be exactly one of: "
        + ", ".join(mind.KEYWORDS)
        + ". These are the only words the DM automation listens for, so any other "
          "keyword means the commenter never receives the breakdown.")
    if directives:
        parts.append(f"Operator directive for this reel: {directives}")
    if last_keyword:
        parts.append(
            f"Do not use the keyword {last_keyword}; it was used on the previous "
            f"reel. Choose a different one from the list above.")
    return "\n".join(parts)


def _retarget_cta(onscreen: str, keyword: str) -> str:
    """Point the CTA line at `keyword`, preserving the spec's CTA wording.

    The CTA is a fixed template ("Comment X and I'll send you the full breakdown"),
    so only the keyword token varies. If the model names a keyword that is not wired
    to the automation, swapping the token keeps the reel usable instead of discarding
    a finished message over one word.
    """
    out = []
    for ln in _lines(onscreen):
        if "comment" in ln.lower() and "breakdown" in ln.lower():
            fixed = re.sub(r"(?i)(comment\s+)([A-Za-z]+)", rf"\g<1>{keyword}", ln,
                           count=1)
            out.append(fixed)
        else:
            out.append(ln)
    return "\n".join(out)


def _shape(data: dict, memory: dict, offline: bool) -> dict:
    """Turn one raw LLM response into the content dict the pipeline consumes."""
    keyword = pick_keyword(memory, str(data.get("keyword", "")))
    onscreen = _fix_line_count(str(data.get("onscreen_text", "")))
    # The model may name a keyword outside the automation's list; retarget the CTA
    # onto the keyword actually chosen so the comment always triggers a DM rule.
    onscreen = _retarget_cta(onscreen, keyword)
    caption = _ensure_hashtags(_retarget_cta(str(data.get("caption", "")), keyword))
    topic_label = _clean(data.get("topic")) or "social anxiety"

    cluster, bg_type, mood, scene, legacy_topic = mind.profile(keyword)
    if legacy_topic not in config.load_config()["topics"]:
        legacy_topic = config.load_config()["topics"][0]

    lines = _lines(onscreen)
    return {
        "onscreen_text": onscreen,
        "caption": caption,
        "keyword": keyword,
        "topic_label": topic_label,
        # derived pipeline settings — the footage must match the story
        "cluster": cluster,
        "archetype": cluster,
        "bg_type": bg_type,
        "mood": mood,
        "scene": scene,
        "topic": legacy_topic,
        # compatibility fields the video/publish stages still read
        "hook": lines[0] if lines else "",
        "landing": lines[-1] if lines else "",
        "deepening": lines[1:-1],
        "blocks": lines,
        "on_screen_text": " ".join(lines),   # legacy alias kept for reports
        "hashtags": extract_hashtags(caption),
        "loop_technique": "visual_echo",
        "is_hope": cluster == "quiet_hope",
        "include_whisper": False,
        "exploit": True,
        "rationale": f"keyword {keyword}",
        "bg_source": "",
        "bg_is_video": False,
        "music_source": "",
        "trending_ref": {"title": "", "artist": "", "genre": "", "trend_score": None},
    }


def _record(memory: dict, content: dict) -> None:
    memory["last_keyword"] = content["keyword"]
    counts = memory.setdefault("keyword_counts", {})
    counts[content["keyword"]] = counts.get(content["keyword"], 0) + 1
    memory.setdefault("used_hooks", []).append({
        "hook": content["hook"], "date": config.today_utc().isoformat(),
        "archetype": content["cluster"], "topic": content["topic"],
    })
    memory["used_hooks"] = memory["used_hooks"][-200:]


def generate(dry_run: bool = False, offline: bool = False) -> dict:
    cfg = config.load_config()
    memory = config.load_memory()
    directives = config.load_directives()

    if offline:
        content = _shape(dict(OFFLINE_CONTENT), memory, offline=True)
        content["bg_is_video"] = False
        content["bg_source"] = "bundled"
        content["music_source"] = "drone"
        content["alt_text"] = seo.build_alt_text(
            scene=content["scene"], topic=content["topic"], hook=content["hook"],
            on_screen_text=content["onscreen_text"])
        config.save_content(content)
        return content

    last_kw = str(memory.get("last_keyword") or "")
    user_msg = build_user_prompt(directives, last_kw)

    # Two attempts: a rejected response is usually one rule away from valid, and
    # re-rolling is cheap next to losing the day's post.
    last_problems: list = []
    for attempt in range(2):
        raw = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg}], expect_json=True)
        if not raw and attempt == 0:
            # expect_json can miss on a chatty response; the module still parses
            # raw text, so ask once more before giving up.
            raw = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_msg}], expect_json=False)
        if not isinstance(raw, dict):
            last_problems = ["no JSON object in response"]
            user_msg += ("\n\nYour previous reply was not valid JSON. Return ONLY "
                         "the JSON object with the four keys.")
            continue

        content = _shape(raw, memory, offline=False)
        problems = (onscreen_problems(content["onscreen_text"], content["keyword"])
                    + caption_problems(content["caption"], content["keyword"]))
        if not problems:
            break
        last_problems = problems
        print(f"[generate] attempt {attempt + 1} rejected: {problems}")
        user_msg += ("\n\nYour previous reply broke these rules: "
                     + "; ".join(problems)
                     + ". Fix exactly those and return the full JSON again.")
    else:
        raise RuntimeError(f"text engine produced no valid reel: {last_problems}")

    content["alt_text"] = seo.build_alt_text(
        scene=content["scene"], topic=content["topic"], hook=content["hook"],
        on_screen_text=content["onscreen_text"])

    if not dry_run:
        _record(memory, content)
        memory.setdefault("candidate_log", []).append({
            "date": config.today_utc().isoformat(),
            "keyword": content["keyword"], "topic": content["topic_label"],
            "hook": content["hook"],
        })
        config.save_memory(memory)

    config.save_content(content)
    return content


def preview_meta(content: dict) -> dict:
    """Small summary for logs/reports."""
    lines = _lines(content.get("onscreen_text", ""))
    return {"keyword": content.get("keyword"), "topic": content.get("topic_label"),
            "lines": len(lines), "hashtags": len(content.get("hashtags") or []),
            "caption_paras": len([p for p in content.get("caption", "").split("\n\n")
                                  if p.strip()])}


if __name__ == "__main__":
    print(json.dumps(preview_meta(generate(offline=True)), indent=2))