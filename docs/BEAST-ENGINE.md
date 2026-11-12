# BEAST ENGINE — what it is, and how it works

**Beast Engine is a fully automatic Instagram Reels factory.** Twice a day it writes an
original piece of copy about a specific social moment, chooses a background video and a
trending sound that fit it *emotionally* rather than literally, composites the words
over the footage, renders a 1080×1920 MP4, checks it, delivers it for review, and
publishes it to **@unleashthe.b**. It keeps a written record of every decision it makes
and refuses to publish anything it cannot verify.

- Repo: `github.com/websitecage-hub/beast-engine` (branch `main`)
- The only workflow: `.github/workflows/preview.yml`
- Renderer: `python -m src.run_preview` · Publisher: `python -m src.run_publish`
- Two locked formats: **A** `caption_list_title_v1` (~12.2 s numbered list),
  **B** `social_moment_loop_v1` (~7 s loop)
- Cadence: `0 9 * * *` and `0 17 * * *` (UTC) — two reels a day, one per format

---

# 1. The shape of the system

```
schedule / manual dispatch
        │
        ▼
┌───────────────────────────────────────────────────────────────────────┐
│ src/run_preview.py — one reel, twelve stages, every verdict recorded  │
└───────────────────────────────────────────────────────────────────────┘
   1  format selection        which of A/B (auto = fewer previews)
   2  CONTENT                 card → writer → gates → critic → rewrite
   3  ASSET INTENT            copy → lane queries + audio intent
   4  VISUAL DIRECTOR         copy → ONE 2-3 word visual metaphor
   5  AUDIO DIRECTOR          copy → ONE sonic word
   6  PINTEREST               search → gates (OCR, motion, …) → chosen clip
   7  AUDIO                    trending track for the director's word
   8  TEXT STYLE               yellow on dark / white on bright, from raw luma
   9  RENDER                   HyperFrames template, one bounded pass
  10  QA                       structural (gates) + measured (recorded)
  11  MANIFEST + LEDGER        the audit trail and the no-repeat memory
  12  DELIVERY                 Telegram review channel
        │
        ▼
   src/run_publish.py        newest reel at status "rendered" → Instagram
```

Everything else in `src/` exists to serve those steps. The tree is deliberately small:
29 modules plus 16 format modules, and nothing retired hanging off them.

---

# 2. Repo map (every file that remains, and why)

## Runner and interfaces (`src/`)

| module | role |
|---|---|
| `run_preview.py` | the pipeline: stages, verdicts, manifest, delivery |
| `run_publish.py` | publishes the newest *rendered* reel, records the media id |
| `config.py` | config/env resolution, paths, `resolve_ffmpeg()`, pause flag, memory |
| `llm.py` | the writer's model (Meta API) |
| `critic_model.py` | the critic's model (DeepSeek) — a deliberately different vendor |
| `pinterest.py` | summary: search + download (v3.1 `/api/search` first, old routes as fallback, raw-URL download, HLS fetched as segments) |
| `music.py` | trending-audio API (search + track features) |
| `audio_features.py` | measures energy/tempo/brightness of the chosen track |
| `publish.py` | Instagram Graph API: container → publish → media id, alt text, caption |
| `upload_host.py` | hosts the rendered MP4 publicly so Instagram can fetch it |
| `alerts.py` | Telegram delivery of the review copy |
| `render_gate.py` | structural gate: duration, streams, audio presence, file sanity |
| `lint.py` | text lints shared by the writer path |

## Format layer (`src/formats/`)

