"""Acceptance tests — v5.0 THE COMPLETE MIND.

Run: python3 tests/test_acceptance.py

Covers: the mind prompt, character limits + law gate, loop echo, the Part 5.5
timing map, measured text fit, variety guard, harvest metric hierarchy (sends
first), learning on fixture memory, and workflow YAML contracts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src import (alerts, analyze, background, build_video, config, generate,  # noqa: E402
                 harvest, mind, music, publish, run_create)

FIXTURES = ROOT / "tests" / "fixtures"


# ------------------------------------------------------------ test imports

def test_imports_clean():
    import src.run_create  # noqa: F401
    import src.run_measure  # noqa: F401
    import src.run_learn  # noqa: F401
    import src.run_health  # noqa: F401
    assert True


# ------------------------------------------------------------------- mind

def test_mind_prompt_complete():
    """The text engine prompt is the spec's, verbatim — the ONLY instruction set."""
    sp = generate.SYSTEM_PROMPT
    assert sp == mind.SYSTEM_PROMPT
    for marker in ("COMPASSIONATE WITNESS", "SECTION 1", "SECTION 9",
                   "OUTPUT FORMAT", "Comment [KEYWORD] and I'll send you the full breakdown",
                   "NEVER use \"I\" or \"we\""):
        assert marker in sp, f"mind prompt missing {marker!r}"
    # every keyword the account's DM automation listens for must be known here
    for kw in ("SAFE", "QUIET", "FREE", "REPLAY", "SEEN", "START", "GHOST", "MASK",
               "HEARD", "BLANK", "STILL", "ALONE", "CALM", "ENOUGH", "PEACE", "CLEAR"):
        assert kw in mind.KEYWORDS, f"{kw} missing from KEYWORDS"
        assert kw in mind.KEYWORD_PROFILE, f"{kw} has no pipeline profile"
    # each profile must map onto real config values the pipeline understands
    cfg = config.load_config()
    for kw, (_cl, bg, mood, scene, topic) in mind.KEYWORD_PROFILE.items():
        assert bg in cfg["bg_types"], f"{kw}: bg_type {bg!r} unknown"
        assert mood in cfg["moods"], f"{kw}: mood {mood!r} unknown"
        assert topic in cfg["topics"], f"{kw}: topic {topic!r} unknown"
        assert scene.strip(), f"{kw}: empty scene query"


# ------------------------------------------------------- text engine gates

def test_onscreen_validator_enforces_the_hard_constraints():
    """§8: second person, no emoji/hashtags, CTA carries the keyword, 5-10 lines."""
    good = ("When the phone rings and your whole body freezes.\n"
            "You watch it ring. You watch it stop.\n"
            "You tell yourself you'll call back in five minutes.\n"
            "Then five minutes becomes tomorrow.\n"
            "Then tomorrow becomes sorry, just saw this.\n"
            "It's not laziness. Your body treats a call like a threat.\n"
            "Comment QUIET and I'll send you the full breakdown.")
    assert generate.onscreen_problems(good, "QUIET") == []

    # §8.1 first person. "I'll" is the mandated CTA wording, so it must stay legal.
    bad_i = good.replace("You watch it ring.", "I watch it ring.")
    assert any("first person" in p for p in generate.onscreen_problems(bad_i, "QUIET"))
    assert not any("first person" in p for p in generate.onscreen_problems(good, "QUIET"))

    # §8.4/§8.5 emoji and on-screen hashtags
    assert any("emoji" in p for p in generate.onscreen_problems(good + "\n😂", "QUIET"))
    assert any("hashtag" in p for p in generate.onscreen_problems(good + "\n#anxiety", "QUIET"))
    # §8.3 therapy-speak
    assert any("therapy" in p for p in generate.onscreen_problems(
        good.replace("It's not laziness.", "It's part of your healing journey."), "QUIET"))
    # §8.8 line bounds
    assert any("lines" in p for p in generate.onscreen_problems(
        "\n".join([good.split("\n")[0]] * 4), "QUIET"))
    assert any("lines" in p for p in generate.onscreen_problems(
        "\n".join([good.split("\n")[0]] * 11), "QUIET"))
    # §3.4 the CTA must carry the keyword the automation listens for
    assert any("keyword" in p for p in generate.onscreen_problems(good, "SAFE"))
    assert any("no CTA" in p for p in generate.onscreen_problems(
        good.replace("Comment QUIET and I'll send you the full breakdown.", "That is it."),
        "QUIET"))


def test_caption_validator_enforces_structure():
    # Uses the tags the FINAL INSTALL sanctions (SECTION 4), not a legacy pool tag.
    good = ("You didn't do anything wrong on that call.\n\n"
            "The speed and the two apologies are a nervous system responding to a "
            "threat it invented.\n\nIf this is you, you're not alone.\n\n"
            "Comment QUIET and I'll send you the full breakdown.\n\n"
            "#socialanxiety #overthinking #socialanxietystruggles #anxietyproblems #quietpeople")
    assert generate.caption_problems(good, "QUIET") == []
    # 5-7 hashtags, and only from the sanctioned pool
    assert any("hashtags" in p for p in generate.caption_problems(
        good.replace("#quietpeople", ""), "QUIET"))
    assert any("off-pool" in p for p in generate.caption_problems(
        good.replace("#quietpeople", "#crypto"), "QUIET"))
    # §8.6 no link-in-bio CTA anymore
    assert any("banned" in p for p in generate.caption_problems(
        good.replace("Comment QUIET and I'll send you the full breakdown.",
                     "Link in bio for more."), "QUIET"))
    # keyword must appear in the caption
    assert any("keyword" in p for p in generate.caption_problems(good, "SAFE"))
    # §8.9 max 4 short paragraphs + the tag line
    bloated = good.replace("\n\nIf this is you", "\n\nA\n\nB\n\nC\n\nIf this is you")
    assert any("paragraphs" in p for p in generate.caption_problems(bloated, "QUIET"))


def test_keyword_never_repeats_and_stays_in_rotation():
    """§8.7: never the same keyword twice running; DM rules exist for every keyword."""
    mem = {"last_keyword": "QUIET"}
    seen = set()
    for _ in range(40):
        kw = generate.pick_keyword(mem)
        assert kw in mind.KEYWORDS, f"unknown keyword {kw}"
        assert kw != mem.get("last_keyword"), "keyword repeated consecutively"
        seen.add(kw)
        mem["last_keyword"] = kw
        counts = mem.setdefault("keyword_counts", {})
        counts[kw] = counts.get(kw, 0) + 1
    # least-used-first ordering should reach most of the pool
    assert len(seen) >= 10, f"rotation too narrow: {sorted(seen)}"


