"""Acceptance tests (section 11) — v4.0 BRAIN edition.

Run: python3 tests/test_acceptance.py

Covers: clean imports, law enforcement (banned words, fonts), dedup rejection,
script-block validation, cut map timing (2s hook hold), learn on fixture memory,
and workflow YAML contracts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src import analyze, build_video, config, generate, music  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


# ------------------------------------------------------------ test imports

def test_imports_clean():
    import src.run_create  # noqa: F401
    import src.run_measure  # noqa: F401
    import src.run_learn  # noqa: F401
    import src.run_health  # noqa: F401
    assert True


# ------------------------------------------------- brain law enforcement

def test_law_no_just_no_advice():
    ok, why = generate._laws_ok([
        "You know exactly what to say. You say nothing. Again.",
        "You ran the conversation on the walk over. Word for word.",
        "It was never a knowledge problem.",
    ])
    assert ok, f"clean script rejected: {why}"
    bad = [
        "You know exactly what to say.",
        "Just say it next time.",                      # Law 5: 'just' banned
        "It was never a knowledge problem.",
    ]
    ok, why = generate._laws_ok(bad)
    assert not ok, "'just' script must be rejected"
    bad2 = [
        "The conversation ends.",
        "Here is my advice: stop rehearsing.",         # advice banned
        "The trial begins.",
    ]
    ok, why = generate._laws_ok(bad2)
    assert not ok, "advice script must be rejected"


def test_font_laws():
    # hook + landing anton, never same twice in a row
    assert generate._fonts_ok(["anton", "playfair_italic", "anton"], 3)
    assert not generate._fonts_ok(["playfair_italic", "cormorant_italic", "anton"], 3)
    assert not generate._fonts_ok(["anton", "anton", "bebas"], 3)
    assert not generate._fonts_ok(["anton", "bebas"], 3)              # landing not anton
    assert not generate._fonts_ok(["anton", "playfair_italic"], 3)    # wrong length


def test_candidate_validation():
    cfg = config.load_config()
    good = {
        "blocks": ["You know exactly what to say. You say nothing. Again.",
                   "You ran the conversation on the walk over. Word for word.",
                   "It was never a knowledge problem."],
        "fonts": ["anton", "playfair_italic", "anton"],
        "scene": "empty street night rain",
        "archetype": "the_freeze", "topic": "exposure_fear",
        "mood": "quiet_devastating", "bg_type": "freeze_detour",
    }
    assert generate._valid(good, cfg) is True
    assert generate._valid({**good, "scene": ""}, cfg) is False          # scene required
    assert generate._valid({**good, "blocks": ["short", "lines", "ok"]}, cfg) is False
    assert generate._valid({**good, "mood": "aggressive_phonk"}, cfg) is False
    assert generate._valid({**good, "archetype": "hard_truth"}, cfg) is False
    assert generate._valid({**good, "fonts": ["bebas", "caveat", "bebas"]}, cfg) is False


# ------------------------------------------------------------------ dedup

def test_dedup_rejects_near_duplicate():
    dup = "The conversation ends. The trial begins."
    near = "The conversation ends. The trial begins"
    assert generate._similar(dup, near) > generate.DEDUP_RATIO
    banned = [dup]
    cand = {"blocks": [near, "You review the footage until 2am.",
                       "You've been cross-examining yourself since school."],
            "archetype": "the_aftermath", "topic": "replay_2am"}
    assert generate._dedup_ok(cand, banned, set()) is False
    fresh = {"blocks": ["She matched with you. And you're suspicious.",
                        "Being chosen feels like a setup.",
                        "You're not unlovable. You're unreachable."],
             "archetype": "the_craving", "topic": "dating_app_freeze"}
    assert generate._dedup_ok(fresh, banned, set()) is True
    assert generate._dedup_ok(fresh, [], {("the_craving", "dating_app_freeze")}) is False


# ------------------------------------------------------- script + timing

def test_script_blocks_enforce_font_and_roles():
    content = {"blocks": ["You want to talk. Your mouth disagrees.",
                         "There's a version of you that's funny, warm, easy to be around.",
                         "The words were never the problem. The opening was."],
              "fonts": ["anton", "caveat", "anton"]}
    blocks = build_video.script_blocks(content)
    assert [b["role"] for b in blocks] == ["hook", "middle", "landing"]
    assert blocks[0]["font"] == "anton" and blocks[-1]["font"] == "anton"
    # same font twice in a row gets rewritten
    content2 = {"blocks": ["A", "B", "C"], "fonts": ["anton", "anton", "anton"]}
    blocks2 = build_video.script_blocks(content2)
    assert blocks2[1]["font"] != blocks2[0]["font"]
    assert blocks2[-1]["font"] == "anton"


def test_cut_map_hook_hold_law():
    beats = [0.8, 1.6, 2.4, 3.2, 4.0, 4.8, 5.6, 6.4, 7.2, 8.0]
    states = build_video.cut_map(["a"] * 4, beats, 10.0)
    assert states[0]["start"] == 0.0
    assert states[1]["start"] >= 1.8, f"2-second hold law violated: {states}"
    assert states[-1]["end"] == 10.0
    for a, b in zip(states, states[1:]):
        assert b["start"] >= a["start"] + 1.0          # readable holds
    # single block: full reel
    assert build_video.cut_map(["a"], [], 9.0) == [{"index": 0, "start": 0.0, "end": 9.0}]


# --------------------------------------------------------------- analyzer

def test_learn_on_fixture_memory():
    memory = json.loads((FIXTURES / "memory_fixture.json").read_text(encoding="utf-8"))
    cfg = config.load_config()
    for p in memory["posts"]:
        p.setdefault("dna", {}).setdefault("exploit", True)
    posts = analyze.scored_posts(memory)
    assert len(posts) == 10
    G = analyze._global_mean(posts)
    assert 0.02 < G < 0.15, f"global mean out of expected band: {G}"

    strat = config.default_strategy()
    followers = {}
    for arch in cfg["archetypes"]:
        adj, n, wsum = analyze._adjusted(posts, lambda p: (p["dna"] or {}).get("archetype"),
                                         arch, G)
        followers[arch] = (adj, n)
    # shrinkage: a sparse value must be pulled toward G, not sit at its raw mean
    # (v4.0: the_craving appears exactly once, with the lowest raw score)
    sparse, raw_low = None, None
    for arch in cfg["archetypes"]:
        adj, n, _ = analyze._adjusted(posts, lambda p: (p["dna"] or {}).get("archetype"),
                                      arch, G)
        if n == 1:
            raw = [p["score"] for p in posts if (p["dna"] or {}).get("archetype") == arch][0]
            if raw < G:
                sparse, raw_low = arch, raw
                break
    assert sparse is not None and raw_low is not None, \
        "fixture must contain a sparse low-scoring archetype"
    adj_sparse = analyze._adjusted(posts, lambda p: (p["dna"] or {}).get("archetype"),
                                  sparse, G)[0]
    assert abs(adj_sparse - G) < abs(raw_low - G) + 1e-9, \
        "sparse value must shrink toward G"

    # normalized weights
    norm = analyze._normalize({k: v for k, v in {a: followers[a][0] for a in followers}.items()})
    assert abs(sum(norm.values()) - 1.0) < 1e-6

    # report renders with the saves/shares emphasis
    strategy = config.default_strategy()
    strategy["adj"] = {"archetype": {a: followers[a][0] for a in followers},
                       "topic": {}, "mood": {}, "bg_type": {}}
    strategy["n"] = {"archetype": {a: followers[a][1] for a in followers},
                     "topic": {}, "mood": {}, "bg_type": {}}
    strategy["hour_scores"] = {"13:30": 0.06, "15:00": 0.12, "16:30": 0.03}
    strategy["next_post_hour"] = "15:00"
    strategy["experiments"] = [{"archetype": "the_freeze", "topic": "asking_coworker"}]
    text = analyze._write_report(memory, strategy, posts, G)
    assert "Trending alignment" in text
    assert "Exploit vs explore" in text
    assert "insufficient" not in text.lower()


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
        # PyYAML parses the bare `on:` key as boolean True
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
        if name != "learn.yml":        # learn never calls the Instagram API
            assert run_step["env"]["IG_ACCESS_TOKEN"].startswith("${{ secrets.")
        if "actions/checkout@v4" not in [s.get("uses") for s in job["steps"]]:
            raise AssertionError(f"{name}: missing actions/checkout@v4")
    health = yaml.safe_load((wf / "health.yml").read_text(encoding="utf-8"))
    assert health["permissions"].get("secrets") == "write"


# ------------------------------------------------------------- data files

def test_seed_files_and_no_pause():
    for name in ("config.json", "strategy.json", "memory.json",
                 "trending_styles.json", "token_state.json"):
        assert (ROOT / "data" / name).exists(), f"missing data/{name}"
    assert not (ROOT / "data" / "PAUSE").exists(), "PAUSE must not be seeded"
    strat = json.loads((ROOT / "data" / "strategy.json").read_text(encoding="utf-8"))
    assert strat["next_post_hour"] in strat["hour_scores"]
    assert len(strat["weights"]["archetype"]) == 11
    assert len(strat["weights"]["topic"]) == 20


def test_config_families_line_up():
    cfg = config.load_config()
    strat = json.loads((ROOT / "data" / "strategy.json").read_text(encoding="utf-8"))
    assert set(cfg["archetypes"]) == set(strat["weights"]["archetype"])
    assert set(cfg["topics"]) == set(strat["weights"]["topic"])
    assert set(cfg["moods"]) == set(strat["weights"]["mood"])
    assert set(cfg["bg_types"].keys()) == set(strat["weights"]["bg_type"])
    # v4.0: every archetype maps to a bg cluster
    for arch in cfg["archetypes"]:
        assert arch in cfg["archetype_bg_map"], f"{arch} missing from archetype_bg_map"


# ------------------------------------------------- trending confidence filter

def test_trending_filter_confidence():
    data = json.loads((FIXTURES / "trending_fixture.json").read_text(encoding="utf-8"))
    min_conf = 0.5
    good = [r for r in data["results"] if float(r["confidence"]) >= min_conf]
    good.sort(key=lambda r: r["trend_score"], reverse=True)
    assert len(good) == 3, f"expected 3 rows >= 0.5 confidence, got {len(good)}"
    assert sorted(r["confidence"] for r in good) == [0.5, 0.62, 0.86]
    top = good[0]
    assert top["title"] == "Slowed Phonk Drift"
    assert top["trend_score"] == 91
    genre = top["category"].split(":", 1)[1].strip()
    assert genre == "rap"
    assert all(float(r["confidence"]) >= min_conf for r in good)
    assert "Low Confidence Filler" not in [r["title"] for r in good]


# ------------------------------------------------------ pixabay URL regex

def test_pixabay_regex_finds_all_three():
    html = (FIXTURES / "pixabay_fixture.html").read_text(encoding="utf-8")
    found = music.PIXABAY_MP3_RE.findall(html)
    unique = sorted(set(found))
    assert len(unique) == 3, f"expected 3 unique mp3 URLs, got {unique}"
    for u in unique:
        assert u.startswith("https://cdn.pixabay.com/audio/") and u.endswith(".mp3")


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