| module | role |
|---|---|
| `content_factory.py` | the creative brain: card, writer A/B, deterministic gates, critic, rewrite loop |
| `template_payloads.py` | per-format payload schemas + validation |
| `template_renderer.py` | the two HyperFrames renderers: profiles, injection, render, QA |
| `renderer_base.py` | the four-method renderer interface + shared structural gate |
| `text_style.py` | text colour from the footage's raw luma |
| `asset_intent.py` | copy → search brief (lanes, audio intent, core moment) |
| `visual_director.py` | copy → one 2-3 word visual metaphor (+ the real Pinterest docs) |
| `audio_director.py` | copy → one sonic word (+ the real trending-audio docs) |
| `asset_broker.py` | search order, hard gates, download, raw selection |
| `asset_probe.py` | measurement: luma/sat, motion, busyness, **OCR text check** |
| `asset_scoring.py` | taste score and pair-fit audit (both advisory) |
| `asset_ledger.py` | used-asset memory and the no-repeat window |
| `run_manifest.py` | the run manifest: load/save/record, corruption self-heal |
| `manifest_merge.py` | semantic union-merge for the manifest (git driver) |

## Support

| path | role |
|---|---|
| `templates/{caption_list_title_v1,social_moment_loop_v1}/` | the two HyperFrames templates (source-immutable; the engine injects content and media) |
| `scripts/merge_manifest.py` | git merge driver for the manifest |
| `scripts/repair_data_json.py` | pre-commit repair of conflicted state files |
| `tests/` | 80 tests: pipeline, assets, render gate, music, audio contract, production-line contracts |
| `docs/BEAST-ENGINE.md` | this document — the single authoritative description |
| `docs/API-REFERENCE.md` | every external service: base URLs, endpoints, parameters, measured quirks, failure signatures and where each credential lives |
| `docs/strategy/` | the source specs the formats and copy rules were built from (STRATEGY, ENGINE_SPEC, PROMPTS, LANGUAGE) |
| `docs/research/` | the frame/OCR research audit behind the format clones |
| `docs/hermes/` | historical experiment notes |
| `data/config.json` | engine config (`short_publish_enabled`, caps, `bg_grade`) |
| `data/template_manifest.json` | every run: payload, assets, verdicts, publish result |
| `data/asset_ledger.json` | reserved/used assets + no-repeat window |
| `data/memory.json` | counters (which format ran last, daily counts) |
| `outputs/` | local artifacts from manual runs |

---

# 3. The workflow, step by step

`.github/workflows/preview.yml` — the only workflow in the repo.

1. **Triggers**: `schedule` at 09:00 and 17:00 UTC, or `workflow_dispatch` with optional
   inputs (`format`, `topic`, `quality`, `deliver`, `publish`).
2. **Defaults for autonomous runs**: a scheduled wake has no inputs, so the job sets
   `RUN_FORMAT=auto`, `RUN_QUALITY=delivery`, `RUN_DELIVER=true`, `RUN_PUBLISH=true`
   (a manual dispatch keeps whatever it was given — a hand-run test does not post by
   accident).
3. **Setup**: checkout → Python 3.12 → install ffmpeg (tolerant: an apt blip must not
   kill a run, the bundled binary in `requirements.txt` is the fallback) → pip install →
   `compileall` (fail fast on syntax) → verify ffmpeg resolves and runs.
4. **Preview**: `python -m src.run_preview --format $RUN_FORMAT --quality $RUN_QUALITY`
   with the runner's temp dir as scratch, the repo's ledger/manifest as durable state,
   and Telegram secrets for delivery.
5. **Publish** — only when `RUN_PUBLISH` is true **and** the previous step succeeded:
   `python -m src.run_publish --format $RUN_FORMAT`. The `success()` guard exists
   because a failed render must not fall through and post an older reel.
6. **Artifact**: the manifest and any rendered previews are uploaded (14-day retention).
7. **Commit state**: `data/` is committed back to `main` with the manifest merge driver
   installed, and any conflicted JSON repaired or restored before `git add` — committed
   conflict markers once made the manifest unreadable and silently broke every later run.

---

# 4. Stage 1 — CONTENT (the creative brain)

Six passes in `src/formats/content_factory.py`. Nothing renders until the copy passes.

