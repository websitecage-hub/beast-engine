# ABOUT.md — BEAST ENGINE

**A fully autonomous Instagram Reels publishing system for the account `@unleashthe.b`
("Unleash The Beast").**

Written 2026-10-06 (UTC) for the purpose of handing the system's full context to a
higher-reasoning model that will design the content strategy. Everything factual in this
document was read from the live system (Instagram Graph API, the repo's own state files,
the running services) during writing — not recalled or estimated. Where something is
unknown or unverified, it is marked as such rather than filled with a plausible guess.

---

## 0. THE 60-SECOND VERSION

| | |
|---|---|
| What | An unattended pipeline that writes, renders, and publishes two Instagram Reels per day |
| Account | `@unleashthe.b` — "Unleash The Beast" |
| Product | An ebook on social anxiety, sold on Gumroad (`unleashthebeast.gumroad.com/l/dufrt`) |
| Audience | Men, 16–30, with social anxiety, scrolling late at night |
| Compute | GitHub Actions (free tier). Nothing runs on a personal machine. |
| State | JSON files committed back into the repo — the repo *is* the database and the brain |
| Language | Python 3.11, ~6,300 lines across 21 modules |
| Repo | `github.com/websitecage-hub/beast-engine` (public — this is what makes Actions minutes free) |
| Live since | 2026-09-20 (first commit); current posting format in place since 2026-09-26 |
| Reels published | 29 total on the account; 22 by this engine |
| Followers | **14** |
| Status | Working reliably. Performance is poor — that is the problem this document exists to solve. |

**The one-paragraph summary:** the machine is a fully working factory. It reliably produces
a polished, correctly-formatted, on-brand Reel twice a day, gates it with hard QA checks,
publishes it to Instagram, measures the result, and learns from it. The *engineering* is
done. What it does not have is a content strategy that makes strangers stop scrolling. Every
reel gets roughly the same ~100 reach, 0 shares, 0 saves, 0 likes. It is a good factory
producing a product nobody is picking up off the shelf.

---

## 1. WHAT THE SYSTEM ACTUALLY DOES

### 1.1 Daily loop

Two Reels per day, at fixed times (all times UTC; IST shown because the operator is in India):

| Post | Due (UTC) | Due (IST) |
|---|---|---|
| Post 1 | 13:30 | 19:00 |
| Post 2 | 17:30 | 23:00 |

The workflow *wakes* every 4 hours — 01:30, 05:30, 09:30, 13:30, 17:30, 21:30 UTC — and a
scheduler inside the code decides whether a post is due. The extra wakes exist because
GitHub's cron scheduler is unreliable (observed running ~6.5 hours late on this repo, and
sometimes dropping a slot entirely). A late wake still finds the post due and publishes it,
so a slot is recovered rather than lost. The 01:30–09:30 wakes are intentionally quiet —
they never publish. A hard daily cap refuses a third post no matter how many times the
workflow fires.

### 1.2 The create pipeline (one run, ~11–24 minutes)

Each publishing run executes these steps in order:

```
1.  services_awake    ping the three self-hosted services to wake them (free-tier cold start)
2.  jitter_start      random delay so posting looks human
3.  scheduler         decide whether a post is due today; idempotency + daily cap
4.  trending_joined   fetch today's trending music reference
5.  generate          LLM writes the reel's entire text payload
6.  background        search Pinterest video, download, crop/scale, loop
7.  music             pick a track, analyze it, beat-sync the edit
8.  build_video       composite the text, render the 1080x1920 MP4
9.  QA gate           reject the reel if it fails format / motion / text / placement checks
10. upload_host       park the MP4 on a public file host (Instagram must fetch it by URL)
11. publish           Instagram Graph API: create container -> poll status -> publish
12. alerts            Telegram report to the operator
13. state/memory      record every choice made, commit the JSON back to the repo
```

Longest observed publish runs: 24.3 min. Skip runs (scheduler decides nothing is due):
~1.1 min. The workflow's timeout is 30 min.

### 1.3 The other three workflows

| Workflow | Schedule | Purpose |
|---|---|---|
| `measure` | 04:00, 10:00, 16:00 UTC daily | Pull insights for posts 24h–7d old; score them; prune logs |
| `learn` | Sundays 18:00 UTC | Decay old scores, reweight every attribute, learn the best posting hour, write a report |
| `health` | 05:00 UTC daily | Ping all services, warm the trending cache, check and rotate the Instagram token |
| `notify` | manual only | Send a Telegram message from CI (keeps the bot token out of the local machine) |

### 1.4 What it does *not* do

- No human review before publishing. The QA gate is the only reviewer.
- No comments management, no DM handling, no reply automation.
- No paid promotion, no cross-posting to other platforms, no TikTok/YouTube.
- No video generation — backgrounds are real footage sourced from Pinterest.
- No A/B testing of creative *format* (only of small attributes — see §6).