def test_hashtags_are_topped_up_into_the_required_range():
    """The model sometimes writes 4 tags (its own EXAMPLE 4 does); spec wants 5-7."""
    cap = ("Something true.\n\nA mechanism.\n\nYou're not alone.\n\n"
           "Comment SAFE and I'll send you the full breakdown.\n\n"
           "#socialanxiety #overthinking #socialanxietystruggles")
    fixed = generate._ensure_hashtags(cap)
    tags = generate.extract_hashtags(fixed)
    assert generate.HASHTAGS_MIN <= len(tags) <= generate.HASHTAGS_MAX, tags
    for t in tags:
        assert t.lstrip("#") in generate.HASHTAG_POOL


def test_static_block_makes_the_loop_structural():
    """The text is one static block visible the whole time, so the first and last
    frame are identical by construction and the loop is unconditional."""
    cfg = config.load_config()
    full = {
        "onscreen_text": ("The conversation ends. The trial begins.\n"
                          "What you said. What you didn't. The face they made.\n"
                          "You'll review the footage until 2am.\n"
                          "You've been cross-examining yourself since school.\n"
                          "Comment REPLAY and I'll send you the full breakdown."),
        "keyword": "REPLAY",
    }
    assert generate.onscreen_problems(full["onscreen_text"], "REPLAY") == []
    blocks = build_video.text_blocks(full)
    assert len(blocks) == 1
    assert build_video.loop_echo_ok(blocks) is True


def test_onscreen_text_is_taken_verbatim_from_the_engine():
    """The engine's line breaks are the design — re-flowing them is a regression."""
    content = {"onscreen_text": "One.\nTwo.\nThree.\nFour.\nFive.\nComment SAFE and "
                                "I'll send you the full breakdown."}
    blocks = build_video.text_blocks(content)
    assert len(blocks) == 1, "exactly one block"
    assert blocks[0]["lines_source"] == [
        "One.", "Two.", "Three.", "Four.", "Five.",
        "Comment SAFE and I'll send you the full breakdown."]
    assert blocks[0]["text"] == content["onscreen_text"]


# ------------------------------------------------- Part 5.3 / 5.5 format

def test_text_blocks_and_timing_map():
    """§1/§2: exactly ONE block containing the whole message, on screen from 0.0 to
    the end with no timing."""
    cfg = config.load_config()
    content = {
        "onscreen_text": ("The conversation ends. The trial begins.\n"
                          "What you said. What you didn't. The face they made.\n"
                          "You'll review the footage until 2am.\n"
                          "You've been cross-examining yourself since school.\n"
                          "Comment REPLAY and I'll send you the full breakdown."),
        "keyword": "REPLAY",
    }
    blocks = build_video.text_blocks(content)
    assert len(blocks) == 1, "§2: exactly one text block"
    assert blocks[0]["kind"] == "message"
    src = blocks[0]["lines_source"]
    assert build_video.MIN_LINES_ON_SCREEN <= len(src) <= build_video.MAX_LINES_ON_SCREEN
    assert blocks[0]["text"] == "\n".join(src)

    states = build_video.state_map(blocks, cfg, 9.5)
    assert len(states) == 1
    assert states[0]["start"] == 0.0 and states[0]["end"] == 9.5
    assert build_video.loop_echo_ok(blocks) is True


def test_final_format_one_overlay_no_timing():
    """§1/§7: the assembly must contain ONE overlay with NO enable= condition.

    `overlay=enable='between(t,...)'` is an explicit failure condition. Inspects
    the actual string literals the function builds (via AST), not its docstring —
    the docstring *describes* the forbidden pattern, which would false-positive a
    naive source grep. Audio `afade` is allowed: the ban is on TEXT animation.
    """
    import ast
    import inspect
    fn = ast.parse(inspect.getsource(build_video.assemble).lstrip()).body[0]
    # Exclude the docstring NODE by identity: ast.get_docstring() returns a cleaned
    # string, so comparing values does not match the raw Constant.
    doc_ids = set()
    first = fn.body[0] if getattr(fn, "body", None) else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        doc_ids.add(id(first.value))

    literals = [n.value for n in ast.walk(fn)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in doc_ids]
    joined = " ".join(literals)

    assert "enable=" not in joined, \
        "§7: any enable='between(...)' on text is a failure"
    assert "overlay=0:0" in joined, \
        "§1: text must be composited from frame 0, always on"
    # no VIDEO fade on the text layers. Check for a video fade target (`fade=t=in`
    # not preceded by 'a'): the audio `afade` is legitimate and expected.
    import re
    for lit in literals:
        if lit.startswith(":"):
            continue                     # this is the audio chain (afade) — allowed
        assert not re.search(r"(?<!a)fade=t=in:st=", lit), \
            f"§1: text/vision must never fade in: {lit!r}"


def test_render_block_text_visible_with_and_without_scrim():
    """Regression: drawing the scrim AFTER creating the draw handle discarded the
    glyphs, so the text was invisible on every frame when the bg was bright.

    Renders the same block with bg_luma=None and bg_luma=bright and asserts BOTH
    contain bright text pixels.
    """
    from PIL import Image
    cfg = config.DEFAULT_CONFIG
    lines = ["You rehearse your order", "twelve times. Then still", "mess it up."]
    out = config.OUTPUTS / "test_block.png"

    def bright_pixels(p):
        with Image.open(p) as im:
            return sum(im.convert("L").histogram()[235:256])

    for luma in (None, 200.0):          # dark bg, and bright bg (scrim fires)
        build_video.render_block(lines, 82, build_video.FONT_HOOK, "", out, cfg,
                                 fixed_top=600, bg_luma=luma)
        n = bright_pixels(out)
        assert n >= 300, f"text invisible when bg_luma={luma} (bright px {n})"


def test_report_matches_the_rendered_png():
    """THE test that would have caught the shipped bug.

    placement_report said gap_above 52 / gap_below 53, but the overlay that actually
    got composited measured 74 / 32 — because build() passed fixed_top=<estimate>,
    which skipped smart placement, while the report measured the smart path. A gate
    that measures a different code path than the renderer is worse than no gate.

    So: render through the SAME entry point the build uses, then assert the PNG's own
    pixels agree with the report.
    """
    from PIL import Image
    cfg = config.DEFAULT_CONFIG
    h = int(cfg["reel"]["h"])
    lines = ["You let the call ring out", "then text sorry just saw this",
             "You saw it on the first ring.",
             "Comment HEARD and I'll send you the full breakdown."]
    entries, px, font_path = build_video.fit_message("\n".join(lines), cfg)
    out = config.OUTPUTS / "test_report_match.png"
    build_video.render_block(entries, px, font_path, "", out, cfg,
                             fixed_top=None, bg_luma=None)

    # measure the PNG
    with Image.open(out) as im:
        import numpy as _np
        mask = _np.array(im.convert("RGBA"))[:, :, 3] >= 200
        rows = _np.where(mask.any(axis=1))[0]
        box = (0, int(rows[0]), 1, int(rows[-1]) + 1) if len(rows) else None
    assert box, "nothing rendered"
    t, b = box[1], box[3]

    rep = build_video.placement_report(entries, px, cfg)
    band_top, band_bot = int(h * build_video.TEXT_SAFE_TOP), int(h * build_video.TEXT_SAFE_BOTTOM)
    png_gap_above, png_gap_below = t - band_top, band_bot - b

    assert abs(png_gap_above - rep["gap_above"]) <= 2, (png_gap_above, rep["gap_above"])
    assert abs(png_gap_below - rep["gap_below"]) <= 2, (png_gap_below, rep["gap_below"])
    assert abs(png_gap_above - png_gap_below) <= build_video.PLACEMENT_TOLERANCE, \
        f"PNG not centred: {png_gap_above} above vs {png_gap_below} below"
    assert b <= int(h * build_video.UI_ZONE_TOP), f"PNG text under IG UI: {b}"