## 4.1 The audience-moment card (passes 1–2)

**Prompt** (verbatim shape):

```
You are the Creative Director for @unleashthe.b — people 18-30 who want connection but
get preoccupied with how they are being perceived. They rehearse, hesitate,
self-monitor, avoid, over-apologise, replay afterwards. Never broken, pathetic,
antisocial or incapable. The situation and the overactive inner commentary may be
funny; the person keeps their dignity.

Work in two steps and return BOTH, as JSON only.

STEP 1 - audience moment card. Choose ONE concrete social moment (not a topic):
{"situation": "where and when, one specific context",
 "observable_behaviour": "what a camera could actually see them doing",
 "private_worry": "what they fear it signals to someone else",
 "emotional_tension": "one sentence naming the contradiction",
 "audience_cluster": "after-conversation replay | group conversation entry | texting and
   group chats | asking and needing help | parties and social events | making and
   maintaining friends | everyday performance moments | boundaries and people-pleasing |
   quiet progress"}

STEP 2 - a slate of exactly 3 concepts, each a DIFFERENT situation, each usable in ONE
of the two formats:
[{"concept_id": "kebab-case", "situation": "...", "behaviour": "...",
  "private_meaning": "...", "turn": "...",
  "format": "caption_list_title_v1|social_moment_loop_v1"}]

Then pick the strongest concept and say why in one line.
RULES
- Concrete beats broad: bus, group chat, doorway, phone screen, kitchen, lift, counter,
  call button. No "social anxiety is hard", no "healing takes time".
- Never diagnose, never claim a list proves a condition.
- Do not reuse anything in the RECENTLY USED list.
RECENTLY USED: {last 8 topics/headlines}
```

A *moment*, not a topic label: where and when, what a camera sees, the private worry,
the contradiction. The recent list stops the account repeating itself.

## 4.2 The writer (pass 3)

One prompt per format, sharing a rule set:

- **A**: a title carrying a finite promise and exactly that many items, each an
  observable moment. The number is a contract with the caption.
- **B**: one thought, ≤45 words, whose last line completes the first so a replay lands
  as a deliberate turn.

Shared rules: concrete beats broad (time, place, object, message, pause); ordinary
spoken language; no therapist vocabulary unless the line makes it concrete; the viewer
is never the punchline and never diagnosed; no emoji.

**Who writes**: Meta first (recorded as `meta-api`); if that call fails, the DeepSeek
bridge (`deepseek-bridge`) and the run is marked `independent_critic: false`.
`BEAST_WRITER=meta` forbids the fallback so a Meta outage fails loudly.

**Retries**: 3 attempts per card, 2 cards, 420 s content budget. When *distinctiveness*
is the only failing dimension the retry prompt names the exact openings already used
and forbids them; when a card is refused three times a different card is taken.

## 4.3 Deterministic gates (before any critic call)

- **Specificity ladder**: level 0 = no concrete anchor (reject); level 2 = concrete scene
  anchor; level 3 = plus a private meaning or a turn. Regex-decided, reasons recorded.
- A: number equals item count, items distinct, no clinical vocabulary, no near-duplicate
  of a recent headline. B: one thought, ≤45 words.
- Rights are never written by the model; the broker attaches them (unknown ⇒ preview-only).

## 4.4 The critic (pass 4)

Runs on DeepSeek — a different vendor, so it is a real second opinion. Rubric:

```
{"scene_specificity":0-5,"recognition_authenticity":0-5,"natural_voice":0-5,
 "emotional_truth_dignity":0-5,"format_fit":0-5,"distinctiveness":0-5,
 "shareability":0-5,"caption_value":0-5,"product_trust":0-5,
 "hard_fail":null,
 "evidence":"<=50 words quoting the actual wording you judged",
 "sendability_who":"the exact person who would send this and to whom",
 "rewrite_notes":"<=40 words, concrete, only about what failed"}
```

