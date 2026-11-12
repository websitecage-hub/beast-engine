# BEAST ENGINE — API reference: every service, endpoint and key

This is the complete external surface of the engine. Every call the pipeline makes goes
through one of the ten services below. Each entry says: what it is for, the base URL, the
exact endpoints and parameters, the shapes that come back, the quirks that were measured
(not assumed), the failure signature, and where its credential lives.

Two rules the whole system follows:

1. **A call that fails is recorded, not guessed at.** Every stage writes its verdict to
   the run manifest — including "this service did not answer".
2. **Nothing is hardcoded that can be resolved.** The IG account id is resolved at
   publish time with `/me`; the Pinterest route is discovered by trying the current one
   first and the older one second.

---

# 0. How credentials flow

```
local shell / manual run          GitHub Actions (scheduled)
        │                                   │
        │  ~/.beast-secrets/*.env            │  repository secrets
        ▼                                   ▼
   environment variables  ─────────────►  environment variables
        │                                   │
        └──────────────► src/config.py ◄────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
           llm.py        publish.py        alerts.py
```

`src/config.py` is the only place that reads the environment. Individual modules call
`config.env("NAME")`; nothing else touches `os.environ` directly.

| secret | env var | local file | GitHub secret | used by |
|---|---|---|---|---|
| Instagram long-lived token | `IG_ACCESS_TOKEN` | `~/.beast-secrets/ig.env` | `IG_ACCESS_TOKEN` | `publish.py` |
| Instagram account id | `IG_USER_ID` | `~/.beast-secrets/ig.env` | `IG_USER_ID` | `publish.py` |
| Telegram bot token | `TELEGRAM_BOT_TOKEN` | `~/.beast-secrets/telegram.env` | `TELEGRAM_BOT_TOKEN` | `alerts.py` |
| Telegram chat id | `TELEGRAM_CHAT_ID` | `~/.beast-secrets/telegram.env` | `TELEGRAM_CHAT_ID` | `alerts.py` |
| GitHub PAT (repo admin) | — | `~/.git-credentials` | — | pushing, release upload, secret rotation |
| Apify token (research only) | `APIFY_TOKEN` | `~/.beast-secrets/apify.env` | — | `research/scrape_reels.py` |
| Gemini key (research only) | `GEMINI_API_KEY` | `~/.beast-secrets/vision.env` | — | frame/OCR research |

**The repository is public.** Secret values belong in `~/.beast-secrets/` (mode 600) and
in GitHub repository secrets — never in a tracked file. The companion file
`~/.beast-secrets/API-KEYS.md` holds the real values for local agents; this document holds
the variable names and the file paths.

Load them into a shell with:

```bash
set -a; . ~/.beast-secrets/ig.env; . ~/.beast-secrets/telegram.env; set +a
```

---

# 1. Pinterest search service — the background library

**Purpose:** find and return vertical video pins for the visual director's query.
**Base:** `https://pinterest-api-inyg.onrender.com`
**Auth:** none (public service)
**Source:** `~/pinterest-api` (FastAPI app, deployed on Render free tier)
**Client:** `src/pinterest.py`

### Endpoints

| method | path | params | returns |
|---|---|---|---|
| GET | `/api/status` | — | service + session status (also the wake probe) |
| GET | `/health` | — | FastAPI liveness |
| GET | `/api/search` | `q`, `scope=videos`, `page_size` | `{"pins": [ ... ]}` (v3.1 shape) |
| GET | `/search/videos` | `q`, `page_size` | older FastAPI shape (fallback) |

The client tries `/api/search` with `scope=videos` first, then the legacy `/search/videos`,
so either deployment works without a config change.

### Pin shape (normalised by `_row()`)

```json
{"id": "…", "title": "…", "description": "…",
 "video_url": "https://v1.pinimg.com/videos/iht/hls/…m3u8",
 "image_url": "https://i.pinimg.com/…jpg",
 "duration": 12.4, "creator": "…", "source_url": "https://www.pinterest.com/pin/…",
 "is_video": true}
```

**Measured quirks**

- `is_video` is unreliable: pins with a `video_url` have come back with `is_video: false`.
  The client types a row as video when a video URL exists, regardless of the flag.
