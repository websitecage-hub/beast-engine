# BEAST ENGINE — MASTER ARCHITECTURE & IMPLEMENTATION BLUEPRINT

**Project:** Unleash The Beast — `@unleashthe.b`  
**Product:** *Social Anxiety and You* ebook (Gumroad)  
**Audience target:** English-speaking adults 18–30, primarily US/UK/Canada  
**Architecture status:** Design specification; not yet implemented  
**Publishing status:** KEEP PAUSED until an explicit owner go-live approval  
**Purpose:** Build a research-led, multi-format Reels production and learning system that raises the probability of creating content people watch, remember, send privately, and act on—without promising guaranteed virality.

---

## 0. Executive decision

**Do not replace v2 with one new “winning” format.** Keep the tested v2 confession renderer, bring the already-built v3 two-beat-short modules into a controlled experiment runner, and add only the minimum orchestration needed to compare clearly different formats. Let observed performance—not a strategy document, one viral example, or an LLM's confidence—decide where to invest next.

The system should have three layers:

1. **Evidence layer:** harvest and normalize competitor/account/reel evidence, analyze actual videos visually, and maintain a catalog of repeatable structural patterns with source URLs, performance context, and confidence.
2. **Creative production layer:** produce several candidate creative blueprints, critique them, route each to a format-specific renderer, attach rights-cleared media, and reject anything that fails the gates.
3. **Measurement layer:** track delivery separately from content quality; measure retention, sends/shares, saves, profile actions and Gumroad outcomes where available; run disciplined experiments; promote patterns only after replication.

Do not build an autonomous “viral oracle.” Build a **bounded experimentation engine** that makes evidence-based choices, can explain each choice, and learns without overfitting small samples.

## 1. What the supplied system docs establish

Treat these as the owner-supplied current-state description, but verify the local working tree and remote branch before editing because the docs also report local/remote divergence.

- **v2 is the current tested path:** `src/run_v2.py`, `src/writer.py`, `src/lint.py`, `src/paced.py`, `src/learner.py`, `src/insights.py`, and the acquisition/publish modules. The full create path has passed test renders and final-file checks.
- **Publishing is intentionally paused:** `confess.yml` has its schedule commented out; manual dispatch defaults to `dry_run: true`. Preserve both protections during implementation.
- **v3 short modules already exist but are not wired into a runner:** `src/generate_short.py`, `src/lint_short.py`, `src/build_short.py`, `src/templates.json`. They self-test but haven't been validated as a full end-to-end publish-ready pipeline.
- **Legacy v1 remains for auditability:** `run_create.py`, `generate.py`, `build_video.py`, old score/analyze modules and `create.yml`. Do not accidentally re-enable it.
- **Measurement design already requests useful signals:** views/reach, saves, shares, watch time, skip rate where supported; snapshots at +2h/+24h/+72h/+7d. Missing metrics are supposed to be unavailable, not zero. The current doc identifies 24h as the learning snapshot; the new design below distinguishes early monitoring from final decisions.
- **Research assets reportedly exist:** a prior run of 346 hashtag Reels plus 300 Reels from 20 accounts, alongside a seven-query harvest. The raw corpus and its quality have not been independently audited in this chat. Reuse and audit this evidence before spending more credits on collection.
- **Owner docs identify critical dependencies:** Meta writer endpoint, DeepSeek critic bridge, Pinterest service, audio scraper, optional Pexels, Instagram Graph API, GitHub Actions JSON state, FFmpeg, and Telegram alerts.

**First engineering gate:** check `git status`, active branch, local HEAD, `origin/main`, `git log`, and the complete local-vs-remote diff. The supplied docs say local `main` is ahead of `origin/main` and remote still contains the older v1 commits. Do not reset, force-push, or merge blindly. Make a verified backup reference before any implementation.

## 2. What the research so far suggests—and does not prove

Eight public Reels were submitted to vidIQ for scene-by-scene visual analysis. The following observed structures were found in high-performing outlier examples:

| Example | Approx. views / creator median reported by vidIQ | Observed structure | Useful hypothesis |
|---|---:|---|---|
| [@rock.s.w](https://www.instagram.com/reel/Ddwm1XUx5Nw/) | 964K / 31x | ~6.7s POV walk, static line, comic final clause, seamless loop | Specific social truth + punchline can beat a generic paragraph, even with minimal editing. |
| [@lifeliftman](https://www.instagram.com/reel/DeRsolMAyoa/) | 2.1M / 143x | ~12s atmospheric shot, static curiosity hook; promised list deferred to caption | Caption-led curiosity may work for this creator; test whether it earns meaningful watch, sends and profile actions for our account. |
| [@sam_socialanxiety](https://www.instagram.com/reel/Dd3Y6dAT_vP/) | 796K / 33x | ~83s real phone-call challenge, tension, real interaction, emotional resolution | Real stakes and a genuine event create a payoff an automated text card cannot fake. Use as story-structure evidence, not as a directive to fabricate personal experiences. |
| [@sadiealdis](https://www.instagram.com/reel/DdlzBcjAYBf/) | 1.3M / 17x | ~35s direct-to-camera list with live delivery and phrase-synced captions | Identity framing plus a sequence of recognizable specifics can hold attention across a longer piece. Human presence is a dependency. |
| [@_jeffmarr](https://www.instagram.com/reel/DeGUSSQg_Xa/) | 1.2M / 6.8x | ~96s reflective voiceover, night-driving visuals, kinetic captions, open emotional question | Motion can come from text timing and narrative rather than many camera cuts; long narration is only useful when the material earns the runtime. |
| [@tamaniaaa](https://www.instagram.com/reel/Dd2TCaOOI84/) | 836K / 154x | ~72s casual in-car book reading, a repeated daily motif that accumulates to insight | A tangible source/prop and conversational delivery can create trust; do not copy the passage or imply endorsements. |
| [@pedronfrey](https://www.instagram.com/reel/DeRxq8Th_KV/) | 4.6M / 318x | ~26s ten-item countdown, persistent title, fast caption changes, emotional turn in final item | Progress indicators and escalation may increase completion; requires a renderer that can pace items intentionally. |
| [@huntrex_](https://www.instagram.com/reel/DeVgtPbucy7/) | 970K / 27x | ~19s multi-scene cinematic montage, short phrases synced to voiceover, contrast-based reframe | Visual progression can help a short idea feel like a story rather than a static card. |

These view counts and outlier multiples are third-party search outputs, not verified Instagram Insights. The eight are selected outliers, so they are subject to survivorship bias. They do **not** prove causality, repeatability, sales, or that these formats will work for `@unleashthe.b`.

**Important conclusion:** static text is not inherently the failure. The failed assumption is treating all static text blocks as the same creative: a funny, specific, sendable punchline is not the same as a dense, generic, all-at-once paragraph with a keyword CTA. The engine needs to vary the *creative mechanic*, not just wording, background query, font, or duration.

## 3. Audience promise and editorial rules

### 3.1 Audience

Use **adults aged 18–30** as the default audience in prompts and targeting logic. The system docs mention 16–30 in one module; reconcile that contradiction in the single source of truth. The commercial audience can be US/UK/Canada English-speaking, without pretending a search result has proved geographic distribution. Store audience/region as a hypothesis, then validate from account Insights if available.

### 3.2 Editorial job

The content's first job is to make a specific person recognize a specific behavior or thought and feel accurately understood. The ebook's job is to provide the deeper explanation and practical work. Every Reel should do one coherent job:

- **Recognition:** “That exact thing I do has a name/logic.”
- **Reframe:** replace a self-condemning interpretation with a compassionate, plain-language explanation.
- **Progression:** give a short sequence that becomes more revealing or emotionally meaningful.
- **Demonstration/story:** show an actual action or situation, with a genuine payoff. Never fabricate a first-person life event or portray synthetic dialogue as a real testimonial.
- **Bridge to product:** only once trust and content quality are demonstrated; no permanent hard sell at the end of every post.

### 3.3 Distinctiveness and truth

- Use precise behavioral details (a phone ringing, the unsent message, the rehearsed order, the post-conversation replay) rather than generic “you are not alone” statements.
- Avoid repeating stock “you are not X, you are Y” syntax so often that it becomes another detectable template. This is one creative device, not the whole brand.
- Do not make clinical, medical, cure, guarantee, medication, diagnosis, or “psychology proves” claims without valid substantiation. Do not impersonate a therapist or claim professional credentials.
- Do not mine self-harm/suicide/crisis language as a viral hook. Mental-health material that can fall under recommendation-sensitive categories must be kept out of the content generator or routed to a human-only review / excluded, not merely softened by the critic.
- Never invent an actual creator experience, therapist quote, book quote, comment, endorsement, or outcome. If a scene is illustrative fiction, describe it as a scenario rather than presenting it as a real confession.
- Structure may be inspired by observed formats; wording, footage, audio and identity must be original or properly licensed. Do not re-upload competitors' footage with captions or minor edits.
- Avoid “comment KEYWORD” as the default CTA. Treat comment-triggered DM automation as a deliberate funnel experiment, not a mandatory creative line on every Reel. Avoid engagement bait and give the viewer value even when they do not comment.

## 4. Format portfolio: one router, multiple renderers

Create a versioned **Format Registry**. Every format defines its purpose, input schema, length, pacing rules, visual requirements, audio options, quality gates, asset requirements and supported metrics. The router may select only formats whose hard prerequisites are satisfied.

### Format A — Two-beat shareable short (v3; first implementation priority)

**Purpose:** test high-specificity, one-friend-sendable thoughts with minimal cognitive load.

- Typical first test length: 6–12 seconds; duration follows actual hold/reading time, not a blind target.
- Approximately 8–22 words total as the initial v3 constraint. Hook appears at frame 0; payoff arrives after enough setup (the current v3 spec says 55–65% of runtime). Verify this against actual render timing.
- At most two text beats; allow background motion, a subtle push-in, or one well-chosen cut when it improves the point. Don't add motion just to tick a box.
- Test licensed/owned Pexels clip, properly licensed still with a slow push-in, and a deliberately minimal text-only canvas as separate visual arms—not all simultaneously in the same test.
- Audio options: rights-cleared instrumental, original ambient sound, or silence. Trending audio is a discovery signal; it is not permission to download and republish copyrighted audio.
- No in-video keyword CTA in the baseline arm. Caption can carry a natural next step where warranted.

### Format B — Paced-reveal confession (v2; preserve as control)

**Purpose:** test a slightly deeper moment with a clear beginning, progression and final reframe.

- Use the existing 5–8-line, paced reveal, with one thought appearing at a time, legible line holds, previous lines de-emphasized if that is part of the current renderer, and a deliberate final hold.
- Initial length range follows the existing validated v2 gate (14–34 seconds) until experiment evidence supports changing it.
- The concept must contain a specific situation and a meaningful shift; five lines of poetic mood text do not automatically make a story.
- Use this as an actual control format, not a universally forced output.

### Format C — Cinematic phrase-synced narration

**Purpose:** test a short emotional reframe with visual progression and/or narrated phrases.

- Initial experimental range: ~15–25 seconds, 4–8 source-matched shots, typically 2–4 seconds per shot; do not force every clip to match a rigid duration.
- Voiceover is allowed only when the script carries enough substance. Clearly distinguish non-first-person narration from personal testimony. TTS cannot reproduce genuine human vulnerability; never use fake tears/voice cracks or pretend that a generated narrator personally experienced the described event.
- Kinetic captions appear in phrase-sized chunks; evaluate caption speed and readability on a phone.
- B-roll must progress semantically: the visual should illustrate the current beat, not be random dark scenery under any script.
- Include a text-only/no-voice alternative as a separate arm if audio licensing or TTS quality fails.

### Format D — Progression/list

**Purpose:** test identity recognition or a list with anticipation from item to item.

- Only run when at least 3–5 distinct items can escalate or change the viewer's understanding. Use a persistent title/progress indicator and synchronized phrase captions.
- Initial experimental range: 20–35 seconds; item count adapts to the material, not a compulsory ten-item list.
- Current v3 two-beat renderer is not enough for this format; add it only after the renderer has real item-level timeline support and tests.
- Fully automated avatars are not assumed to be authentic. Prefer narration over genuinely relevant licensed visuals or an optional human-recorded creator asset if the owner later chooses to supply one. Don't block Phase 1 on talking-head generation.

### Not in the first production phase

- Fake “real challenge” or confession videos, AI-generated testimonial faces, green-screen commentary on stolen clips, unlicensed trending audio, generic quote-card factories, and formats requiring licensed footage the pipeline cannot verify.
- Carousels and cross-platform publishing can remain later phases. Don't let already-built dormant modules distract from proving the core Reel loop.

## 5. Research and evidence system

### 5.1 Reuse before recollecting

First parse the current harvested assets already named in the owner's environment: `videos.json`, outputs from `harvest_searches.py`, hashtag datasets, the reported 346 hashtag Reels and 300 Reels across 20 accounts, and any existing `data/research/` / corpus files. Audit schema, duplicates, timestamps, missing values, language, platform, URL type and whether play counts are current. Do not launch a new Apify job until the audit identifies a concrete missing field or sample.

Use remaining vidIQ credits only for a clearly stated research question that the local corpus cannot answer. The current balance reported after two four-video batches is **45 credits**; preserve at least 25 until the existing data has been assessed. Costs can change; check the live balance before each paid action. Apify screen showed $0.11/$5.00 usage; treat as a screenshot snapshot, not live billing truth. Before any Actor run, inspect pricing and set a cap supported by that Actor's billing model.

### 5.2 Sampling method (reduce survivorship bias)

For each of 3–5 adjacent subniches—social anxiety in concrete moments, overthinking/replaying, introversion/social boundaries, people-pleasing/approval fear, self-understanding/reframes—assemble:

- Outlier examples: multiple creators whose Reel substantially exceeds their own baseline.
- Normal-performing comparisons from the **same creators**, preferably matched by time period and format.
- Failed/weak examples only when view history is credible and the post is not simply too new to judge.
- A mix of durations and production styles; don't preselect only text overlays.
- Audience/region labels as hypotheses unless supported by source data.

An outlier score should use creator baseline and time since publication, not raw views alone. Don't compare a fresh post's views with a 9-month-old viral post. Keep Instagram and TikTok samples separated even when the same structural pattern appears across both.

### 5.3 Research record schema

Each Reel record should preserve:

- `platform`, `canonical_url`, `creator_handle`, `content_id`, `posted_at`, `captured_at`.
- `views`, `likes`, `comments`, `shares`, `saves`, `reach`, `followers` when supplied; each field gets a source, timestamp, and nullable status.
- `creator_baseline_views`, `outlier_multiple`, `outlier_method`, duration, language, niche/topic and confidence.
- `hook_visual`, `hook_text`, `hook_audio`, script/transcript where legally and technically available; first 0–3 seconds are separately documented.
- Scene sequence and approximate timestamps; camera movement, cut frequency, text changes, text position/size, on-screen density, sound/voice/music, pacing, climax, payoff, ending/loop and CTA.
- `format_family`, `hook_mechanic`, `narrative_shape`, `visual_arm`, `audio_arm`, `source_quality`, `rights_notes`, `observed_facts`, `hypotheses`, `confidence`, `analyst_version`.
- Sales evidence is `unknown` unless directly measured. Do not invent competitor conversion rates.

Preserve the original evidence URL and raw snapshot. Never replace observed facts with generated interpretation; keep `observations` and `hypotheses` in separate fields.

### 5.4 Pattern acceptance gate

A pattern is not promoted because one Reel has millions of views. It must:

1. appear in at least 3 examples from at least 2 creators (prefer more),
2. be materially stronger or show a plausible mechanism relative to same-creator controls,
3. have a reproducible description of its structure, not merely a topic or sentence,
4. pass originality, rights, safety and audience-fit checks,
5. be feasible for the current tools, or have an explicitly priced dependency,
6. become a **hypothesis to test on our account**, not a fact about causation.

## 6. Candidate creation: blueprint before prose before render

Replace the current implicit “LLM writes text, then a background is found” coupling with a single validated **Content Blueprint** contract. The existing v2 and v3 paths can adapt to this schema.

Suggested JSON fields:

```json
{
  "schema_version": "1.0",
  "experiment_id": "exp-YYYYMM-###",
  "format_id": "two_beat_v3",
  "series_id": "recognition",
  "topic_id": "phone_call_avoidance",
  "audience_hypothesis": "English-speaking adults who avoid personal calls",
  "viewer_moment": "phone lights up with a known relative calling",
  "hook_mechanic": "specific action + concealed social cost",
  "beats": [
    {"kind": "hook", "text": "...", "start_ratio": 0.0, "visual_intent": "phone lights up"},
    {"kind": "payoff", "text": "...", "start_ratio": 0.6, "visual_intent": "same situation, emotional reframe"}
  ],
  "caption": "...",
  "cta_variant": "none_or_caption_only",
  "scene_query": "...",
  "audio_plan": {"mode": "licensed_ambient", "track_id": null},
  "evidence_refs": ["research-record-id"],
  "hypothesis": "...",
  "variant_of": null,
  "rights_requirements": ["visual license", "audio license"],
  "risk_flags": [],
  "critic": {"specificity": 0, "payoff": 0, "sendability": 0, "novelty": 0, "truthfulness": 0, "brand_fit": 0}
}
```

The example texts are placeholders, not copy recommendations.

### 6.1 Candidate tournament

For each scheduled production opportunity, create **3–5 candidates** only from formats currently allowed by the experiment schedule. Candidate selection should combine:

- real situation from the research/corpus,
- format and hook mechanics from the registry,
- a clear, single viewer payoff,
- media feasibility, length and tool budget,
- diversity constraints and recent-content similarity.

Avoid generation of 20 candidates and heavy model calls if only a few can be meaningfully reviewed. Use a cheap local/rule-based prefilter first, then writer model, then an independent critic for the finalists. `critic unavailable = fail closed` remains.

### 6.2 Critic rubric

Use structured, evidence-conscious ratings for:

- **specificity:** could it describe a recognizable moment rather than “anxiety is hard”?
- **hook clarity:** is the promised subject/action clear immediately?
- **payoff:** does the video deliver what its hook promises on-screen, not only via vague clickbait?
- **sendability:** can someone imagine sending this to one specific person without needing to explain it?
- **novelty:** new wording, fresh scenario, and no mechanical near-duplicate of recent content or a research sample.
- **emotional respect:** recognition without humiliating, pathologizing or manipulating viewers.
- **truthfulness:** no fake personal claims, fake quotation, unsupported psychology claim or promised result.
- **format feasibility:** can current rendering and assets actually deliver the idea?

Reject failures; do not permit the critic to rewrite a factual or rights problem into acceptance. A model score is a filter, not a prediction of virality.

## 7. Rendering and asset system

### 7.1 Shared asset resolver

Asset selection should come after the narrative beats, because the visual must match the story. Query and score candidate assets by semantic match to each beat, shot type, movement, color/mood, duration and crop safety. Avoid using the same clip repeatedly. Track hashes and use cooldowns by **asset**, not only by query string.

**Rights policy:** Pinterest may be useful for inspiration and visual discovery, but a public Pinterest URL does not establish reuse rights. Publish only owned material, content generated for the project, or assets whose license/permission is recorded and permits the intended usage. Prefer license-cleared Pexels/other stock or generated original assets where appropriate. If rights status is ambiguous, reject the asset. Store license URL, license label/version, creator, source URL, retrieval timestamp, checksum, allowed use, and whether attribution is required. Apply the same principle to music; scraping audio off Instagram is not proof of a license. Do not package trending copyrighted audio into API-published MP4s unless reuse is authorized.

### 7.2 Renderer interface

Each renderer should implement the same interface, approximately:

- `validate_blueprint(blueprint) -> errors`
- `build_timeline(blueprint, assets, audio) -> timeline`
- `render(timeline, output_path) -> artifact`
- `qa(artifact, blueprint) -> report`

A timeline has explicit beat/scene start/end times, text positions, safe zones, audio fades, source clip trims, transitions and intended motion. Use one canonical 1080×1920/9:16 output profile, 30 fps, H.264 MP4 unless the current platform requirements dictate otherwise. Read exact constraints from existing `render_gate.py` before replacing any profile.

### 7.3 Visual quality requirements

- Hook text is visible on the first decoded frame where the format requires it.
- Keep text in safe zones and preview at actual phone size; enforce minimum text size/contrast and maximum density.
- Use phrase-sized text chunks; measure actual reading speed, not just line count.
- Every background must match the beat and be visually clean; detect burned-in text, logos/watermarks, blank/broken footage, unwanted faces, vertical-crop failures and source reuse.
- Motion must have a reason: reveal, reframing, emotional contrast, progress, or proof. Low motion is permissible for a deliberately static meme/loop arm; don't make motion a universal gate that rejects a valid control format.
- Mix audio to consistent loudness, avoid clipping, ensure speech remains intelligible, ensure silent arm is truly deliberate, and preserve legal source metadata.
- Render gate must perform real ffprobe/ffmpeg decode checks: file exists and nonzero, correct resolution/aspect/SAR/fps, duration allowed for selected renderer, codec/container valid, no truncation/no missing moov, audio contract valid, size cap, full decode.

## 8. Publishing, attribution and conversion

### 8.1 Publishing safety

- Keep `data/PAUSE` as a hard stop; preserve `confess.yml` schedule-disabled and default `dry_run: true` during architecture implementation.
- No production branch may turn on schedules or change dry-run defaults automatically.
- Keep idempotency: create a durable pending-publish claim before API calls; store container/media IDs and distinguish retryable from terminal states to prevent double posts.
- Maintain owner approval gate before any live publication or bulk catch-up. Add an explicit environment/input flag for the chosen format runner, defaulting to dry run.
- The public file host must remain valid long enough for Meta to fetch the MP4. The current docs list short-lived hosts; validate availability and complete the upload/publish operation inside the known validity window. Retry after URL expiry requires re-uploading, not reusing a dead URL.
- Log structured run ID, input hash, blueprint ID, asset provenance IDs, model IDs, prompt versions, critic results, renderer version, gate report, cost/time estimates, publish status and final media ID. Do not log secrets.

### 8.2 Organic CTA and DM automation

Do not append a keyword CTA to every creative just because a comment automation can handle it. Keep three caption-level CTA conditions to test only after baseline content works: no CTA; a genuine discussion question; a transparent optional resource invitation. A comment-to-DM route can be an explicitly measured treatment, never a hidden prerequisite for understanding the video. Ensure messaging is compliant, consentful, non-spammy, and respects opt-outs; no repeated nudges.

### 8.3 Funnel measurement

Track the funnel in separate layers:

1. **Distribution:** reach, views/plays, non-follower reach if available.
2. **Attention:** average watch time / duration (retain raw values; loops can exceed 1), total watch time, retention chart when available, skip rate if supported.
3. **Resonance:** shares/sends and saves per reach, comments per reach, profile actions per reach.
4. **Conversion:** profile visits, bio-link taps, Gumroad landing views, checkout/purchase count and revenue where measured.

Do not treat a Reel with views but no attributable sales as a proven commercial winner. Use per-post attribution/UTM tags where Gumroad supports reliable tracking, and maintain a stable baseline link so links do not break. Do not over-credit a post if attribution is unavailable. The primary growth measure for early testing should be **share/send rate per reach alongside attention**, while commercial validation uses profile/link actions and sales when data exists.

## 9. Experiment engine: what gets tested and when

### Phase A — Calibration (first 15 valid published Reels, one/day maximum)

Use three arms in a balanced, predeclared schedule:

- **A:** v3 two-beat short.
- **B:** v2 paced-reveal confession.
- **C:** cinematic multi-scene phrase-synced format only once its renderer and rights path pass end-to-end tests; otherwise use the two implemented arms and do not pretend C was tested.

Rotate topic/situation categories and post time in a blocked way rather than choosing every arm from unrelated situations. Avoid simultaneously changing format, CTA, audio, topic and posting time on every post. Log the deliberate variable(s) for each experiment. This is observational sequential testing, not a randomized scientific trial; note confounders.

Keep one daily post at most during calibration; allow each post to collect enough history. Don't publish two/day until the account has demonstrated consistent attention and content outcomes. Do not use a low initial reach as proof of bad creative; classify it as a distribution-delivery problem when reach is below the current minimum evidence threshold.

### Phase B — Evaluate every post

Capture available snapshots at +2h, +24h, +72h and +7d. Early snapshots are monitoring only. Use 72h and 7d for decisions where data maturity permits; keep a separate early response so promising posts can be reviewed faster. Do not overwrite old snapshots. Record which metrics were actually available per post.

Suggested primary fields:

- `avg_watch_ratio_raw = avg_watch_time_seconds / duration_seconds` (retain raw and note replays can make it >1).
- `share_rate = shares_or_sends / reach` where the API metric genuinely corresponds; document exact metric names/availability.
- `save_rate = saves / reach`.
- `profile_action_rate = profile_visits_or_actions / reach` where available.
- `link_click_rate`, `purchase_conversion`, `revenue` if available.

Never score `missing` as zero or as a positive. Likes are a useful secondary signal but should not dominate shareability or product intent. With small denominators, show counts and uncertainty alongside rates. Exclude delivery failures from content ranking, but use them in infrastructure diagnostics.

### Phase C — Promote only after replication

- Treat the first high outlier as a **breakout candidate**.
- Repeat the structural mechanism (not the wording, exact footage or hook) in one or two subsequent posts to test reproducibility.
- Require at least 3 independent supporting examples across posts/creatives before a strategy-level promotion. A single post may be lucky, timely, audience-specific or incorrectly measured.
- Retire only after adequate matured samples. Do not punish a format for an API failure, a dead media URL, missing insights or tiny delivery.

### Phase D — Adaptive selection

Only after roughly 5 valid, mature samples per arm and metrics available for most of them, switch on a conservative contextual bandit. Keep a meaningful exploration floor (at least ~30% of eligible opportunities initially) and limit a single arm from owning the whole schedule. Apply creative fatigue/cooldown constraints before sampling. Do not clone a breakout for five consecutive posts automatically; test a structural replication and preserve variation.

Treat the existing per-attribute Thompson sampling as a later-stage option. For a small account, combinations of format × length × audio × background × topic × CTA lead to sparse, confounded data. Begin with arm-level comparisons; add attribute-level learning only when sample sizes support it.

## 10. Learning and strategy governance

The learner must not rewrite core rules based on a small batch. Separate:

- **Hard policy:** safety, rights, format validity, publishing pause, daily cap, originality, no false testimony. Only code/owner can change these.
- **Strategy hypotheses:** topics, hook mechanics, visual families, length ranges, CTA treatment, pacing. The learner can adjust priors within limits.
- **Evidence:** immutable post snapshots, data provenance and experiment metadata.
- **Reports:** concise explanations of wins, losses, uncertainty and missing data.

Every strategist change should state evidence IDs, sample size, comparison group, confidence, expected benefit, risk and rollback condition. Restrict writes to strategy/prior fields; no automated changes to workflow schedules, secrets, publish mode, safety rules, rights gate or renderer code. A weekly strategist may produce a proposed diff/report; it should not directly implement unreviewed core changes.

## 11. Reliability, spend control and observability

### Spend

- Before paid vidIQ calls, query live credit balance. Price every planned call; don't spend remaining reserve for low-value checks. Current balance after eight analyses: 45 credits; set a protected floor of 25 until the existing research corpus is reviewed.
- Before Apify runs, inspect Actor pricing model and schema; set `maxTotalChargeUsd` for pay-per-event or `maxItems` for pay-per-result when supported, and limit actor input `maxResults` as the primary bound. These caps don't apply to every pricing model, so verify the model. Start with small batches.
- Record estimate, actual, output usefulness and remaining allowance for each tool call. If cost cannot be bounded, do not run it by default.
- Avoid repeated polling that is not required; check job IDs, do not resubmit a still-running job.

### Run-state

Use explicit states, persisted as data:

`DISCOVERY -> BLUEPRINTED -> CRITIC_PASSED -> ASSETS_READY -> RENDERED -> QA_PASSED -> DRY_RUN_READY -> OWNER_APPROVED -> PUBLISH_PENDING -> PUBLISHED -> SNAPSHOTS_PENDING -> SCORED`.

Any failure enters `REJECTED` or `RETRYABLE_FAILURE` with a reason. Only the valid next transition is allowed. State updates must be atomic and idempotent. An expired media URL, missing metric, or critic timeout must not silently become a successful or bad content score.

### Alerts

Alert on: no valid candidates, repeated critic failures, asset-license uncertainty, render failure, missing file host, Instagram token expiry, publish duplicate-risk, insights outage, spend threshold, failed state commit and schedule unexpectedly enabled. Summarize exact next action, never include secrets.

## 12. Proposed module boundaries

Preserve existing modules where they already work; adapt behind interfaces rather than replacing wholesale.

- `src/research/normalize.py`: normalize Apify/vidIQ/Pinterest/search JSON into one schema.
- `src/research/analyze.py`: enrich with format labels, outlier/baseline fields, scene observation records.
- `src/research/report.py`: generate the evidence report and gaps; no publishing side effects.
- `src/formats/registry.py`: declarative format contracts and eligibility checks.
- `src/blueprint.py`: schema validation and timeline-ready content contract.
- `src/candidates.py`: generate diversified candidates from a situation and format set.
- `src/critic_model.py` / existing writer critic: extend to structured rubric and explicit failure reasons.
- `src/assets.py` (or a thin facade over existing background/Pinterest/Pexels/provenance modules): rights-aware asset selection and deduplication.
- Existing `src/build_short.py`: v3 renderer; keep its independent tests; connect to orchestration only after real end-to-end dry-run QA.
- Existing `src/paced.py`: keep v2 renderer behind the same renderer interface.
- `src/build_cinematic.py`: add only if the cinematic arm is selected for Phase A, with timeline-driven shot/caption/audio support.
- `src/experiment.py`: predeclared arm schedule, cooldowns, sample maturity and replication state.
- `src/run_experiment.py`: new dry-run-default orchestration entrypoint; no workflow schedule initially.
- Existing `src/insights.py`, `run_measure.py`, `learner.py`, `state.py`, `render_gate.py`, `publish.py`, `upload_host.py`, `alerts.py`: reuse; add schema support and guards instead of parallel duplicates.
- `data/research/`: immutable source snapshots and normalized corpus index.
- `data/experiments.json`: declared plans and assignment log.
- `data/metrics.jsonl` or equivalent append-only table: snapshots and metric availability.
- `data/PAUSE`: remains the universal kill switch.

Avoid creating a large collection of new agents. Build a deterministic orchestrator and a few narrow model calls with auditable inputs/outputs.

## 13. End-to-end production flow

1. Load `PAUSE`, config, branch/revision and run mode. Abort safely if paused.
2. Verify model/service availability and current budget if a paid external call is needed.
3. Load eligible arms and the predeclared experiment assignment; enforce cooldowns and sample balancing before generation.
4. Select a real situation + evidence refs from the corpus.
5. Generate 3–5 blueprint candidates; rule prefilter; independent critic; choose one only if a candidate passes all quality bars.
6. Resolve rights-cleared assets semantically against the beats; record provenance; reject unsupported sources.
7. Render with the format-specific renderer and create a QA report.
8. Run all gates: content/safety/novelty; source rights; timeline; audio; text safe-zone/reading speed; media file integrity; duration/resolution/fps/codec/size; no burned-in unapproved text; publish metadata validity.
9. Save a preview, artifact checksum, blueprint and full logs. In normal development, finish here; do not publish.
10. After explicit owner go-live approval only: check final paused/schedule state, claim slot idempotently, upload with a sufficiently durable URL, create/poll/publish the IG container, record media IDs and actual publish time, then notify.
11. Measure snapshots without overwriting history; classify delivery failures independently; calculate mature metrics; update report and only then update experiment posterior/strategy proposal.

## 14. Implementation plan: coordinated, but reversible

### Step 0 — Repository truth and backup

- Inspect `git status --short`, branch, HEAD SHA, `git log --oneline --decorate -10`, `git diff --stat origin/main...HEAD`, and untracked files.
- Create a backup branch/tag from the actual working HEAD only after confirming it contains the v2 rewrite. Do not reset or force-push.
- Compare local and remote workflows; confirm remote schedule remains disabled and remote source has not lost local v2 code.
- Inventory the reported harvested research files and summarize existing research first.

### Step 1 — Research audit, no paid scrape

- Validate and deduplicate all local research JSON.
- Produce `docs/research/RESEARCH_AUDIT.md` with sample sizes, data coverage, missing fields, a top outlier table and same-creator controls.
- Link the eight visual analyses above and keep observations separate from hypotheses.
- List remaining research questions; only then decide whether to spend remaining vidIQ/Apify budget.

### Step 2 — Contracts and registry

- Add/validate `ContentBlueprint` schema, format registry and renderer interface.
- Unit-test schema validation, unsupported fields, duration constraints, format eligibility, unsupported rights state and metric null semantics.
- Keep v2 working tests green.

### Step 3 — Wire v3 behind a new dry-run runner

- Use the already-existing `generate_short.py`, `lint_short.py`, `build_short.py`, `templates.json`; do not duplicate these modules.
- Add `run_experiment.py` which can call v2 or v3, but defaults to `dry_run=true` and writes artifacts locally/into the existing test output path only.
- Run real model + asset acquisition + render + `render_gate`; no publishing API call in these integration tests.
- Preserve current production workflow settings.

### Step 4 — Add third renderer only if justified

- Implement cinematic multi-scene text/voiceover only when source research shows it is worth testing and the current `voiceover.py` + background infrastructure supports it with rights-cleared assets.
- Add scene timelines, caption synchronization, shot relevance checks and real-file render tests.
- Don't build talking-head/real-action automation without a verified authentic asset source.

### Step 5 — Measurement and attribution

- Review real `insights.py` support/field names against current Instagram API account capability. Fetch owner insights only if authorized and available.
- Log per-metric availability, snapshots, format/experiment assignment, duration and actual publish time.
- Add 72h/7d decision reports while preserving the older 24h snapshot. Never rewrite historical metric data.
- Integrate profile/link/Gumroad measures only when their sources can actually be read; label unavailable otherwise.

### Step 6 — Preview review and controlled rollout

- Produce several previews per active renderer and inspect on phone-size simulations / actual device.
- Run full CI under the same environment/FFmpeg path as scheduled jobs. A local pass alone is insufficient according to the project docs.
- Maintain `PAUSE`, disabled schedule and dry-run defaults. Owner reviews the actual preview and must separately approve first live post.
- Start at one Reel/day; first calibration uses 15 valid published examples split as evenly as feasible among the formats that passed QA. If there aren't enough eligible renderers, run fewer formats—never claim to have tested a disabled arm.
- Only later consider 2/day, Trial Reels, carousels, automatic promotions or Shorts cross-posting.

## 15. Acceptance criteria (definition of done)

A design or implementation is not done merely because the modules import or a Reel renders.

### Research
- Local corpus audit complete with source URLs, data timestamps, creator baselines, duplicated items, language and missing-field analysis.
- Outliers plus same-creator normal controls and at least three independent examples per proposed pattern where possible.
- Research report distinguishes observation / hypothesis / unsupported sales claim.

### Content
- Every candidate has a validated blueprint, one clear viewer payoff and source references.
- Hard rules reject dangerous/clinical overclaims, fake stories/testimonials, copied wording, unsupported claims, generic filler, broken hook-payoff promise and unavailable rights.
- Critic failure/timeouts fail closed; rewrites carry explicit critic notes.
- Template repetition, same premise repetition and asset reuse are separately checked.

### Render
- Every enabled renderer has deterministic/unit tests plus real FFmpeg integration outputs passing the final file gate.
- Caption timing/readability, first-frame hook, safe zones, visual-semantic match, audio contract, no accidental baked-in source text, output metadata and rights provenance are tested.
- Format-specific durations are allowed rather than forcing every Reel to 14–34 seconds or 6–12 seconds.

### Operations
- Test path cannot call `media_publish`.
- Production workflow remains paused until explicit approval; `data/PAUSE` overrides every entrypoint.
- Retries cannot duplicate a post; missing metrics are not scored; expired media URL cannot be reused.
- State commit, alerts, spend guard, fail-closed behavior and rollback path are covered.

### Learning
- Report includes rates and raw counts plus sample size/maturity, not views alone.
- Early metrics and mature evaluation are separated.
- A format only receives a policy-level promotion after replication; one viral post is not treated as proof.

## 16. Decisions to make now

1. **Keep v2; do not declare it the winner.** It is a baseline arm and a tested renderer.
2. **Wire and validate v3 next.** It is already implemented enough to avoid a rewrite, but not yet proven end to end.
3. **Do not launch a third renderer before auditing existing research JSON.** If the research audit supports a cinematic arm, add it as a separate reversible experiment.
4. **No default mandatory keyword CTA.** Test conversion mechanics separately after baseline resonance, with compliance and user trust protected.
5. **Use actual owner Insights and Gumroad outcomes where available.** Don't infer sales from competitor views.
6. **Keep live publishing disabled throughout build/test.** The go-live decision is a separate owner approval, not a side effect of a merge.
7. **Don't spend the remaining 45 vidIQ credits yet.** Inspect the local corpus and research gaps first; retain at least 25 credits until there's a specific unresolved question. For Apify, verify live billing and every Actor's price before spending.

## 17. One-shot Hermes implementation brief

Give Hermes this file as the master specification. Hermes should work in the actual Cloud Shell repository and execute the implementation in staged commits, but not ask the owner to shuttle small outputs back and forth. It should automatically collect its own evidence and place artifacts/reports in the repository for a single final summary.

**Instructions to Hermes:**

> Read `BEAST_ENGINE_MASTER_ARCHITECTURE.md` fully and inspect the whole repository before editing. First establish the authoritative working tree; inspect local/remote divergence and preserve a backup reference. Do not reset or force-push. Do not enable publishing or change `confess.yml` scheduling/dry-run defaults. Reuse existing v2/v3 modules, avoid parallel duplicates, audit current research JSON before paid scraping, and keep all new orchestration dry-run by default. Implement Steps 0–3 and only implement Step 4 if the research audit supports it. Run the full test suite, model-backed dry-run, asset acquisition, actual FFmpeg render and final-file gates. Do not call `media_publish` or post anything. Commit changes to a dedicated feature branch, run CI-compatible tests, and create a pull request if the environment has permissions; otherwise produce a patch and a clear report. Do not silently skip blocked tests or treat unavailable metrics as zero. Finish with one report containing commit/branch, files changed, tests run and results, render artifact paths, evidence found, budget consumed, known blockers, and exact owner approval still required. If any safety, rights, cost or repository-state ambiguity remains, stop that specific operation and report it rather than guessing.

---

## 18. Evidence and current platform notes

- Meta/Instagram introduced Trial Reels as a way to test content with non-followers and review early engagement, including views, likes, comments and shares, before sharing more widely. Availability/eligibility should be feature-detected in the actual account, not assumed: [Meta announcement](https://about.fb.com/news/2024/12/trial-reels-try-content-non-followers-first-see-what-perfoms-best/).
- Instagram explains Reels total and average watch time as useful measures for understanding whether an opening holds attention: [Meta/Instagram announcement](https://about.fb.com/news/2023/04/instagram-reels-trending-audio-and-gifts-updates/).
- Instagram recommendations have separate Recommendation Guidelines; allowed content may still be ineligible for recommendation, including certain sensitive material, clickbait/engagement bait and unoriginal repurposed content: [Meta Recommendation Guidelines](https://about.fb.com/news/2020/08/recommendation-guidelines/) and [Instagram Help Center archived copy](https://archive.ph/9lHBC).
- Instagram's `Edits` app announcement describes data-driven insights including skip rate and tools for timing text/clips, useful as an available creator workflow/reference—not a guarantee that those measurements are accessible through this project's Graph API: [Meta Edits announcement](https://about.fb.com/news/2025/04/introducing-edits-streamlined-video-creation-app/).
- The current visual evidence set consists of eight selected public Reel analyses listed in Section 2. These are observational examples, not controlled experiments or evidence of competitor sales.

**Final position:** the best achievable setup is not one supposedly perfect prompt. It is a system that finds specific audience situations, develops original structures, selects the appropriate renderer, checks quality/rights before delivery, and learns from mature first-party outcomes while keeping costs and publishing under explicit control.