- **Critical, all ≥4**: scene_specificity, recognition_authenticity, natural_voice,
  emotional_truth_dignity, format_fit, distinctiveness.
- **Supporting, all ≥3**: shareability, caption_value, product_trust.
- **Hard fails**: clinical/diagnostic claim, fabricated book content, copied wording,
  humiliating portrayal, more than one unrelated scene, manipulative sales pressure.
- The hard-fail list is **format-aware**: the loop format is told it has no numbered
  list, so A's count rule can never be applied to it.
- The critic judges *wording only*; the format and template are never a penalty.
- **A missing score is not a zero**: an unscored supporting dimension means "not judged";
  an unscored critical dimension fails closed as `not scored`.

## 4.5 The rewrite (pass 5) and the payload (pass 6)

The failing dimensions, the critic's `rewrite_notes` and `sendability_who` go back to
the writer with "fix ONLY this". A malformed critic reply gets one protocol retry then
fails closed rather than spending a blind rewrite. The result is schema-validated and
recorded.

---

# 5. Stage 2 — ASSET INTENT

An LLM call turns the finished copy into something searchable: lane queries
(**literal** the scene as written, **adjacent** the same feeling elsewhere,
**emotional** the mood), an audio intent (arousal, energy, avoid-list, emotional role)
and the core human moment. Numbers are normalised (a 0–100 arousal is coerced to 0–1
rather than thrown away — measured: it once killed a whole brief and fell back to two
lanes). A failure degrades to copy-derived queries and is recorded as degraded.

---

# 6. Stages 3–4 — THE TWO DIRECTORS

This is the layer that stops the background being a literal illustration. Both read the
copy the pipeline just wrote.

## Visual director — one metaphor (`src/formats/visual_director.py`)

```
You are an elite Visual Director for viral short-form content. You understand that
literal stock footage looks cheap, but overly poetic descriptions break search APIs.

Here is the generated script content:
"{text}"

Here is the exact API documentation for the Pinterest search tool you are using:
{docs}

YOUR TASK:
Suppose you need a background video from Pinterest based on this script.
1. Analyze the underlying emotional vibe and semantic meaning (what the visual should
   FEEL like).
2. Generate the exact search terms to find this on Pinterest.
3. CRITICAL RULE: Make sure the search terms are SEMANTICALLY RELATED and BROAD enough
   to get highly creative, high-quality, aesthetic results. Do NOT use long sentences. Do
   NOT use literal emotions like "sad" or "anxiety". Use 2-3 word cinematic aesthetic
   terms.

Output ONLY valid JSON:
{"semantic_analysis": "1 sentence on the visual vibe.",
 "pinterest_search_query": "2-3 broad, aesthetic words"}
```

`{docs}` is the **real** Pinterest API guide read from `~/pinterest-api/USAGE.md`
(trimmed to the search surface, with a built-in fallback). Output is validated: 1–3
words, and a banned regex rejects literal/clinical terms (`sad`, `anxiety`, `person`…).
Example lists were removed on purpose — they anchored the model.

## Audio director — one word (`src/formats/audio_director.py`)

```
You are an elite Sound Designer for viral short-form content. You understand that
literal music ruins the edit, but random sounds destroy the pacing.

Here is the generated script content:
"{text}"

Here is the exact API documentation for the TikTok Audio search tool you are using:
{docs}

YOUR TASK:
Suppose you need a trending audio track from TikTok based on this script.
1. Analyze the sonic pacing, tension, and underlying emotional vibe of the text.
2. Generate the exact search term to find this on the TikTok API.
3. CRITICAL RULE: Make sure the search term is SEMANTICALLY RELATED and BROAD enough to
   get highly creative, high-energy, or highly emotional trending sounds. It MUST be
   exactly ONE WORD. Do NOT use literal emotions like "sad" or "anxiety". Use broad
   sonic genres or moods.

Output ONLY valid JSON:
{"sonic_analysis": "1 sentence on the audio vibe.",
 "tiktok_search_query": "1 broad word only"}
```