---

## 2. THE CONTENT IT PRODUCES

### 2.1 Format — locked and verified

This is the part most worth understanding, because it is the thing the strategy has to work
within or deliberately change. The format was derived by extracting frames from reference
reels the operator supplied and vision-analyzing them — it is a measured match, not a guess.

**Visual spec, as actually rendered (verified by inspecting published frames):**

- **9:16 vertical, 1080x1920, 30 fps, 9–10 seconds.**
- **Background:** real cinematic video footage of a person — typically a lone young man,
  shot from behind or in shadow, at night, in an urban or interior setting. Footage ships
  as-shot with **zero colour grading** (a deliberate decision — the grade chain is
  disabled). Clips are cropped to 9:16, trimmed from the middle, and seamlessly looped.
- **Audio:** a track acquired from a self-hosted audio service, normalized to −16 LUFS,
  edited so text changes land on musical beats. The soundtrack is **baked into the MP4** —
  no Instagram-native audio, no trending-audio attribution.
- **Text:** **ONE static text block**, centered horizontally, **visible on every frame from
  frame 0 to the last frame.** Nothing animates, nothing fades in, nothing types on. There
  is no per-word or per-line reveal.
- **Typeface:** Coolvetica Regular (a single family for the whole block, per the operator's
  choice), near-white `#F5F5F5`, with a soft blurred drop shadow (blur 8, ~70% opacity).
  **No outline, no background box, no scrim over the footage.**
- **Placement:** the block is centered *inside a safe band* (13%–72% of frame height), not
  at the geometric centre. This is deliberate: the bottom ~24% of the frame belongs to
  Instagram's caption and action bar, so a block centred on `h/2` ships its last lines
  underneath the UI. The gate asserts this on measured pixels before every publish.
- **CTA line renders at 70% of the body size** with extra leading above it, so the ask reads
  as a footer rather than another story beat.
- **Watermark:** `UNLEASHTHE.B_` in small Inter, faint, near the bottom.
- **Like count is hidden** on the account.

**Text structure (from the generation spec, enforced in code):**

| Part | Lines | Job |
|---|---|---|
| Hook | 1 | Stop the scroll in 1–2 seconds |
| Micro-story | 3–4 | The experience from the inside, physical and specific |
| Compassionate pivot | 1–2 | Name why it happens; offer no fix |
| CTA | 1 | "Comment [KEYWORD] and I'll send you the full breakdown." |

Total: **5–7 lines** (hard bounds 5–7). Second person only. No emoji, no hashtags on screen,
no exclamation marks, no advice, no "journey/healing/trauma/toxic".

### 2.2 A real example (published 2026-10-05 21:30 UTC)

On-screen text:

```
POV: your phone lights up and you watch it ring out.
You saw the name on the first ring. You hold your breath until it stops.
Then you wait 10 minutes and type "sorry, just saw this".
Your chest is still tight. Your heart is still racing over a call that never happened.
You're not flaky. You're trying to hide the panic before they hear it in your voice.
Comment QUIET and I'll send you the full breakdown.
```

Caption:

```
That missed call isn't you being rude. It's you sitting there with the phone in your hand,
holding your breath until the ringing stops so you can finally breathe again.

Your nervous system doesn't hear a ringtone. It hears a trial. One where your voice might
shake, your mind might blank, and they might hear the flaw you've been hiding. So it freezes
your hand until the threat passes. Texting feels safe because you can rewrite it until it
sounds normal.

If you let calls ring out then text "sorry, just saw this" when you saw it on the first ring,
you're not alone. Comment QUIET and I'll send you the full breakdown — the why behind the
freeze and how to stop treating every call like a trial.

#socialanxiety #overthinking #socialanxietystruggles #anxietyproblems #introvertstruggles
#quietpeople #latenightthoughts
```

Background: a young man alone, night, urban. Reach: 104. Likes: 0. Saves: 0. Shares: 0.

### 2.3 The 16 content pillars

Content rotates through 16 topics, each bound to one DM keyword. The keyword is not
cosmetic — it is the conversion trigger (see §5).

| Keyword | Archetype | The lived experience |
|---|---|---|
| SAFE | the_rehearsal | Rehearsing a food order, then freezing anyway |
| QUIET | the_detour | Letting calls ring out, then texting "sorry, just saw this" |
| FREE | the_detour | Cancelling a plan, feeling relief, then guilt about the relief |
| REPLAY | the_aftermath | Rewriting an old conversation at 2am, making it crueller each time |
| SEEN | the_freeze | Going mute in a group while able to talk one-on-one |
| START | the_losses | Feeling years behind everyone the same age |
| GHOST | the_losses | Fake-scrolling a dead screen to avoid people |
| MASK | the_mask | Performing "fine" at work, falling apart alone |
| HEARD | the_sealed_mouth | Laughing at a joke you didn't hear |
| BLANK | the_freeze | Going blank when silence hits mid-conversation |
| STILL | the_rehearsal | "I'll do better next time" that never comes |
| ALONE | the_losses | Feeling relieved when friends stop inviting you |
| CALM | quiet_hope | Rehearsing a conversation that never happens |
| ENOUGH | the_losses | The "I'm not good enough" baseline |
| PEACE | quiet_hope | A rare calm moment |
| CLEAR | quiet_hope | Walking through rain, looking ahead |