- Video pins often have `best_video: null`; the playable URL is a `.m3u8` inside
  `videos[]`. HLS is fetched segment-by-segment in Python (see §11) because ffmpeg's HLS
  reader segfaults on this host.
- The free tier sleeps. `wake()` probes both `/api/status` and `/health` because the two
  deployments answer on different paths.
- Downloads use the URL the search returned, verbatim. The `/download` proxy is only a
  fallback: measured 3/3 direct downloads in 0.1–1.8 s.

### Failure signatures

| symptom | cause |
|---|---|
| `the Pinterest search returned no results for any lane` | service cold, redeploying, or scoped to images |
| `no candidate survived the prefilters: N rejected` | real gate rejections — read the cause list |
| many `download failed or exceeded the cap` | media host throttling or HLS breakage |

### Smoke test

```bash
curl -s "https://pinterest-api-inyg.onrender.com/api/search?q=liminal%20space&scope=videos&page_size=3" | head -c 400
```

---

# 2. Trending audio service — the sound

**Purpose:** return currently-trending TikTok sounds for one keyword.
**Base:** `https://audi0-scraper.onrender.com` (`config["trending_api"]`)
**Auth:** none (a bearer token exists at `~/.beast-secrets/audi0-pat` for the private
deployment; the pipeline does not require it)
**Client:** `src/music.py`

### Endpoint

```
GET /v1/trending?keyword=<one word>&limit=20
```

`limit` is capped by the service at 50. Either `keyword` or `hashtag` may be sent.

### The async contract (important)

The service scrapes on demand, so the response is **status-driven**, not code-driven:

```json
// 202 while scraping
{"status": "processing", "poll_with": "https://…/v1/trending?…&fresh=false",
 "retry_after_seconds": 6}

// when finished
{"status": "ready", "data": [ {"id": "...", "title": "...", "artist": "...",
  "video_count": 324500, "duration": 30, "play_url": "..."} ]}
```

- poll `poll_with` exactly as given (it carries `fresh=false`, so polling never restarts
  the scrape)
- respect `retry_after_seconds` (clamped to 2–20 s), give up after 12 polls or the
  150 s budget and return `{}` — the caller falls back to the next keyword
- rows arrive under **`data`**

### Failure signature

`audio` stage recorded as degraded, `keywords_tried` lists each attempt with its row
count, and the run continues with a silent track rather than dying.

### Smoke test

```bash
curl -s "https://audi0-scraper.onrender.com/v1/trending?keyword=lofi&limit=3" | head -c 400
```

---

# 3. Meta chat bridge — the writer

**Purpose:** generate the audience-moment card and the copy (Format A and B).
**Base:** `https://meta-api-chat-h326.onrender.com`
**Auth:** none (public bridge)
**Model id:** `meta-ai-thinking` (the only id the service exposes)
**Client:** `src/llm.py`

### Endpoints

| method | path | body | returns |
|---|---|---|---|
| GET | `/health` | — | liveness, used to wake before the first call |
| GET | `/v1/models` | — | `["meta-ai-thinking"]` |
| POST | `/v1/chat/completions` | `{"model": "meta-ai-thinking", "messages": [{role, content}]}` | OpenAI-shaped completion |
| POST | `/api/image` | `{"prompt": "...", "mode": "instant"}` | image URLs (assets experiments) |
| POST | `/api/image2` | same | fallback when `/api/image` reports quota |

### Measured quirks

- **A prompt sent as a `system` message is silently discarded.** The full instruction must
  be the `user` message, or the model answers without it. This is the single most expensive
  quirk in the system: it produces plausible output that ignores the spec.
- One model id only, so the writer/critic separation is a *vendor* separation, not a
  parameter (see §4).
- Cold start on the free tier: the first request can take 60–90 s. `BEAST_WRITER_TIMEOUT`
  (default 150 s, CI 120 s) bounds it.
- JSON is requested in the prompt and parsed defensively (`_json_from`); a code fence or a
  trailing sentence is tolerated, a missing object is a failure.

### Failure signature

`[content] writer attempt N failed: …`, then the DeepSeek bridge is used with
`independent_critic: false` recorded. `BEAST_WRITER=meta` forbids the fallback so an
outage fails loudly instead of silently changing the writer.

---

# 4. DeepSeek bridge — the critic