`{docs}` is the real trending-audio guide from `~/trending-audio-scraper/USAGE.md`.
Validated to exactly one word, same banned list. Either director failing is recorded as
degraded and the run continues on the intent lanes alone.

Real outputs recorded in the manifest: `morning elevator aesthetic`,
`night bedroom aesthetic`, `supermarket liminal aesthetic`, `morning bus window`;
audio `lofi`, `cinematic`, `indie`, `quirky`, `glitch`, `suspense`.

---

# 7. Stage 6 — BACKGROUND ACQUISITION (`asset_broker.py`)

## 7.1 Search order

```
1. the director's metaphor query           ← always first
2. the metaphor's own 2-word pairs         "liminal hallway haze" → "liminal haze", …
3. adjacent lane queries
4. emotional lane queries
5. literal lane queries                    ← LAST, deliberately
```

Measured: a metaphor returned exactly one clip, that clip failed a gate, and the run fell
straight to the literal lane — the exact behaviour the director's briefing forbids. A
like-for-like image of the words is now the *least* desirable background.

## 7.2 The service and the download

- `wake()` first (a free-tier service sleeps; accepts `/api/status` or `/health`).
- Search: `/api/search?q=…&scope=videos` on the v3.1 service, `/search/videos` as
  fallback, so either deployment works.
- Row normalisation: the service's `is_video` flag is unreliable (measured `false` on
  pins that carry a `video_url`), so a row with a video URL is typed as video.
- **Download uses the exact URL the search returned.** Measured: 3 of 3 clips in
  0.1–1.8 s; the old `/download` proxy is only a fallback.
- HLS is fetched segment-by-segment in Python and only muxed by ffmpeg from local bytes:
  ffmpeg's own HLS reader segfaults on this host (`rc -11`, empty stderr) and silently
  failed nearly every clip before this.
- Downloads are bounded (per-read timeout, retries, wall-clock deadline) because a
  trickle of bytes keeps a stream alive forever — one pin once held a run nine minutes.

## 7.3 Hard gates — the only things that may reject a clip

| gate | rule | why it exists |
|---|---|---|
| video, not image | pin type must be video/gif | images are a different format |
| real motion | mean frame difference ≥ 0.0015 | a published reel once measured 0.0003 — a still frame dressed as video |
| long enough | ≥ format duration (12.2 s / 7.0 s) | shorter footage freezes or loops |
| vertical | usable crop ≥ 720 wide | the formats are 9:16 |
| not an ad | commerce/affiliate domains, CTAs, stock/repost | these are not backgrounds |
| fresh | not used inside the no-repeat window | the same clip must not return too soon |
| **no on-screen text** | RapidOCR must positively find nothing | the owner's single hardest rule |

The OCR gate samples frames **head-weighted** (0.2, 0.8, 1.5, 2.5 s, mid, late) because
overlay text often exists only in the opening second — a clip that published with
"HEALTHY / banana oatmeal cookies" burned in had its text at 1.0 s and nothing later.
`has_text` is recorded; **"could not run" is a rejection, not a pass** (the old code
answered "clean" from a pixel heuristic when the OCR engine was missing, and two
text-carrying clips went out).

## 7.4 Selection is raw

No luma rule, no darkness preference, no colour preference, no taste score chooses the
background. A candidate qualifies by surviving the gates and **the first qualifier in
search order wins**. The taste score is still computed and stored for audit; it does not
select.

---

# 8. Stages 7–9 — AUDIO, TEXT, RENDER

**Audio**: the trending API searched with the director's single word; the chosen track
is recorded with stable id, title, artist, usage count and measured features
(energy/tempo/brightness) plus a distance from the requested intent. Rights are always
`unknown` ⇒ preview-only.