def test_final_format_no_person_queries_are_person_bearing():
    """§3: every bg query must imply a human figure in frame."""
    cfg = config.load_config()
    for cluster, queries in cfg["bg_types"].items():
        for q in queries:
            assert any(w in q.lower() for w in background.PERSON_WORDS), \
                f"{cluster} query has no human signal: {q!r}"
    # §3's own exact queries must all be recognised as person-bearing
    for q in background.PERSON_QUERIES:
        assert any(w in q.lower() for w in background.PERSON_WORDS), q


def test_hide_like_count_is_a_manual_step_not_a_false_claim():
    """The Graph API cannot hide reel like counts, so the engine must not imply it did.

    Measured: the endpoint accepts a bogus param AND hide_like_count=NOTABOOLEAN
    (so 200 proves nothing), `fields=hide_like_count` is a nonexistent field, and the
    published reel still reports like_count. The workaround is a surfaced manual step.
    """
    import inspect
    src = inspect.getsource(publish.publish_reel)
    assert "manual" in src, "publish_reel must collect app-only steps"
    assert "Hide like" in src, "the manual step must name the app setting"
    # the alert must carry it, or the step is invisible
    sig = inspect.signature(alerts.success)
    assert "manual_steps" in sig.parameters, "alerts.success must accept manual_steps"
    # and run_create must pass it through
    rc = inspect.getsource(run_create.run)
    assert "manual_steps" in rc, "run_create must forward manual_steps to the alert"


def test_scheduler_hours_match_the_workflow_crons():
    """The learner must only be offered hours a cron actually fires at.

    HOURS used to include 21:30 while create.yml only fires 13:30/15:00/16:30. The
    learner picked 21:30, so no scheduled run was ever inside the window and the
    machine stopped posting on its own while every unit test stayed green.
    """
    import re
    from pathlib import Path
    yml = (Path(config.ROOT) / ".github" / "workflows" / "create.yml").read_text()
    crons = set(re.findall(r'cron:\s*"(\d+)\s+(\d+)', yml))
    cron_times = {f"{int(h):02d}:{int(m):02d}" for m, h in crons}
    assert cron_times == set(analyze.HOURS), (
        f"analyze.HOURS {sorted(analyze.HOURS)} != cron times {sorted(cron_times)} — "
        "the learner could pick an hour nothing triggers")
    # and the scheduler's own slot list must agree with both
    assert set(run_create.SLOT_HOURS) == cron_times


def test_scheduler_retries_are_not_dead_weight():
    """A failed slot must be retryable by the next one.

    Slots are 90 minutes apart. The old +/-35min window meant only one cron could
    ever post, so the other two were decorative and a failure lost the whole day.
    """
    from datetime import datetime, timezone
    cfg = config.load_config()
    strat = {"warmup_until": "2000-01-01", "next_post_hour": "15:00"}
    # at slot 1 and slot 2 (with nothing posted yet) the day is still open
    for slot in run_create.SLOT_HOURS[:2]:
        hh, mm = (int(x) for x in slot.split(":"))
        fake = datetime(2026, 9, 27, hh, mm, tzinfo=timezone.utc)
        real = run_create.datetime
        class _DT(real):
            @classmethod
            def now(cls, tz=None):
                return fake
        run_create.datetime = _DT
        try:
            ok, why = run_create.scheduler_check(cfg, strat, {}, offline=False, force=False)
        finally:
            run_create.datetime = real
        assert ok, f"slot {slot} should be able to post: {why}"
    # after the last slot the day is closed (so we never post at midnight)
    fake = datetime(2026, 9, 27, 23, 30, tzinfo=timezone.utc)
    real = run_create.datetime
    class _DT2(real):
        @classmethod
        def now(cls, tz=None):
            return fake
    run_create.datetime = _DT2
    try:
        ok, why = run_create.scheduler_check(cfg, strat, {}, offline=False, force=False)
    finally:
        run_create.datetime = real
    assert not ok and "last slot" in why, why
    # idempotency beats everything, force included
    ok, why = run_create.scheduler_check(cfg, strat, {"last_post_date": config.today_utc().isoformat()},
                                        offline=False, force=True)
    assert not ok and "already posted" in why


def test_skip_decision_happens_before_the_jitter_sleep():
    """A run destined to skip must not burn CI time sleeping first.

    Observed on a real CI run: jitter_start 1064.5s, then "already posted today
    (idempotency)" — 17.7 minutes of a 30-minute job budget spent before deciding
    there was nothing to do. The skip check has to come first, with a re-check after
    the sleep so the one-post-per-day cap still holds.
    """
    import inspect
    src = inspect.getsource(run_create.run)
    # locate the positions of the two markers in the source
    pre = src.find("Pre-check BEFORE sleeping")
    jit = src.find('step("jitter_start"')
    post = src.find("Re-check after the delay")
    assert pre != -1 and jit != -1 and post != -1, "jitter/skip ordering markers missing"
    assert pre < jit, "pre-jitter skip check must come before jitter_start"
    assert jit < post, "post-sleep re-check must come after the sleep"
    # and the pre-check must actually return before sleeping
    seg = src[pre:jit]
    assert "return 0" in seg, "pre-jitter skip path must return without sleeping"
    assert "time.sleep" not in seg, "nothing may sleep before the skip decision"


def test_final_format_text_fills_width():
    """§2: the block must fill >=75% of the frame width (readable at thumbnail)."""
    cfg = config.load_config()
    lines, px, fp = build_video.fit_block(
        "You rehearse your\norder 12 times.\n\nThen still mess\nit up.", "message", cfg)
    widest = build_video.assert_fits(lines, px, fp, cfg)
    fill = widest / int(cfg["reel"]["w"])
    assert fill >= build_video.MIN_WIDTH_FILL, f"text fills only {fill:.0%} of width"
    assert len(lines) <= build_video.MAX_LINES_ON_SCREEN


def test_text_blocks_and_timing_map_old_name_removed():
    """Guard: the old multi-card helper must not come back."""
    import inspect
    src = inspect.getsource(build_video.text_blocks)
    assert "deepening\", \"kind\"" not in src


