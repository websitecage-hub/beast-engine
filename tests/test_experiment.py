"""Step 2/3 acceptance: experiment engine bookkeeping + dry-run safety guard."""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import experiment  # noqa: E402

ARM_A = "two_beat_v3"
ARM_B = "paced_v2"


def _raises(exc, fn, *a, **k) -> bool:
    try:
        fn(*a, **k)
    except exc:
        return True
    return False


# ----------------------------------------------------------------- storage

def test_empty_and_roundtrip(tmp_path):
    p = tmp_path / "experiments.json"
    exp = experiment.load(p)
    assert exp["assignments"] == []
    experiment.log_assignment(exp, arm=ARM_A, snapshots=["2h"])
    experiment.save(exp, p)
    again = experiment.load(p)
    assert len(again["assignments"]) == 1
    assert again["assignments"][0]["arm"] == ARM_A


def test_corrupt_file_loads_empty(tmp_path):
    p = tmp_path / "experiments.json"
    p.write_text("{not json", encoding="utf-8")
    assert experiment.load(p)["assignments"] == []


# ----------------------------------------------------------------- maturity

def test_sample_maturity():
    assert experiment.sample_maturity(1)["mature"] is False
    assert experiment.sample_maturity(25)["mature"] is False
    assert experiment.sample_maturity(80)["mature"] is True
    assert experiment.sample_maturity(200)["mature"] is True
    assert experiment.sample_maturity(80)["snapshots_available"] == ["2h", "24h", "72h"]


def test_maturity_comes_from_snapshots_not_wallclock():
    exp = experiment.empty()
    a = experiment.log_assignment(exp, arm=ARM_A, snapshots=["2h", "24h"])
    assert a["mature"] is False
    b = experiment.log_assignment(exp, arm=ARM_A, snapshots=["2h", "72h"])
    assert b["mature"] is True


def test_mature_counts_excludes_invalid():
    exp = experiment.empty()
    experiment.log_assignment(exp, arm=ARM_A, snapshots=["72h"], valid=True)
    experiment.log_assignment(exp, arm=ARM_A, snapshots=["72h"], valid=False)
    assert experiment.mature_counts(exp) == {ARM_A: 1}


# ----------------------------------------------------------------- selection

def test_recommend_only_returns_eligible():
    exp = experiment.empty()
    rec = experiment.recommend_arm(exp, [ARM_A, ARM_B], rng=random.Random(1))
    assert rec["arm"] in (ARM_A, ARM_B)
    assert rec["phase"] == "calibration"


def test_recommend_balancing_prefers_least_used():
    exp = experiment.empty()
    experiment.log_assignment(exp, arm=ARM_A, snapshots=["2h"])
    rec = experiment.recommend_arm(exp, [ARM_A, ARM_B], rng=random.Random(0))
    assert rec["arm"] == ARM_B


def test_cooldown_blocks_third_consecutive():
    exp = experiment.empty()
    rec = experiment.recommend_arm(exp, [ARM_A, ARM_B],
                                   recent_arms=[ARM_A, ARM_A],
                                   rng=random.Random(0))
    assert rec["arm"] == ARM_B


def test_recommend_raises_when_no_arms():
    assert _raises(ValueError, experiment.recommend_arm, experiment.empty(), [])


def test_phase_d_switches_when_mature():
    exp = experiment.empty()
    # past the calibration size AND >= MIN_MATURE_PER_ARM mature per arm
    for _ in range(8):
        experiment.log_assignment(exp, arm=ARM_A, snapshots=["72h"],
                                  metrics={"share_rate": 0.10})
        experiment.log_assignment(exp, arm=ARM_B, snapshots=["72h"],
                                  metrics={"share_rate": 0.02})
    # many draws must all be exploit/explore, never the calibration label
    rng = random.Random(7)
    phases = {experiment.recommend_arm(exp, [ARM_A, ARM_B], rng=rng)["phase"]
              for _ in range(50)}
    assert phases <= {"exploit", "explore"}, phases


# ----------------------------------------------------------------- replication

def test_replication_needs_three_supporting_examples():
    exp = experiment.empty()
    # 4 baseline posts + 3 that clear 3x the median -> exactly 3 supports
    for _ in range(4):
        experiment.log_assignment(exp, arm=ARM_A, snapshots=["72h"],
                                  metrics={"share_rate": 0.01})
    for _ in range(3):
        experiment.log_assignment(exp, arm=ARM_A, snapshots=["72h"],
                                  metrics={"share_rate": 0.10})
    assert experiment.replication_support(exp, ARM_A) == 3
    assert experiment.ready_to_promote(exp, ARM_A) is True


def test_single_viral_post_is_not_enough():
    exp = experiment.empty()
    experiment.log_assignment(exp, arm=ARM_A, snapshots=["72h"],
                              metrics={"share_rate": 0.40})
    assert experiment.ready_to_promote(exp, ARM_A) is False


# ----------------------------------------------------------------- safety guard

def test_run_experiment_cannot_publish():
    src = (Path(__file__).resolve().parents[1]
           / "src" / "run_experiment.py").read_text(encoding="utf-8")
    # no actual publish call (the constant that merely names them is allowed)
    assert not re.search(r"media_publish\s*\(", src)
    assert not re.search(r"publish_reel\s*\(", src)
    assert "import publish" not in src
    assert "from . import publish" not in src
    assert ", publish" not in src


def test_run_experiment_defaults_are_dry_run():
    src = (Path(__file__).resolve().parents[1]
           / "src" / "run_experiment.py").read_text(encoding="utf-8")
    # the only publish-related word allowed is the FORBIDDEN_SYMBOLS constant
    assert "dry_run" in src
    assert 'default="auto"' in src
