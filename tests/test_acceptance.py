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

from src import analyze, build_video, config, generate, harvest, mind, music  # noqa: E402

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
    """Part 6's own example shares no words between hook and landing, so the
    loop is guaranteed structurally (same font/size/pinned top), not lexically."""
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
    # structurally, hook and landing pin to the same top
    blocks = build_video.text_blocks(full)
    hook = next(b for b in blocks if b["kind"] == "hook")
    land = next(b for b in blocks if b["kind"] == "landing")
    hook["pinned_top"] = 700
    land["pinned_top"] = 700
    assert build_video.loop_echo_ok(blocks) is True
    land["pinned_top"] = 800
    assert build_video.loop_echo_ok(blocks) is False


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
    cfg = config.load_config()
    content = {
        "hook": "The conversation ends. The trial begins.",
        "deepening": ["What you said. What you didn't. The face they made.",
                      "You'll review the footage until 2am."],
        "landing": "You've been cross-examining yourself since school.",
    }
    blocks = build_video.text_blocks(content)
    assert len(blocks) == 4
    assert [b["kind"] for b in blocks] == ["hook", "deepening", "deepening", "landing"]
    states = build_video.state_map(blocks, cfg, 9.5)
    assert states[0]["start"] == 0.0                     # hook IS the thumbnail
    hook_state = next(s for s in states if s["kind"] == "hook")
    assert hook_state["end"] == 3.5                      # Part 5.5 map
    land_state = next(s for s in states if s["kind"] == "landing")
    assert land_state["start"] == 7.0 and land_state["end"] == 9.5
    # 3-block format: single deepening block
    three = {**content, "deepening": ["Only one deepening block here."]}
    b3 = build_video.text_blocks(three)
    assert len(b3) == 3
    assert build_video.loop_echo_ok(b3) is True


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
    health = yaml.safe_load((wf / "health.yml").read_text(encoding="utf-8"))
    assert health["permissions"].get("secrets") == "write"


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
    assert cfg["timing"] == {"hook_end": 3.5, "deepen_end": 7.0}
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
