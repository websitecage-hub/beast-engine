# BEAST ENGINE — the complete production line

Everything that happens between "a workflow wakes" and "a reel is live on
@unleashthe.b", in order, with the reason each rule exists. Where a rule came from a
measured failure, the measurement is written down next to it, because most of these
guards only exist because something broke in production.

- Repo: `websitecage-hub/beast-engine` (branch `main`)
- Workflow: `.github/workflows/preview.yml`
- Entry point: `python -m src.run_preview` (render) and `python -m src.run_publish` (post)
- Two formats, locked: **A = `caption_list_title_v1`** (~12.2 s numbered list),
  **B = `social_moment_loop_v1`** (~7 s loop)

---

## 0. Trigger

The workflow has two triggers:

```yaml
on:
  schedule:
    - cron: "0 9 * * *"     # 09:00 UTC
    - cron: "0 17 * * *"    # 17:00 UTC
  workflow_dispatch:         # manual, with optional inputs
```

A scheduled wake carries **no inputs**, so the job-level env sets the defaults that
make it autonomous:

| env | schedule | dispatch |
|---|---|---|
| `RUN_FORMAT` | `auto` | `inputs.format` (default `auto`) |
| `RUN_QUALITY` | `delivery` | `inputs.quality` (default `delivery`) |
| `RUN_DELIVER` | `true` | `inputs.deliver` (default `true`) |
| `RUN_PUBLISH` | `true` | `inputs.publish` (default `false`) |

`auto` means "the format with fewer previews", so two wakes a day alternate A and B:
two reels a day, which is how the account ran before.

---

## 1. Job setup (before any intelligence runs)

1. `actions/checkout@v4` — the runner gets the repo **and** the committed state files.
2. `actions/setup-python@v5` (3.12) with pip caching.
3. **Install ffmpeg** — `apt-get install -y ffmpeg`. ffmpeg is a hard runtime
   dependency: four modules shell out to it. `config.resolve_ffmpeg()` resolves in
   this order: `BEAST_FFMPEG` env → `ffmpeg` on PATH → imageio-ffmpeg's bundled
   binary (pinned in `requirements.txt`). This step is deliberately tolerant —
   measured: an apt blip killed a whole scheduled run at this step, and the bundled
   binary would have worked.
4. `pip install -r requirements.txt` (includes `rapidocr-onnxruntime` for OCR).
5. **Compile check** — `python -m compileall -q src research`. Syntax errors fail here
   in seconds instead of 15 minutes into a render.
6. **Verify ffmpeg** — resolves the path and runs `ffmpeg -version`.

---

## 2. Format selection

`src/run_preview.py` records the run id (`<UTC stamp>-<6 hex>`), resolves the scratch
mount (`BEAST_SCRATCH`, a roomy runner temp dir — never the small home mount), and
picks the format:

- explicit `A`/`B`/format id → that format
- `auto` → `content_factory.pick_format()`, which counts previews per format in
  `data/memory.json` and returns the one with fewer, alternating A and B.

---

## 3. CONTENT — the creative brain (`src/formats/content_factory.py`)

Six passes, in the order they run. This stage is where the copy is written, judged and
rewritten; nothing renders until it passes.

### Pass 1–2 — the audience-moment card

**Prompt** (`_CARD_PROMPT`, verbatim structure):

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
 "audience_cluster": "after-conversation replay | group conversation entry | texting
   and group chats | asking and needing help | parties and social events | making and
   maintaining friends | everyday performance moments | boundaries and people-pleasing
   | quiet progress"}

STEP 2 - a slate of exactly 3 concepts, each a DIFFERENT situation (not three versions
of one), each usable in ONE of the two formats:
[{"concept_id": "kebab-case", "situation": "...", "behaviour": "...",
  "private_meaning": "...", "turn": "...",
  "format": "caption_list_title_v1|social_moment_loop_v1"}]

Then pick the strongest concept and say why in one line.

RULES
- Concrete beats broad: bus, group chat, doorway, phone screen, kitchen, lift,
  counter, call button. No "social anxiety is hard", no "healing takes time".
- Never diagnose, never claim a list proves a condition.
- Do not reuse anything in the RECENTLY USED list.

