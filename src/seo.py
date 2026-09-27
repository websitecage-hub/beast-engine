"""Instagram SEO layer — makes every reel a searchable, permanent asset.

Two search systems read this content:

  * Instagram Search  — name field, bio, caption, hashtags, alt text, on-screen text
  * Google Search     — captions, alt text, ON-SCREEN TEXT (crawlers read the overlay),
                        profile fields. Since Jul 2025 public professional accounts are
                        indexable, so a reel is a search asset that compounds for months.

Design rules, taken from the SEO spec:

  1. Primary keyword lands in the FIRST line of the caption, and it must read naturally.
     Matching the on-screen hook is preferred because it reinforces one phrase in both
     places. If the hook carries no keyword, we prefix a natural keyword sentence rather
     than jamming a phrase into the hook itself.
  2. ONE primary keyword per caption, 1-2 secondary, long-tail used sparingly.
  3. Hashtags: 1-2 branded + 3-4 primary + 2-3 long-tail == 6-9 total. Never 30.
  4. Alt text describes the visual, then names the keyword topic, then the brand.
  5. No keyword stuffing. If a sentence reads like it was written for a crawler, it is
     wrong — we return the natural text instead.

This module is pure text assembly: no network, no LLM. That keeps it deterministic and
testable, and it means SEO cannot silently degrade a reel's creative content.
"""

from __future__ import annotations

import random
import re

# ---------------------------------------------------------------- keyword banks

PRIMARY_KEYWORDS = [
    "social anxiety",
    "overthinking conversations",
    "socially anxious",
    "quiet people",
    "introvert struggles",
]

SECONDARY_KEYWORDS = [
    "fear of being judged",
    "canceling plans relief",
    "hating phone calls",
    "replaying conversations",
    "feeling behind everyone",
    "wanting to be alone but lonely",
    'performing "fine"',
    "exhausted from socializing",
]

LONG_TAIL_KEYWORDS = [
    "why do i overthink every conversation",
    "relief when plans get cancelled",
    "social anxiety at work meetings",
    "how to stop replaying conversations",
    "introvert vs social anxiety",
]

# ---------------------------------------------------------------- hashtag pools

BRANDED_TAGS = ["#unleashthebeast", "#socialanxietyhelp"]

PRIMARY_TAGS = [
    "#socialanxiety", "#introvert", "#overthinking", "#quietpeople", "#mentalhealth",
    "#anxietyrelief", "#selfimprovement", "#introvertlife", "#sociallyanxious",
    "#overthinker", "#quietmind", "#innerwork",
]

LONG_TAIL_TAGS = [
    "#cancelingplans", "#replayingconversations", "#phonecallanxiety",
    "#meetinganxiety", "#eatingalone", "#2amthoughts", "#beingthequietone",
    "#performingfine", "#socialanxietyatwork", "#introvertproblems",
    "#behindeveryone", "#wantingtobealone",
]

BRAND_NAME = "Unleash The Beast"

# Topic -> the long-tail tags and keyword that fit that specific reel. Chosen so the
# hashtags match the CONTENT, not just the theme: a reel about cancelled plans should
# not carry #meetinganxiety.
TOPIC_MAP = {
    "fake_phone":            (["#beingthequietone", "#performingfine"], "quiet people"),
    "cancelled_plans":       (["#cancelingplans", "#wantingtobealone"], "social anxiety"),
    "the_2am_replay":        (["#2amthoughts", "#replayingconversations"], "overthinking conversations"),
    "food_order":            (["#eatingalone", "#phonecallanxiety"], "social anxiety"),
    "phone_call":            (["#phonecallanxiety", "#hatingphonecalls"], "socially anxious"),
    "work_meeting":          (["#socialanxietyatwork", "#meetinganxiety"], "social anxiety at work"),
    "small_talk":            (["#beingthequietone", "#introvertproblems"], "introvert struggles"),
    "group_chat":            (["#socialanxiety", "#quietmind"], "quiet people"),
}


def _keyword_for_topic(topic: str, fallback: str | None = None) -> str:
    """The primary keyword that best fits this reel's topic."""
    if topic and topic in TOPIC_MAP:
        return TOPIC_MAP[topic][1]
    if fallback:
        return fallback
    return "social anxiety"