def test_measured_fit_no_overflow():
    cfg = config.load_config()
    long_text = ("Your manager thinks you are less competent than you actually are "
                 "because in meetings your voice files its resignation and the quiet "
                 "gets graded instead of the work")
    lines, px, font_path = build_video.fit_block(long_text, "hook", cfg)
    widest = build_video.assert_fits(lines, px, font_path, cfg)
    assert widest <= int(cfg["reel"]["w"]) - 2 * build_video.SIDE_MARGIN
    assert px <= build_video.HOOK_PX_LADDER[0]
    # short hook keeps the biggest size
    _, px2, _ = build_video.fit_block("The trial begins.", "hook", cfg)
    assert px2 == build_video.HOOK_PX_LADDER[0]
    # body uses the Playfair ladder
    _, pxb, fpb = build_video.fit_block("Because alone, your work is excellent.",
                                        "deepening", cfg)
    assert fpb == build_video.FONT_BODY and pxb <= build_video.BODY_PX_LADDER[0]


def test_variety_guard_keyword_rotation():
    """The old cluster/opening diversify pass is gone; variety is now enforced by the
    keyword rotation (never twice running, least-used first) plus topic rotation."""
    mem = {"last_keyword": "SAFE"}
    seq = []
    for _ in range(20):
        kw = generate.pick_keyword(mem)
        assert kw != mem["last_keyword"]
        seq.append(kw)
        mem["last_keyword"] = kw
        mem.setdefault("keyword_counts", {})
        mem["keyword_counts"][kw] = mem["keyword_counts"].get(kw, 0) + 1
    # no immediate repeats anywhere in the sequence
    assert all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))
    assert len(set(seq)) >= 8, f"rotation stuck: {seq}"


def test_static_clip_probe_and_source_quality():
    """The motion probe must exist, and the best-quality variant must be preferred."""
    from src import background as B
    # a 1080p variant must beat the 720p default that `best_video` always returns
    e = {"best_video": "https://x/720.mp4",
         "videos": [{"url": "https://x/720.mp4", "width": 720, "height": 1280},
                    {"url": "https://x/1080.mp4", "width": 1080, "height": 1920}]}
    assert B.best_source_url(e, target_w=1080) == "https://x/1080.mp4"
    # if nothing reaches the target, take the widest on offer
    e2 = {"best_video": "https://x/720.mp4",
          "videos": [{"url": "https://x/720.mp4", "width": 720, "height": 1280},
                     {"url": "https://x/906.mp4", "width": 906, "height": 1384}]}
    assert B.best_source_url(e2, target_w=1080) == "https://x/906.mp4"
    # HLS playlists are not files — never chosen as the download
    e3 = {"best_video": "https://x/720.mp4",
          "videos": [{"url": "https://x/hls.m3u8", "width": 1080, "height": 1920},
                     {"url": "https://x/720.mp4", "width": 720, "height": 1280}]}
    assert B.best_source_url(e3, target_w=1080) == "https://x/720.mp4"
    # no usable variants -> fall back to best_video rather than failing the download
    assert B.best_source_url({"best_video": "https://x/720.mp4"}, 1080) == "https://x/720.mp4"


def test_final_render_is_high_quality():
    """The shipped encode must not be a throwaway: CRF 16, high profile, faststart."""
    src = (ROOT / "src" / "build_video.py").read_text(encoding="utf-8")
    body = src.split("def assemble", 1)[1]
    assert '"veryslow"' in body, "final preset should be veryslow"
    assert '"16"' in body, "final CRF should be 16"
    assert '"high"' in body, "h264 high profile expected"
    assert "+faststart" in body, "faststart is required for streaming"
    assert '"192k"' in body, "audio bitrate should be 192k"


def test_hide_like_count_is_configurable_and_sent():
    """The user asked for hidden like counts; the container must carry the param."""
    assert config.DEFAULT_CONFIG["hide_like_count"] is True
    src = (ROOT / "src" / "publish.py").read_text(encoding="utf-8")
    assert 'data["hide_like_count"] = "true"' in src, "hide_like_count never sent"
    # ...but alt_text must STILL never be sent (Graph rejects it with a 400)
    assert 'data["alt_text"]' not in src


# ------------------------------------------------------- harvest metrics

def test_harvest_metric_hierarchy():
    sends_heavy = {"reach": 1000, "shares": 30, "saved": 5, "likes": 100}
    saves_heavy = {"reach": 1000, "shares": 2, "saved": 60, "likes": 100}
    # sends outweigh saves (Part 4.1 hierarchy)
    assert harvest.compute_score(sends_heavy) > harvest.compute_score(saves_heavy)
    assert harvest.sends_per_reach(sends_heavy) == 0.03
    assert harvest.saves_per_reach(saves_heavy) == 0.06
    # likes are near-worthless: removing them barely moves the score
    with_likes = harvest.compute_score({"reach": 1000, "shares": 10, "saved": 10,
                                        "likes": 0})
    more_likes = harvest.compute_score({"reach": 1000, "shares": 10, "saved": 10,
                                        "likes": 500})
    assert more_likes - with_likes < 0.15


# --------------------------------------------------------------- analyzer

def test_learn_on_fixture_memory():
    memory = json.loads((FIXTURES / "memory_fixture.json").read_text(encoding="utf-8"))
    cfg = config.load_config()
    for p in memory["posts"]:
        p.setdefault("dna", {}).setdefault("exploit", True)
    posts = analyze.scored_posts(memory)
    assert len(posts) == 10
    G = analyze._global_mean(posts)
    assert 0.02 < G < 0.6, f"global mean out of expected band: {G}"
    followers = {}
    for arch in cfg["archetypes"]:
        adj, n, _ = analyze._adjusted(posts, lambda p: (p["dna"] or {}).get("archetype"),
                                      arch, G)
        followers[arch] = (adj, n)
    norm = analyze._normalize({k: v for k, v in {a: followers[a][0] for a in followers}.items()})
    assert abs(sum(norm.values()) - 1.0) < 1e-6

    strategy = config.default_strategy()
    strategy["adj"] = {"archetype": {a: followers[a][0] for a in followers},
                       "topic": {}, "mood": {}, "bg_type": {}, "loop_technique": {}}
    strategy["n"] = {"archetype": {a: followers[a][1] for a in followers},
                     "topic": {}, "mood": {}, "bg_type": {}, "loop_technique": {}}
    strategy["hour_scores"] = {"13:30": 0.06, "15:00": 0.12, "16:30": 0.03, "21:30": 0.2}
    strategy["next_post_hour"] = "21:30"
    strategy["experiments"] = [{"archetype": "the_freeze", "topic": "asking_coworker"}]
    text = analyze._write_report(memory, strategy, posts, G)
    assert "Self-improvement metrics" in text     # Part 7.3
    assert "sends per reach" in text
    assert "Trending alignment" in text
    assert "Exploit vs explore" in text


