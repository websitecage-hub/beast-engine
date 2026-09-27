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

from src import (analyze, background, build_video, config, generate, harvest,  # noqa: E402
                 mind, music)

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
    sp = generate.SYSTEM_PROMPT
    assert sp == mind.SYSTEM_PROMPT
    # Part 8 directive, Part 2 evidence library, Part 6 calibration, contract
    for marker in ("terrified of confirmation", "EVIDENCE LIBRARY", "CALIBRATION",
                   "OUTPUT CONTRACT", "this is exactly [friend's name]"):
        assert marker in sp, f"mind prompt missing {marker!r}"
    # every cluster appears in the evidence library
    for cluster in config.ARCHETYPES:
        assert cluster in mind.EVIDENCE_LIBRARY, f"{cluster} missing from evidence"


# ------------------------------------------------------- law + limit gates

def test_limits_and_laws():
    cfg = config.load_config()
    good = {
        "hook": "You know exactly what to say. You say nothing. Again.",
        "deepening": ["You ran the conversation on the walk over. Word for word.",
                      "Then the moment came and your body filed for silence."],
        "landing": "It was never a knowledge problem.",
        "cluster": "the_freeze", "topic": "freeze_at_work",
        "mood": "quiet_devastating", "bg_type": "freeze",
        "loop_technique": "visual_echo",
    }
    ok, why = generate._limits_ok(good, cfg)
    assert ok, f"clean candidate rejected: {why}"
    assert generate._valid(good, cfg) is True

    # character limits are enforced
    long_hook = {**good, "hook": "You know exactly what to say and you say nothing "
                                 "again and again and again and again"}
    assert len(long_hook["hook"]) > cfg["text_limits"]["hook"]
    assert generate._valid(long_hook, cfg) is False
    # laws
    assert generate._laws_ok("Just say it next time.")[0] is False
    assert generate._laws_ok("Here is my advice.")[0] is False
    assert generate._laws_ok("Share this with someone.")[0] is False   # Law 12
    assert generate._laws_ok("You have social anxiety.")[0] is False   # Law 7
    assert generate._laws_ok("You know exactly what to say.")[0] is True
    # cliffhanger humour / exclamation rejected (tone)
    assert generate._laws_ok("You did it!")[0] is False


def test_echo_score_is_advisory():
    """Part 6's own example shares no words between hook and landing, so the loop
    never depended on a lexical echo. Under FINAL FORMAT the text is one static
    block visible the whole time, so the first and last frame are identical by
    construction and the loop is unconditional."""
    cfg = config.load_config()
    calib = {"hook": "The conversation ends. The trial begins.",
             "landing": "You've been cross-examining yourself since school."}
    # the spec's quality bar must pass validation
    full = {**calib, "deepening": ["What you said. What you didn't.",
                                   "You'll review the footage until 2am."],
            "cluster": "the_aftermath", "topic": "replay_2am",
            "mood": "heavy_shadow", "bg_type": "aftermath",
            "loop_technique": "visual_echo"}
    assert generate._valid(full, cfg) is True, "spec example 2 must be valid"
    # the echo score is reported but never blocks
    assert generate._echo_score(calib) >= 0.0
    # FINAL FORMAT §1: one static block -> the loop is structural, not lexical
    blocks = build_video.text_blocks(full)
    assert len(blocks) == 1
    assert build_video.loop_echo_ok(blocks) is True


def test_dedup_rejects_near_duplicate():
    dup = {"hook": "You know exactly what to say. You say nothing. Again."}
    near = {"hook": "You know exactly what to say. You say nothing. Again"}
    assert generate._similar(dup["hook"], near["hook"]) > generate.DEDUP_RATIO
    assert generate._dedup_ok(near, [dup["hook"]], set()) is False
    fresh = {"hook": "She matched with you. And you're suspicious.",
             "cluster": "the_craving", "topic": "dating_app_freeze"}
    assert generate._dedup_ok(fresh, [dup["hook"]], set()) is True
    assert generate._dedup_ok(fresh, [], {("the_craving", "dating_app_freeze")}) is False


# ------------------------------------------------- Part 5.3 / 5.5 format

def test_text_blocks_and_timing_map():
    """FINAL FORMAT §1/§2: exactly ONE block containing the whole message,
    on screen from 0.0 to the end with no timing."""
    cfg = config.load_config()
    content = {
        "hook": "The conversation ends. The trial begins.",
        "deepening": ["What you said. What you didn't. The face they made.",
                      "You'll review the footage until 2am."],
        "landing": "You've been cross-examining yourself since school.",
    }
    blocks = build_video.text_blocks(content)
    assert len(blocks) == 1, "§2: exactly one text block"
    assert blocks[0]["kind"] == "message"
    # the whole message is in the one block, 3-5 lines (§2)
    src = blocks[0]["lines_source"]
    assert 3 <= len(src) <= 5
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