**Text style**: the colour follows the footage's **raw** luma — warm yellow `#E0D29B`
below ~92, warm off-white `#F7F0D2` in the middle, pure white above ~122, each with a
2–3 px dark outline. (Evidence: the outlier reels measured at 5.7 M / 3.4 M views use
exactly these.)

**Render**: HyperFrames renders background + music + copy from the template.

| | A `caption_list_title_v1` | B `social_moment_loop_v1` |
|---|---|---|
| duration | 12.2 s (11.6–12.8) | 7.0 s (6.4–7.8) |
| canvas | 1080×1920 @30 | 1080×1920 @30 |
| title | 4-line white Inter 700, 81 px/1.00, 4 px `#09090b` outline, block 42.0–57.7 % | static lowercase Inter 700 `#feffac`, 86 px/1.28, 3 px outline + soft shadow, block 50.8–84.2 % |
| luma target | 105 ± 14 | 78.6 ± 20 |
| saturation target | 14.2 ± 5.0 | 8.34 ± 4.0 |

**No colour layer is applied to the footage.** The clip the search returned is the clip
the viewer sees; the measured band and its drift are still recorded on every run so
drift is visible in the manifest, but nothing is corrected. `BEAST_BG_GRADE=1` restores
the old bounded correction (`brightness` 0.85–1.32 / `saturate` 0.60–1.45 for A,
0.80–1.45 / 0.60–1.60 for B); it is off by default by the owner's instruction.

---

# 9. Stage 10 — QA, and what may stop a reel

- **Structural gate** (`render_gate`): duration, streams, audio presence, file sanity.
  **Can fail the run.**
- **Visual QA**: measured luma/saturation, drift, and whether text is actually painted
  in the expected band on frame 0. **Advisory** — recorded, never blocking.
- **Pair-fit audit**: how well copy, background and sound mean one thing together.
  **Advisory** — because the format is built on the background *not* repeating the words,
  a literal-minded score must not veto the contrast.
- **Audio validation**: an audio stream must exist with measured features. **Can fail.**

---

# 10. Stages 11–12 — STATE, DELIVERY, PUBLISHING

**Manifest** (`data/template_manifest.json`): one entry per run — format, template hash,
payload, background (pin id, creator, URL, rights), audio, render result, measurements,
every stage verdict, the full asset selection (chosen pin, lane, query, query log,
rejected candidates *with reasons*), content metadata, publish result. It is both the
audit trail and the dedupe source.

**Ledger** (`data/asset_ledger.json`): a dict of `visual`/`audio`/`reservations`. Assets
are reserved during a run and only committed once a valid artifact exists. A wrong-shaped
document is treated as corruption (empty + flagged), never as licence to reuse.

**Delivery**: the preview goes to the Telegram review channel with format, topic, the
first two lines, lane, scores, and the reason if anything was held.

**Publishing** (`src/run_publish.py`): takes the newest entry at status `rendered`,
builds caption + alt text, creates the container, publishes, records `media_id`, `at`,
`container_id`, `ig_id`. Two guarantees: only a reel at status `rendered` is ever taken
(so nothing posts twice), and the workflow step requires `success()`.

---

# 11. Running it by hand

```bash
# the full automatic behaviour, including publishing
gh workflow run preview.yml -f format=auto -f publish=true

# a safe check: render and deliver, do not post
gh workflow run preview.yml -f format=A

# locally, one reel, no publishing
python -m src.run_preview --format A --quality delivery
python -m src.run_publish --format A --dry-run     # inspect the publish payload
```

**Environment switches**