The three `quiet_hope` keywords are the only non-dark ones, and they are used sparingly.

### 2.4 The tonal contract

The voice is defined in the system prompt as **"a compassionate witness"** — someone who has
lived the pain, describing the viewer's experience back to them with precision and zero
judgment. Explicitly *not*: a quote card, a coach, a therapist, a clever friend, motivational
content. The rule is **recognition, never advice**. The reel names the shame without
wallowing in it, and reframes the behaviour as a nervous-system response rather than a
character flaw.

The intended viewer response is not "good advice" — it is **"this is literally me."** That is
what the whole format is built to produce, and what the CTA monetizes.

### 2.5 Caption and SEO layer

Every reel also ships: a caption (validation → mechanism → universality → CTA → hashtags), a
5–7 hashtag set from a sanctioned pool, and **alt text** written for Google (the on-screen
text is indexed). Keyword strategy targets: *social anxiety, overthinking conversations,
quiet people, introvert struggles, fear of being judged, canceling plans relief, hating phone
calls, replaying conversations, feeling behind everyone*. Intended as a search/discovery
layer so the account is findable, not just feedable.

---

## 3. HOW IT IS BUILT

### 3.1 Architecture

No server. No database. The pipeline runs as short-lived GitHub Actions jobs; **JSON files
committed in the repo are the database, the memory, and the learning state.** Every run reads
state, does its job, writes state back, and commits. This design gives unlimited free compute
(public repo) and a complete auditable history of every decision the machine has ever made.

### 3.2 Modules (21 files, ~6,300 lines)

| Module | Lines | Responsibility |
|---|---|---|
| `build_video.py` | 1180 | Text fitting, placement, compositing, rendering, QA gate |
| `music.py` | 919 | Track sourcing, dedup by spectral fingerprint, beat detection, trend alignment |
| `background.py` | 507 | Pinterest video search, selection, crop/scale/loop |
| `config.py` | 521 | All tunables; central binary resolution (ffmpeg) |
| `run_create.py` | 461 | The scheduler and the whole pipeline orchestration |
| `generate.py` | 398 | Text engine driver; enforces the spec's rules in code |
| `publish.py` | 360 | Instagram Graph API publishing, insights, token refresh |
| `analyze.py` | 347 | The Sunday learning pass and report writer |
| `seo.py` | 304 | Keyword injection, alt text, hashtag assembly |
| `mind.py` | 271 | **The system prompt** — the account's entire writing brain |
| `llm.py` | 253 | LLM API client (JSON extraction via brace scanning) |
| `harvest.py` | 137 | Insight harvesting and scoring |
| `run_health.py` | 138 | Service pings, token rotation |
| `pinterest.py` | 112 | Pinterest API client |
| `state.py` | 99 | Git state commits, log pruning |
| `alerts.py` / `notify_send.py` | 81 / 72 | Telegram reporting, GitHub Issue fallback |
| `upload_host.py` | 75 | Public file hosting for the MP4 |
| `run_measure.py` / `run_learn.py` | 40 / 39 | Thin workflow entrypoints |

### 3.3 External services (all free, all no-API-key)

Nothing here uses a paid API. Three are **self-hosted on Render's free tier** by the operator;
the rest are public endpoints.

| # | Service | URL | Role |
|---|---|---|---|
| 1 | Pinterest API | `pinterest-api-inyg.onrender.com` | Video search → background footage. Own service; the job never scrapes Pinterest directly. |
| 2 | Audio scraper API | `audi0-scraper.onrender.com` | Track search, download, and the trending-music feed. Own service. |
| 3 | Meta LLM | `meta-api-chat-h326.onrender.com` | The writing model (`meta-ai-thinking`). Own service. |
| 4 | Instagram Graph API | `graph.instagram.com/v23.0` | The real publication path, insights, token refresh |
| 5 | Telegram Bot API | `api.telegram.org` | Operator reporting |
| 6 | File hosts | `litterbox.catbox.moe` (72h), `uguu.se` (fallback) | Makes the MP4 publicly fetchable by Instagram |
| 7 | Internet Archive / Pixabay | — | Music fallbacks when the audio API yields nothing |