def test_insufficient_data_report():
    memory = {"posts": []}
    text = analyze._write_report(memory, config.default_strategy(), [], 0.0, insufficient=True)
    assert "insufficient data" in text


# ------------------------------------------------------------- workflows

def test_workflows_parse_and_contracts():
    wf = ROOT / ".github" / "workflows"
    expected = {
        "create.yml": ("30 13 * * *", 30, "beast-create", "run_create"),
        "measure.yml": ("0 4,10,16 * * *", 10, "beast-measure", "run_measure"),
        "learn.yml": ("0 18 * * 0", 10, "beast-learn", "run_learn"),
        "health.yml": ("0 5 * * *", 10, "beast-health", "run_health"),
    }
    for name, (cron, timeout, group, script) in expected.items():
        doc = yaml.safe_load((wf / name).read_text(encoding="utf-8"))
        triggers = doc.get("on") or doc.get(True)
        assert triggers is not None, f"{name}: no trigger block"
        crons = [c["cron"] for c in triggers["schedule"]]
        assert cron in crons, f"{name}: missing cron {cron} (has {crons})"
        assert "workflow_dispatch" in triggers
        assert "pull_request" not in triggers, f"{name}: pull_request trigger forbidden"
        perms = doc["permissions"]
        assert perms["contents"] == "write" and perms["issues"] == "write"
        assert doc["concurrency"]["group"] == group
        assert doc["concurrency"]["cancel-in-progress"] is False
        # Find the job that RUNS the entrypoint, rather than assuming it is jobs[0].
        # Hard-coding the first job meant adding any preceding job (e.g. a test gate)
        # broke this assertion even though the contract was intact.
        job = None
        for _j in doc["jobs"].values():
            if any(script in str(s.get("run", "")) for s in _j.get("steps", [])):
                job = _j
                break
        assert job is not None, f"{name}: no job runs {script}.py"
        assert job["timeout-minutes"] == timeout, f"{name}: timeout {job['timeout-minutes']}"
        run_step = next(s for s in job["steps"] if script in str(s.get("run", "")))
        assert run_step["env"]["GITHUB_TOKEN"].startswith("${{ secrets.")
        if name != "learn.yml":
            assert run_step["env"]["IG_ACCESS_TOKEN"].startswith("${{ secrets.")
        if "actions/checkout@v4" not in [s.get("uses") for s in job["steps"]]:
            raise AssertionError(f"{name}: missing actions/checkout@v4")
    # The old assertion here demanded `secrets: write`, an invalid scope that made
    # GitHub refuse to parse health.yml — every run died at trigger time. Permissions
    # are validated in test_workflows_use_only_valid_permission_scopes; here we only
    # assert health declares the scopes it actually uses.
    health = yaml.safe_load((wf / "health.yml").read_text(encoding="utf-8"))
    assert health["permissions"]["contents"] == "write", "health needs contents:write"
    assert health["permissions"]["issues"] == "write", "health needs issues:write"


def test_safe_band_constants_are_pinned_and_clear_the_ui():
    """The band constants themselves must be locked, and must clear the UI zone.

    Found by injecting TEXT_SAFE_BOTTOM = 0.95: the suite still passed 56/56, because
    every placement test asked "is the text centred inside the band?" — and a band that
    extends into Instagram's caption bar is still a band. Centred-in-band is not the
    invariant that matters; text-above-the-UI is. Pin both:
      * the constants have the values the fix was verified at
      * TEXT_SAFE_BOTTOM leaves real clearance below the UI zone threshold
    """
    assert build_video.TEXT_SAFE_TOP == 0.13, build_video.TEXT_SAFE_TOP
    assert build_video.TEXT_SAFE_BOTTOM == 0.72, build_video.TEXT_SAFE_BOTTOM
    assert build_video.UI_ZONE_TOP == 0.76, build_video.UI_ZONE_TOP
    # The band must end ABOVE where the UI begins, with margin. If someone raises
    # TEXT_SAFE_BOTTOM to chase a taller block, this fails before a reel ships.
    assert build_video.TEXT_SAFE_BOTTOM < build_video.UI_ZONE_TOP, \
        "safe band overlaps the Instagram UI zone"
    assert build_video.UI_ZONE_TOP - build_video.TEXT_SAFE_BOTTOM >= 0.03, \
        "less than 3% of frame height between the band and the UI"
    # and the derived cap must agree with the band
    assert build_video.MAX_BLOCK_H == build_video.TEXT_SAFE_BOTTOM - build_video.TEXT_SAFE_TOP

    # The strongest form: render a block and assert its ink clears the UI zone by
    # construction, for the tallest block the fitter can produce.
    from PIL import Image
    import numpy as _np
    cfg = config.DEFAULT_CONFIG
    h = int(cfg["reel"]["h"])
    long_text = "\n".join(["You rehearse the whole thing then it still falls apart"] * 5
                          + ["Comment HEARD and I'll send you the full breakdown."])
    entries, px, fp = build_video.fit_message(long_text, cfg)
    out = config.OUTPUTS / "test_band_clearance.png"
    build_video.render_block(entries, px, fp, "", out, cfg, fixed_top=None, bg_luma=None)
    with Image.open(out) as im:
        mask = _np.array(im.convert("RGBA"))[:, :, 3] >= 200
    rows = _np.where(mask.any(axis=1))[0]
    assert len(rows), "nothing rendered"
    ink_bottom = int(rows[-1])
    ui_top = int(h * build_video.UI_ZONE_TOP)
    assert ink_bottom < ui_top, f"tallest block reaches {ink_bottom} vs UI {ui_top}"
    # and it still sits inside the band
    assert int(rows[0]) >= int(h * build_video.TEXT_SAFE_TOP)
    assert ink_bottom <= int(h * build_video.TEXT_SAFE_BOTTOM) + 1


def test_all_third_party_imports_are_declared_in_requirements():
    """Every third-party module the code imports must be in requirements.txt.

    PyYAML was imported by the tests but never declared: it worked on the dev box
    (installed globally) and died in CI with 'ModuleNotFoundError: No module named
    yaml' the moment the test gate ran. Any module that is importable locally is a
    false green.
    """
    import ast
    import pathlib
    import sys

    # name -> the distribution that provides it (import name != package name)
    DIST = {"PIL": "pillow", "yaml": "pyyaml", "nacl": "pynacl",
            "soundfile": "soundfile", "librosa": "librosa", "numpy": "numpy",
            "requests": "requests", "pytest": "pytest"}
    STDLIB = set(sys.stdlib_module_names)
    LOCAL = {"src", "tests"}

    declared = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            declared.add(line.split(">=")[0].split("==")[0].split("[")[0].strip().lower())

    missing = set()
    for py in list((ROOT / "src").glob("*.py")) + list((ROOT / "tests").glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for m in names:
                if m in STDLIB or m in LOCAL:
                    continue
                dist = DIST.get(m, m).lower()
                if dist not in declared:
                    missing.add(f"{m} (from {py.name})")
    assert not missing, "undeclared third-party imports: " + ", ".join(sorted(missing))


def test_create_publish_is_gated_on_the_acceptance_suite():
    """The create workflow must run the acceptance tests and publish only if they pass.

    Without a gate, a regression in src/ (broken placement, dead publish, bad
    contract) reached Instagram untested — CI only ran the entrypoint. This asserts
    the gate exists, that it runs the suite, and that the publishing job depends on it.
    """
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "create.yml")
                         .read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    gate = None
    for name, j in jobs.items():
        if any("test_acceptance" in str(s.get("run", "")) for s in j.get("steps", [])):
            gate = name
            break
    assert gate is not None, "create.yml has no acceptance-test step"
    # the publisher must WAIT on the gate
    pub = [n for n, j in jobs.items()
           if any("run_create" in str(s.get("run", "")) for s in j.get("steps", []))]
    assert pub, "create.yml has no job running run_create"
    for p in pub:
        assert jobs[p].get("needs") == gate, \
            f"publishing job {p!r} must needs: {gate!r} (got {jobs[p].get('needs')!r})"


