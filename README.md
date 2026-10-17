# Unleash The Beast — BEAST ENGINE

Fully autonomous Instagram Reels engine for **@unleashthe.b** (social anxiety,
audience 18–30, US/UK/CA). The product is an ebook
(Gumroad: `unleashthebeast.gumroad.com/l/dufrt`).

The engine is **stateless compute + committed JSON**. GitHub Actions are the
compute; the files in `data/` are the database and the brain. Nothing runs on a
local machine. Every job reads state, does its work, and commits the updated
state back into the repository.

> **CURRENT CONDITION: BUILT, TESTED, PAUSED.**
> The v2 confession engine is complete and the full pipeline has been proven
> end-to-end with real renders. **Nothing is publishing** — the schedule in
> `confess.yml` is commented out, and a bare dispatch defaults to
> `dry_run: true`. Going live needs two deliberate edits (see
> [Ignition](#ignition)). The owner has not yet approved publishing.

---

## Table of contents

1. [How it works](#how-it-works)
2. [The three formats](#the-three-formats)
3. [Architecture and data flow](#architecture-and-data-flow)
4. [Repository layout](#repository-layout)
5. [Workflows (the runners)](#workflows-the-runners)
6. [The v2 daily loop step by step](#the-v2-daily-loop-step-by-step)
7. [Module reference](#module-reference)
8. [The learning system](#the-learning-system)
9. [External services](#external-services)
10. [State and data files](#state-and-data-files)
11. [Verification gates](#verification-gates)
12. [Secrets](#secrets)
13. [Running it locally / in CI](#running-it-locally--in-ci)
14. [Tests](#tests)
15. [Ignition](#ignition)
16. [Pause, kill switch, token recovery](#pause-kill-switch-token-recovery)
17. [Current condition, honestly](#current-condition-honestly)
18. [Phases](#phases)

---

## How it works

One publishing cycle does the following, in order:

1. **Decide whether to post.** A scheduler checks the UTC post window and the
   daily cap; a `data/PAUSE` file aborts the whole run.
2. **Wake the sleeping services.** The self-hosted Render services (Pinterest,
   audio scraper) and the Meta/DeepSeek model endpoints are on the free tier and
   sleep when idle, so each is pinged `/health` before use.
3. **Choose what to make.** The bandit learner picks one arm per attribute
   (series, hook type, scene family, layout, voice, length, CTA, slot, background
   source, music mood), with hard constraints applied *before* the draw.
4. **Write it.** The writer model (Meta) produces a hook + beats + caption inside
   a strict brief; a linter rejects rule violations; the critic model (DeepSeek,
   a different vendor) scores the draft and the writer gets up to N note-carrying
   rewrites. A missing critic is a **fail**, never a pass.
5. **Build the media.** Fetch + normalise a background (Pinterest clip / Pexels
   clip / still with push-in), pick music (or TTS voiceover, or silence), then
   composite the paced text and render a 1080×1920 h264 MP4 with ffmpeg.
6. **Gate the result.** Multiple fail-closed gates run before anything ships: the
   linter, the critic, `paced.qa_paced()`, `render_gate.check()`, and the
   background text/burn-in check.
7. **Publish.** Upload the MP4 to a public host, create an Instagram media
   container, poll it, then `media_publish`.
8. **Record + report.** Write the post's DNA and the *real* publish time into
   `data/memory.json`, alert Telegram, and commit the state.
9. **Measure + learn.** Three times a day a job pulls insights into snapshots
   (+2h / +24h / +72h / +7d); the 24h snapshot is the learning signal.

Everything is **fail-closed**: a rejected slot is skipped and logged, never
filled with a fallback. A failed gate publishes nothing and raises a Telegram
alert.

---

## The three formats

The repository contains three generations of the pipeline. Only v2 is live; the
others are kept intact for auditability and reverting.

### v1 — static card (superseded)

- Entrypoint: `src/run_create.py`, workflow `create.yml`.
- Two posts/day, a static card readable in one glance, a `POV:` label, a
  comment-keyword CTA.
- `STRATEGY.md` reverses this format, so its scheduled triggers were removed.
  The code path and workflow remain for manual dispatch so the old setup stays
  auditable.

### v2 — paced-reveal confession (current)

- Entrypoint: `src/run_v2.py`, workflow `confess.yml`.
- 5–8 short lines. The hook sits alone on frame 0; each line reveals when the
  viewer has had time to read the previous one; older lines dim to 55% alpha; the
  final line holds ~2.5s so the loop closes.
- 14–34 seconds, 1080×1920, ≤60 MB, −16 LUFS.
- The old build put ~78 words on screen at once (≈8.2 words/s against a
  comfortable ~3); zero shares in 21 posts. v2's whole point is readability and
  "written to be sent, not nodded at".

### v3 — two-beat short (new, not yet wired in)

- Modules: `src/generate_short.py`, `src/lint_short.py`, `src/build_short.py`,
  `src/templates.json` (all present in the working tree).
- 8–22 words total: a hook (≤14 words) at frame 0 and a payoff (≤12 words) at
  55–65% of the runtime. Reads like a meme, not a diary line. Win condition: a
  stranger sends it to ONE specific friend.
- Three visual arms: A = Pexels stock clip, B = Pinterest still with push-in,
  C = text post on a plain dark background.
- Three audio arms: `soft_instrumental`, `trending_spedup`, `quiet_ambient`.
- Ten structural templates (T1–T10, e.g. "People think X. It's actually Y.",
  "Not X. Just Y.", "3am brain"), derived from breakout research
  (346 hashtag reels + 300 reels from 20 accounts, 2026-10-15/16). Template
  *structures* are reused; template *wording* is never copied (a 5-content-word
  overlap check enforces this).
- **Status:** the modules exist and self-test, but no runner calls them yet.
  They are not in `run_v2.py` and no workflow dispatches them.

---

## Architecture and data flow

```
        data/PAUSE ─┐
                    ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                     RUN_V2  (the daily loop)                  │
   │                                                              │
   │  wake services ─► scheduler (window + daily cap)             │
   │        │                                                     │
   │        ▼                                                     │
   │  learner picks arms ──► writer (Meta) ──► lint ──► critic     │
   │        (constraints first)              (fail-closed) (DeepSeek,│
   │                                             up to N rewrites) │
   │        │                                                     │
   │  background (Pinterest clip | Pexels | still) ─► paced render │
   │  music | TTS voiceover | silence          (build_video)      │
   │        │                                                     │
   │        ▼                                                     │
   │  render_gate.check()  ──► upload_host ──► publish (Instagram) │
   │        │                                      │               │
   │        ▼                                      ▼               │
   │  record DNA + real publish time ──► data/memory.json          │
   └──────────────────────────────────────────────────────────────┘
                          │
      MEASURE  (3×/day)   ▼   snapshots +2h/+24h/+72h/+7d → learn on 24h
      STRATEGIST (Sun)        rewrites DIRECTIVES.md inside a hard allowlist
      LISTENING (Sat)         refreshes the situation corpus
      HEALTH (daily)          token refresh/rotation + service check
      KEEPALIVE (weekly)      heartbeat so GitHub cannot disable the crons
                          │
                          ▼
                   data/*.json  (the brain)
```

---

## Repository layout

```
beast-engine/
├── src/                     the engine (Python 3.11)
│   ├── run_v2.py            v2 daily loop entrypoint              (current)
│   ├── run_create.py        v1 daily loop entrypoint              (legacy)
│   ├── run_measure.py       snapshot collection                   (3×/day)
│   ├── run_health.py        daily health + token rotation
│   ├── run_learn.py         legacy weekly analyser
│   ├── writer.py            v2 writer + lint + critic + rewrites
│   ├── lint.py              v2 QA module + reveal timing
│   ├── paced.py             v2 renderer + its own gate
│   ├── learner.py           bandit: arms, reward, constraints, breakout
│   ├── insights.py          metric snapshots w/ feature detection
│   ├── strategy_v2.py       strategist, listening, safety rails
│   ├── score.py             v1 confession learner (win/flop/mutate)
│   ├── analyze.py           v1 weekly attribute learner
│   ├── harvest.py           legacy insight harvest + score
│   ├── build_video.py       v1 compositor / renderer
│   ├── background.py        background acquire + normalise
│   ├── music.py             music acquire / synthesis
│   ├── publish.py           Instagram create + publish
│   ├── upload_host.py       public file hosting for the MP4
│   ├── render_gate.py       final-file gate (moov/px/dur/fps/decode)
│   ├── pexels.py            licence-clean background arm
│   ├── pinterest.py         Pinterest search + download
│   ├── voiceover.py         TTS arm (aligned to the reveal schedule)
│   ├── carousel.py          Phase 3 carousel publisher
│   ├── platform.py          Trial Reels + cover_url (feature-detected)
│   ├── provenance.py        image provenance records (licence + sha256)
│   ├── seo.py               SEO/caption checklist helpers
│   ├── state.py             atomic state commit helpers
│   ├── config.py            config/env/ffmpeg resolution + load/save
│   ├── llm.py               Meta LLM client (writer + image)
│   ├── critic_model.py      DeepSeek critic client (2nd model family)
│   ├── generate_short.py    v3 writer (SHORT)                     (new)
│   ├── lint_short.py        v3 lint gate (SHORT)                  (new)
│   ├── build_short.py       v3 renderer (SHORT)                   (new)
│   ├── templates.json       v3 template bank (T1–T10)             (new)
│   ├── hooks_seed.json      v2 seed hook bank (36 verified hooks)
│   ├── sample_reels.json    v2 gold samples (used by tests)
│   ├── alerts.py            Telegram + GitHub Issue alerts
│   ├── notify_send.py       deliver a Telegram message from CI
│   ├── keepalive.py         weekly heartbeat + staleness report
│   └── reachability.py      is writer/critic/model up? (used by tests)
├── tests/                   acceptance + unit suites
├── .github/workflows/       the runners (7 workflows)
├── data/                    the database + brain (committed back)
├── docs/                    SYSTEM.md + strategy spec-of-record
├── assets/                  fonts, fallback image, audio manifest
├── outputs/                 rendered artefacts (gitignored except .gitkeep)
└── tmp/                     one-off diagnostic scripts (gitignored)
```

---

## Workflows (the runners)

| Workflow | Trigger | Entrypoint | Purpose |
|---|---|---|---|
| `confess.yml` | **schedule disabled**; manual dispatch (`dry_run=true` default) | `python -m src.run_v2` | The v2 daily loop. Gated on a `test` job that runs the full suite first. |
| `create.yml` | manual dispatch only | `python -m src.run_create` | The superseded v1 pipeline, kept auditable. Gate: `test_acceptance`. |
| `measure.yml` | `cron 0 4,10,16 * * *` | `python -m src.score` then `python -m src.run_measure` | Snapshot insights 3×/day; the confession learner runs first. |
| `learn.yml` | `cron 0 18 * * 0` (Sun 18:00 UTC) | `python -m src.run_learn` | Legacy weekly analyser (v1 attribute families). |
| `health.yml` | `cron 0 5 * * *` | `python -m src.run_health` | Token refresh/rotation + service reachability. |
| `keepalive.yml` | `cron 0 6 * * 1` (Mon 06:00 UTC) | `python -m src.keepalive` | Committed heartbeat + token/post staleness report. Publishes nothing. |
| `notify.yml` | manual dispatch (`text`, `photo`) | `python -m src.notify_send` | Send a Telegram message from CI (token stays a secret). |

Every data-writing job ends with the same commit step:

```bash
git config user.name "beast-engine[bot]"
git config user.email "beast-engine[bot]@users.noreply.github.com"
git pull --rebase --autostash || true
git add data/            # confess also adds outputs/
git commit -m "<job>: state update" || echo "nothing to commit"
git push || (git pull --rebase --autostash && git push) || echo "push failed, next run recovers"
```

Workflows hold `contents: write` (and `issues: write` for alert-capable jobs)
and use a non-cancelling `concurrency` group, so a wake can never collide with
the previous run still working.

### `confess.yml` in detail

- The `schedule:` block is **commented out** and a dispatch defaults to
  `dry_run: true`. Re-enabling needs **two** deliberate edits (see
  [Ignition](#ignition)) — it cannot re-arm itself.
- The intended wake grid (all UTC) brackets the 23:30 publish moment:
  `15 23`, `35 23`, `30 1`, `30 4`. A delayed fire still lands inside the window;
  the scheduler decides whether the post is still due.
- Two jobs: `test` (timeout 15m) runs `tests.test_acceptance`,
  `tests.test_v2`, `tests.test_learner`, and `python -m src.lint --selftest`;
  `confess` (`needs: test`, timeout 20m) verifies ffmpeg resolves and runs
  `python -m src.run_v2`.
- `force` is wired to the explicit input only — never exported
  unconditionally — so scheduled runs cannot bypass the slot window. `force`
  ignores the slot window but **never** the daily cap.

---

## The v2 daily loop step by step

`src/run_v2.py` (`PUBLISH_FMT = "paced_text"`):

1. **Pause check.** If `data/PAUSE` exists, stop immediately.
2. **Wake services.** `llm.wake()`, `critic_model.available()`, and the
   background/audio providers ping `/health` before first use.
3. **Scheduler.** Compute whether a post is due from the UTC slot grid and the
   `daily_cap`; a `pending_publish` claim is written before the API call so a
   crash between publish and commit cannot double-post.
4. **Select arms.** `select_arms()` draws one arm per attribute. Constraints are
   applied **before** the draw, so a winning arm can never break a safety rule:
   - POV cap ≤ 20% (`hook_pov_cap`),
   - series ≤ 2 in a row,
   - topic cooldown 7 days,
   - keyword-CTA alternation (never two keyword CTAs in a row),
   - hook types are restricted by series (`types_for_series`) — a "permission"
   hook can never be paired with a recognition-only series.
5. **Write.** `writer.write(series, hook_type, situation, length, layout, cta)`
   builds a strict brief, calls the Meta writer, runs `lint.lint()`, calls
   `writer.critic_pass()` (DeepSeek), and carries the critic's real notes into
   up to N rewrites — never a blind re-roll.
6. **Background.** `background.build()` fetches (Pinterest clip, or the Pexels
   arm, or a still animated with a push-in), normalises to a loop-ready
   1080×1920 MP4, and rejects burned-in text / caption-card structure. Pin
   cooldown = 60 posts.
7. **Audio.** `music` picks a track (per-mood query palette), or the `voiceover`
   TTS arm (~15% of reels) aligned to the reveal schedule, or deliberate silence.
   The `bed.mode` in config controls the ambient bed.
8. **Render.** `paced.assemble_paced()` composites the text treatment with
   per-line reveal timing from `lint.reveal_schedule()` and renders the MP4.
9. **QA.** `paced.qa_paced()` (every line held ≥ 1.0s, ≤ 3.2 words/s, hook at
   frame 0, 14–34s) then `render_gate.check()` on the finished file.
10. **Publish.** `upload_host` parks the MP4 on a public host; `publish` creates
    the IG container, polls, and `media_publish`es it.
11. **Record.** Write DNA + the API's **real** publish time (not the preferred
    slot — recording the preference was the bug that invalidated hour learning)
    into `memory`, update the `used_music_queries` memory, and commit.

A breakout (reach ≥ 3× median or ≥ 3 shares) is cloned for the next 5 posts —
shape only, never the same hook.

---

## Module reference

### Entrypoints

| Module | How invoked | Job |
|---|---|---|
| `run_v2.py` | `python -m src.run_v2 [--dry-run] [--force]` | v2 daily loop (current). |
| `run_create.py` | `python -m src.run_create [--dry-run]`; force via `BEAST_FORCE_POST=1` | v1 daily loop (legacy). Wakes services, jitter sleep, scheduler, trending join, generate → background → music → build → publish. Exposes `DUE_HOURS`. |
| `run_measure.py` | `python -m src.run_measure` | Collects insight snapshots 3×/day. |
| `run_health.py` | `python -m src.run_health` | Daily health; refreshes and rotates the IG token while >21 days remain; commits `token_state`. |
| `run_learn.py` | `python -m src.run_learn` | Legacy weekly analyser (v1 families). |
| `score.py` | `python -m src.score [--dry-run]` | v1 confession learner: harvest → win/flop/neither → mutate. |
| `harvest.py` | `python -m src.harvest` | Legacy insight harvest + legacy score. |
| `analyze.py` | `python -m src.analyze [--dry-run]` | Legacy weekly attribute weighting → `REPORT.md`. |
| `keepalive.py` | `python -m src.keepalive` | Writes `data/keepalive.json`; reports token days-left and days-since-last-post. |
| `notify_send.py` | `python -m src.notify_send` | Sends a Telegram message from CI (splits >4096 chars; stdlib only). |
| `lint.py` | `python -m src.lint --selftest` | Runs the v2 linter's own self-test. |
| `lint_short.py` | `python -m src.lint_short` | Runs the v3 short linter's self-test. |
| `publish.py` | `python -m src.publish` | Manual publish helpers. |
| `generate.py` | main-guard present | v1 content generation. |

### Content layer (write → lint → critic)

| Module | Key API | Job |
|---|---|---|
| `writer.py` | `write()`, `writer_system_prompt()`, `critic_pass()`, `hook_types()` | Builds the v2 writer brief (5 series, 12 hook types, one real situation from the corpus), runs lint, then the DeepSeek critic. Scores specificity / sendability / tone_safety / originality 1–5; **pass = all ≥ 4** plus `claims_ok` and `loop_ok`. Feeds the critic's actual notes into up to 3 rewrites. |
| `lint.py` | `lint(reel, recent)`, `reveal_schedule(lines)`, `words()`, `_selftest()` | The owner-supplied QA gate and reveal timing. Enforces hook length, word budget, line count, banned claims (`cure`/`heal`/`permanent`/`guarantee`/`effortless`/`proven`/"in N days"/`diagnose`/`trauma`/`journey`/`toxic`/"link in bio"/`100%`), no advice in a recognition reel, POV cap, hook similarity to the last 60, CTA keyword rules, caption + hashtag rules. `MIN_DUR, MAX_DUR = 14.0, 34.0`; per-line hold `max(1.6s, 0.34s × words)`. |
| `paced.py` | `assemble_paced()`, `qa_paced(lines, starts, dur, …)` | The v2 paced renderer and its gate. `qa_paced` rejects a line held < 1.0s, a line > 3.2 words/s, a late hook, or a duration outside the window. Requires a **real audio file** (`is_file()`, never `exists()`) — an empty path would otherwise resolve to the current directory and become an invalid ffmpeg input. |
| `generate_short.py` | `write_one()`, `writer_prompt()`, `critic_prompt()`, `pick_template()`, `scene_queries_for()` | **v3 writer.** Template-driven two-beat shorts. `VISUAL_ARMS = [A,B,C]`, `AUDIO_TYPES = [soft_instrumental, trending_spedup, quiet_ambient]`, `SCENE_NOUNS` maps topic → concrete visual queries. Same write → lint → critic → rewrite loop as v2 but with the short rules. |
| `lint_short.py` | `check()`, `payoff_start()`, `overlap()`, `words()`, `selftest()` | **v3 lint gate.** 8–22 words total; hook ≤ 14, payoff ≤ 12; banned claims; never "you have (social) anxiety"; POV ≤ 1-in-5; payoff must share a content word with the hook (loop). Rejects any 5-content-word overlap with a template example or recent reel. `payoff_start = 60%` of runtime. |

### Learning layer

| Module | Key API | Job |
|---|---|---|
| `learner.py` | `reward()`, `choose()`, `is_learnable()`, `series_ok()`, `pov_ok()`, `keyword_cta_ok()`, `topic_ok()`, `breakout()`, `cta_shares_sane()`, `length_bucket_of()` | The bandit. `ATTRIBUTES = [series, hook_type, scene_family, layout, voice, length_bucket, cta_type, slot, bg_source, music_mood]` (topic is deliberately **not** an arm). Thompson sampling with per-attribute shrinkage. Reward is retention-weighted. Constraints: `series_ok`, `pov_ok` (≤1-in-5), `keyword_cta_ok`, `topic_ok` (7-day cooldown). `breakout()` = ≥3× median reach **or** ≥3 shares, with a median floor. |
| `insights.py` | snapshots, `metric=` allowlist | Metric snapshots with feature detection. `SNAPSHOTS = [("2h",2),("24h",24),("72h",72),("7d",168)]`; `LEARN_SNAPSHOT = "24h"`. Drops and retries on 400. `metric=` must be ONE comma-joined string (a list silently fails every metric). `CRITICAL = ["reach", "ig_reels_avg_watch_time"]`. |
| `strategy_v2.py` | `run_strategist()`, `run_listening()`, `reset_triggers()`, `apply_rails()` | Safety rails + weekly agents. Rails: 14 flat posts → explore 50% + force the Pexels arm + alert; low watch ratio → shorten the bucket; publish-error storm (rate 0.30) → pause 6h. `RESET_POSTS=14`, `RESET_REACH=60`, `LOW_HOLD_POSTS=10`, `LOW_HOLD_RATIO=0.45`. The strategist may edit **only** `DIRECTIVES.md` and strategy priors: `ALLOWED_KEYS = {directive_text, retire_hooks, promote_topics, hypotheses, confidence}`, `MAX_DIRECTIVE_WORDS=400`; anything else is refused and written to `REPORT.md`. Listening rejects quotes/handles (privacy) and keeps situations only. |
| `score.py` | `score()`, `verdict()`, `mutate()`, `sends_per_1k()`, `hold()` | The v1 confession learner. Insights at 24h (heartbeat) and 72h (decision). WIN: sends/1k ≥ 15, or reach ≥ 3× median AND ≥ 3 sends. FLOP: reach < floor at 48h, or reach > 500 with zero sends. Mutate every 6 *scored* posts (×1.2 win, ×0.7 flop, floor 8 samples). The reach floor is `min(200, median × 0.5)` so a young account is not ratcheted to death. |
| `analyze.py` | `main()`, weights | Legacy weekly attribute weighting (archetype/topic/mood/bg_type/loop_technique) with decay `0.5**(age_days/28)` and global-mean shrinkage. Writes `REPORT.md`. |
| `harvest.py` | `main()`, legacy `compute_score()` | Legacy insight harvest and scoring (`(6·sends+3·saved+2·watch+comment+0.25·likes)/reach`), retained for audit but removed from the live path. |

### Render / acquire / publish layer

| Module | Key API | Job |
|---|---|---|
| `build_video.py` | assemble/render | The v1 compositor: text overlay + MP4 render. `TEXT_X0, TEXT_X1 = 150, 930` in `build_short`; v1 uses its own bands. Low-level frame probing works without `ffprobe`. |
| `build_short.py` | `build()`, `beat_times()` | **v3 renderer.** One beat on screen at a time, bold sans white with black stroke, max 2 lines, x = 150–930, dark scrim, hook larger than payoff, audio from t=0 with a tail fade. Arms A/B fall back to C when unavailable. Runs `render_gate.check(..., duration_bounds=(6.0, 12.0))` after every render. |
| `background.py` | `build()`, `_is_valid_video()`, loop helpers | Acquires + normalises the background. Rejects 0-byte "valid" files (size + decodable duration, not `exists()`); loop join uses `concat` (xfade fails on `trim`'d input); rejects burned-in text and static clips where motion is required. |
| `music.py` | query palette, `resolve` | Music acquisition. `USED_QUERIES_KEY = "used_music_queries"` — uniqueness comes from never repeating a **query** (query→track is 1:1 at the service), with a spectral guard as backup. Palette is ≤3-word queries (longer phrases resolve poorly), no banned terms. |
| `voiceover.py` | TTS arm | `TTS_ENV = BEAST_TTS_URL`, `WPM = 145`. ~15% of reels; aligned to the reveal schedule. |
| `pexels.py` | `fetch()`, `available()` | Licence-clean A/B background arm. `API = https://api.pexels.com/videos/search`; key from `~/.beast-secrets/pexels.env` (`PEXELS_API_KEY`). Share: Pinterest 0.85 / Pexels 0.15. |
| `pinterest.py` | `search()`, `search_videos()` | The operator's Pinterest service. `BASE = https://pinterest-api-inyg.onrender.com`; `/search/videos`, `/search`. |
| `carousel.py` | publish helpers | Phase 3 carousel publisher. `GRAPH = https://graph.instagram.com/v21.0`; slides 1080×1350 (4:5), JPEG quality 92, 6–8 slides, ~2/week. |
| `platform.py` | feature probes | Trial Reels + `cover_url`, feature-detected. `STATE_PATH = data/platform_state.json`; `RETRY_DAYS = 7`. |
| `render_gate.py` | `check()`, `describe()` | The final-file gate, run on real files (no mocks in tests). Catches a truncated encode with no moov atom (shipped once), wrong resolution/aspect (SAR), wrong duration, wrong fps, invalid/absent audio, oversize, and full decode failure. `MIN_S, MAX_S` must agree with `lint`. `config.check(good)` returns a report with `width, height, sar, fps, duration_s, size_bytes, decode_ok, moov_head`. |
| `publish.py` | `publish_reel()`, `insights()`, `account_profile_views()` | Instagram create → poll → `media_publish`. Route `graph.instagram.com/v23.0` (Instagram-Login) accepts only `video_url`, so the MP4 is hosted first. Never sends alt text to the container (it is unsupported). |
| `upload_host.py` | `upload()` | Parks the MP4 on a public host: `litterbox.catbox.moe` (72h, primary), `uguu.se` (3h, fallback). |
| `provenance.py` | record writer | Writes `{source, source_url, licence, licence_url, author, pin_id, query, retrieved_at, checksum_sha256}` to `data/provenance/images.jsonl`. `image_source` switch lives in `config.json`; `ALLOWED_SOURCES = (pinterest, pexels, unsplash, generated)`. |
| `seo.py` | checklist helpers | SEO/caption checklist + alt-text description helpers. |
| `alerts.py` | `fail()`, `success()`, `telegram()`, `telegram_video()`, `issue()` | Alerts. Telegram (optional) with a GitHub Issue fallback. Never fatal. `success()` surfaces app-only manual steps on the post that needs them. |
| `reachability.py` | `writer_up()`, `critic_up()`, `models_up()` | Health probes used by the model-backed tests. |

### Infra / support

| Module | Key API | Job |
|---|---|---|
| `config.py` | `load_config()`, `load_strategy()`, `load_memory()`, `save_*()`, `env()`, `resolve_ffmpeg()`, `OUTPUTS`, `ASSETS`, `ROOT` | Load/save the JSON brain; env access; central ffmpeg resolution (`BEAST_FFMPEG` → PATH → `imageio-ffmpeg`). |
| `state.py` | commit helpers | Atomic state commit helpers for the brain files. |
| `llm.py` | `chat()`, `image()`, `wake()`, `json_first()`, `_json_largest()` | Meta LLM client. The upstream **ignores the `system` role**, so system text is folded into the user turn. Brace-scanning JSON extraction (largest object, not first). `image()` downloads immediately (CDN URLs expire) with refusal-rephrase and quota-retry. |
| `critic_model.py` | `chat()`, `available()`, `model_ids()`, `family()` | The **second model family** (DeepSeek, keyless bridge). Raises on failure so the caller fails closed. `family()` proves the critic was not the writer's family. |

---

## The learning system

- **Snapshot cadence:** +2h / +24h / +72h / +7d. The **24h** snapshot is the
  learning signal; others are heartbeat/history.
- **Reward (v2 `learner.reward`)** from `reward_weights`:
  `1.0·watch + 20·(shares/reach) + 8·(saves/reach) + 3·(comments/reach)
  + 1·(likes/reach) − 0.5·max(0, skip − 0.5)`, with `watch` capped at 2.0 and a
  reach floor of 25. Missing `reels_skip_rate` is treated as **neutral**, not as
  a good skip rate.
- **Selection:** Thompson sampling per attribute with shrinkage; one arm drawn
  per attribute, constraints applied first.
- **Delivery failures:** posts with reach < 25 are logged as delivery failures
  and excluded from learning — they are a plumbing signal, not a content signal.
- **Breakout cloning:** a breakout is cloned for the next 5 posts (shape, not
  hook).
- **Safety rails:** see `strategy_v2`. A winning arm can never break a rule.
- **Weekly agents:** the strategist (Sun) may only touch `DIRECTIVES.md` and
  priors; listening (Sat) refreshes the situation corpus, rejecting quotes and
  handles.

---

## External services

| Service | URL | Auth | Used for |
|---|---|---|---|
| Meta LLM | `https://meta-api-chat-h326.onrender.com` | none | Writer (`meta-ai-thinking`) + image generation fallback. |
| DeepSeek bridge | `https://deepseek-web-bridge.onrender.com` | none | Critic (`deepseek-chat` / `deepseek-reasoner`). |
| Pinterest API | `https://pinterest-api-inyg.onrender.com` | none | Background footage/stills. |
| Audio scraper | `https://audi0-scraper.onrender.com` | none | Music (`/v1/song`, `/v1/download`, `/v1/file/...`). |
| Instagram Graph | `https://graph.instagram.com/v23.0` (publish), `v21.0` (carousel) | `IG_ACCESS_TOKEN` | Container create + publish + insights + refresh. |
| Pexels | `https://api.pexels.com/videos/search` | `PEXELS_API_KEY` | Licence-clean background arm. |
| File hosts | `litterbox.catbox.moe` (72h), `uguu.se` (3h) | none | Public URL for the MP4. |
| Telegram | `https://api.telegram.org/bot{token}/...` | `TELEGRAM_BOT_TOKEN` | Alerts + reports. |
| Secondary music | `archive.org`, `pixabay.com/music` | none | Music fallbacks only if the audio API yields nothing. |

The self-hosted Render services are on the free tier and sleep when idle, so each
is woken with a `/health` ping before first use. The audio scraper's second repo
(`audi0-scraper`) lived at `/tmp/audi0-repo` during development and was pushed
and deployed; the `/tmp` location is volatile.

---

## State and data files

| File | Role |
|---|---|
| `data/config.json` | The configuration: brand voice, 11 archetypes, 20 topics, 5 moods, background query map, music palette/hints, `slots_utc`, reel/text limits, SEO presets, `confession` timing, `consideration` thresholds, `cta_shares` + `cta_texts`, `hook_pov_cap`, `pin_cooldown`, `bg_source_share`, `bg_share`, `daily_cap`, `reward_weights`, `image_source`. |
| `data/strategy.json` | v1 weights/`n`/`adj` per attribute family, `hour_scores`, `next_post_hour`, `experiments`. |
| `data/memory.json` | `posts[]` (media_id, container_id, real `created_at`, slot, hook, caption, hashtags, `dna`, `metrics`, `verdict`, `derived`) + `account_profile_views`. |
| `data/DIRECTIVES.md` | The weekly strategist's only text surface (≤400 words). |
| `data/REPORT.md` | Generated weekly brain report (weighted scores, top hooks, self-improvement metrics). |
| `data/token_state.json` | `{"expires_at": ..., "last_refresh": ...}` for the IG token. |
| `data/keepalive.json` | Heartbeat written by `keepalive.yml`. |
| `data/trending_styles.json` | Audio style strings refreshed from the scraper. |
| `data/logs/<date>-<job>.json` | Per-day, per-workflow run logs. |
| `data/provenance/images.jsonl` | One provenance record per acquired image. |
| `data/research/` | Reddit corpora (`.xml`, `corpus.json`) used by the writer/listening. |
| `data/PAUSE` | **Kill switch** — if present, `run_v2` stops before anything else. |
| `src/hooks_seed.json` | v2 seed hook bank (36 hooks across 5 series, all ≤9 words). |
| `src/sample_reels.json` | v2 gold samples used by tests. |
| `src/templates.json` | v3 template bank (`v3-2026-10-15`, T1–T10, 12 topics). |

---

## Verification gates

Every gate fails closed. A rejected slot is **skipped and logged, never filled
with a fallback**. A failed gate publishes nothing and alerts Telegram.

| Gate | Where | Catches |
|---|---|---|
| `lint.lint()` | before the critic | hook length, word budget, line count, banned claims, advice in a recognition reel, POV cap, hook similarity to the last 60, CTA keyword rules, caption/hashtag rules. |
| critic (§2) | after lint | generic beats, unsendable drafts, tone violations, template hooks — with a concrete rewrite reason. Missing critic = FAIL. |
| `lint_short.check()` | v3, before + after the critic | 8–22 words, hook ≤ 14 / payoff ≤ 12, banned claims, diagnosis phrasing, POV cap, loop closure, template/recent wording copy. |
| `paced.qa_paced()` | after render | line held ≥ 1.0s, ≤ 3.2 words/s, hook at frame 0, duration 14–34s. |
| `render_gate.check()` | v2/v3, on the finished FILE | moov atom present, 1080×1920, SAR 1:1, duration window, 30 fps, audio valid or deliberately absent, size under limit, full decode. |
| background check | before delivery | burned-in text / caption-card structure; pin cooldown 60 posts; static-clip rejection where motion is required. |
| `tests/test_acceptance.py` | in `create.yml` and `confess.yml` before publish | contract + invariant regressions anywhere in `src/`. |

---

## Secrets

**Settings → Secrets and variables → Actions**

| Secret | Required | Powers |
|---|---|---|
| `IG_ACCESS_TOKEN` | yes | publishing, insights, refresh. |
| `IG_USER_ID` | yes | fallback Instagram id only (runtime `/me` wins). |
| `TELEGRAM_BOT_TOKEN` | optional | alerts (skip/fail/breakout/success). |
| `TELEGRAM_CHAT_ID` | optional | alerts. |
| `PEXELS_API_KEY` | optional | the licence-clean A/B arm. |
| `BEAST_TTS_URL` | optional | the voiceover arm. |
| `BEAST_GH_PAT` | present | token rotation + keepalive. The automatic `GITHUB_TOKEN` **cannot** write Actions secrets, so token rotation silently failed under it. |
| `GITHUB_TOKEN` | automatic | alert issues; fallback for `BEAST_GH_PAT`. |

Meta LLM, the DeepSeek critic bridge, Pinterest and the audio scraper need **no
keys**.

---

## Running it locally / in CI

```bash
# the recommended way to watch it work — never publishes
python3 -m src.run_v2 --dry-run --force

# the suites
python3 -m tests.test_acceptance     # the contract suite
python3 -m tests.test_learner        # learner math + the §2.8 simulation
python3 -m tests.test_v2             # v2 pipeline; model-treated tests skip offline
cd src && python3 lint.py --selftest
python3 -m src.lint_short            # v3 short linter self-test

# sample a v2 reel in two stages (a timeout can only cost one stage)
python3 -u tests/_v2_sample.py 1     # select → write → critic → background → music
python3 -u tests/_v2_sample.py 2     # render → upload gate

# dry-run semantics: real build against real services, publishes nothing,
# mutates no state, sends no Telegram, and ignores the daily cap (it posts nothing)
```

`ffmpeg` is a **hard runtime dependency** — `music.py`, `background.py`,
`build_video.py`, `build_short.py` and `pinterest.py` shell out to it.
`config.resolve_ffmpeg()` resolves it via `BEAST_FFMPEG` → PATH → the
`imageio-ffmpeg` bundled static build, so CI and local behave identically with no
`apt`/`sudo`.

Dependencies (`requirements.txt`): `requests`, `Pillow`, `numpy`, `librosa`,
`soundfile`, `pynacl`, `PyYAML`, `imageio-ffmpeg`.

---

## Tests

| Suite | What it proves |
|---|---|
| `tests/test_acceptance.py` | The full contract: validators, scheduler (hours match the crons, two posts + retries, 4h wakes), ffmpeg resolution + declaration, music palette size/uniqueness, daily cap, dry-run semantics, token rotation credential, publish gating, third-party import declaration, spec invariants (text bands, timing windows, no colour filters by default, no alt text to the container), and more. |
| `tests/test_learner.py` | Reward formula, watch cap, missing-skip neutral, delivery floor, the §2.8 planted-winner simulation (>60% draws), constraints never broken, POV/CTA/topic rules, breakout definition, rails, strategist write-surface restriction, listening privacy, series↔hook-type pairing, empty-audio-path guard. |
| `tests/test_v2.py` | v2 pipeline: lint selftest, reveal schedule, words/s, gold samples through the linter, gate/window agreement, unreadable/late-hook rejection, writer prompt completeness, and the model-backed critic/writer tests (skipped offline). |
| `tests/test_render_gate.py` | The gate against **real** ffmpeg output (no mocks): good clip passes; truncated encode fails on moov; SAR/aspect, duration, resolution, missing-file, and audio-contract cases. |
| `tests/_*.py` | Helper/sample scripts (`_v2_sample.py`, `_v2_render_audio.py`, `_bg_text_check.py`). |

A build is not "verified" until it has been measured under CI conditions — three
of the five historical ffmpeg bugs were invisible locally.

---

## Ignition

Publishing is deliberately hard to arm. Two edits in
`.github/workflows/confess.yml` are required, **both**:

1. uncomment the `schedule:` block, and
2. flip the dispatch default `dry_run: true` → `false`.

Until then a bare dispatch builds the reel and uploads nothing. Slots live in
`data/config.json` (`slots_utc`). Phase 1 is 1 reel/day; 2/day only after
retention is healthy.

---

## Pause, kill switch, token recovery

- **Pause:** create `data/PAUSE` (any content) — `run_v2` stops before anything
  else. Delete it to resume. The daily cap (`daily_cap: 2`) is enforced
  separately from the schedule, so a re-firing cron can never double-post.
- **Token death:** a **"TOKEN DEAD"** alert means: developers.facebook.com → your
  app → Instagram → *API setup with Instagram login* → Generate token → update
  the `IG_ACCESS_TOKEN` secret. The health job refreshes and rotates
  automatically while more than 21 days remain. v2 requires this to be proven
  before go-live (needs the IG app secret).
- **Keepalive:** a weekly committed heartbeat stops GitHub's 60-day scheduled-
  workflow disable, and reports the two numbers that go stale unnoticed (token
  days-left, days-since-last-post).

---

## Current condition, honestly

**Working and verified:** the full v2 cycle (writer → critic → background →
render → gate) has produced multiple gate-PASS reels with confirmed audio
streams. The learner demonstrably explores. `lint --selftest` passes and its
computed durations match the spec. The v3 short modules self-test.

**Not yet proven, because it needs the account:**

- zero published posts in the new format — every green number is a test, not a
  viewer.
- Phase 1's gate (median retention ≥ 0.6) can only be answered by posting.
- insights/snapshot collection has never run against real published posts.
- token refresh (needs the IG app secret); the token expires **2026-11-19**.
- carousels, Trial Reels, voiceover, and the Pexels arm are built but
  feature-detected and unexercised against the live API.
- the v3 short modules are not wired into any runner yet.

**Repository state:** local `main` is ahead of `origin/main`, which still holds
the older automated `post:`/`measure:`/`health:` commits from the v1 pipeline.
The v2 rewrite has not been pushed.

**Owner actions outstanding:** rotate the GitHub PAT; supply the IG app secret;
optionally `PEXELS_API_KEY` and `BEAST_TTS_URL`; approve go-live.

---

## Phases (STRATEGY §6)

| Phase | Days | Ships | Gate |
|---|---|---|---|
| 0 | done | `DIRECTIVES.md` + config keys | — |
| 1 | 1–10 | paced reels, lint gate, snapshots, time fix | median retention ≥ 0.6 |
| 2 | 11–30 | bandit, cloning, cooldowns, 2/day | ≥1 reel with ≥3 shares or 3× reach |
| 3 | 31–60 | carousels, Trial Reels, Pexels arm, voiceover | sends/reach ≥ 1% top quartile |
| 4 | 60+ | Shorts cross-post | — |

Phase 3+ modules are built ahead of schedule; they feature-detect and stay
dormant until the account meets their conditions.

---

## Notes

- One post per slot, `daily_cap` per day, enforced by a `pending_publish` claim
  written before the API call — a crash between publish and commit cannot
  double-post.
- No `pull_request` triggers anywhere; workflows hold `contents: write` (and
  `issues: write` where they alert).
- The soundtrack is baked into the MP4 by design (the API's reels carry original
  audio only).
- Images carry provenance records (source, licence, sha256) in
  `data/provenance/`.
- Full strategy and engineering specs live in `docs/strategy/` — `STRATEGY.md`,
  `ENGINE_SPEC.md`, `PROMPTS.md`, `LANGUAGE.md` — and `docs/SYSTEM.md`. They are
  the source of truth.