def primary_in(text: str) -> str | None:
    """Which primary keyword (if any) already appears in the text. Case-insensitive.

    Used to avoid double-inserting a keyword the creative already used — the spec's
    no-stuffing rule enforced mechanically.
    """
    low = (text or "").lower()
    for kw in PRIMARY_KEYWORDS:
        if kw in low:
            return kw
    return None


def _natural_prefix(keyword: str) -> str:
    """A human sentence carrying the keyword, used when the hook has none.

    Written as standalone lines so a caption opens with a real sentence rather than a
    bare keyword fragment. Each one is chosen to sit naturally before the hook.
    """
    table = {
        "social anxiety": "This is what social anxiety actually looks like.",
        "overthinking conversations": "For anyone who overthinks every conversation.",
        "socially anxious": "If you are socially anxious, this one is yours.",
        "quiet people": "Quiet people know this feeling exactly.",
        "introvert struggles": "The introvert struggle nobody warns you about.",
        "social anxiety at work": "Social anxiety is loudest in a room full of people.",
    }
    return table.get(keyword, f"This is what {keyword} feels like.")


# ---------------------------------------------------------------- caption

def build_caption(hook: str, body: str = "", landing: str = "", *,
                  topic: str = "", on_screen_text: str = "", hashtags: list | None = None,
                  include_whisper: bool = False, whisper_line: str = "") -> str:
    """Assemble a caption that is searchable AND still reads like a person wrote it.

    Structure (spec §3.1):
        [hook line — carries the primary keyword naturally]
        [1-2 sentences describing the reel, with 1-2 secondary keywords]
        [landing line — the emotional payoff]
        [hashtags]
    """
    keyword = _keyword_for_topic(topic)
    lines = []

    # Line 1: the hook, carrying the primary keyword. If the hook has no keyword we
    # bind a natural keyword sentence to the SAME line rather than appending it below —
    # the spec wants the keyword in the first line, and a separate line would also read
    # as an afterthought. The hook's own rhythm is preserved as the second sentence.
    hook = (hook or "").strip()
    if hook:
        if primary_in(hook) is None and primary_in(on_screen_text) is None:
            lines.append(f"{_natural_prefix(keyword)} {hook}")
        else:
            lines.append(hook)

    # Body: 1-2 secondary keywords, naturally placed.
    secs = [s for s in SECONDARY_KEYWORDS if s not in (hook + " " + body).lower()]
    random.shuffle(secs)
    picked = secs[:2]
    if body:
        lines.append(body.strip())
    if picked:
        lines.append("The part nobody says out loud: " + " — ".join(picked) + ".")
    elif keyword and primary_in(" ".join(lines)) is None:
        lines.append(_natural_prefix(keyword))

    # Landing: the emotional payoff, kept last before tags. Skipped when the on-screen
    # block already ends with it — the final on-screen line IS the landing, so keeping
    # both duplicated the closing line verbatim in the shipped caption.
    landing = (landing or "").strip()
    if landing:
        def _norm(s: str) -> str:
            # Compare on letters and spaces only: the model often closes the hook with a
            # period and the landing with an em dash ("...now." vs "...now—"), which made
            # a naive equality check miss the duplicate and ship the line twice.
            return re.sub(r"[^a-z ]+", "", (s or "").lower()).strip()

        nl = _norm(landing)
        already = any(nl and nl in _norm(x) for x in lines)
        if not already:
            lines.append(landing)

    if include_whisper and whisper_line:
        lines.append(whisper_line.strip())

    if hashtags:
        lines.append(" ".join(hashtags))

    return "\n\n".join(x for x in lines if x)[:2200]


# ---------------------------------------------------------------- hashtags