# ------------------------------------------------------------- data files

def test_seed_files_and_config_shape():
    for name in ("config.json", "strategy.json", "memory.json",
                 "trending_styles.json", "token_state.json"):
        assert (ROOT / "data" / name).exists(), f"missing data/{name}"
    assert not (ROOT / "data" / "PAUSE").exists(), "PAUSE must not be seeded"
    cfg = config.load_config()
    strat = json.loads((ROOT / "data" / "strategy.json").read_text(encoding="utf-8"))
    assert strat["next_post_hour"] in strat["hour_scores"]
    assert len(strat["weights"]["archetype"]) == 11
    assert len(strat["weights"]["topic"]) == 20
    assert len(strat["weights"]["loop_technique"]) == 3
    # Part 5 locked format
    assert cfg["reel"]["min_s"] == 9.0 and cfg["reel"]["max_s"] == 10.0
    assert cfg["timing"] == {"hook_end_frac": 0.45, "deepen_end_frac": 0.88}  # spec §4
    assert set(cfg["archetypes"]) == set(strat["weights"]["archetype"])
    assert set(cfg["moods"]) == set(strat["weights"]["mood"])
    assert set(cfg["bg_types"].keys()) == set(strat["weights"]["bg_type"])
    # every cluster maps to a bg type and a sound family
    for arch in cfg["archetypes"]:
        assert arch in cfg["archetype_bg_map"], f"{arch} missing from archetype_bg_map"
        assert arch in cfg["cluster_mood_map"], f"{arch} missing from cluster_mood_map"
        assert cfg["archetype_bg_map"][arch] in cfg["bg_types"]
        assert cfg["cluster_mood_map"][arch] in cfg["moods"]


# ------------------------------------------------------- background/music

def test_background_queries_are_video():
    cfg = config.load_config()
    for bg, queries in cfg["bg_types"].items():
        assert queries, f"{bg} has no queries"
        for q in queries:
            assert "video" in q, f"{bg} query not video-first: {q!r}"


def test_mood_tempo_bands_cover_all_moods():
    cfg = config.load_config()
    for mood in cfg["moods"]:
        assert mood in music.MOOD_TEMPO_BANDS, f"{mood} has no tempo band"
        lo, hi = music.MOOD_TEMPO_BANDS[mood]
        assert 0 < lo < hi < 200


def test_trending_filter_confidence():
    data = json.loads((FIXTURES / "trending_fixture.json").read_text(encoding="utf-8"))
    min_conf = 0.5
    good = [r for r in data["results"] if float(r["confidence"]) >= min_conf]
    assert len(good) == 3


def test_pixabay_regex_finds_all_three():
    html = (FIXTURES / "pixabay_fixture.html").read_text(encoding="utf-8")
    found = music.PIXABAY_MP3_RE.findall(html)
    assert len(sorted(set(found))) == 3


# ------------------------------------------------- VISUAL SPEC v1.0 §4 / §5

def test_text_anchor_and_block_height_agree():
    """The block's placement is measured, centred in the safe band, and clear of the UI.

    This replaces an arithmetic assertion (top == h*frac - block_h/2) that passed while
    the real render put the text at 82% of the frame, under Instagram's caption bar.
    """
    cfg = config.DEFAULT_CONFIG
    h = int(cfg["reel"]["h"])
    entries = [("one", False), ("two", False), ("a third line of text", False),
               ("Comment SAFE and I'll send you the full breakdown.", True)]
    px = 72
    rep = build_video.placement_report(entries, px, cfg)
    assert not rep.get("empty"), rep
    # the three invariants the user's bug violated
    assert rep["inside_band"], rep
    assert rep["clears_ui"], rep
    assert rep["centred"], rep
    # top-anchored mode still clamps inside the band
    old = build_video.TEXT_TOP_FRAC
    build_video.TEXT_TOP_FRAC = 0.30
    try:
        anchored = build_video.placement_report(entries, px, cfg, top=None)
    finally:
        build_video.TEXT_TOP_FRAC = old
    assert anchored["inside_band"], anchored
    # The CTA renders smaller than the body. (Its LINE is not shorter: the extra
    # leading above it (CTA_GAP) makes the line taller than a body line, which is
    # what gives the footer its visual separation.)
    cta_px = max(int(96 * build_video.CTA_SCALE), build_video.CTA_MIN_PX)
    assert cta_px < 96, "CTA must render smaller than the body"
    assert build_video.CTA_MIN_PX >= 36, "CTA must stay legible"
    # and the CTA font is never allowed below the legibility floor
    assert max(int(40 * build_video.CTA_SCALE), build_video.CTA_MIN_PX) == build_video.CTA_MIN_PX


def test_spec4_shadow_blur_and_opacity():
    """§4: shadow blur radius 8 at ~70% opacity."""
    assert build_video.SHADOW_BLUR == 8
    assert abs(build_video.SHADOW_ALPHA / 255 - 0.70) < 0.02


def test_spec4_timing_satisfies_all_three_windows():
    """FINAL FORMAT supersedes the §4 timing windows: there is no timing any more.

    The text is on screen from frame 0 to the end, so the old "hook 4-5s /
    deepening / landing 8-9s" map no longer exists. Guard against it returning.
    """
    cfg = dict(config.DEFAULT_CONFIG)
    blocks = [{"text": "h\nd\nl", "kind": "message"}]
    for dur in (9.0, 9.5, 10.0):
        states = build_video.state_map(blocks, cfg, dur)
        assert len(states) == 1, "exactly one on-screen state spans the whole reel"
        assert states[0]["start"] == 0.0
        assert states[0]["end"] == dur


