# Unleash The Beast — BEAST ENGINE

Fully autonomous Instagram Reels engine for **@unleashthe.b** (social anxiety,
audience 18–30, US/UK/CA). The product is an ebook
(Gumroad: `unleashthebeast.gumroad.com/l/dufrt`).

The engine is **stateless compute + committed JSON**. GitHub Actions are the
compute; the files in `data/` are the database and the brain. Every job reads
state, does its work, and commits the updated state back into the repository.

> **CURRENT CONDITION: v3 BUILT, TESTED, DELIVERING TO TELEGRAM. PUBLISHING OFF.**
> The v3 short format is complete and the full pipeline has been proven
> end-to-end with real renders (Pinterest backgrounds, audio, gate PASS, gate
> measured). **Nothing is publishing:** `short_publish_enabled` is `false` in
> `data/config.json`, and **zero posts in memory carry an IG `media_id`**. Reels
> are delivered to Telegram for review instead. Two further things are
> outstanding and are called out honestly in
> [Current condition](#current-condition-honestly):
> there is **no scheduled workflow for v3 yet** (the existing workflows drive v2),
> and the critic/instrumentation changes are fresh enough that only a handful of
> live runs have exercised them.

---

## Table of contents

1. [How it works](#how-it-works)
2. [The v3 format](#the-v3-format)
3. [The Pinterest asset contract (v2.5)](#the-pinterest-asset-contract-v25)
4. [Text treatment](#text-treatment)
5. [What is code, what is judgment](#what-is-code-what-is-judgment)
6. [The learning system](#the-learning-system)
7. [Architecture and data flow](#architecture-and-data-flow)
8. [Repository layout](#repository-layout)
9. [Workflows (the runners)](#workflows-the-runners)
10. [The v3 loop step by step](#the-v3-loop-step-by-step)
11. [Module reference](#module-reference)
12. [External services](#external-services)
13. [State and data files](#state-and-data-files)
14. [Verification gates](#verification-gates)
15. [Secrets](#secrets)
16. [Running it locally / in CI](#running-it-locally--in-ci)
17. [Tests](#tests)
18. [Ignition](#ignition)
19. [Pause, kill switch, token recovery](#pause-kill-switch-token-recovery)
20. [Current condition, honestly](#current-condition-honestly)

---

## How it works

One publishing cycle does the following, in order:

1. **Decide whether to post.** A scheduler checks the UTC post window and the
   daily cap; a `data/PAUSE` file aborts the whole run.
2. **Wake the sleeping services.** The self-hosted Render services (Pinterest,
   audio scraper) and the Meta/DeepSeek model endpoints are on the free tier and
   sleep when idle, so each is pinged before use.
3. **Choose what to make.** The bandit learner picks one arm per attribute
   (template, topic, visual arm, audio type, length bucket) with hard
   constraints applied *before* the draw, not after.
4. **Write it.** The writer model produces `hook` + `payoff` + caption inside a
   strict brief, shown a handful of register examples. A **linter** (code)
   rejects rule violations; the **critic** (a different vendor's model) scores
   the draft; the writer gets up to 5 note-carrying attempts.
5. **Build the media.** Fetch and normalise a **Pinterest** background (clip, or
   still with a push-in), burn the two beats in with a scrim, mix audio.
6. **Gate it.** The render gate measures the actual file — dimensions, SAR,
   fps, duration, moov, decode, and whether an audio stream exists. A file that
   fails the gate is never published or delivered.
7. **Publish or deliver.** With publishing on: `publish.publish_reel` to
   Instagram and power-up hosts the file. With publishing off: upload to a free
   host and send the reel to **Telegram** for review. Both paths record the real
   outcome in memory (including whether delivery actually succeeded).
8. **Learn.** A later pass reads insights, computes the reward, and updates the
   arm statistics and the topic/format memory.

---

## The v3 format

Replaces the 22-second paced text reel. Six to twelve seconds, two beats, one
idea.

| Attribute | Values |
|---|---|
| Length | 6–12s (`6-8` / `8-10` / `10-12` buckets, 9.0s is the working default) |
| Beats | **hook** (frame 0) and **payoff** (55–65% of runtime), then hold to loop |
| Templates | `T1`–`T10` in `src/templates.json` |
| Visual arms | `pin_video` (Pinterest clip) and `pin_still` (Pinterest still + push-in) |
| Audio | quiet ambient / trending, from the audio scraper or a local bed |
| Caption | one line, plus hashtags; the idea lives in the video |

**Topics** are relatable moments, never medical claims: social battery, group
chats, replying late, cancelled plans, phone calls, small talk, "I'm fine",
leaving parties, 3am replays, rehearsing sentences, reading a text five times.

**Sendability is the whole game.** The research that produced v3 found that
share rate separated the winners (4–13% of views on the big reels) while the
engine's own reels had collected zero shares. So the brief asks the writer to
name the ONE friend a reel gets sent to, and the critic scores exactly that.

### The ten templates

Structures observed in winners, with original example wording. The writer is
told to use the *structure* and never the words — and the linter enforces that
(see [What is code](#what-is-code-what-is-judgment)).

| Code | Structure |
|---|---|
| T1 | People think X. It's actually Y. |
| T2 | X before / Y after |
| T3 | Send this to the friend who... |
| T4 | Not X. Just Y. |
| T5 | Therapist / me dialogue |
| T6 | Me vs. me |
| T7 | Battery / counter |
| T8 | 3am brain |
| T9 | Introverts when... |
| T10 | POV (**capped at 1 in 5 reels**) |

`templates.json` also carries a `few_shot` block: five register examples shown to
the writer as the target voice. They are *also* fed to the copy linter, because
the easiest way to satisfy a prompt full of examples is to parrot them.

---

## The Pinterest asset contract (v2.5)

Pinterest is the **only** asset source (`src/pinterest.py`,
`src/background.py`, `src/pin_still.py`). Base:
`https://pinterest-api-inyg.onrender.com`, keyless. Reference:
`/home/testflowthrowout/pinterest-api/USAGE.md`.

The failure that shaped this integration, and the rules it produced:

| Symptom | Root cause | Rule now enforced |
|---|---|---|
| Backgrounds silently became synthetic | **13 of 23** video results are HLS-only: `best_video` is `null` and the media lives in `videos[].m3u8`. The old picker discarded them, so the arm "failed" and fell back | `media_url()` returns an `.m3u8` when no MP4 exists; ffmpeg remuxes it |
| A download 403'd for pins that clearly had media | Opaque ids (`e1_...`) do not resolve through `/pin/{id}/`, and direct CDN fetches are refused | `download()` uses the **`/download?url=` proxy**, which accepts `i.pinimg.com`, `v1.pinimg.com`, `v.pinimg.com` |
| Only 25 candidates seen per query | `page_size` was pinned at 25 | `page_size` up to 100, pagination via `next_bookmark` |
| The same clip kept coming back | No provenance check | `_recent_pin_ids(memory)` excludes recently used pins; usage is recorded |

**No synthetic background arm exists.** A generated gradient was tried and
removed: when the Pinterest arm failed, the correct fix was the client above,
not a stand-in. Offline mode still renders, and returns `background.build`'s
bundled source **flagged by name** so it is never reported as Pinterest.

The still arm (`src/pin_still.py`) follows USAGE.md's documented route:
`best_image` (or `images.orig`) from the search payload → `/download?url=` proxy
→ `animate_still` push-in, preferring pins with a person in frame.

---

## Text treatment

The flaw that killed v2: text appearing one beat on top of another in the same
position, and backgrounds unrelated to the words.

- **One beat on screen at a time.** Never stacked, never in the same slot.
- **Max 2 lines per beat**; overflow words are dropped rather than drawn.
- Hook is drawn **larger** than the payoff (74px vs 58px, shrinking to fit).
- A **dark scrim** (`colorchannelmixer=aa=0.45`) sits between footage and text —
  ffmpeg ignores `overlay`'s `alpha=`, so the mixer is used instead.
- Text lives inside a **safe band** (`TEXT_X0, TEXT_X1 = 150, 930`) so Reels UI
  never covers it.
- `scene_queries[]` must name concrete nouns that match the reel's own words; a
  mood word is rejected.

---

## What is code, what is judgment

Deliberate split, so the system never depends on a model for something a
comparison can decide:

**Code (deterministic)** — `src/lint_short.py`:
word budgets; banned claims and phrasing; **5+ content-word overlap** against the
template examples, the few-shot examples, and recent reels; the T10 POV cap;
and the loop check (the payoff must share a content word with the hook).
`lint_short.selftest()` covers these.

**Judgment (model)** — the critic: `specificity`, `sendability`, `tone_safety`,
`originality`, plus `loop_ok` / `claims_ok`, and it must name *who* would send
the reel and what they'd reply.

> **Rubric bug, fixed:** the critic's rubric said originality
> `template-feel ... = max 3` while the pass bar is *all scores ≥ 4*. Since the
> writer is **required** to follow a template, the gate was unsatisfiable — and
> that is exactly what the logs showed: 4 of 5 attempts failing on
> `originality: 3` alone with specificity/sendability at 4–5. The rubric now
> judges the **wording** (stale meme phrasing caps at 3) and states that using
> the assigned template is never a penalty.

---

## The learning system

`src/learn_short.py` on top of the proven `src/learner.py` math
(Thompson sampling, shrinkage, medians, CIs).

**Reward weights** — shares dominate, because shares separated winners:

```python
REWARD_WEIGHTS = {"shares_over_reach": 25.0, "saves_over_reach": 8.0, ...}
```

- **Breakout rule:** reach ≥ `max(5 × median(last 20), 500)` **or** 5+ shares.
- **Clone rule:** a breakout spawns up to 5 clones that vary the arm that won.
- **Kill rule:** an arm is retired after 8+ posts if its mean reward sits in the
  bottom 25% *and* its CI is clear of the median.
- **Exploration rate** adapts to how much signal exists.
- **Hard constraints before the draw:** no repeat topic/template too soon, and
  the POV cap — applied in the picker so a whole slot is never lost to an
  unwinnable draft afterwards.

---

## Architecture and data flow

```
                 ┌──────────────── data/*.json  (the brain) ─────────────────┐
                 │  memory.json · config.json · strategy.json · DIRECTIVES   │
                 └───────────────┬───────────────────────────┬──────────────┘
                                 │ read                      │ commit
   schedule / dispatch ──────────▼───────────────────────────▲──────────────
                                 │                           │
                        ┌────────┴────────┐                  │
                        │  src/run_short  │  the v3 loop     │
                        └────────┬────────┘                  │
             learner.choose_v3 ──┤                            │
                                 ├─ generate_short (writer) ─ critic ─→ 5 attempts max
                                 ├─ lint_short (code gate)
                                 ├─ build_short + background/pin_still ─→ Pinterest
                                 ├─ music.acquire
                                 └─ render_gate.check (measures the FILE)
                                 │
                 publishing ON ──┼─ publish.publish_reel → Instagram
                 publishing OFF ─┴─ upload_host → alerts.telegram_video
```

Nothing is trusted because a function returned. The gate **measures the
artifact**; delivery records its own outcome.

---

## Repository layout

```
beast-engine/
├── .github/workflows/     confess · create · measure · learn · health · keepalive · notify
├── data/                  the brain (memory, config, strategy, DIRECTIVES, provenance)
├── docs/                  SYSTEM.md · strategy/{STRATEGY,ENGINE_SPEC,PROMPTS,LANGUAGE}.md · hermes
├── outputs/               rendered reels, backgrounds, ass files, v3_drafts.jsonl
├── src/                   the engine (see Module reference)
├── tests/                 suites + operator scripts (_v3_quick.py, _v3_sample.py, …)
├── assets/                bundled fallback background, fonts
├── README.md · ABOUT.md · HANDOFF.md · BEAST_ENGINE_MASTER_ARCHITECTURE.md
└── requirements.txt
```

---

## Workflows (the runners)

| Workflow | Trigger | Role |
|---|---|---|
| `confess.yml` | dispatch only (cron **commented out**) | the v2 posting role; wiring the switch that turns publishing on |
| `create.yml` | dispatch (`dry_run`, default true) | the v2 create/render job |
| `measure.yml` | cron `0 4,10,16 * * *` | reads insights, writes metrics |
| `learn.yml` | cron `0 18 * * 0` | the learning pass |
| `health.yml` | cron `0 5 * * *` | service health |
| `keepalive.yml` | cron `0 6 * * 1` | keeps GitHub from disabling crons on a quiet repo |
| `notify.yml` | dispatch | Telegram notify |

**There is no workflow for v3 yet.** `src/run_short.py` runs on the daily
cadence (`12:30 / 17:30 / 01:00` UTC) but nothing schedules it in Actions. That
is the main outstanding item — see [Current condition](#current-condition-honestly).

---

## The v3 loop step by step

`src/run_short.py`, `build_one()`:

0. **Kill-rule arms** are read from memory and excluded.
1. **The learner picks** template, topic, visual arm, audio type, length bucket —
   constraints applied before the draw.
2. **Write → lint → critic.** Up to **5** drafts per slot. Rationale in the code:
   the critic's bar rejects most first drafts, and a slot that dies after 3
   attempts loses the whole posting window. Every draft — passed or rejected —
   is appended to `outputs/v3_drafts.jsonl` so rejected words are never lost.
3. **Music** via `music.acquire()` (writes a fixed `outputs/track.mp3`; the
   return value is a *source name*, not a path).
4. **Background**, always Pinterest: `pin_still` → still push-in, else
   `pin_video` → `background.build` (clip first, still push-in inside).
5. **Render** (`build_short.build`) → hook/payoff burned in, scrim, audio mixed.
6. **Gate** (`render_gate.check`) measures dimensions, SAR, fps, duration, moov,
   decode, audio presence.
7. **Record** the post in memory with the *real* outcome.
8. **Deliver**: publish (if enabled) or Telegram, then log whether delivery
   actually succeeded (`telegram_delivered`) — a `False` used to look exactly
   like a success.
9. **Commit state** back to the repository.

---

## Module reference

**v3 core**

| Module | Role |
|---|---|
| `run_short.py` | the v3 daily loop and CLI (`--force`, `--no-publish`, `--dry-run`) |
| `generate_short.py` | the writer: brief, few-shot register examples, critic rubric |
| `lint_short.py` | deterministic gate (budgets, bans, copy overlap, POV cap, loop) |
| `build_short.py` | renderer: Pinterest backgrounds, text treatment, scrim, audio |
| `learn_short.py` | v3 learner: reward, arms, breakout, clones, kill rule |
| `pin_still.py` | the Pinterest still-push-in arm |
| `templates.json` | T1–T10 + topics + few-shot examples |

**Assets / plumbing (proven, reused)**

| Module | Role |
|---|---|
| `pinterest.py` | Pinterest v2.5 client (search, HLS-aware `media_url`, proxy `download`) |
| `background.py` | fetch → validate → normalise → loop/animate; provenance |
| `music.py` | audio acquisition (fixed output path) |
| `render_gate.py` | measures the rendered file before anything ships |
| `publish.py` | `caption_for`, `publish_reel` to Instagram |
| `upload_host.py` / `alerts.py` / `notify_send.py` | public hosting, Telegram, notify |
| `state.py` | `commit_all` — pushes state back to the repo |
| `config.py` | config/memory load-verify-save, `env()` with local-secrets fallback |
| `critic_model.py` / `llm.py` | critic and writer endpoints (different vendors) |
| `learner.py` | the shared bandit math |

**Legacy v2** (kept, still referenced by the existing workflows):
`run_v2.py`, `paced.py`, `writer.py`, `strategy_v2.py`, `carousel.py`,
`voiceover.py`, `render_gate.py`'s 14–34s paced window, plus the Hermes layer
(`blueprint.py`, `experiment.py`, `formats/`).

---

## External services

| Service | Endpoint | Notes |
|---|---|---|
| Writer | `https://meta-api-chat-h326.onrender.com` | model `meta-ai-thinking` |
| Critic | `https://deepseek-web-bridge.onrender.com` | `deepseek-chat`, keyless, ~40–85s/call |
| Pinterest | `https://pinterest-api-inyg.onrender.com` | keyless, Media API v2.5 |
| Audio | `https://audi0-scraper.onrender.com` | trending audio |
| Instagram | Graph API | token in secrets, expires **2026-11-19** |
| Hosts | litter.catbox.moe, uguu.se | public file hosting for delivery |

Free-tier services sleep; the loop pings them before use.

---

## State and data files

| File | Contents |
|---|---|
| `data/memory.json` | posts (real publish/delivery outcome), arm stats, topics seen, counts |
| `data/config.json` | endpoints, `reel` geometry/fps, slots, **`short_publish_enabled`** |
| `data/strategy.json` | strategy state for the v2 loop |
| `data/DIRECTIVES.md` | operator directives the engine reads |
| `data/provenance.json` | asset provenance (which pin came from where) |
| `data/REPORT.md`, `data/research/`, `data/logs/` | reporting and run logs |
| `outputs/v3_drafts.jsonl` | **every** v3 draft, accepted or rejected |
| `outputs/reel_short.mp4`, `bg.mp4`, `short_text.ass`, `track.mp3` | current artifacts |

---

## Verification gates

Nothing ships on a function's word alone.

- **`render_gate.check`** measures the file: 1080×1920, SAR 1:1, fps, duration
  inside the window (v2: 14–34s; v3: overridden to the short window), a valid
  `moov`, a full decode, and audio presence (`expect_audio=True`).
- **`lint_short.check`** must pass before any render.
- **`lint.py --selftest`** and **`lint_short.py` selftest** prove the gates stay
  calibrated.
- A truncated encode (killed mid-render) is **refused by the gate** — this has
  caught real timeouts.

---

## Secrets

Never printed, never committed. Locally in `~/.beast-secrets/`; in Actions, env
secrets. `config.env()` reads `os.environ` first and falls back to those files
(`setdefault`, so Actions always wins).

| Secret | Path / name |
|---|---|
| GitHub PAT | `~/.beast-secrets/audi0-pat` (also `BEAST_GH_PAT` in Actions) |
| Instagram token | `~/.beast-secrets/ig.env` |
| Telegram | `~/.beast-secrets/telegram.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) |

> Operational notes: a local run cannot reach Telegram without that fallback —
> that was a real bug (a run "succeeded" while delivering nothing). The PAT
> should be rotated and moved to Actions secrets. The IG app ID/secret are not
> configured, so token refresh is unverified.

---

## Running it locally / in CI

```bash
# the v3 loop, delivering to Telegram, publishing off
python3 -m src.run_short --force --no-publish

# one reel from a fixed brief (fast operator path)
python3 tests/_v3_quick.py T4 "phone calls" pin_video
python3 tests/_v3_quick.py T8 "3am replays of old conversations" pin_still

# a specific brief through the sample driver
python3 tests/_v3_sample.py T2 "being fine alone, quiet in groups" pin_video
```

Long steps (writer ~200s, a 22s v2 render ~240s+) exceed a single foreground
command window: run them with `background=true` and poll, or use the staged
scripts in `tests/`.

---

## Tests

```bash
python3 -m pytest tests/test_short.py -q            # v3 short format — 16 passed
python3 -m pytest tests/test_v2.py -q               # v2 renderer
python3 -m pytest tests/test_learner.py -q          # learner math
python3 -m pytest tests/test_render_gate.py -q      # gate
python3 -m pytest tests/test_acceptance.py -q       # end-to-end acceptance
python3 src/lint_short.py                           # lint selftest
python3 src/lint.py --selftest
```

`tests/_v3_quick.py`, `tests/_v3_sample.py`, `tests/_v2_sample.py`,
`tests/_bg_text_check.py`, `tests/_publish_off.py` are **operator scripts**, not
assertion suites: they drive one deliberate render or flip one switch.

---

## Ignition

Publishing is off by one switch plus the workflow wiring:

1. `data/config.json` → `"short_publish_enabled": true`.
2. Add a v3 workflow (or re-enable `confess.yml`'s cron) so the slots fire
   unattended.
3. Confirm the Instagram token is valid before the first live post.

Until both are done a bare dispatch does **not** publish.

---

## Pause, kill switch, token recovery

- **Pause:** create `data/PAUSE` — the loop aborts before doing work.
- **Kill a bad arm:** the learner's kill rule retires it automatically after 8+
  posts with bottom-quartile reward; to do it by hand, remove it from the arm
  list in `learn_short.py`.
- **Token expiry:** IG tokens last ~60 days. Refresh before `2026-11-19`.
- **Repo is PUBLIC by the owner's explicit choice.** No secret may ever be
  committed.

---

## Current condition, honestly

**Working and evidenced**

- v3 pipeline end-to-end: learner → writer → lint → critic → **Pinterest
  background** → render → gate → Telegram. Multiple live reels delivered, with
  audio and a measured gate PASS.
- Pinterest v2.5 integration: HLS-only pins accepted, `/download?url=` proxy
  used, pagination up, provenance respected.
- Critic rubric contradiction fixed; writer passes in 1–2 attempts where it
  previously failed 4 of 5.
- `tests/test_short.py` 16/16 green; lint selftests green.
- Every draft persists to `outputs/v3_drafts.jsonl`.

**Outstanding, stated plainly**

1. **No scheduled v3 workflow.** `run_short` is manual today. The 3/day cadence
   exists in code, not in `.github/workflows/`.
2. **Publishing has never been executed on v3.** Zero IG posts; the button is
   off by design and by the owner's instruction.
3. **Only a handful of live runs** have exercised the new critic rubric and the
   delivery logging, so their long-run behaviour is not yet proven at volume.
4. **The learning loop has no v3 signal yet** — with zero published reels there
   are no insights, so the arms are still at their priors.
5. **Instagram token refresh is unverified** (no app ID/secret configured).
6. **The PAT should be rotated** into Actions secrets.
7. The DM funnel is disabled and the bio cannot be written through the API.
8. Legacy v2 modules remain in the tree beside v3; the workflows still point at
   v2.

The rule this repository runs on: **do not claim anything is done without
evidence** — show the file, the command output, or the test.