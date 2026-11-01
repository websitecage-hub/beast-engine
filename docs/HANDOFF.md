# HANDOFF — BEAST ENGINE, state at 2026-11-01 17:40 UTC

Everything here is written so the next session (or a rebooted VM) can resume without
re-deriving anything. Secrets are **referenced by path, never copied into this repo**
(the repo is public). The files below already exist on the persistent `/home` mount.

---

## 1. Where the work stands

Branch: `feat/two-format-templates` (local commits only — **never pushed**; no GitHub
credential on this machine). Latest: `a9772a9`.

Formats are locked:
- **A** `caption_list_title_v1` — numbered promise on screen, list in the caption, 12.2 s
- **B** `social_moment_loop_v1` — one lowercase social moment, 7 s, loops

Working end to end, preview-only, Telegram delivery, publishing off
(`short_publish_enabled: false`, zero IG posts ever).

Proven clean runs:
- **A** `20261031T185810Z-1d9b66` — `status: rendered`, footage 80/100, audio 100, luma 106
  in band, critic 5.0, ladder 4. Delivered.
- **B** `20261101T165713Z-0d1d73` — `status: rendered`, footage 78/100, audio 100, luma 80.6,
  text colour auto-chosen **`#E0D29B`** because the clip measured 79 (dark). Delivered.

What is already fixed and verified by tests (74 offline tests green):
- OCR gate: local **RapidOCR** (offline, no quota), 3 frames @1080w. A caption = 5+ char
  string, or 4+ chars at ≥3% frame height, or the same string in 2 frames. Short numbers
  (price tags) ignored. Verified: reads "Antique Shopping" on a clip that used to pass.
- Text colour: `src/formats/text_style.py` maps the luma the viewer sees to
  `#E0D29B` (<92) / `#F7F0D2` (92–122) / `#FFFFFF` (>122); stroke 2 px vs 3 px follows.