def test_spec4_text_band_only_when_bright():
    """§4: the dark scrim is conditional on the background being bright."""
    cfg = config.DEFAULT_CONFIG
    assert build_video.TEXT_BAND_ALPHA == 77          # 30% black
    assert build_video.TEXT_BAND_BRIGHT_MIN > 0
    # a dark band must NOT be laid on this project's dark footage
    assert build_video.TEXT_BAND_BRIGHT_MIN >= 60


def test_spec5_no_piano_or_lofi_anywhere_in_audio_vocab():
    """§5: dark trending audio only — piano/lofi must not be searchable."""
    cfg = config.DEFAULT_CONFIG
    banned = cfg["music"]["banned_music_terms"]
    assert "piano" in banned and "lofi" in banned

    # code defaults
    for mood, q in cfg["music"]["mood_search"].items():
        assert not music._spec5_violates(q, cfg), f"{mood} query violates §5: {q}"
    for style in config.default_trending_styles():
        assert not music._spec5_violates(style, cfg), f"trending style violates §5: {style}"

    # the DATA files too — a stale one silently reintroduces the banned terms.
    # Check the searchable VALUES, not the raw file text (the file legitimately
    # contains the words "piano"/"lofi" inside banned_music_terms itself).
    live = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    for mood, q in live["music"]["mood_search"].items():
        assert not music._spec5_violates(q, cfg), f"data/config mood {mood} violates §5: {q}"
    for style in json.loads((ROOT / "data" / "trending_styles.json").read_text(encoding="utf-8")):
        assert not music._spec5_violates(style, cfg), f"data style violates §5: {style}"


def test_spec5_safe_query_never_returns_banned_term():
    cfg = config.DEFAULT_CONFIG
    for mood in cfg["moods"]:
        q = music._spec5_safe_query(cfg, mood)
        assert not music._spec5_violates(q, cfg), q


def test_spec_audio_chain_includes_song_provider():
    """The /v1/song tier must be wired into acquire() (name -> audio, IG-first)."""
    import inspect
    src = inspect.getsource(music.acquire)
    assert "song_provider" in src
    src_song = inspect.getsource(music.song_provider)
    assert "/v1/song" in src_song


def test_seo_checklist_matches_the_current_text_engine():
    """The checklist must validate the CURRENT contract (5-7 tags, keyword shipped).

    It previously enforced a superseded 6-9 hashtag rule, which reported a false
    failure on every valid reel once the text engine took over the caption.
    """
    from src import seo as S
    good = {
        "onscreen_text": ("When the phone rings you freeze.\n"
                          "You watch it ring out.\n"
                          "You said sorry, just saw this.\n"
                          "You saw it on the first ring.\n"
                          "Comment QUIET and I'll send you the full breakdown."),
        "caption": ("The phone was never the problem. It's the performance.\n\n"
                    "If this is you, you're not alone.\n\n"
                    "Comment QUIET and I'll send you the full breakdown.\n\n"
                    "#socialanxiety #phoneanxiety #overthinking "
                    "#socialanxietystruggles #quietpeople"),
        "hashtags": ["#socialanxiety", "#phoneanxiety", "#overthinking",
                     "#socialanxietystruggles", "#quietpeople"],
        "keyword": "QUIET",
        "alt_text": "A man alone holding a phone in a dark room.",
    }
    res = S.checklist(good)
    assert all(res.values()), [k for k, v in res.items() if not v]
    # a 9-tag caption is a FAIL now (the old rule); 5-7 is the contract
    too_many = dict(good, hashtags=good["hashtags"] * 2)
    assert S.checklist(too_many)["hashtags_5_to_7"] is False
    # keyword missing from the overlay must be caught
    assert S.checklist(dict(good, onscreen_text="Nothing here."))["keyword_on_screen"] is False
    # a keyword with no DM rule would mean commenters never get the link
    assert S.checklist(dict(good, keyword="NOTAKEYWORD"))["keyword_is_dm_enabled"] is False


def test_seo_alt_text_describes_visual_and_ends_with_brand():
    """SEO §6.2: visual description -> keyword topic -> brand, well-formed."""
    from src import seo as S
    alt = S.build_alt_text(scene="A lone silhouette walking through a dark street",
                           topic="fake_phone", hook="Phone out. Head down.",
                           on_screen_text="Phone out. Head down. Still invisible.")
    assert alt.startswith("A lone silhouette"), alt
    assert alt.endswith("From Unleash The Beast."), alt
    assert ".." not in alt, f"double period: {alt}"
    assert "content about" in alt.lower()
    # No lowercase sentence start after a full stop.
    import re as _re
    for m in _re.finditer(r"\.\s+([a-z])", alt):
        raise AssertionError(f"sentence starts lowercase: ...{alt[m.start():m.start()+30]!r}")


def test_seo_on_screen_searchable_without_keyword_stuffing():
    """§5: honest scene text counts as searchable; empty filler does not."""
    from src import seo as S
    assert S.has_searchable_phrase("Phone out. Head down. Still invisible."), \
        "an honest scene with phone/invisible should be searchable"
    assert not S.has_searchable_phrase("The end. A time."), \
        "content-free text must not pass as searchable"
    assert S.on_screen_seo_score("Phone out. Head down. Still invisible.") > 0.3


def test_config_declares_no_background_darkening():
    """The footage ships AS SHOT: no grade, no darken, in code AND data/config.json."""
    assert config.DEFAULT_CONFIG["bg_darken"] == 0.0, "default darken must be 0"
    assert config.DEFAULT_CONFIG["bg_grade"] is False, "default grade must be off"
    live = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    assert live["bg_darken"] == 0.0, "config.json still darkens the background"
    assert live["bg_grade"] is False, "config.json still grades the background"


def test_background_filters_emit_no_colour_filters_by_default():
    """With grade+darken off, the ffmpeg chains must contain no eq/colorbalance.

    This is the mechanical guarantee that the source video is not darkened — a config
    value alone would not catch a hard-coded filter left behind in the chain.
    """
    from src import background as B
    src = (ROOT / "src" / "background.py").read_text(encoding="utf-8")
    # The cinematic chain may exist, but must only be reachable via bg_grade.
    body = src.split("def _grade_segments", 1)[1].split("def process_clip", 1)[0]
    assert "if cfg.get(\"bg_grade\")" in body, "cinematic grade not gated behind bg_grade"
    assert B._grade_segments({}, 0.0) == "", "default grade chain must be empty"
    assert B._grade_segments({"bg_darken": 0.0, "bg_grade": False}, 0.0) == ""
    assert "eq=" in B._grade_segments({}, -0.2), "darken must still work"
    assert "eq=" in B._grade_segments({"bg_grade": True}, 0.0), "grade must still work"
    assert B.GRADE_CINEMATIC, "cinematic chain should be preserved for re-enabling"