All three self-hosted services were verified **live** (HTTP 200) during writing this document.
They are on free Render tiers, so they sleep when idle and the pipeline wakes them first.

### 3.4 The publishing path (a real constraint worth knowing)

Instagram has two API routes and this account uses the more limited one. On
`graph.instagram.com` (Instagram-Login), `POST /{ig-id}/media` **only accepts a `video_url`** —
there is no resumable upload. The resumable flow exists only on the Facebook-Login route.

So the engine: renders the MP4 → uploads it to an anonymous public file host → hands Instagram
the URL → polls until the container reports `FINISHED` → publishes. The resumable flow is still
implemented as an automatic fallback.

**Practical consequences:** the reel's audio is baked in (Instagram shows "original audio", not
the trending track), and a handful of fields are simply not available on this route — notably
**alt text cannot be set on a Reel container via the API** (it returns 400). The alt text is
generated for every reel but must be pasted manually by the operator.

### 3.5 Quality gates (why nothing broken ships)

Before publishing, the build must pass a hard gate: duration 9–10s, exactly 1080x1920, H.264,
AAC audio, under 60 MB, faststart flag set, exactly one text block, 5–7 source lines, a CTA
line present, the loop echo intact, text measured to fit inside the frame margins, the ink
block measured as centred inside the safe band and clear of the Instagram UI zone, and a
frame-difference check proving the background actually moves. If a check cannot be measured,
the gate **fails closed** rather than passing.

There is also a 1,745-line acceptance suite (75 tests) that must pass before the publishing
job runs at all — the workflow has a test job and the publish job depends on it.

### 3.6 Reliability engineering already done

- **GitHub cron drift** — wake grid every 4h + scheduler decides, instead of trusting an exact
  cron time (the root cause of several missed posts).
- **The daily cap** — prevents extra posts regardless of wake frequency.
- **Token rotation** — the health job refreshes and re-writes the Instagram token as a repo
  secret while >21 days remain; token death raises a loud alert with recovery instructions.
- **Dependency declaration** — ffmpeg and PyYAML were only available by accident on the dev
  box; both are now declared, so CI and local behave identically.
- **Idempotency** — a post is recorded by date, so a re-run cannot double-post.
- **Log pruning** — the repo does not grow without bound.

---

## 4. WHAT IT IS DOING TODAY — THE HONEST PERFORMANCE PICTURE

This is the section that matters most for strategy. All numbers read live from the Instagram
Graph API on 2026-10-06.

### 4.1 The account

```
Followers:  14
Following:  0
Posts:      29 (22 from this engine)
Bio:        "Self transformation from within / Break limits. Understand yourself. / Get the ebook"
Link:       unleashthebeast.gumroad.com/l/dufrt   (product live, HTTP 200)
```

### 4.2 Every reel since the current format went live

| Date | UTC | Reach | Views | Likes | Cmts | Saved | Shares |
|---|---|---|---|---|---|---|---|
| 09-27 | 08:30 | 105 | 122 | 3 | 3 | 0 | 0 |
| 09-27 | 11:18 | 123 | 156 | 2 | 0 | 0 | 0 |
| 09-27 | 13:45 | 122 | 172 | 1 | 0 | 0 | 0 |
| 09-27 | 14:04 | **5** | 9 | 2 | 2 | 0 | 0 |
| 09-27 | 15:46 | 106 | 126 | 3 | 1 | 0 | 0 |
| 09-27 | 16:02 | 110 | 143 | 1 | 0 | 0 | 0 |
| 09-28 | 15:55 | 107 | 143 | 0 | 2 | 0 | 0 |
| 09-29 | 16:29 | 109 | 150 | 0 | 0 | 0 | 0 |
| 09-29 | 18:54 | 64 | 76 | 0 | 0 | 0 | 0 |
| 09-30 | 00:55 | 105 | 127 | 0 | 0 | 0 | 0 |
| 09-30 | 07:37 | 110 | 143 | 0 | 0 | 0 | 0 |
| 10-01 | 16:58 | 127 | 185 | 1 | 0 | **4** | 0 |
| 10-01 | 19:21 | 99 | 132 | 0 | 0 | 0 | 0 |
| 10-02 | 16:05 | **29** | 43 | 0 | 0 | 0 | 0 |
| 10-02 | 21:46 | 117 | 150 | 0 | 0 | 0 | 0 |
| 10-03 | 14:30 | 122 | 154 | 2 | 0 | 0 | 0 |
| 10-03 | 17:49 | 104 | 131 | 0 | 0 | 0 | 0 |
| 10-04 | 15:04 | 113 | 135 | 1 | 0 | 0 | 0 |
| 10-04 | 18:08 | 112 | 122 | 1 | 0 | 1 | 0 |
| 10-05 | 18:48 | 113 | 158 | 0 | 0 | 0 | 0 |
| 10-05 | 21:30 | 104 | 109 | 0 | 0 | 0 | 0 |