RECENTLY USED: {the last 8 topics/headlines from the manifest}
```

Why a card: the writer is bad at inventing situations but good at writing once it has
one. The card is a *moment* (where/when, what a camera sees, the private worry, the
contradiction), not a topic label. The recent list stops the account repeating itself.

### Pass 3 — the writer

Two format-specific prompts share one rule set.

**A — numbered list (`caption_list_title_v1`)**: a title carrying a finite promise
("7 things you do when…") and exactly that many items, each an observable moment a
camera could catch. The caption is a contract: the number must equal the item count.

**B — the loop (`social_moment_loop_v1`)**: one thought, ≤ 45 words, whose last line
completes the first so a replay lands as a deliberate turn.

Shared hard rules, in the prompt and then enforced in code:

- concrete beats broad; prefer time, place, object, message, pause
- ordinary spoken language, no therapist vocabulary ("insecure", "awkward",
  "overthinking") unless the line makes it concrete
- the viewer is never the punchline and is never diagnosed
- no emoji; lowercase is applied by the template
- A: number == item count, every item distinct; B: one thought, ≤45 words, closes the
  loop

**Vendor choice**: `_writer_chat` calls the Meta wrapper first (`llm.chat`, vendor
recorded as `meta-api`); if that errors it falls back to the DeepSeek bridge
(`deepseek-bridge`). `BEAST_WRITER=meta` disables the fallback so a Meta outage fails
loudly instead of silently switching writers. The vendor is recorded per run, and the
critic is marked `independent_critic: false` when both sides came from the same
service.

**Retry policy** (`_round` + `generate`): `MAX_ATTEMPTS = 3` writer passes per card,
`TOPIC_ROUNDS = 2` cards before giving up, `CONTENT_BUDGET_S = 420` seconds wall clock,
after which no further attempt is started.

When **distinctiveness** is the only failing dimension, the retry prompt names the
exact openings already produced and forbids them — measured: three attempts on one
card all came back at `distinctiveness 3/5` and the run died with a usable topic pool
untouched. If a card is refused three times, round 2 takes a *different* card and the
refused material goes into the avoid list.

### Pass 3b — deterministic gates (before any critic call)

These are mechanical, so a hollow payload never spends a critic call:

- **Specificity ladder** (`ladder_level`): level 0 = no concrete anchor (rejected);
  level 2 = a concrete scene anchor; level 3 = plus a private meaning or a turn.
  A regex set (`_ANCHORS`, `_PRIVATE`, `_TURN`) decides, and the reasons are recorded.
- A: number vs item count, duplicate items, banned clinical vocabulary.
- B: one thought, ≤45 words.
- Near-duplicate of a recent headline, or a repeated recent topic.

### Pass 4 — the independent critic

Runs on **DeepSeek** (a different vendor from the writer, so it is a real second
opinion). Prompt (`_RUBRIC`):

```
Score this reel copy. Return ONLY JSON:
{"scene_specificity":0-5,"recognition_authenticity":0-5,"natural_voice":0-5,
 "emotional_truth_dignity":0-5,"format_fit":0-5,"distinctiveness":0-5,
 "shareability":0-5,"caption_value":0-5,"product_trust":0-5,
 "hard_fail":null,
 "evidence":"<=50 words quoting the actual wording you judged",
 "sendability_who":"the exact person who would send this and to whom",
 "rewrite_notes":"<=40 words, concrete, only about what failed"}