def test_build_video_chain_has_no_brightness_by_default():
    """build_video's assemble chain must not darken the background either."""
    src = (ROOT / "src" / "build_video.py").read_text(encoding="utf-8")
    body = src.split("def assemble", 1)[1].split("\n    last =", 1)[0]
    assert "scale=1080:1920" in body, "assemble should scale/crop the background"
    # The literal darken filter must not be baked into the base chain string.
    assert "eq=brightness=" not in body.split("grade_parts")[0], \
        "base chain still hard-codes a brightness filter"
    assert "bg_grade" in body, "assemble should gate the grade on bg_grade"


def test_background_rejects_static_clips_before_build():
    """A still pin re-encoded as video must be skipped at selection, not caught at the
    final QA gate (where a whole build is discarded)."""
    from src import background as B
    src = (ROOT / "src" / "background.py").read_text(encoding="utf-8")
    assert hasattr(B, "clip_has_motion"), "background needs a motion probe"
    # The probe must run BEFORE the clip is accepted and returned.
    seg = src.split("if process_clip(raw, looped, duration_s, fps, cfg):", 1)[1]
    seg = seg.split("return out_norm", 1)[0]
    assert "clip_has_motion(looped" in seg, "motion check must gate acceptance"
    assert "continue" in seg, "a static clip must skip to the next query"
    # Threshold must match build_video's so an accepted clip cannot fail downstream.
    bv = (ROOT / "src" / "build_video.py").read_text(encoding="utf-8")
    import re as _re
    bv_default = _re.search(r'motion_min_diff",\s*([0-9.]+)', bv)
    assert bv_default and float(bv_default.group(1)) == B.MOTION_MIN_DIFF, \
        "background and build_video motion thresholds disagree"


def test_publish_never_sends_alt_text_to_container():
    """Graph rejects alt_text on REELS containers (HTTP 400). Sending it killed the
    entire publish, so it must never appear in either container's request params."""
    src = (ROOT / "src" / "publish.py").read_text(encoding="utf-8")
    # No assignment of alt_text into a container payload anywhere.
    assert 'data["alt_text"]' not in src, "alt_text assigned into container params"
    assert 'data_in["alt_text"]' not in src, "alt_text assigned into url container params"
    # Both container functions still receive it so the caller can surface it.
    assert "alt_text: str = \"\"" in src
    assert "alt_text_for(content, cfg)" in src


def test_workflows_use_only_valid_permission_scopes():
    """An invalid scope key makes the whole workflow unparseable — GitHub rejects the
    file and the schedule silently never runs. `secrets: write` is NOT a real scope and
    did exactly that to health.yml."""
    import glob as _glob
    import yaml
    valid = {"actions", "attestations", "checks", "contents", "deployments",
             "discussions", "id-token", "issues", "models", "packages", "pages",
             "pull-requests", "repository-projects", "security-events", "statuses"}
    for f in sorted(_glob.glob(str(ROOT / ".github" / "workflows" / "*.yml"))):
        d = yaml.safe_load(open(f, encoding="utf-8"))
        for scope in (d.get("permissions") or {}):
            assert scope in valid, f"{f}: invalid permission scope {scope!r}"


def test_workflows_invoke_modules_not_file_paths():
    """`python src/run_x.py` breaks the package's relative imports
    ("attempted relative import with no known parent package") — every scheduled run
    died instantly. The entrypoints must be invoked as modules."""
    import glob as _glob
    import yaml
    for f in sorted(_glob.glob(str(ROOT / ".github" / "workflows" / "*.yml"))):
        d = yaml.safe_load(open(f, encoding="utf-8"))
        for job in (d.get("jobs") or {}).values():
            for step in job.get("steps", []):
                cmd = str(step.get("run", "") or "")
                if "run_" in cmd and ".py" in cmd:
                    raise AssertionError(
                        f"{f}: runs a script path ({cmd!r}); use -m src.run_x instead")


def test_entrypoint_modules_are_importable_as_modules():
    """Each workflow entrypoint must work under `python -m src.<name>`."""
    import importlib
    for mod in ("run_create", "run_health", "run_learn", "run_measure"):
        m = importlib.import_module(f"src.{mod}")
        assert hasattr(m, "main") or hasattr(m, "run"), f"{mod} has no entry callable"


def test_llm_folds_system_into_user_turn():
    """The upstream endpoint discards the `system` role, so a spec sent that way is
    silently lost and the model free-styles. It must be folded into the user turn."""
    from src import llm
    folded = llm._fold_system([{"role": "system", "content": "RULE: say BANANA"},
                               {"role": "user", "content": "hello"}])
    assert len(folded) == 1 and folded[0]["role"] == "user", folded
    assert "BANANA" in folded[0]["content"], "system spec lost"
    assert "hello" in folded[0]["content"], "user message lost"
    # no system message -> only a user turn is sent
    assert all(m["role"] != "system" for m in folded)
    # a system-only call still produces a usable user turn
    only = llm._fold_system([{"role": "system", "content": "RULES"}])
    assert only and only[0]["role"] == "user" and "RULES" in only[0]["content"]


def test_llm_picks_the_largest_json_object_not_the_first():
    """Prompt examples contain small JSON literals; the answer is the biggest object.
    json_first() returned {"keyword": "SAFE"} and the reel was built from it."""
    from src import llm
    txt = ('example: {"keyword":"SAFE"}\n'
           'answer:\n{"onscreen_text":"a\\nb","caption":"c","keyword":"QUIET",'
           '"topic":"d"}')
    assert llm.json_first(txt) == {"keyword": "SAFE"}, "precondition changed"
    biggest = llm._json_largest(txt)
    assert biggest["keyword"] == "QUIET", biggest
    assert set(biggest) == {"onscreen_text", "caption", "keyword", "topic"}
    # tolerant of prose around the object and of trailing commas
    assert llm._json_largest('blah {"a": 1, "b": 2} tail')["b"] == 2
    assert llm._json_largest("") is None


def test_generate_retargets_cta_onto_a_dm_enabled_keyword():
    """The keyword the CTA names must be one the automation fires on, or comments
    are lost. FREEZE is in the spec's §5 examples but is NOT enabled here."""
    o = ("POV: you freeze.\nYou watch it ring.\nYou say nothing again.\n"
         "You hate this part.\nComment FREEZE and I'll send you the full breakdown.")
    fixed = generate._retarget_cta(o, "QUIET")
    assert "Comment QUIET" in fixed, fixed
    assert "FREEZE" not in fixed
    # body lines are untouched
    assert fixed.split("\n")[0] == "POV: you freeze."
    # a caption CTA is retargeted too
    cap = "True.\n\nMechanism.\n\nComment FREEZE and I'll send you the full breakdown."
    assert "Comment QUIET" in generate._retarget_cta(cap, "QUIET")


def test_data_config_matches_code_defaults_for_spec_keys():
    """data/config.json must not contradict the code defaults."""
    live = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    for key in ("font_path", "timing", "text_limits", "bg_darken"):
        assert live[key] == config.DEFAULT_CONFIG[key], f"{key} drifted from defaults"
    assert live["music"]["mood_search"] == config.DEFAULT_CONFIG["music"]["mood_search"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