**Totals across 21 reels: 2,106 reach · 2,686 views · 17 likes · 8 comments · 5 saves ·
0 shares.**

### 4.3 The diagnosis, stated plainly

- **Mean reach 100.3. Median 109.** The distribution is *flat* — values cluster tightly
  between 99 and 127. There is no variance to learn from and no breakout. Roughly 100 is the
  signature of the same small pool of people being shown the post every time.
- **0 shares across 21 reels.** This is the single most damning number. The system's own
  scoring model ranks *sends* as the king signal (see §6), and the component health target is
  >2% sends-per-reach. Current: **0.00%**. Not one person has ever forwarded one of these
  reels to another person.
- **Mean saves-per-reach 0.24%** (5 saves / 2,106 reach). Five saves total, four of them on
  one reel.
- **11 of 21 reels got zero likes.**
- **Two reels show reach of 5 and 29** — approximately 20–25x below the account's own baseline.
  These are, in the operator's words, "not getting views." In the operator's own reporting
  the account is described as "stuck at 200 reach," which matches this data.

**What these numbers are consistent with:** content that is being served only to the existing
follower base and then stopping. The pattern — flat reach near follower-count-plus-margin,
near-zero saves, exactly zero shares, no variance — is what you see when a post is *not being
distributed to non-followers*. Likely contributors, in order of how much evidence supports
each:

1. **Nothing in the content gives anyone a reason to send it to a specific person.** A "this
   is literally me" recognition post earns a nod, not a forward. Zero shares across 21
   attempts is a content-design signal, not an audience-size signal.
2. **The format itself fights retention.** One static text block, visible from frame 0,
   nothing changing for 9–10 seconds. On a platform where reels are judged on completion and
   rewatch, a single unchanging screen is close to the worst possible retention shape.
3. **Audio is baked in.** The reel shows "original audio" rather than riding a trending track.
   The trend-alignment machinery exists, but Instagram cannot see the audio identity.
4. **The account is 14 followers, 0 following, no social proof, no engagement loop.** Nothing
   signals to the platform that this is a live, relevant account.
5. **The content is thematically identical every single time.** Same wound, same structure,
   same tone, same caption shape, same hashtags — for 21 consecutive posts. See §4.5.

**What this data does *not* support:** bad craft. The renders are technically clean and match
the operator's supplied references precisely. The QA gate is strict and passing. The writing
is competent and on-tone. **The execution is right; the strategic choices are the problem.**

### 4.4 The control data — proof of what the account *can* do

Three older reels, posted *before* the current engine and format, are still on the account.
They used a different format (multi-line lowercase stacked text, no CTA line, no engine):

| Date | Reach | Views | Likes | Saved | Shares | Format |
|---|---|---|---|---|---|---|
| 2026-03-31 | 115 | 130 | 1 | 1 | 0 | pre-engine |
| 2026-04-02 | **2,494** | 3,089 | 95 | **56** | **35** | pre-engine |
| 2026-04-04 | **1,751** | 2,314 | 168 | **45** | **33** | pre-engine |

**These two April reels are the highest-performing content in the account's history — and they
outperform everything the engine has produced by 15–25x in reach, and infinitely in shares
(68 shares vs 0).**

This is the most important finding in this document, so it is worth being precise about what
it does and does not mean. It is **not** a controlled experiment — these were manual posts, at
different dates, with different distribution history, and the account was in a different state.
But the magnitude (2,494 reach vs a flat ~100) is far too large to dismiss, and the *shares*
difference in particular cannot be explained by distribution alone: 35 shares means 35 people
each decided to send it to someone. That is the exact behaviour the current content is failing
to produce.