| variable | default | effect |
|---|---|---|
| `BEAST_SCRATCH` | `/tmp/beast-render` | where a run renders |
| `BEAST_LEDGER` | `<repo>/data/asset_ledger.json` | asset memory |
| `BEAST_WRITER` | `auto` | `auto` = Meta then DeepSeek; `meta` = never fall back |
| `BEAST_WRITER_TIMEOUT` | 150 (CI 120) | per writer call |
| `BEAST_DL_TIMEOUT` / `BEAST_DL_RETRIES` / `BEAST_DL_DEADLINE` | 90 / 2 / 75 | download bounds |
| `BEAST_FFMPEG` | unset | pin an exact ffmpeg binary |
| `BEAST_BG_GRADE` | unset (off) | `1` restores the background colour correction |
| `IG_ACCESS_TOKEN`, `IG_USER_ID` | GitHub secrets | publishing |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | GitHub secrets | review delivery |

**Credentials live outside the repo** (it is public — values are never committed):

| what | path | keys |
|---|---|---|
| Telegram review channel | `~/.beast-secrets/telegram.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Instagram | GitHub secrets | `IG_ACCESS_TOKEN`, `IG_USER_ID` |
| Pinterest service | none (public API) | — |
| trending audio | none (public API) | — |
| research tools (not in the pipeline) | `~/.beast-secrets/apify.env`, `~/.beast-secrets/vision.env` | `APIFY_TOKEN`, `GEMINI_API_KEY` |

---

# 12. Failure map — what each message actually means

| symptom | real cause |
|---|---|
| `no candidate survived the prefilters: N rejected` | genuine rejections; read the causes list |
| `the Pinterest search returned no results for any lane` | service cold, redeploying, or serving a surface without videos |
| `download failed or exceeded the cap` en masse | media host throttling, or the HLS path is broken |
| `content generation failed on 2 cards` | the critic refused every draft on two different cards — read the printed dimensions |
| `'list' object has no attribute 'setdefault'` | the ledger was rewritten as a list; fix its shape |
| run dies at `Install ffmpeg` | runner apt blip; re-run (ffmpeg is also bundled) |
| `manifest was corrupt` | committed conflict markers; the loader now self-heals them |
| `needs_review` | a structural or audio failure — the reel was delivered, not published |

---

# 13. What was removed, and where it went

The engine used to carry several parallel pipelines. Everything not reachable from the
live path was deleted, and the history is in git if any piece is ever wanted back:

    run_create, run_short, run_v2, run_confess-era code, build_video, build_short,
    background, pin_still, blueprint, registry, renderers, pexels, voiceover, carousel,
    generate, generate_short, writer, mind-era helpers, analyze, harvest, insights,
    learner, learn_short, strategy_v2, state, score, platform, provenance, reachability,
    paced, lint_short, seo-era helpers, experiment, run_experiment, draftlog,
    keepalive, notify_send, run_health, run_learn, run_measure

Also removed: the seven workflows that drove them (`create`, `confess`, `health`,
`keepalive`, `learn`, `measure`, `notify`), and the test files that covered them.
`preview.yml` is the only workflow. What remains is the pipeline, its state, its
templates and its contracts — nothing retired hanging off the side.

---

# 14. The rules that came from a real failure

Every guard in this system exists because something broke. The short list:

- **No text on the background** → a clip published with burned-in text because frames
  were sampled evenly and the text existed only at 1.0 s; OCR now samples the head and
  "couldn't run" is a rejection.
- **Not a still frame** → a reel published with motion 0.0003.
- **The literal lane goes last** → the director's metaphor was abandoned after one clip.
- **The metaphor is broadened** → a 3-word metaphor returned a single result.
- **The critic gets a different vendor** → the writer judging itself is not a review.
- **A missing score is not a zero** → `product_trust: null` refused three good drafts in
  one run.
- **A refused card takes a different card** → a run died with a usable topic pool unused.
- **Downloads are bounded** → one pin held a run for nine minutes.
- **HLS is fetched in Python** → ffmpeg's HLS reader segfaults here; nearly every clip
  failed silently before this.
- **The manifest merge is semantic** → committed conflict markers made the manifest
  unreadable and every later run died on load.
- **No colour layer** → the footage ships as shot; the band is reported, not corrected.