```

- **Critical** (all must be ≥4): scene_specificity, recognition_authenticity,
  natural_voice, emotional_truth_dignity, format_fit, distinctiveness.
- **Supporting** (must be ≥3): shareability, caption_value, product_trust.
- **Hard fails** (any one refuses the draft): clinical/diagnostic claim, fabricated book
  content, copied distinctive wording, humiliating portrayal, more than one unrelated
  scene, manipulative sales pressure.
- The hard-fail list is **format-aware**: B's rubic explicitly says the 7-second loop has
  no numbered list, so a "title/list count mismatch" can never be reported for it —
  measured: the shared rubric hard-failed valid B loops with A's rule.
- The critic judges **wording only**; the format and template are never a penalty.

**Missing scores are not zeros.** A `null` supporting score means "not judged" and is
ignored; a missing critical score fails closed as `not scored`. Measured: the critic
returned `product_trust: null` on an otherwise passing draft and the old
`float(x or 0)` refused it — and on B it did that three times in one run.

### Pass 5 — targeted rewrite

The failing dimensions plus the critic's `rewrite_notes` and `sendability_who` are
handed back: "fix ONLY this, keep everything that already worked". Nothing else changes.

A malformed critic reply gets one protocol retry, then fails closed
(`critic_unparseable`) rather than spending a blind rewrite.

### Pass 6 — the payload

Validated against `template_payloads.validate`, rights attached by the broker (never by
the model; unknown means preview-only), and the whole content stage is summarised into
the run's `content` record: attempts, ladder, critic scores, card vendor, degraded flag.

---

## 4. ASSET INTENT (`src/formats/asset_intent.py`)

An LLM call turns the finished copy into a searchable brief: lane queries
(**literal** — the scene as written; **adjacent** — the same feeling elsewhere;
**emotional** — the mood), plus an audio intent (arousal, energy, avoid-list,
emotional role) and `core_human_moment`. Values are normalised — measured: a 0–100
"arousal" killed a whole brief and fell back to two lanes, so 0–100 scales are coerced
to 0–1 rather than discarded. If the call fails, the broker falls back to
copy-derived queries (`degraded: true`).

---

## 5. THE TWO DIRECTORS

Both read **the copy the pipeline generated** and answer with one search term. This is
the layer that stops the background being a literal illustration of the words.

### 5.1 Visual director (`src/formats/visual_director.py`)

Prompt (owner-authored; `{docs}` is the **real** Pinterest API guide pulled from
`~/pinterest-api/USAGE.md`, trimmed to the search surface, with a built-in fallback):

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
   to get highly creative, high-quality, aesthetic results. Do NOT use long sentences.
   Do NOT use literal emotions like "sad" or "anxiety". Use 2-3 word cinematic
   aesthetic terms.

Output ONLY valid JSON:
{"semantic_analysis": "1 sentence on the visual vibe.",
 "pinterest_search_query": "2-3 broad, aesthetic words"}
```

Notes:
- The **example lists were removed deliberately**: they anchored the model. Measured:
  with examples, B's audio answer was `cinematic` (an example); without them it became
  `introspective`. The visual vocabulary barely moved, which says that part came from
  the material, not the list.
- Output is validated: 1–3 words, and a banned-word regex rejects literal/clinical
  choices (`sad`, `anxiety`, `person`, …). Both key spellings
  (`pinterest_search_query` and the older `pinterest_query`) are accepted so a stale
  caller still works.
- `director_vendor` is recorded. Today's real outputs: `morning elevator aesthetic`,
  `night bedroom aesthetic`, `office morning aesthetic`, `supermarket liminal
  aesthetic`, `convenience store aesthetic`, `liminal supermarket aesthetic`.

### 5.2 Audio director (`src/formats/audio_director.py`)

Same shape, one word, with the **real** trending-audio API guide injected from
`~/trending-audio-scraper/USAGE.md`:

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

Validated to exactly one word with the same banned-word list. Real outputs: `lofi`,
`cinematic`, `indie`, `quirky`, `glitch`, `introspective`, `suspense`.

Both directors degrade safely: a failure is recorded as a degraded stage (with the
error), the run continues on the intent lanes alone, and the manifest shows it.

---

## 6. BACKGROUND ACQUISITION (`src/formats/asset_broker.py`)

### 6.1 Search order (the plan)

```
1. the director's metaphor query                      ← leads, always
2. the metaphor's own 2-word pairs                    ← "liminal hallway haze" →
                                                         "liminal haze", "hallway haze"
3. adjacent lane queries (from asset intent)
4. emotional lane queries
5. literal lane queries                               ← LAST, deliberately
```

Measured: `liminal hallway haze` returned exactly ONE clip, that clip failed a hard
check, and the run fell straight to the literal lane (`person holding mug standing in
office`) — the behaviour the director's briefing forbids. The literal lane is now the
least desirable background, not the first fallback.

### 6.2 The service and the download

- `pinterest.wake()` is called before searching: a free-tier service sleeps, and the
  first searches of a wake-up return nothing. It accepts `/api/status` **or** `/health`.
- Search hits `/api/search?q=…&scope=videos` on the v3.1 service, and falls back to
  `/search/videos` (the older FastAPI route) so either deployment works.
- Rows are normalised: the service's `is_video` flag is **unreliable** (measured
  `false` on pins that carry a `video_url`), so a row with a `video_url` is typed as a
  video. `type` matters — the prefilter rejects anything that is not video/gif.