**Purpose:** the second, independent review of every draft.
**Base:** `https://deepseek-web-bridge.onrender.com` (`CRITIC_BASE_URL`)
**Model id:** `deepseek-chat` (`CRITIC_MODEL`; `deepseek-reasoner` also available)
**Auth:** none (the bridge holds the session)
**Client:** `src/critic_model.py`

### Endpoints

| method | path | body | returns |
|---|---|---|---|
| GET | `/v1/models` | — | `["deepseek-chat", "deepseek-reasoner"]` |
| POST | `/v1/chat/completions` | `{"model": "deepseek-chat", "messages": [...]}` | OpenAI-shaped completion |

`CRITIC_TIMEOUT` defaults to 300 s. The bridge is OpenAI-compatible with no key, so there
is no credential to rotate.

### Why a different vendor

ENGINE_SPEC requires writer and critic to be different model families. This bridge is the
only way to satisfy that with a single Meta bridge available. A model judging its own copy
is not a review.

### Failure signature

`critic model unavailable (deepseek-chat @ …)` — one protocol retry, then fail closed
rather than spending a blind rewrite.

---

# 5. Instagram Graph API — publishing

**Purpose:** publish the rendered reel to @unleashthe.b.
**Base:** `https://graph.instagram.com/v23.0`
**Auth:** `access_token` query parameter (Instagram-Login long-lived token, ~60 days)
**Client:** `src/publish.py`

### Endpoints

| method | path | params / body | returns |
|---|---|---|---|
| GET | `/me` | `fields=id,username` | `{"id": "...", "username": "unleashthe.b"}` |
| POST | `/{ig-id}/media` | `media_type=REELS`, `upload_type=resumable`, `mime_type=video/mp4`, `caption` | `{"id": "<container>", "uri": "https://rupload…"}` |
| POST | `https://rupload.instagram.com/...` | raw MP4 body, header `Authorization: OAuth <token>` | upload progress |
| GET | `/{container}` | `fields=status_code` | `IN_PROGRESS` / `FINISHED` / `ERROR` |
| POST | `/{ig-id}/media_publish` | `creation_id=<container>` | `{"id": "<media_id>"}` |
| GET | `/{media_id}` | `fields=id,permalink,like_count,...` | published media |
| GET | `https://graph.instagram.com/refresh_access_token` | `grant_type=ig_refresh_token`, `access_token` | a new 60-day token |
| POST | `/{ig-id}/media` | `media_type=REELS`, `video_url=<public mp4>` | hosted-URL route (§6) |

### The publish sequence actually used

```
POST /{ig-id}/media            -> container id + rupload uri
PUT  rupload.instagram.com/…   -> raw bytes (<60 MB, single PUT after a HEAD for offset)
GET  /{container}?fields=status_code   -> poll until FINISHED
POST /{ig-id}/media_publish    -> media_id
```

The resumable route exists because `graph.instagram.com` does **not** support the
hosted-URL route for Instagram-Login tokens in every case; when the resumable upload is
unavailable, `src/upload_host.py` hosts the MP4 (§6) and the container is created with
`video_url`.

### Measured quirks

- **`alt_text` is rejected for REELS** — HTTP 400 "The param alt_text is not supported for
  REEL". The container is created without it and the alt text is persisted to
  `data/memory.json` for manual entry in the app.
- **`hide_like_count` is accepted and ignored.** A bogus parameter is also accepted and
  still returns a container id, so HTTP 200 proves nothing about a parameter. There is no
  API route to hide like counts on a reel; it is an app-only setting.
- **401 means the token is dead**, not that the request was wrong. The client raises
  `PermissionError("401 TOKEN DEAD")` so the failure is unambiguous.
- The account is resolved with `/me` at publish time; a mismatch with the configured
  handle is a warning, not a silent post to the wrong account.
- Caption is capped at 2200 characters, alt text at 1000.

### Token lifecycle

Long-lived Instagram-Login tokens last ~60 days. `refresh_token()` rotates it and, when a
GitHub PAT is available, writes the new value back into the repository secret so the
scheduled workflow keeps working. **If the workflow starts returning 401, this is the
first thing to check.**

### Failure signatures

