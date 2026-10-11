# 🐺 Unleash The Beast — BEAST ENGINE v2

A fully autonomous Instagram Reels engine for **@unleashthe.b** (social anxiety,
18–30, US/UK/CA). Public repo = unlimited free GitHub Actions minutes. Jobs are the
compute; JSON files in `data/` are the database and the brain. Every run reads state,
does its job, and commits updated state back.

**⚠️ CURRENT CONDITION: BUILT, TESTED, PAUSED.** The v2 engine is complete and the
full pipeline has been proven end-to-end with real renders (samples delivered to the
owner's Telegram). **Nothing is publishing** — the schedule is commented out and a
bare dispatch defaults to `dry_run: true`. Going live needs two deliberate edits
(see *Ignition* below). The owner has NOT yet approved publishing.

```
            ┌────────────────────────────────────────────────┐
            │              RUN_V2  (the daily loop)          │
            │                                                │
   pause/PAUSE file                                            │
   safety rails  ──► learner picks arms ──► writer ──► lint ──► critic (3 tries)
            │                                                │
   background (Pinterest clip | Pexels arm)  ──►  paced render (build_video)
            │                                                │
   music | TTS voiceover arm | silence        upload gate (render_gate)
            │                                                │
            └── publish ──► record (real publish time) ──► memory ──┘
                             │
            MEASURE (3×/day)  snapshots +2h/+24h/+72h/+7d → score on the 24h one
            STRATEGIST (Sun)  rewrites DIRECTIVES.md inside a hard allowlist
            LISTENING (Sat)   refreshes the situation corpus
                        data/*.json  (the brain)
```

## What v2 changed and why (STRATEGY.md)

The old build put ~78 words on screen at once for ~9.5s — **8.2 words/s** against a
comfortable reading speed of ~3. Measured from the account's own reels; zero shares in
21 posts. The two April hits (2,494 and 1,751 reach, 68 shares) both *argued something
about the viewer*. The engine's reels described a mood instead.

The v2 changes, in impact order:

1. **Paced text reveal** (`src/paced.py`, timing from `src/lint.py`). Hook alone on
   frame 0; each line appears when the viewer has had time to read the previous one
   (`max(1.6s, 0.34s × words)`); older lines dim to 55% so the eye follows the newest
   line; 14–34s total; everything visible for the last 2s so the loop closes.
2. **Written to be sent, not nodded at.** Five series, each with a send trigger:
   caught_in_the_act (30%), why_you_do_that (25%), show_them_this (20%),
   the_quiet_cost (15%), small_steps (10%). The writer is briefed on the series, the
   hook type (12 types, 36 verified seeds in `src/hooks_seed.json`), and one real
   situation from the corpus.
3. **Two model families.** Writer: Meta (`meta-ai-thinking`). Critic: DeepSeek
   (`deepseek-chat` via the owner's bridge, keyless). The critic scores specificity /
   sendability / tone_safety / originality 1–5 (pass = all ≥ 4) and the writer gets up
   to 3 rewrites carrying the critic's actual notes — never a blind re-roll.
4. **CTA policy** (STRATEGY §5.2): none 50% / send 25% / guide 20% / book 5%. Never
   two keyword CTAs in a row. Never "link in bio".
5. **The learner owns the choices** (`src/learner.py`): Thompson sampling with
   shrinkage per attribute (series, hook_type, scene_family, layout, voice,
   length_bucket, cta_type, slot, bg_source, music_mood). Reward is retention-weighted
   with large bonuses for sends/saves. A breakout (≥3× median reach or ≥3 shares) is
   cloned for the next 5 posts — shape, never the same hook. Hard constraints always
   beat the draw: POV ≤ 20%, series ≤ 2 in a row, topic cooldown 7 days.
6. **Honest measurement** (`src/insights.py`): snapshots at +2h/+24h/+72h/+7d
   (the 24h one is the learning snapshot), feature-detected metric names, the post
   records the API's real publish time (not the preferred slot — a bug in the old
   build that invalidated hour learning). Posts with reach < 25 are logged as delivery
   failures and excluded from learning.
7. **Safety rails** (`src/strategy_v2.py`): 14 flat posts → explore 50% + force the
   Pexels arm + alert; low watch ratio → shorten the bucket; publish-error storm →
   pause 6h + alert. The weekly strategist may edit ONLY `DIRECTIVES.md` and strategy
   priors — anything else it returns is refused and written to `REPORT.md`.

## The format (what one reel is)

- 1080×1920, 30fps, h264, 14–34s, SAR 1:1
- 5–8 lines including the hook, 30–60 words total, second person, present tense
- hook ≤ 9 words naming a *behaviour*; beats ≤ 12 words each, one concrete image each
- the closer echoes a word from the hook so a replay feels intentional
- caption: search phrase in line 1, one added fact, one real question, 3–5 lowercase
  hashtags from the pool (27 tags in `data/config.json`)
- background: Pinterest video clip (60%) or still with slow zoom (40%) — no faces, no
  burned-in text, night/evening. Music instrumental at −22 LUFS, under the text.

## Verification gates (fail closed, always)

| Gate | Where | What it catches |
|---|---|---|
| `lint.lint()` | before the critic | hook length, word budget, line count, banned claims (cure/heal/permanent/guarantee/effortless/proven/"in N days"/diagnose/trauma/journey/toxic/"link in bio"/100%), advice in a recognition reel, POV cap, hook similarity to the last 60, CTA keyword rules, caption + hashtag rules |
| critic (§2) | after lint | generic beats, unsendable drafts, tone violations, template hooks — with a rewrite reason |
| `paced.qa_paced()` | after render | every line held ≥ 1.0s, ≤ 3.2 words/s, hook at frame 0, duration in 14–34s |
| `render_gate.check()` | before publish, on the finished FILE | moov atom present (a truncated encode — shipped once in the old build), 1080×1920, SAR 1:1, 14–34s, 30fps, audio stream valid or deliberately absent, size under limit, full decode |
| background check | before delivery | burned-in text / caption-card structure, pin cooldown 60 posts |

A rejected slot is **skipped and logged, never filled with a fallback**. A failed
gate publishes nothing and alerts Telegram.

## The proven pipeline (unchanged underneath)

`publish.publish_reel()` — IG container → poll → `media_publish`; `upload_host`
(litterbox/uguu); Telegram alerts; `state.commit_all`. The token route is
`graph.instagram.com` (Instagram-Login), which only accepts `video_url` — so the MP4
is parked on a public host and handed to Instagram as a URL. `src/run_create.py` and
the whole v1 path remain for audit; v2 does not use them.

## Modules (v2)

| Module | Job |
|---|---|
| `src/writer.py` | PROMPTS §1 writer + lint gate + §2 critic + rewrites |
| `src/lint.py` | the owner-supplied QA module and reveal timing (`--selftest` passes) |
| `src/paced.py` | the paced renderer + its gate |
| `src/learner.py` | arms, reward, Thompson sampling, constraints, breakout |
| `src/insights.py` | metric snapshots with feature detection, followers |
| `src/strategy_v2.py` | strategist, listening, rails, cloning |
| `src/carousel.py` | 6–8 JPEG slides, 3-step publish (Phase 3) |
| `src/platform.py` | Trial Reels + cover_url, feature-detected |
| `src/pexels.py` | licence-clean A/B arm (needs `PEXELS_API_KEY`) |
| `src/voiceover.py` | TTS arm (needs `BEAST_TTS_URL`), aligned to the reveal schedule |
| `src/run_v2.py` | the daily loop |
| `src/run_measure.py` | snapshot collection 3×/day (the old scorer is `--legacy`) |

## Running it

```bash
# dry run (never publishes) — the recommended way to watch it work
python3 -m src.run_v2 --dry-run --force

# the suites
python3 -m tests.test_acceptance     # 75/75
python3 -m tests.test_learner        # 18/18 (includes the §2.8 simulation)
python3 -m tests.test_v2             # 10/10 (model-backed tests skip when offline)
cd src && python3 lint.py --selftest

# sample a reel in two stages (a timeout can only cost one stage)
python3 -u tests/_v2_sample.py 1     # select → write → critic → background → music
python3 -u tests/_v2_sample.py 2     # render → upload gate
```

## Ignition — NOT DONE, needs owner approval

Two deliberate edits in `.github/workflows/confess.yml`:

1. uncomment the `schedule:` block (`30 0,4,8,12,16,20 * * *`),
2. flip the dispatch default `dry_run: true` → `false`.

Both were made hard on purpose so a schedule cannot re-arm itself. Until then a bare
dispatch builds the reel and uploads nothing. Slots live in `data/config.json`
(`slots_utc: 00:30 / 12:30 / 17:30 UTC`). Phase 1 is 1 reel/day; 2/day only after
retention is healthy.

## Current condition, honestly

**Working and verified:** the full cycle (writer → critic → background → render →
gate) has produced multiple gate-PASS reels with confirmed audio streams, delivered to
the owner. The learner demonstrably explores (two runs picked different series, hook
types, layouts and lengths). The linter's own selftest passes; its computed durations
(20.26/19.82/22.06s) match the spec's claim.

**Not yet proven, because it needs the account:**
- zero published posts in the new format — every green number is a test, not a viewer
- Phase 1's gate (median retention ≥ 0.6) can only be answered by posting
- insights/snapshot collection has never run against real published posts
- token refresh (needs the IG app secret); token expires **2026-11-19**
- carousels, Trial Reels, voiceover, Pexels arm are built but feature-detected and
  unexercised against the live API

**Owner actions outstanding:**
1. **rotate the GitHub PAT** (handled in plain files during development)
2. **IG app secret** for token refresh
3. optional: `PEXELS_API_KEY`, `BEAST_TTS_URL`
4. approve go-live (the two edits above)

## Secrets

**Settings → Secrets and variables → Actions**

| Secret | Required | Powers |
|---|---|---|
| `IG_ACCESS_TOKEN` | yes | publishing, insights, refresh |
| `IG_USER_ID` | yes | fallback Instagram id only (runtime `/me` wins) |
| `TELEGRAM_BOT_TOKEN` | optional | alerts (skip/fail/breakout) |
| `TELEGRAM_CHAT_ID` | optional | alerts |
| `PEXELS_API_KEY` | optional | the licence-clean A/B arm |
| `BEAST_GH_PAT` | present | token rotation, keepalive |

`GITHUB_TOKEN` is automatic. The Meta LLM endpoint, the DeepSeek critic bridge and
Pinterest need **no keys**.

## Pause / kill switch

Create `data/PAUSE` (any content) — `run_v2` stops before anything else. Delete it to
resume. The daily cap (`daily_cap: 2`) is enforced separately from the schedule, so a
re-firing cron can never double-post.

## Token death recovery

A **"TOKEN DEAD"** alert means: developers.facebook.com → your app → Instagram →
**API setup with Instagram login** → Generate token → update the `IG_ACCESS_TOKEN`
secret. The health job refreshes and rotates automatically while more than 21 days
remain. v2 requirement: refresh must be proven before go-live (needs the app secret).

## Phases (STRATEGY §6)

| Phase | Days | Ships | Gate |
|---|---|---|---|
| 0 | done | DIRECTIVES.md + config keys | — |
| 1 | 1–10 | paced reels, lint gate, snapshots, time fix | median retention ≥ 0.6 |
| 2 | 11–30 | bandit, cloning, cooldowns, 2/day | ≥1 reel with ≥3 shares or 3× reach |
| 3 | 31–60 | carousels, Trial Reels, Pexels arm, voiceover | sends/reach ≥ 1% top quartile |
| 4 | 60+ | Shorts cross-post | — |

Phase 3+ modules are built ahead of schedule; they feature-detect and stay dormant
until the account meets their conditions.

## Notes

- One post per slot, `daily_cap` per day, enforced by a `pending_publish` claim written
  before the API call — a crash between publish and commit cannot double-post.
- No `pull_request` triggers anywhere; workflows hold `contents: write, issues: write`.
- Keep-alive: weekly committed heartbeat so GitHub's 60-day schedule disable can't hit.
- The soundtrack is baked into the MP4 by design (API reels carry original audio only).
- Images carry provenance records (source, licence, sha256) in `data/provenance/`.
- Full strategy and engineering specs live in `docs/strategy/` — `STRATEGY.md`,
  `ENGINE_SPEC.md`, `PROMPTS.md`, `LANGUAGE.md`. They are the source of truth.