def test_variety_guard():
    def cand(cluster, hook):
        return {"cluster": cluster, "hook": hook}
    batch = [cand("the_mask", f"You do thing number {i}.") for i in range(5)]
    batch += [cand("the_freeze", "The order you rehearsed, fumbled anyway."),
              cand("the_aftermath", "Two years ago the phone rang and you let it."),
              cand("the_losses", "There is a version of you that everyone likes.")]
    kept = generate.diversify(batch)
    counts = {}
    for c in kept:
        counts[c["cluster"]] = counts.get(c["cluster"], 0) + 1
    assert counts.get("the_mask", 0) <= 3, f"cluster cap failed: {counts}"
    assert generate._opening_pattern("You smile on cue.") == "you"
    assert generate._opening_pattern("There is a version of you.") == "there"
    assert generate._opening_pattern("Two years ago you couldn't.") == "scene"


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
        job = doc["jobs"][list(doc["jobs"].keys())[0]]
        assert job["timeout-minutes"] == timeout, f"{name}: timeout {job['timeout-minutes']}"
        run_step = next((s for s in job["steps"] if script in str(s.get("run", ""))), None)
        assert run_step is not None, f"{name}: no step runs {script}.py"
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

def test_spec4_text_anchored_at_35_percent():
    """§4: the text sits ~a third down, not dead-centre."""
    cfg = config.DEFAULT_CONFIG
    h = int(cfg["reel"]["h"])
    assert build_video.TEXT_TOP_FRAC == 0.35
    # a 2-line hook must start within a few px of 35% (offset only by half its height)
    top = build_video.hook_top(["one", "two"], 96, cfg)
    assert abs(top - (h * 0.35 - 96 * 1.32)) < 3, top


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


def test_seo_hashtags_are_content_matched_and_within_limits():
    """SEO §4.2: 6-9 tags = 1-2 branded + 3-4 primary + 2-3 long-tail, topic-matched."""
    from src import seo as S
    for topic in ("fake_phone", "cancelled_plans", "the_2am_replay"):
        tags = S.build_hashtags(topic=topic, on_screen_text="Phone out. Head down.")
        assert 6 <= len(tags) <= 9, f"{topic}: {len(tags)} tags"
        assert len({t.lower() for t in tags}) == len(tags), "duplicate tags"
        branded = [t for t in tags if t in S.BRANDED_TAGS]
        primary = [t for t in tags if t in S.PRIMARY_TAGS]
        long_tail = [t for t in tags if t in S.LONG_TAIL_TAGS]
        assert 1 <= len(branded) <= 2, f"{topic}: branded {branded}"
        assert 3 <= len(primary) <= 4, f"{topic}: primary {primary}"
        assert 2 <= len(long_tail) <= 3, f"{topic}: long-tail {long_tail}"
        # Every long-tail tag must belong to THIS topic, not be random filler.
        topic_tags = S.TOPIC_MAP[topic][0]
        assert all(t in topic_tags for t in long_tail), \
            f"{topic}: mismatched long-tail {long_tail} vs {topic_tags}"


def test_seo_caption_puts_keyword_in_first_line_without_stuffing():
    """SEO §3.2: primary keyword in line 1, 1+ secondary keyword, no repetition."""
    from src import seo as S
    on_screen = "Phone out. Head down. Still invisible."
    tags = S.build_hashtags(topic="fake_phone", on_screen_text=on_screen)
    cap = S.build_caption("Phone out. Head down. Nobody can tell.",
                          "This is what it looks like from the outside.",
                          "Phone out again. Head down. Still invisible.",
                          topic="fake_phone", on_screen_text=on_screen, hashtags=tags)
    first = cap.split("\n", 1)[0]
    assert S.primary_in(first) is not None, f"no primary keyword in line 1: {first!r}"
    assert any(s in cap.lower() for s in S.SECONDARY_KEYWORDS), "no secondary keyword"
    # Keyword stuffing guard: no single keyword may appear more than twice overall.
    for kw in S.PRIMARY_KEYWORDS:
        assert cap.lower().count(kw) <= 2, f"keyword stuffed: {kw}"
    assert cap.rstrip().endswith(tags[-1]), "hashtags must close the caption"
    # The caption must not simply repeat the on-screen text (it has to add a description).
    assert len(cap.split()) >= 12


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


def test_seo_checklist_all_pass_on_generated_content():
    """The §9 checklist must be fully green for a normal reel."""
    from src import seo as S
    on_screen = "Phone out. Head down. Nobody can tell. You scroll through nothing."
    tags = S.build_hashtags(topic="fake_phone", on_screen_text=on_screen)
    cap = S.build_caption("Phone out. Head down. Nobody can tell.",
                          "This is what it looks like from the outside.",
                          "Phone out again. Head down. Still invisible.",
                          topic="fake_phone", on_screen_text=on_screen, hashtags=tags)
    alt = S.build_alt_text(scene="A lone silhouette on a wet street at night",
                           topic="fake_phone", on_screen_text=on_screen)
    res = S.checklist({"caption": cap, "hashtags": tags,
                       "on_screen_text": on_screen, "alt_text": alt})
    bad = [k for k, v in res.items() if not v]
    assert not bad, f"checklist failures: {bad}"


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
    assert 'def create_container(ig_id: str, caption: str, test: bool = False,\n                     alt_text: str = "")' in src
    assert 'def create_container_url(ig_id: str, caption: str, video_url: str,\n                         alt_text: str = "")' in src
    # The alt text must still be returned/printed rather than silently dropped.
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