| message | meaning |
|---|---|
| `401 TOKEN DEAD` | token expired or revoked — refresh or re-issue |
| `container create failed 400 …` | caption/video rejected; read the body |
| `container never reached FINISHED` | the MP4 is malformed or too large |
| `media_publish failed` | container expired (they are short-lived) — re-create |

---

# 6. Temporary media hosting — the public URL for Instagram

**Purpose:** give Instagram a public HTTPS URL when the hosted-URL route is needed.
**Services:** Litterbox (catbox) primary, Uguu fallback
**Auth:** none
**Client:** `src/upload_host.py`

| service | endpoint | params | TTL |
|---|---|---|---|
| Litterbox | `POST https://litterbox.catbox.moe/resources/internals/api.php` | `reqtype=fileupload`, `time=1h|3h|12h|24h|72h`, `fileToUpload` | chosen TTL |
| Uguu | `POST https://uguu.se/upload` | multipart `files[]` | ~3 h |

Both return a plain-text public URL. The URL is only needed long enough for Instagram to
fetch the file (seconds to minutes), so a 1-hour TTL is enough; the code asks for the
shortest that covers the publish.

**Failure signature:** no URL returned → the publish falls back to the resumable upload;
if both routes fail the run stops before creating a container.

---

# 7. Telegram Bot API — review delivery

**Purpose:** send the finished preview to the review channel.
**Base:** `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>`
**Auth:** the token is in the path
**Client:** `src/alerts.py`

| method | path | body |
|---|---|---|
| POST | `/sendVideo` | `chat_id`, `video` (multipart or URL), `caption` (≤1024 chars), `parse_mode=HTML`, `supports_streaming=true` |
| POST | `/sendMessage` | `chat_id`, `text`, `parse_mode=HTML`, `disable_web_page_preview=true` |
| POST | `/sendDocument` | `chat_id`, `document` (the manifest, when asked) |

Limits that matter: a bot can upload 50 MB; the reels are 1–8 MB, well inside it. Caption
text over 1024 characters is truncated by Telegram, so the message is built short (format,
topic, first two lines, lane, scores).

**Failure signature:** delivery is best-effort — a Telegram failure is logged and the run
still completes and publishes. Delivery must never be able to block a post.

### Smoke test

```bash
set -a; . ~/.beast-secrets/telegram.env; set +a
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"
```

---

# 8. GitHub API — state commits and secret rotation

**Purpose:** the workflow commits `data/` back to `main`; the publisher can rotate the IG
token secret.
**Base:** `https://api.github.com`
**Auth:** a fine-grained PAT with Contents: read/write and Secrets: write
**Client:** the workflow (`git` over HTTPS) and `src/publish.py` (secrets API)

### Endpoints used

| method | path | purpose |
|---|---|---|
| GET | `/repos/{repo}/actions/secrets/public-key` | fetch the sealed-box public key |
| PUT | `/repos/{repo}/actions/secrets/{name}` | write an encrypted secret |
| POST | `/repos/{repo}/releases` | create a release (reference hosting) |
| POST | `https://uploads.github.com/repos/{repo}/releases/{id}/assets` | upload an asset |

Secret values must be encrypted with libsodium sealed boxes (`PyNaCl`) against the
repository public key; plaintext PUT is rejected.

### The PAT

It lives in `~/.git-credentials` in the form
`https://x-access-token:<PAT>@github.com`. The workflow uses the built-in `GITHUB_TOKEN`
for its own commits; the PAT is only needed from this machine.

**Scope required:** `Contents: Read and write` on `websitecage-hub/beast-engine`, plus
`Secrets: Read and write` if token rotation is used.

---

# 9. Apify — research only (not in the pipeline)

**Purpose:** scrape creator reels to find outliers for the reference library.
**Base:** `https://api.apify.com/v2`
**Auth:** `APIFY_TOKEN` in `~/.beast-secrets/apify.env`
**Client:** `research/scrape_reels.py`

```
POST /acts/apify~instagram-reel-scraper/run-sync-get-dataset-items?token=<TOKEN>
body: {"username": ["lifeliftman"], "resultsLimit": 12,
       "includeTranscript": false, "includeDownloadedVideo": false}
```

Returns dataset items with `videoUrl`, `videoViewCount`, `likesCount`, `commentsCount`.
The hashtag scraper was verified to return images/carousels only, which is why discovery
goes through creators rather than hashtags.

