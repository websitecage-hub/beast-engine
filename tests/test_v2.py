"""v2 pipeline tests (ENGINE_SPEC §1.6 acceptance).

Covers the things that can silently break the live path:

  * lint.lint's own selftest passes (the spec's gate, run before publish)
  * the spec's OWN gold samples pass the §2 critic — a gate that rejects the
    spec's quality bar aborts all valid work. This is not hypothetical: the
    critic failed sample 3 for carrying the mandated `guide` CTA until it was
    told to score the writing rather than the assigned brief.
  * the writer clears the guide-CTA case, which it previously could not
  * a gold sample renders through the paced path and passes the upload gate
  * the reveal schedule obeys the format: hook at 0, one line at a time, a
    reading time per line, total inside 14-34s

The model-backed tests are marked so a CI run without network can skip them
rather than fail; everything mechanical runs unconditionally.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import lint, paced, render_gate, writer  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"


def _samples() -> list:
    return json.loads((SRC / "sample_reels.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------- mechanical

def test_lint_selftest_passes():
    here = os.getcwd()
    os.chdir(SRC)
    try:
        rc = lint._selftest(str(SRC))
    finally:
        os.chdir(here)
    assert rc == 0, "lint --selftest failed"


def test_reveal_schedule_matches_the_format():
    """STRATEGY §3.2: hook alone at 0, one reading time per line, 14-34s."""
    for reel in _samples():
        lines = [reel["hook"]] + reel["beats"]
        starts, dur = lint.reveal_schedule(lines)
        assert starts[0] == 0.0, "the hook must be on screen at frame 0"
        assert all(b > a for a, b in zip(starts, starts[1:])), \
            f"reveal times must increase: {starts}"
        assert lint.MIN_DUR <= dur <= lint.MAX_DUR, f"duration {dur}s out of range"
        # every line gets at least its reading time before the next arrives
        for i, ln in enumerate(lines):
            nxt = starts[i + 1] if i + 1 < len(starts) else dur
            held = nxt - starts[i]
            assert held >= lint.MIN_GAP - 0.01, \
                f"line {i + 1} held {held:.2f}s (min {lint.MIN_GAP})"


def test_words_per_second_is_readable():
    """The whole point of v2: no line may exceed 3.2 words/s on screen."""
    for reel in _samples():
        lines = [reel["hook"]] + reel["beats"]
        starts, dur = lint.reveal_schedule(lines)
        for i, ln in enumerate(lines):
            nxt = starts[i + 1] if i + 1 < len(starts) else dur
            held = nxt - starts[i]
            rate = len(lint.words(ln)) / held
            assert rate <= 3.2, f"line {i + 1} is {rate:.1f} words/s: {ln!r}"


def test_gold_samples_pass_the_linter():
    recent = []
    for reel in _samples():
        errs = lint.lint(reel, recent)
        assert not errs, f"gold sample failed lint: {errs}"
        recent.append(reel["hook"])


def test_upload_gate_accepts_the_v2_duration_window():
    """The gate must accept 14-34s. It was 16-20s from the one-block format and
    rejected a valid 20.27s paced reel."""
    assert render_gate.MIN_S == lint.MIN_DUR, \
        "the gate and the linter must agree on the minimum duration"
    assert render_gate.MAX_S == lint.MAX_DUR, \
        "the gate and the linter must agree on the maximum duration"


def test_paced_gate_rejects_an_unreadable_line():
    """A line shown too briefly must fail, not ship."""
    lines = ["You didn't miss the call. You watched it ring.",
             "You saw the name on the first ring."]
    # 0.5s between lines: under the 1.0s floor the gate enforces
    ok, info = paced.qa_paced(lines, [0.0, 0.5], 20.0, {}, Path("/tmp/x.mp4"))
    assert not ok, "a 0.5s line gap must be rejected"
    assert any("held" in p for p in info["problems"]), info["problems"]


def test_paced_gate_rejects_a_late_hook():
    lines = ["A hook line here.", "A second line here."]
    ok, info = paced.qa_paced(lines, [1.0, 5.0], 20.0, {}, Path("/tmp/x.mp4"))
    assert not ok, "a hook that does not open the reel must be rejected"
    assert any("first line" in p for p in info["problems"]), info["problems"]


def test_writer_prompt_carries_the_spec_rules():
    """The writer must be told the real constraints, not a paraphrase of them."""
    p = writer.writer_system_prompt()
    assert "3-9 words" in p, "hook word limit missing"
    assert "30-60 words" in p, "on-screen word budget missing"
    assert "5-8 lines" in p, "line count missing"
    for claim in ("cure", "heal", "trauma", "journey"):
        assert claim in p, f"banned-claim list missing {claim!r}"
    assert len(writer.hook_types()) == 12, "expected the 12 hook types"


# ----------------------------------------------------------------- model-backed

def _models_up() -> bool:
    try:
        from src import reachability
        return reachability.models_up()
    except Exception:  # noqa: BLE001
        return False


def test_gold_samples_pass_the_critic():
    """The spec's own examples are the quality bar. A critic that fails them is
    miscalibrated and would reject valid work forever."""
    if not _models_up():
        print("  (skipped: critic model not reachable)")
        return
    for i, reel in enumerate(_samples(), 1):
        passed, v = writer.critic_pass(reel)
        assert passed, f"gold sample {i} failed the critic: {json.dumps(v)[:220]}"


def test_writer_clears_the_guide_cta_case():
    """The writer previously could not pass a guide-CTA reel at all: the critic
    docked sendability for the CTA that STRATEGY §5.2 mandates."""
    if not _models_up():
        print("  (skipped: writer model not reachable)")
        return
    res = writer.write("why_you_do_that", "open_loop",
                       "you go silent in a group but talk fine one-on-one",
                       "18-24", "stack", "guide")
    assert res["ok"], f"writer failed the guide case at {res['stage']}: {res['notes'][:200]}"
    reel = res["reel"]
    assert not lint.lint(reel, []), "a passing draft must also pass lint"


if __name__ == "__main__":
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)