- **Download uses the exact URL the search returned**, raw and direct. Measured:
  3 of 3 clips in 0.1–1.8 s; the old `/download` proxy route is only a fallback because
  the v3.1 app does not serve it (404).
- HLS (`.m3u8`) is fetched **segment by segment in Python** and only muxed by ffmpeg
  from local bytes. Measured: ffmpeg's own HLS network reader segfaults on this host
  (`rc -11`, empty stderr), which silently failed nearly every clip; the same URLs now
  land at 22.5 MB in 4.5 s.
- Downloads are bounded: per-read timeout (`BEAST_DL_TIMEOUT`, default 90 s), retries
  (`BEAST_DL_RETRIES`, default 2) and a wall-clock deadline (`BEAST_DL_DEADLINE`,
  default 75 s) because a trickle of bytes keeps a stream alive forever — measured: one
  pin held a run for nine minutes.

### 6.3 Hard gates (the only things allowed to reject a clip)

| gate | rule | why |
|---|---|---|
| video, not image | pin type must be video/gif | images are a different format |
| real motion | mean frame difference ≥ **0.0015** | a published B reel measured **0.0003** — a still frame dressed as video |
| long enough | ≥ the format's duration (12.2 s / 7.0 s) | a clip shorter than the reel loops or freezes |
| vertical | usable crop ≥ 720 wide | the formats are 9:16 |
| not an ad | commerce/affiliate domains, CTAs, stock/repost sources rejected | these are not backgrounds |
| fresh | not used inside the no-repeat window (`data/asset_ledger.json`) | the same clip must not come back too soon |
| **no on-screen text (OCR)** | RapidOCR must positively find nothing | see below |

**The OCR gate is the one the owner cares about most.** Frames are sampled
**head-weighted** — 0.2 s, 0.8 s, 1.5 s, 2.5 s, mid, late — because overlay text often
exists only in the opening second. Measured: a clip that published with "HEALTHY /
banana oatmeal cookies" burned in had its text at 1.0 s and nothing later, so even
sampling called it clean. The verdict is recorded (`by: rapidocr`, `has_text`,
`text_said`) and **"could not run" is a rejection, not a pass** — the old code answered
"clean" from a pixel heuristic when RapidOCR was absent, and two clips that carried
text went through as a result.

### 6.4 Selection is raw

By explicit instruction: **no luma rule, no darkness preference, no colour preference
and no look score chooses the background.** A candidate qualifies by surviving the hard
gates above, and the first qualifier **in search order** is taken. The taste score is
still computed and stored in the manifest for audit, but it does not select.

---

## 7. AUDIO PICK (`src/formats/audio_broker` path inside the broker)

Trending-audio API (`https://audi0-scraper.onrender.com`) searched with the audio
director's single word; rows are ranked and the chosen track is recorded with its
`stable_id`, title, artist, `video_count`, and measured features (energy, tempo,
brightness) plus a distance from the requested intent. Rights are always recorded as
`unknown` → preview-only.

---

## 8. TEXT STYLE (`src/formats/text_style.py`)

The on-screen text colour follows the background: **yellow on dark footage, white on
bright footage**, derived from the background's measured luma. (See §11 for the
colour-layer decision.)

---

## 9. RENDER (`src/formats/template_renderer.py` + `templates/`)

HyperFrames renders the template with the background, the audio and the copy.

Format profiles (measured from the reference clones, and the definition of "the look"):

| | A `caption_list_title_v1` | B `social_moment_loop_v1` |
|---|---|---|
| duration | 12.2 s (bounds 11.6–12.8) | 7.0 s (bounds 6.4–7.8) |
| luma target | 105.0 ± 14.0 | 78.6 ± 20.0 |
| saturation target | 14.2 ± 5.0 | 8.34 ± 4.0 |
| grade limits | brightness 0.85–1.32, saturate 0.60–1.45 | brightness 0.80–1.45, saturate 0.60–1.60 |

The renderer applies **one bounded correction pass** — a CSS
`brightness() saturate() contrast(1.02)` filter computed by `grade_for()` to move the
clip toward the target band, clamped by the limits above. It never pushes a dark clip
until the blacks lift; a clip outside the limits is reported as a stretch pick.

After rendering:

- **structural gate** (`gate_ok`): duration, streams, file size. This one can fail a run.
- **visual QA**: measured luma/saturation + drift + *is text actually painted in the
  expected band on frame 0* (bright-pixel fraction inside the format's text band).
  Recorded on every run; **advisory**, it cannot hold a reel back.
- **audio validation**: an audio stream must exist and the track's features must be
  present. This one can fail a run.

---

## 10. RECORD, DELIVER, PUBLISH

1. **Manifest** (`data/template_manifest.json`) — one entry per run: format, template
   hash, payload, background ref (pin id, creator, URL, rights), audio ref, render
   result, measured values, every stage verdict, the full asset selection (chosen pin,
   lane, query, query log, rejected candidates with reasons), content metadata and
   publish result. This file is the audit trail and the dedupe source.
2. **Asset ledger** (`data/asset_ledger.json`) — dict of `visual` / `audio` /
   `reservations`; assets are *reserved* during a run and only *committed* once a valid
   artifact exists. A corrupt ledger is treated as corruption (empty + flagged), never
   as a licence to reuse.
3. **Delivery** — the preview is sent to the Telegram review channel with the format,
   topic, first two lines, lane, scores and (when applicable) the needs-review reason.
4. **Publishing** (`src/run_publish.py`, separate step, only when `RUN_PUBLISH` is
   true) — takes the newest manifest entry at status `rendered`, builds the caption and
   alt text, creates the container, publishes, and records `media_id`, `at`,
   `container_id`, `ig_id`. Two guarantees: it only ever takes a reel at status
   `rendered` (so nothing posts twice), and the workflow step requires `success()` (so a
   failed render cannot fall through to an older reel).
5. **Commit state** — the workflow commits `data/` back to `main` with a semantic merge
   driver for the manifest (union by `run_id`, `.gitattributes` + `scripts/merge_manifest.py`)
   and a marker repair step (`scripts/repair_data_json.py`) that only unions
   list-shaped files and restores dict-shaped ones from git. Measured: committed
   conflict markers once made the manifest unreadable and every later run died on load;
   a blanket repair then rewrote the ledger as `[]` and every asset stage died with
   `'list' object has no attribute 'setdefault'`.

---

## 11. The colour layer (current state and the change requested)

**Today**: the background is passed through a bounded CSS grade
(`brightness(x) saturate(y) contrast(1.02)`) computed to pull the clip toward the
format's measured band, clamped by the table in §9.

**Requested**: remove it. The background should be shown exactly as downloaded, with
the copy and the music over it, and the text colour decided from the **raw** luma of the
clip rather than the graded value. The band measurement stays as a recorded number, so
drift is still visible in the manifest.

---

## 12. Environment variables that change behaviour

| variable | default | effect |
|---|---|---|
| `BEAST_SCRATCH` | `/tmp/beast-render` | where a run renders (CI: runner temp) |
| `BEAST_LEDGER` | `<repo>/data/asset_ledger.json` | asset ledger path |
| `BEAST_WRITER` | `auto` | `auto` = Meta then DeepSeek; `meta` = never fall back |
| `BEAST_WRITER_TIMEOUT` | 150 s (CI 120) | per writer call |
| `BEAST_DL_TIMEOUT` | 90 s | per-read timeout for a download |
| `BEAST_DL_RETRIES` | 2 | download attempts |
| `BEAST_DL_DEADLINE` | 75 s | wall-clock cap on one download |
| `BEAST_FFMPEG` | unset | pin an exact ffmpeg binary |
| `IG_ACCESS_TOKEN`, `IG_USER_ID` | secrets | publishing |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | secrets | review delivery |

---

## 13. Failure map — what each message actually means

| symptom | real cause |
|---|---|
| `no candidate survived the prefilters: N rejected` | real rejections; read the causes list |
| `the Pinterest search returned no results for any lane` | the service is cold, redeploying, or has no video surface (it answered nothing) |
| `download failed or exceeded the cap` en masse | the media host is throttling, or the HLS/ffmpeg path is broken |
| `content generation failed on 2 cards` | the critic refused every draft on two different cards — read the printed dimensions |
| `'list' object has no attribute 'setdefault'` | the ledger was rewritten as a list; repair the shape, do not ignore it |
| run dies at `Install ffmpeg` | runner apt blip — re-run; ffmpeg is also bundled as a fallback |
| `manifest was corrupt` | committed conflict markers; the loader now self-heals from them |
