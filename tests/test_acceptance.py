"""Acceptance tests (section 11) — v4.1 single-paragraph edition.

Run: python3 tests/test_acceptance.py

Covers: clean imports, law enforcement on paragraphs, measured-fit overflow
gate, dedup rejection, learn on fixture memory, workflow YAML contracts.
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
    ok, why = generate._laws_ok(
        "You know the exact words. You ran them on the walk over. Your mouth filed "
        "for silence. It was never a knowledge problem.")
    assert ok, f"clean paragraph rejected: {why}"
    ok, why = generate._laws_ok(
        "You know the exact words. Just say them next time. It was never courage.")
    assert not ok and "just" in why, "'just' must be rejected"
    ok, why = generate._laws_ok(
        "The conversation ends. Here is my advice: stop rehearsing. The trial begins.")
    assert not ok, "advice must be rejected"
    ok, why = generate._laws_ok("Two sentences only. Nothing more.")
    assert not ok, "under 3 sentences must be rejected"


def test_candidate_validation():
    cfg = config.load_config()
    good = {
        "paragraph": ("You know the exact words. You ran them on the walk over, word "
                      "for word. Then the moment arrived and your mouth filed for "
                      "silence. It was never a knowledge problem."),
        "scene": "empty street night rain",
        "archetype": "the_freeze", "topic": "exposure_fear",
        "mood": "quiet_devastating", "bg_type": "freeze_detour",
    }
    assert generate._valid(good, cfg) is True
    assert generate._valid({**good, "scene": ""}, cfg) is False          # scene required
    assert generate._valid({**good, "paragraph": "too short."}, cfg) is False
    assert generate._valid({**good, "mood": "aggressive_phonk"}, cfg) is False
    assert generate._valid({**good, "archetype": "hard_truth"}, cfg) is False


# ------------------------------------------------------------------ dedup

def test_dedup_rejects_near_duplicate():
    dup = ("You know the exact words. You ran them on the walk over. Your mouth "
           "filed for silence. It was never a knowledge problem.")
    near = ("You know the exact words. You ran them on the walk over. Your mouth "
            "filed for silence. It was never a knowledge problem")
    assert generate._similar(dup, near) > generate.DEDUP_RATIO
    banned = [dup]
    cand = {"paragraph": near, "archetype": "the_freeze", "topic": "exposure_fear"}
    assert generate._dedup_ok(cand, banned, set()) is False
    fresh = {"paragraph": ("She matched with you. Being chosen feels like a setup. "
                           "You never reply. You are not unlovable, you are "
                           "unreachable."),
             "archetype": "the_craving", "topic": "dating_app_freeze"}
    assert generate._dedup_ok(fresh, banned, set()) is True
    assert generate._dedup_ok(fresh, [], {("the_craving", "dating_app_freeze")}) is False


# --------------------------------------------------- measured fit (no overflow)

def test_fit_paragraph_never_overflows():
    cfg = config.load_config()
    long_p = ("You know the exact words and you ran them on the walk over, word for "
              "word, and then the moment arrived and your mouth filed for silence "
              "while everyone watched and the trial starts tonight at two in the "
              "morning reviewing what you did not say.")
    lines, px = build_video.fit_paragraph(long_p, cfg)
    build_video.assert_fits(lines, px, cfg)      # raises on any overflow
    assert len(lines) >= 2 and px > 0
    # short paragraph keeps the biggest font
    lines2, px2 = build_video.fit_paragraph("You know the exact words.", cfg)
    assert px2 == build_video.PX_LADDER[0]


# --------------------------------------------------------------- corpus

def test_reddit_corpus_loaded():
    posts = generate.load_corpus()
    assert len(posts) >= 50, f"corpus too small: {len(posts)}"
    sample = generate._corpus_sample()
    assert 1 <= len(sample) <= generate.CORPUS_SAMPLE
    assert all("title" in p or "text" in p for p in sample)


def test_batch_variety_guard():
    def cand(arch, para):
        return {"archetype": arch, "paragraph": para, "topic": "exposure_fear",
                "mood": "quiet_devastating", "bg_type": "mask", "scene": "s"}
    batch = [
        cand("the_mask", "You smile on cue. You nod. You vanish inside it."),
        cand("the_mask", "You pull out your phone. You scroll nothing. You hide."),
        cand("the_mask", "You rehearse the order. You mumble. You apologize."),
        cand("the_mask", "You laugh too late. You keep your voice low. You edit."),
        cand("the_freeze", "The order you rehearsed, fumbled anyway. Then silence."),
        cand("the_aftermath", "Two years ago the phone rang and you let it. Still."),
        cand("the_losses", "There is a version of you that everyone likes. Gone."),
    ]
    kept = generate.diversify(batch)
    arch_counts = {}
    for c in kept:
        arch_counts[c["archetype"]] = arch_counts.get(c["archetype"], 0) + 1
    assert arch_counts.get("the_mask", 0) <= 3, f"archetype cap failed: {arch_counts}"
    assert len(kept) >= 4
    # opening patterns are classified, and 'you_verb' is capped
    assert generate._opening_pattern("You smile on cue.") == "you_verb"
    assert generate._opening_pattern("Two years ago you couldn't order pizza.") == "time"
    assert generate._opening_pattern("There is a version of you.") == "there"
    assert generate._opening_pattern("The order you rehearsed, fumbled.") == "scene"


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

    followers = {}
    for arch in cfg["archetypes"]:
        adj, n, wsum = analyze._adjusted(posts, lambda p: (p["dna"] or {}).get("archetype"),
                                         arch, G)
        followers[arch] = (adj, n)
    sparse, raw_low = None, None
    for arch in cfg["archetypes"]:
        adj, n, _ = analyze._adjusted(posts, lambda p: (p["dna"] or {}).get("archetype"),
                                      arch, G)
        if n == 1:
            raw = [p["score"] for p in posts if (p["dna"] or {}).get("archetype") == arch][0]
            if raw < G:
                sparse, raw_low = arch, raw
                break
    assert sparse is not None and raw_low is not None
    adj_sparse = analyze._adjusted(posts, lambda p: (p["dna"] or {}).get("archetype"),
                                  sparse, G)[0]
    assert abs(adj_sparse - G) < abs(raw_low - G) + 1e-9

    norm = analyze._normalize({k: v for k, v in {a: followers[a][0] for a in followers}.items()})
    assert abs(sum(norm.values()) - 1.0) < 1e-6

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