- Asset selection simplified (owner's instruction): no luma/grade rubric. Hard rejects:
  ads, promoted pins, commerce domains, stock/repost sources. Then look/tag fit (40),
  text-free (30), technical (20), freshness (10). Text is a hard stop, never a deduction.
- Query viability: object fragments ("plate held in", "panel two people") are rejected;
  a query needs a place, or an action in a 4+ word phrase.
- Format B prefers luma < 100 among passing candidates (its references sit near 78).
- Format B template: fill `#E0D29B`, 2 px stroke (measured from rock.s.w, 5.7M views).

## 2. Keys and APIs (paths only)

| what | file | vars | status |
|---|---|---|---|
| Apify (Instagram research) | `~/.beast-secrets/apify.env` | `APIFY_TOKEN` | live, FREE plan |
| Google AI Studio / Gemini | `~/.beast-secrets/vision.env` | `GEMINI_API_KEY` | live; **free tier = 20 req/day**, exhausted by a full research pass |
| Telegram review channel | `~/.beast-secrets/telegram.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | live |
| Instagram (NOT used; publishing off) | `~/.beast-secrets/ig.env` | `IG_ACCESS_TOKEN`, `IG_USER_ID` | unused by design |
| GitHub push | `~/.git-credentials` | — | **empty**; push blocked until a PAT is supplied |
| Deployed services | `data/config.json` | — | meta-api, deepseek bridge, pinterest-api, audi0 |

Load them with `set -a; . ~/.beast-secrets/apify.env; . ~/.beast-secrets/vision.env; set +a`.

Note: the Meta writer is intermittent; `content_factory` falls back to the DeepSeek bridge and
records `writer_vendor` + `independent_critic: false`. Gemini free tier is the binding limit for
vision work — batch requests, and cache every result in the repo so it never needs re-spending.

## 3. Storage — the real constraint

`/home` is **4.8 GB** and is the only persistent disk (`/root` and `/var/tmp` are wiped on
restart; verified: `/root` was empty and recreated at boot).

Rules, now enforced by habit and scripts:
1. **Nothing large is written to `/home`.** Renders, downloads, npm cache, OCR libs, Chrome and
   Gemini binaries all live in `/var/tmp` (currently ~9 GB free).
2. **Knowledge is committed, media is not.** Research output goes to `research/*.json` and
   markdown in the repo (a few hundred KB). Reference videos are deleted after analysis.
3. `scripts/clean_home.sh` — prunes previews (keeps the newest 2 per format), Hermes logs older
   than 14 days, npm/pip caches, and reports the top consumers. Run it when `/home` > 85%.
4. Current: 3.7 GB used, **912 MB free** (81%). Reclaimable on demand: old previews (~10 MB),
   Hermes logs (13 MB), session DB (59 MB).

Already moved off `/home` with symlinks (still functional):
`~/.cache/hyperframes → /var/tmp/beast-cache/hyperframes`,
`~/.gemini/bin → /var/tmp/beast-cache/gemini-bin`.

## 4. Research — what was done, what must be re-run

**Done (with the owner's keys):** Apify scraped 97 reels across 9 creators; outlier multiple
computed vs each creator's median; the top outlier per creator (8 reels) downloaded and
frame-read with Gemini. Measured results:

| creator | views | outlier | measured look |
|---|---|---|---|
| colleen.at.home | 3,370,480 | 1682× | `#F8E7B1`, bold, thick outline, 4 lines, lower |
| rock.s.w | 5,698,481 | 580× | `#E0D29B`, lowercase, regular, no outline, 5 lines, centre |
| dalmatean | 24,876,716 | 444× | white, bold, thick outline, 3 lines, upper |
| darewithdrea | 195,590 | 158× | white, bold, thick outline, 3 lines, lower |
| grishu01 | 405,083 | 128× | no text detected at 0.4/1.0/2.0 s |
| tamannah_a_wish | 7,570,389 | 97× | `#D4AF37` gold on black, 2 lines |
| lifeliftman | 100,126 | 45× | white, bold, thick outline, 2 lines, centre |
| jeneral.reels | 1,515,146 | 8× | white, bold, upper, 4 lines |

**Lost:** the downloaded videos, frames and JSON lived in `/var/tmp/beast-lab` and were wiped by
a restart. Only the table above survives.

**To re-run (full study, ~25–35 min, small Apify spend):**
1. `research/scrape_reels.py` — Apify `instagram-reel-scraper` per handle (the hashtag scraper
   returns images/carousels only; verified). Writes `research/reels.json`.
2. `research/analyse_frames.py` — pick the strongest outlier per creator, download to
   `/var/tmp/beast-lab/refs` (bounded 100 MB), sample 0.5 s + mid, ask Gemini for text, colour,
   weight, outline, position, line count, background, stock/ad judgement, format A/B. Caches
   into `research/frames.json` so quota is never spent twice.
3. `research/code_captions.py` — code all 97 captions: numbered promise, identity anchor,
   private worry, turn/payoff type, word count. Writes `research/captions.json`.
4. `research/REPORT.md` — measured spec per format, and the exact template changes implied.

**Scope note:** the first pass was a pilot (8 reels). It produced the colour/stroke decisions but
no systematic hook/caption coding. That is what step 3 adds.

## 5. Pending fixes (small, queued)

1. **Cap scene queries at ~6 words.** Measured: 3–6 word phrases return 13–25 results; long
   specific ones return 0–1 ("person standing in apartment doorway holding mug" → 1 candidate).
2. **Reject real-estate/tour footage** ("home tour", "open concept", "walkthrough", "listing",
   "apartment tour") — same wrong-but-tidy class as stock.
3. **Require the place to match**, not just any room word: a "doorway" scene must not be
   satisfied by "living room".
4. **Format A's band vs text colour interaction** — when a bright clip is chosen the text goes
   white, which is correct, but A's references are also white on bright; verify after the above.
5. **Push** the branch once a PAT exists.

## 6. How to run (exact commands)

```bash
# OCR libs + ffprobe live in /var/tmp and vanish on restart:
pip install --no-cache-dir --target /var/tmp/pylibs rapidocr-onnxruntime
mkdir -p /var/tmp/bin && cd /var/tmp \
  && curl -sSL -o ff.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
  && tar -xJf ff.tar.xz && cp ffmpeg-*-static/{ffmpeg,ffprobe} /var/tmp/bin/ && chmod +x /var/tmp/bin/ff*

cd ~/beast-engine
export PYTHONPATH=/var/tmp/pylibs \
       PATH="/var/tmp/bin:/usr/local/nvm/versions/node/v24.22.1/bin:$PATH" \
       TMPDIR=/var/tmp/beast-run/tmp BEAST_SCRATCH=/var/tmp/beast-run/v7 \
       npm_config_cache=/var/tmp/npm-cache BEAST_WRITER_TIMEOUT=120
python3 -m src.run_preview --format A     # or B
```

Tests: `python3 -m pytest tests/test_asset_intelligence.py tests/test_preview_pipeline.py tests/test_templates.py -q`
(skip `tests/test_acceptance.py` and `tests/test_v2.py` — they hit the network).

## 7. Known failure modes (all seen in production)

| symptom | cause | fix |
|---|---|---|
| `npm EACCES /opt/work` | `~/.npmrc` cache pointed at the wiped overlay | cache → `/var/tmp/npm-cache` (done) |
| `✗ FFprobe not found` | static ffprobe lived in the wiped overlay | reinstall to `/var/tmp/bin` (above) |
| HyperFrames refuses to render | <0.4 GB free on the output filesystem | render in `/var/tmp`, copy back only the mp4 |
| Gemini 429 `free_tier_requests` | 20 requests/day | local RapidOCR for OCR; cache Gemini results |
| `no candidate survived the prefilters` | thin inventory + strict filters | 10 downloads, 100 MB cap, 2 generalisation rounds; still fail-closed |
| `content generation failed after 3 attempts` | critic rejects real weaknesses (good) | inspect the printed copy; writer retries |

## 8. Last actions, in order

`22e4168` 60 MB cap → `8b80290` OCR + adaptive colour → `1a08eaa` fragment-query rejection +
B darkness preference (B then passed clean, `20261101T165713Z-0d1d73`) → `a9772a9` stock/repost
rejection + wider pool. A's next run was aborted manually mid-render (operator kill, not a code
fault); no A artifact from that run exists.