def build_hashtags(topic: str = "", on_screen_text: str = "", *, seed: int | None = None) -> list:
    """1-2 branded + 3-4 primary + 2-3 long-tail, content-matched. Total 6-9.

    Never returns 30 generic tags: broad reach without intent is what the spec calls
    dead. Long-tail tags come from the topic map so they describe THIS reel.
    """
    rng = random.Random(seed)

    branded = list(BRANDED_TAGS[:1]) + rng.sample(BRANDED_TAGS[1:], 1)
    primary = rng.sample(PRIMARY_TAGS, 4)

    topic_tags = TOPIC_MAP.get(topic, (None, None))[0] or []
    long_tail = list(topic_tags)
    # Pad only up to the minimum of 2. Filling to 3 by sampling the whole pool dragged
    # unrelated tags onto a reel (#socialanxietyatwork on a fake-phone reel), and the
    # checklist allows 2-3, so the honest pair is preferable to a mismatched third.
    if len(long_tail) < 2:
        pool = [t for t in LONG_TAIL_TAGS if t not in long_tail]
        long_tail += rng.sample(pool, 2 - len(long_tail))
    long_tail = long_tail[:3]

    # The keyword already on screen gets its matching tag pulled in when we have room.
    if on_screen_text:
        kw = primary_in(on_screen_text)
        if kw:
            slug = "#" + re.sub(r"[^a-z]", "", kw.lower())
            if slug in PRIMARY_TAGS and slug not in primary:
                primary[-1] = slug

    out, seen = [], set()
    for t in branded + primary + long_tail:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            out.append(tl)
    return out[:9]


# ---------------------------------------------------------------- alt text

def _scene_sentences(scene: str) -> list:
    """Scene prompts arrive as comma-separated image directions, not prose.

    A real example: "wide shot, silhouette walking alone, wet city street at night,
    rain, moody lighting, cinematic". Taking the first ". "-split as a "sentence" would
    keep the whole string and truncate mid-phrase, so we split on commas and rebuild a
    readable description instead.
    """
    parts = [p.strip(" .") for p in re.split(r"[,\n]+", scene or "") if p.strip(" .")]
    return parts


def build_alt_text(scene: str = "", topic: str = "", hook: str = "",
                   bg_source: str = "", on_screen_text: str = "") -> str:
    """Alt text: visual description -> keyword topic -> brand. Read directly by Google.

    Without this, Google cannot "see" the video at all, only the caption. With it, the
    visual content itself becomes indexable.
    """
    parts = _scene_sentences(scene)
    if parts:
        visual = ", ".join(parts)
        # The scene only becomes a sentence once we know it is prose; a bare prompt is
        # lowercased and reads as a fragment, which is fine and worse to over-capitalise.
        visual = visual[0].upper() + visual[1:]
    else:
        visual = "A moody vertical video of a person alone in a quiet, ordinary setting"
    visual = visual.rstrip(".")
    if len(visual) > 220:
        visual = visual[:217].rsplit(" ", 1)[0].rstrip(",.") + "..."

    keyword = _keyword_for_topic(topic)
    # Sentence case the keyword clause. It is embedded after "Overlay text reads: ...",
    # so capitalising the whole string is not enough — the clause follows a full stop
    # and must not appear to start a sentence in lowercase.
    kw_clause = f"{keyword} content about overthinking every interaction"
    kw_phrase = kw_clause[0].upper() + kw_clause[1:]

    if on_screen_text:
        onscreen = " ".join(on_screen_text.split())
        # Strip the sentence-ending period BEFORE any truncation ellipsis, then strip
        # again after: truncation may append "...", and a following ". " produced
        # "....." in the shipped alt text.
        onscreen = onscreen.rstrip(".")
        # The on-screen block IS the reel's central keyword asset for Google, so keep
        # as much of it as the field allows rather than clipping at the first sentence.
        if len(onscreen) > 240:
            onscreen = onscreen[:237].rsplit(" ", 1)[0].rstrip(".") + "..."
        kw_phrase = "Overlay text reads: " + onscreen + \
            (" " if onscreen.endswith("...") else ". ") + kw_phrase

    return f"{visual}. {kw_phrase}. From {BRAND_NAME}."


# ---------------------------------------------------------------- on-screen SEO

SEO_STOP = set()
TOKEN = re.compile(r"[a-z]{4,}")