Both April reels share two traits the current format lacks: **they are conversational and
argumentative** ("Not the safe version. Not the edited version. The actual thing you were
thinking." / "What if you've been giving anxiety a much better name than it deserves."), and
**they run long, in flowing lowercase lines rather than a composed 5–7 line card.** They read
like a person talking, not a card being displayed.

**Two other April/May reels did not break out** (2026-05-01: reach 121, 8 likes, 3 saves; 2026-05-02: reach 111, 3 likes, 2 shares). So the format is not a guaranteed win — but it has produced the only shares in the account's history.

**Recommendation for the strategist:** treat these two April reels as the most valuable
evidence available about this audience and this account, and analyze them directly. They are
retrievable from the account. Their full on-screen text is reproduced in §4.6.

### 4.5 Content diversity — measured, not assumed

This is a real and measurable problem that a strategy must address.

- **Hook duplication:** across 19 posts that carry a topic, there are only **17 distinct
  hooks** — two hooks appear twice. One is *"POV: you said 'yeah, I'm good' while your chest
  was caving in."* (2026-10-01 and 2026-10-02). The engine's dedup does not cover hooks.
- **Topic concentration:** the 16 pillars exist, but actual usage is narrow —
  *"performing fine while falling apart"* 4x, *"phone call avoidance"* 3x (plus more variants
  of the same idea further down the list).
- **Keyword concentration:** only 6 of the 16 keywords have ever been used. QUIET appears 5
  times, MASK 4, HEARD 4.
- **Hook form is monotonous:** **17 of 19** hooks open with the literal string `POV:` (the
  other two are `When you...`). It is a formula, and the platform sees it as one.
- **Hashtags are essentially frozen:** the same 7 tags on nearly every post
  (`#socialanxiety #overthinking #socialanxietystruggles #introvertstruggles
  #latenightthoughts #anxietyproblems #quietpeople`). 20 of 22 posts carry the identical set.
- **Visual sameness:** every reel is a lone man at night, exact same font, exact same layout,
  exact same watermark position. There is no visual variety for a viewer to encounter.

### 4.6 Full text of the two breakout April reels (the control data)

**2026-04-02 — reach 2,494, 95 likes, 56 saves, 35 shares:**

```
When was the last time you said exactly what you meant.
To anyone.
Not the safe version.
Not the edited version.
The actual thing you were thinking.
Most people can't answer that.
Not because they're liars.
Because somewhere along the way they learned —
being honest was a risk they couldn't afford.
That's social anxiety.
Not the shaking hands version.
The quiet version.
The one that makes you shrink in rooms.
Say yes when you mean no.
And smile through things that are slowly breaking you.
```

**2026-04-04 — reach 1,751, 168 likes, 45 saves, 33 shares:**

```
That dark room you call a vibe.
That cancelled plan you called being tired.
That silence you called needing space.
What if you've been giving anxiety
a much better name than it deserves.
Social anxiety doesn't look dramatic.
It looks like staying home again.
It looks like watching everyone else live
through a four inch screen.
It looks like thinking people don't like you —
when actually they just forgot you existed.
Because you made yourself
that easy to miss.
Not because you're boring.
Because you kept leaving
before anyone got the chance
to remember you.
That's the cage.
You just decorated it really well.
Full guide on breaking it — link in bio. 🖤
Drop a 🖤 if you called it introversion
when you knew it was something else.
```

Note: no hashtags on either. Both end with an engagement ask, not a DM keyword. Both were
plain text over still imagery (the 04-04 one over a grainy analog-horror image).

### 4.7 One more data-quality problem the strategist must know about

**The learning loop is currently being fed garbage, so its conclusions cannot be trusted.**

The hour-learning feature — which is supposed to discover the best posting time — is
inoperative. Reason: the two due times (13:30 and 17:30 UTC) *are* the schedule, so posts can
only ever be filed under one of two values, and GitHub's cron drift means they land at
effectively arbitrary times anyway. Worse, until 2026-10-06 a bug recorded every post under the
learner's *preference* rather than its actual publish time, so early data is corrupted.

Before that fix, the system also force-published every run regardless of the schedule, meaning
post times in history do not reflect the intended times at all. Example, from the live memory:
posts recorded at 00:55 and 07:37 UTC filed under the "13:30" slot.

**Consequence:** the "best hour" the system reports, and any per-hour analysis, is meaningless.
Do not build strategy on the hour numbers.

Similarly, every attribute weight in the learning state is still at the neutral default of
1.0, with per-attribute `n` of 1 (or 0). There is not enough data for the learner to have
learned anything yet. The `REPORT.md` numbers like "mean sends/reach 0.00% (target >2%)" are
the honest read.

---

## 5. THE MONETIZATION PATH

Understanding this constrains what the content strategy can change, because the CTA is wired
to an automation.

```
Viewer watches reel
   ↓
Reel's final line: "Comment [KEYWORD] and I'll send you the full breakdown."
   ↓
Viewer comments the keyword (e.g. QUIET)
   ↓
Instagram DM automation fires on that exact word  →  delivers a link
   ↓
Gumroad product page  (unleashthebeast.gumroad.com/l/dufrt)
   ↓
Sale
```

**Key constraints:**

- **Only 16 keywords are wired to the automation** (the list in §2.3). A comment with any
  other word fires nothing. This is enforced in code — the generated keyword is validated
  against the list, and the CTA is rewritten if the model invents a different word.
- **Bio link** goes directly to the Gumroad product.
- **`link in bio` is banned as a CTA** in the on-screen text and caption (it was the pre-engine
  format's CTA).
- **Comment counts are the visible funnel metric** — currently 8 comments across 21 reels,
  i.e. the automation has fired approximately never at meaningful volume.
- The operator's phrasing of the goal: *"get maximum views with proper hooks and such content
  that really gets more views and will lead us to sales."*

**The strategy's job, in funnel terms:** the current content maxes out at audience recognition
and never reaches a share. The leak is at the very top — nobody forwards it, so no new people
ever see it. Fixing shares is the highest-leverage move available, and the account's own April
data shows it is achievable with this audience.

---

## 6. THE LEARNING SYSTEM (what it optimizes, and why that may be the wrong target)

Every post is scored on a per-reach basis, weighted toward virality:

```
score = (6·sends + 3·saves + 2·watch + 1·comments + 0.25·likes) / reach
```

- **sends (shares)** carries the most weight — correctly, since a share is the only signal that
  reaches a new human.
- **watch** is a completion/rewatch proxy.
- **likes are nearly worthless** by design.

Every Sunday, the learner: decays old posts (`w = 0.5^(age_days/28)`), shrinks each attribute's
score toward the global mean (`adj = (Σw·s + 5·G)/(Σw + 5)`), reweights the strategy, picks the
best hour, and enqueues experiments on under-sampled attribute combinations.

There is also an **explore rate of 20%** — one in five posts deliberately deviates from the
best-known configuration, to avoid overfitting.

**The correct interpretation of this system:** it is well designed and it is optimizing the
right metric (shares). But it can only recombine *attributes of the current format* — which
archetype, which topic, which mood, which background type, which loop technique. It cannot
invent a new format, because the format is hard-coded. And with reach flat at ~100 and 0
shares, it has no signal to learn from. **The learner is not the bottleneck; the format is.**
This is the single most important strategic point in this document: any strategy handed down
here should be treated as a *format-level* change, and the learner should be re-pointed at it
afterwards.

There are two further content rules in the config that are **declared but not implemented** —
worth knowing so a strategy does not assume they are live:

- `whisper_every_n_posts: 7` + `whisper_line` — a soft product mention every 7 posts.
- `hope_every_n_posts: 10` — a lighter "quiet hope" post every 10 posts.

Neither is wired into the generation path. The learner's "hopeful" cluster exists and is used
occasionally, but the cadence rules are inert.

---

## 7. WHAT THE OPERATOR HAS ASKED FOR

Direct quotes, to convey intent precisely:

> "as you can fetch the stats that is not too good and nor can get enough views in future as i
> think so so now we have done the hard part, to build it correctly and working but now we have
> to make it much better and really follow a content strategy and a different content type or
> format to really get more views and overall sales"

> "create a full ABOUT.md file with what it does how it is built the resources used and the type
> of content it is generating the overall and everything about this system as i will give that
> to a higher reasoning ai to get the full content strategy and we will apply that to get
> maximum views with proper hooks and such content that really gets more views and will lead us
> to sales"

The operator is explicitly **open to a different content type or format** — the current
format is not sacred. Earlier preferences (one static block, visible from frame 0, no
animation) were themselves responses to earlier rejected builds, so any proposal that changes
the format should be argued for explicitly with reasoning, not changed silently.

Also relevant, from earlier direction: the operator prefers a *clean, final, working* system,
wants no filler content, and dislikes advice-giving or motivational tone.

---

## 8. WHAT THE STRATEGY NEEDS TO SOLVE (problem statement for the reasoning model)

Framed as the open questions, with the evidence attached:

**Q1. Why does nothing get shared?** 0 shares across 21 reels, while two pre-engine reels got
68 shares between them. What property do those two have that the current format removes?
(Working hypothesis from the data: they argue a point and address the viewer's self-deception
— "what if you've been giving anxiety a better name than it deserves" — rather than describing
a moment. An argument is forwardable; a description is not.)

**Q2. Is the static single-block format viable at all on Reels, or does it have to change?**
The retention shape of one unchanging screen for 9–10s is poor. What format would serve the
same emotional payload while giving a reason to keep watching and to rewatch?

**Q3. Hook architecture.** The current hook is `POV: you...` almost every time. That is a
recognised pattern the platform and viewers are trained to skip. What hook structures actually
work in this niche, and how do they map onto the 16 pillars? Note the system prompt already
contains a better hook taxonomy (POV / "When you..." / direct declaration) that the model is
not using in practice.

**Q4. Content diversity.** The engine can repeat a topic 4 times in a week with an identical
hook. What rotation policy, how many distinct hook forms, and how much visual variety (footage
type, layout, typography) does the strategy require?

**Q5. Sound.** Audio is baked in, so Instagram sees "original audio." Is that a handicap worth
solving (changing the publishing route, or accepting it), and how should trending audio be
used given this constraint?

**Q6. The cold-start problem.** 14 followers, 0 following, flat reach. What does an account
with no social proof need to do *first* that a well-distributed account does not? Is the
current "2 posts a day forever" cadence right, or is a different ramp needed?

**Q7. Where does the CTA belong?** The DM-keyword CTA is the monetization path and is locked
to 16 words. But the pre-engine breakouts used no CTA line and an engagement ask instead. Is
the CTA costing shares — and if so, how do you keep the funnel while removing the cost?

**Q8. What should the learner optimize once the format changes?** New attributes need to exist
for the learner to explore, or it will keep recombining the old ones.

---

## 9. HARD CONSTRAINTS AND NON-NEGOTIABLES

Things a strategy cannot simply assume away:

**Immutable / expensive to change:**
- Publishing must go through the Instagram-Login Graph API route. No resumable upload. Reels
  are created from a public `video_url`. Alt text cannot be set via API.
- Audio is baked into the MP4, so native audio attribution is unavailable.
- The 16 DM keywords are wired to an external automation — the CTA vocabulary is fixed unless
  the automation is updated.
- Instagram's own safe-band geometry (top chrome, bottom caption/action bar) is a physical
  constraint on any layout.
- The operator is in IST; the audience is (presumably) US/global evening — the posting times
  chosen (19:00 and 23:00 IST) were chosen for the *audience*, not the operator.

**Soft constraints (opinions to respect, not physics):**
- One static text block, visible from frame 0, no animation — currently locked, previously
  chosen by the operator against rejected build attempts.
- Coolvetica Regular, near-white ink, soft shadow, no outline, no scrim.
- No advice, no motivation, no therapy-speak, no emoji in on-screen text.
- The account's brand meaning: *"the real person under the mask — not aggression, not grind."*
  (Note the pre-engine hashtags were `#grind #masculinity #stoicism` — the brand has since
  moved away from that entirely.)

**Free-tier realities:**
- All compute is GitHub Actions free minutes; all services are Render free tier (cold starts).
- A publishing run takes 11–24 minutes; the build cannot be dramatically heavier without
  risking the 30-minute timeout.

---

## 10. REPRODUCTION / OPERATIONS REFERENCE

For anyone operating this system after a strategy is applied.

**Repo layout:**
```
beast-engine/
├── src/                      21 Python modules (~6,300 lines)
├── data/                     THE DATABASE AND BRAIN
│   ├── memory.json           post history, every choice made, dedup keys
│   ├── config.json           all tunables (brand, pillars, hashtags, timings, SEO)
│   ├── strategy.json         learned weights per attribute
│   ├── REPORT.md             weekly learning report (regenerates Sundays)
│   ├── DIRECTIVES.md         optional weekly human steering ("this week focus on loneliness")
│   ├── trending_styles.json  plain-English vibe appended to music queries
│   ├── token_state.json      Instagram token expiry tracking
│   ├── research/corpus.json  mined forum corpus used for grounded writing
│   └── logs/                 per-run JSON logs, pruned periodically
├── .github/workflows/        create, measure, learn, health, notify
├── assets/fonts/             9 font files (only Coolvetica is used)
├── tests/test_acceptance.py  1,745 lines / 75 tests — gates publishing
└── docs/SYSTEM.md            older internal system description
```

**Secrets** (GitHub repo secrets; not present locally):
`IG_ACCESS_TOKEN`, `IG_USER_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
The LLM, Pinterest, and audio services need no keys.

**Steering levers available today (no code change needed):**
- `data/DIRECTIVES.md` — free-text weekly guidance read by the content brain.
- `data/trending_styles.json` — the vibe of music to search for.
- `data/PAUSE` — create this file (any content) to stop creating entirely; delete to resume.
- Manual workflow dispatch with `dry_run` (build without publishing) or `force` (publish now,
  ignoring the schedule but never the daily cap).

**Health / failure modes to watch:**
- Instagram token death → loud alert with recovery steps; rotate via developers.facebook.com.
- The three Render services sleeping or erroring → the pipeline wakes them first, but a
  persistently failing service degrades background or music quality silently.
- GitHub scheduling drift → mitigated by the 4-hourly wake grid (§1.1).
- The `notify` workflow is how Telegram messages are sent, because the bot token lives only in
  CI.

---

## 11. SUMMARY FOR THE STRATEGIST

**What exists:** a reliable, well-engineered, self-hosted content factory that produces a
technically flawless Instagram Reel twice a day, with a QA gate, a measurement loop, a learning
system, and a built-in DM-to-product funnel.

**What it has produced:** 22 reels, flat ~100 reach, 0 shares, 5 saves, 8 comments, 17 likes.
14 followers. No sales visible in the data available.

**The core finding:** the account has previously produced content that reached 2,500 people
and was shared 35 times. The current engine has never produced a single share in 21 attempts.
The difference is format and content architecture, not production quality.

**What is being asked of the reasoning model:** a content strategy — hooks, format, structure,
rotation, cadence, and any format change — that reliably produces shares in this specific
niche, for this specific audience, on this specific account, given the constraints in §9. The
technical system can implement essentially any format decision that is made; the bottleneck is
knowing what to make.