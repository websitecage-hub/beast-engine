# Hermes implementation report — Section 17 one-shot brief

Branch: `feat/hermes-experiment-engine`
Implementation commit: `a473b68` — *feat(hermes): research audit, content blueprint,
format registry, dry-run runner*
Backup reference: tag `backup/pre-hermes-2026-10-17` and branch `backup/main-pre-hermes`
(at `6d8ef14`), pre-work snapshot `073d7f0`.
Patch (if the branch is not reachable): `docs/hermes/hermes-experiment-engine.patch`.

Steps executed: **0, 1, 2, 3**. Step 4 was **evaluated and intentionally skipped**
(registered declaratively, not built). No publishing path was touched.

---

## 1. Files changed (17 files, +2526 / −60)

New:
- `src/research/__init__.py`, `normalize.py`, `analyze.py`, `report.py`
- `docs/research/RESEARCH_AUDIT.md` (generated)
- `src/blueprint.py`
- `src/formats/__init__.py`, `registry.py`, `renderers.py`
- `src/experiment.py`
- `src/run_experiment.py`
- `tests/test_blueprint.py`, `test_formats.py`, `test_experiment.py`, `test_render_short.py`

Modified:
- `src/render_gate.py` — `check()` gains an optional, backward-compatible
  `duration_bounds` (defaults to the v2 14-34s window).
- `src/build_short.py` — six render correctness fixes (below).

## 2. Tests run and results

| suite | tests | result |
|---|---|---|
| `tests/test_acceptance.py` | 75 | PASS |
| `tests/test_learner.py` | 18 | PASS |
| `tests/test_blueprint.py` | 16 | PASS |
| `tests/test_formats.py` | 16 | PASS |
| `tests/test_experiment.py` | 14 | PASS |
| `tests/test_render_short.py` | 4 | PASS (real ffmpeg) |
| `tests/test_v2.py` | 10 | PASS (model-backed) |
| `tests/test_render_gate.py` | — | ALL RENDER-GATE CHECKS PASSED |
| `src/lint_short.py` selftest | — | PASSED |
| `src/lint.py --selftest` | — | PASSED |

Non-model total: 143 passed. Plus 10 model-backed and both selftests.
`python3 -m py_compile src/*.py src/research/*.py src/formats/*.py tests/*.py` clean.

Integration evidence (real, no mocks):
- Offline v3 render through `run_experiment` → gate PASS.
- **Model-backed** v3 render (live Meta writer + DeepSeek critic) → gate PASS.
- **Arm B asset acquisition** (Pinterest via the self-hosted service) → real
  video background → gate PASS.

## 3. Render artifact paths (local; `outputs/` is gitignored)

- `outputs/experiments/full-run/two_beat_v3.mp4` + `preview_hook.png`, `preview_payoff.png`
- `outputs/experiments/model-backed/two_beat_v3.mp4`
- `outputs/experiments/selftest-offline/two_beat_v3.mp4`
- `outputs/armB_acq.mp4` (Pinterest asset acquisition)

Last full run produced (T7, "saying I'm fine", 13 words, arm C, 9.0s, 1080x1920):
hook `me texting i'm fine: battery 89%...` / payoff `them: 'but are you fine fine?': 1%` /
caption `my fine is running on low power mode tbh`. The critic rejected the first
candidate ("social battery is worn meme phrasing", specificity 2) and forced a rewrite.

## 4. Evidence found (research audit)

- Local corpus normalised to **150 records**: `corpus.json` 75 + three Reddit Atom
  feeds 75. Subreddits lonely 50 / socialanxiety 100. **43 duplicate groups.**
  **No performance fields** (no views/likes/baseline) — so no outlier table can be
  built from local data without re-collection.
- The reported **346 hashtag reels / 300 reels across 20 accounts** dataset does
  **not exist** locally (no `videos.json`, no Apify/vidIQ export). It exists only
  as a plan summary in the owner's notes.
- The eight vidIQ analyses are recorded as **third-party observational** evidence,
  kept separate from facts and from sales claims (`docs/research/RESEARCH_AUDIT.md`).
- **Step 4 decision:** no rights-cleared semantic b-roll source exists; the only
  cinematic evidence is uncontrolled third-party footage. `cinematic_v1` is
  registered `not_eligible` and no `build_cinematic.py` was added.

## 5. Budget consumed

**$0 and 0 credits.** No vidIQ or Apify calls. vidIQ reserve (45 credits, floor 25)
untouched. Model calls used the free Meta/DeepSeek bridges only.

## 6. Renderer fixes made while wiring v3 end to end

These were latent defects in the pre-existing, previously-unrun v3 renderer:
1. `drawtext` `y=` prefix missing (invalid filtergraph).
2. Hook silently truncated to 2 lines, dropping words — replaced with `_fit_beat`
   auto-fit that keeps every word.
3. Audio input label off-by-one (`[2:a]` → `[1:a]`).
4. `-loop 1` used on an MP4 (image-demuxer option) → loop by extension.
5. `anullsrc` (a source filter) given an input label → invalid graph.
6. Arm-C glow `geq` ran per output frame (9s reel took ~134s); glow pinned to one
   frame → ~34s, gate-passing.
Also: the pinned static ffmpeg has **libass but not drawtext**, so text is burned
in via an ASS track (`build_short.write_ass`), which also carries beat timing.

## 7. Known blockers / notes

- **Pexels arm A is unavailable** (no `PEXELS_API_KEY`); it falls back to arm C and
  the run logs why. Arm B (Pinterest) works.
- No GitHub auth in this environment (`gh` not logged in), so no PR was opened;
  the patch above is provided instead. The branch is local and not pushed.
- `data/REPORT.md` is production brain-state; a test run regenerated it and it was
  **reverted** — it is not part of the commit.

## 8. Exact owner approval still required

Nothing was published and no schedule or dry-run default was changed. The
following remain owner decisions:
1. **Go-live**: only after reviewing the previews, flip the `confess.yml`
   `dry_run` default to false and uncomment the schedule — deliberately, by hand.
2. **Push / PR**: approve pushing `feat/hermes-experiment-engine` and opening a PR
   (no credentials here to do it).
3. **Paid research**: only if you decide the specific gap in §4 justifies vidIQ/
   Apify spend, with live pricing checks.
```
