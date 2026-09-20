"""Acceptance tests (section 11). Run: python -m pytest tests/ -v  (or python tests/run_tests.py)

Covers: clean imports, trending confidence filter, Pixabay regex, dedup rejection,
learn on the fixture memory, and workflow YAML contracts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src import analyze, config, generate, music  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


# ------------------------------------------------------------ test imports

def test_imports_clean():
    import src.run_create  # noqa: F401
    import src.run_measure  # noqa: F401
    import src.run_learn  # noqa: F401
    import src.run_health  # noqa: F401
    assert True


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


# ------------------------------------------------------------------ dedup

def test_dedup_rejects_near_duplicate():
    dup = "You were not built for comfort"
    near = "You were not built for comfort."          # ratio ~0.98
    assert generate._similar(dup, near) > generate.DEDUP_RATIO
    banned = [dup]
    cand = {"hook": near, "archetype": "hard_truth", "topic": "discipline"}
    assert generate._dedup_ok(cand, banned, set()) is False
    fresh = {"hook": "The mirror is not your friend", "archetype": "hard_truth",
             "topic": "discipline"}
    assert generate._dedup_ok(fresh, banned, set()) is True
    # (archetype, topic) used in the last 7 days is rejected too
    assert generate._dedup_ok(fresh, [], {("hard_truth", "discipline")}) is False


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
    assert followers["secret_reveal"][1] == 1
    assert abs(followers["secret_reveal"][0] - G) < abs(0.0765 - G) + 1e-9

    # normalized weights
    norm = analyze._normalize({k: v for k, v in {a: followers[a][0] for a in followers}.items()})
    assert abs(sum(norm.values()) - 1.0) < 1e-6

    # report renders with the trending-correlation section
    strategy = config.default_strategy()
    strategy["adj"] = {"archetype": {a: followers[a][0] for a in followers},
                       "topic": {}, "mood": {}, "bg_type": {}}
    strategy["n"] = {"archetype": {a: followers[a][1] for a in followers},
                     "topic": {}, "mood": {}, "bg_type": {}}
    strategy["hour_scores"] = {"13:30": 0.06, "15:00": 0.12, "16:30": 0.03}
    strategy["next_post_hour"] = "15:00"
    strategy["experiments"] = [{"archetype": "challenge_dare", "topic": "focus"}]
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
    assert len(strat["weights"]["archetype"]) == 8
    assert len(strat["weights"]["topic"]) == 10


def test_config_families_line_up():
    cfg = config.load_config()
    strat = json.loads((ROOT / "data" / "strategy.json").read_text(encoding="utf-8"))
    assert set(cfg["archetypes"]) == set(strat["weights"]["archetype"])
    assert set(cfg["topics"]) == set(strat["weights"]["topic"])
    assert set(cfg["moods"]) == set(strat["weights"]["mood"])
    assert set(cfg["bg_types"].keys()) == set(strat["weights"]["bg_type"])


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