**This is not called by the engine.** A missing or exhausted token cannot affect a run.

---

# 10. Gemini — research only (not in the pipeline)

**Purpose:** frame description / OCR cross-checks during format research.
**Auth:** `GEMINI_API_KEY` in `~/.beast-secrets/vision.env`
**Used by:** the research scripts, not `src/`.

The pipeline's own OCR is **local** (`rapidocr_onnxruntime`, see §11) — no API, no key, no
per-frame cost, and it works when the network does not.

---

# 11. Local tools that are not services (but matter)

| tool | why it is here |
|---|---|
| `ffmpeg` | probing, HLS muxing, render, QA frame extraction. Resolved by `config.resolve_ffmpeg()`: `BEAST_FFMPEG` → `PATH` → the pip-bundled `imageio_ffmpeg` binary. |
| `rapidocr_onnxruntime` | the on-screen-text gate. Fails **closed**: no engine means "cannot prove text-free", which is a rejection. |
| HLS downloader | pure-Python segment fetch. ffmpeg's HLS reader segfaults on this host (rc -11, empty stderr) and silently failed nearly every clip. |
| HyperFrames | HTML/CSS → MP4 renderer used by the two templates. |

---

# 12. Key inventory (values in `~/.beast-secrets/`)

| # | key | value / location | scope | expires |
|---|---|---|---|---|
| 1 | `IG_ACCESS_TOKEN` | `~/.beast-secrets/ig.env` | Instagram content publish + insights | ~60 days, refreshable |
| 2 | `IG_USER_ID` | `~/.beast-secrets/ig.env` | identifies the IG professional account | static |
| 3 | `TELEGRAM_BOT_TOKEN` | `~/.beast-secrets/telegram.env` | bot API for the review channel | static |
| 4 | `TELEGRAM_CHAT_ID` | `~/.beast-secrets/telegram.env` | the review channel | static |
| 5 | GitHub PAT | `~/.git-credentials` | repo contents + secrets | user-defined |
| 6 | `APIFY_TOKEN` | `~/.beast-secrets/apify.env` | research scraping | account-bound |
| 7 | `GEMINI_API_KEY` | `~/.beast-secrets/vision.env` | research vision | account-bound |
| 8 | temp-host | none — Litterbox/Uguu need no key | — | — |
| 9 | Pinterest service | none — public | — | — |
| 10 | trending audio | none required | — | — |
| 11 | Meta bridge | none — public | — | — |
| 12 | DeepSeek bridge | none — public | — | — |

The three services with no key are the reason the engine can run end-to-end from a clean
machine with only the Instagram and Telegram credentials.

---

# 13. Health check, all services at once

```bash
for u in \
  "https://pinterest-api-inyg.onrender.com/health" \
  "https://audi0-scraper.onrender.com/v1/trending?limit=1" \
  "https://meta-api-chat-h326.onrender.com/health" \
  "https://deepseek-web-bridge.onrender.com/v1/models" ; do
  printf '%-58s ' "$u"
  curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' --max-time 60 "$u"
done
```

Expected: `200` from Pinterest, Meta and DeepSeek; the trending call returns `200` with
`status: ready` or `processing`, both healthy.

An `Instagram` check needs the token:

```bash
set -a; . ~/.beast-secrets/ig.env; set +a
curl -s "https://graph.instagram.com/v23.0/me?fields=id,username&access_token=$IG_ACCESS_TOKEN"
```

---

# 14. Failure signatures, by service

| where it shows | service | real cause |
|---|---|---|
| `no candidate survived the prefilters` | Pinterest | gate rejections — the causes list is the answer |
| `the Pinterest search returned no results for any lane` | Pinterest | cold/redeploying, or serving a surface without videos |
| audio stage degraded, `keywords_tried` all zero rows | trending | scrape window empty for that word |
| `writer attempt N failed` + DeepSeek used | Meta bridge | cold start or 5xx |
| `critic model unavailable` | DeepSeek bridge | bridge down; run fails closed |
| `401 TOKEN DEAD` | Instagram | token expired — refresh first |
| `container never reached FINISHED` | Instagram | bad MP4 or oversized upload |
| no public URL | hosting | both Litterbox and Uguu refused; publish stops before a container |
| delivery failed but run succeeded | Telegram | expected: delivery is best-effort |