# Vocabulary that people actually type when searching this niche. Kept separate from
# the keyword PHRASES because on-screen text is short and colloquial: "Phone out. Head
# down." will never contain "social anxiety", but "phone", "overthinking", "alone" and
# "invisible" are all real query words. Measuring against the phrases alone would score
# every honest reel at zero and push the generator toward stuffing.
SEARCH_VOCAB = {
    "anxiety", "anxious", "social", "socially", "introvert", "overthink",
    "overthinking", "conversation", "conversations", "talk", "talking", "phone",
    "calls", "call", "plans", "cancelled", "canceling", "alone", "lonely", "quiet",
    "people", "judged", "judging", "awkward", "invisible", "exhausted", "drained",
    "tired", "pretending", "perform", "performing", "replay", "replaying", "scared",
    "nervous", "worried", "thoughts", "nobody", "ignored", "left", "behind",
    "avoid", "avoiding", "hide", "hiding", "comfortable", "uncomfortable",
    "interaction", "interactions", "stranger", "strangers", "group", "crowd",
}


def on_screen_seo_score(text: str) -> float:
    """Fraction of on-screen text tokens that are searchable topic words.

    Google reads the overlay, so this is a real signal — but it must not fight the
    creative. We only measure; we never rewrite the hook to chase this number.
    """
    toks = TOKEN.findall((text or "").lower())
    if not toks:
        return 0.0
    hits = [t for t in toks if t in SEARCH_VOCAB]
    return round(len(hits) / len(toks), 3)


def has_searchable_phrase(text: str) -> bool:
    """True when on-screen text carries keyword vocabulary (spec §5 / checklist §9).

    Accepts EITHER a full keyword phrase or two distinct search-vocabulary words. The
    two-word route exists because a reel that says "Phone out. Head down. Still
    invisible." is genuinely searchable ("invisible", "phone") while containing no
    phrase from the bank — rejecting it would force keyword stuffing into the creative,
    which the spec explicitly forbids.
    """
    low = (text or "").lower()
    for kw in PRIMARY_KEYWORDS + SECONDARY_KEYWORDS:
        if kw in low:
            return True
    toks = set(TOKEN.findall(low))
    return len(toks & SEARCH_VOCAB) >= 2


# ---------------------------------------------------------------- profile (human, once)

NAME_FIELD = f"{BRAND_NAME} | Social Anxiety Help"

BIO = ("Overcoming social anxiety. Real posts for the ones who overthink every "
       "conversation. The book that gets it → link below.")


def checklist(content: dict) -> dict:
    """Run the §9 checklist against a generated content dict and report pass/fail.

    Returned for the QA gate and the weekly report — SEO that is not measured quietly
    rots, so the build can assert on this.
    """
    cap = content.get("caption", "") or ""
    tags = content.get("hashtags", []) or []
    on_screen = content.get("on_screen_text", "") or ""
    first_line = cap.split("\n", 1)[0] if cap else ""

    branded = [t for t in tags if t in BRANDED_TAGS]
    primary = [t for t in tags if t in PRIMARY_TAGS]
    long_tail = [t for t in tags if t in LONG_TAIL_TAGS]

    # §5 note: an on-screen block can be deliberately low-SEO. The spec's own example
    # ("The conversation ends. The trial begins.") is called "emotional but low-SEO" and
    # is handled by pairing it with a keyword-bearing caption — NOT by rewriting the
    # creative, which §5 forbids. So this item passes when EITHER the overlay carries
    # searchable vocabulary OR the caption's first line already carries the keyword.
    on_screen_ok = (has_searchable_phrase(on_screen)
                    or primary_in(first_line) is not None)

    return {
        "primary_keyword_first_line": primary_in(first_line) is not None,
        "secondary_keywords_in_body": sum(
            1 for s in SECONDARY_KEYWORDS if s in cap.lower()) >= 1,
        "hashtag_count_6_to_9": 6 <= len(tags) <= 9,
        "hashtags_1_2_branded": 1 <= len(branded) <= 2,
        "hashtags_3_4_primary": 3 <= len(primary) <= 4,
        "hashtags_2_3_longtail": 2 <= len(long_tail) <= 3,
        "on_screen_has_searchable_phrase": on_screen_ok,
        "alt_text_present": bool((content.get("alt_text") or "").strip()),
        "caption_describes_content": len(cap.split()) >= 12,
    }