# 🐺 Unleash The Beast — BEAST ENGINE

> **📌 AGENT: read [`HANDOFF.md`](HANDOFF.md) FIRST.** It is the single canonical handoff for
> this project and the only source of truth about its current state. Everything in it was
> verified by running commands on the machine — believe it over any other notes, older
> branch, worktree, or README claim. Verify it still holds with
> `python3 tmp/verify_handoff.py` (must print `32/32`); if that fails, update the handoff.

A fully autonomous Instagram Reels engine. Public repo = unlimited free GitHub Actions
minutes. Jobs are the compute; JSON files in `data/` are the database and the brain.
Every run reads state, does its job, and commits updated state back.

```
        ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
        │   CREATE     │   │   MEASURE    │   │    LEARN     │
        │ 13:30/15:00/ │   │ 04:00/10:00/ │   │  Sunday 18:00│
        │ 16:30 UTC    │   │ 16:00 UTC    │   │              │
        └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
               │                  │                  │
   generate (LLM) ─┐              │                  │
   background ─────┤              │                  │
   trending audio ─┤         harvest insights   decay + shrinkage
   beat-synced     │         score posts        weights, hours,
   build + QA gate │         prune memory       experiments
   publish (IG) ───┘              │                  │
               │                  │                  │
               └──────────────────┴──────────────────┘
                        data/*.json  (the brain)
```

- **CREATE** — generates 10 candidate concepts with an LLM, judges them, dedups against
  everything already posted, picks a background and a trending-informed free-license track,
  builds a beat-synced 1080×1920 reel (Anton text cards, loudnorm −16 LUFS), runs a hard
  QA gate, then publishes through the Instagram Graph API resumable-upload flow.
- **MEASURE** — pulls insights 24h–7d after each post and scores it:
  `(3·shares + 2·saved + 1.5·comments + likes) / reach`.
- **LEARN** — every Sunday, decays old posts (`w = 0.5^(age/28)`), shrinks each attribute
  toward the global mean (`adj = (Σw·s + 5G)/(Σw + 5)`), reweights the strategy, learns the
  best posting hour, enqueues experiments, and writes `data/REPORT.md`.
- **HEALTH** — daily service pings, warms the trending cache, checks/rotates the IG token.

---

## Secrets

If `IG_ACCESS_TOKEN` and `IG_USER_ID` were already set for you, skip this. Otherwise:
**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Powers |
|---|---|---|
| `IG_ACCESS_TOKEN` | yes | publishing, insights, refresh |
| `IG_USER_ID` | yes | fallback Instagram id only (runtime `/me` wins) |
| `TELEGRAM_BOT_TOKEN` | optional | alerts + daily success ping |
| `TELEGRAM_CHAT_ID` | optional | alerts + daily success ping |

`GITHUB_TOKEN` is provided automatically by Actions (used for the Issue fallback and
secret rotation). The Meta LLM, Pinterest and the audio service need **no** keys.

## Ignition

**Actions tab → `create` → Run workflow.** The first dispatch is the first post.
During warmup (8 days after build) the machine posts every *other* day, then daily.
Cron fires three slots a day (13:30 / 15:00 / 16:30 UTC) but only the learned slot
actually posts — that is how hour-learning works on a static schedule.

## How publishing actually works (important)

The token route in use is `graph.instagram.com` (Instagram-Login): `POST /{ig-id}/media`
there **only accepts `video_url`** — it has no resumable-upload support. The resumable
flow (`upload_type=resumable` + `rupload.instagram.com`) exists only on the
Facebook-Login `graph.facebook.com` route.

So the engine publishes like this:

1. build the reel locally on the runner,
2. park the MP4 on an anonymous public host (`litterbox.catbox.moe`, 72h, fallback `uguu.se`),
3. hand Instagram the URL as `video_url`, poll `status_code` until `FINISHED`,
4. `POST /{ig-id}/media_publish` → media id.

`src/publish.py` still implements the full resumable flow and falls back to it
automatically if the hosted-URL route ever fails. Container `video_url` fetching is
time-sensitive, so the container is created immediately after upload.

## Music is fully automatic

The trending engine picks the day's sound direction from
`/v1/trending/self-improvement`; the engine then resolves a free-license direct MP3
(Pixabay Music first, Internet Archive fallback) and acquires it through your audio
service, then beat-syncs the reel to it. Nobody touches it.

Pixabay now serves a Cloudflare 403 to datacenter IPs, so in practice the Internet
Archive branch — searched with an instrument/genre hint derived from the mood
(`dark phonk beat` → `phonk`) — is the one that lands tracks like
`…PHONK TYPE BEAT.mp3`. Two safety nets still apply: the engine can fetch an
already-public MP3 directly, and the last resort is a synthesized dark ambient drone.

Optional: drop owned tracks into `assets/audio/` and register them in
`assets/audio/manifest.json` (`[{"file","moods":[...],"energy":0.8}]`) — they become the
fallback library tier.

## Steering (30 seconds a week, optional)

- `data/trending_styles.json` — plain-English vibe of what's trending; appended to the
  music search query.
- `data/DIRECTIVES.md` — e.g. `this week focus on loneliness`. Read by the content brain.

## Reading the brain

`data/REPORT.md` regenerates every Sunday: weekly post count, weighted mean score,
per-attribute tables (value | adj | n), top 5 hooks with their DNA, exploit vs explore
means, trending-aligned vs not, and next week's experiments.

## Pause / kill switch

Create `data/PAUSE` (any content) to stop creating and stale-alerts. Delete it to resume.

## Token death recovery

If you get a **"TOKEN DEAD"** alert: open `developers.facebook.com` → your app →
**Instagram** → **API setup with Instagram login** → **Generate token** → update the
`IG_ACCESS_TOKEN` repository secret. The health job refreshes and rotates the secret
automatically while more than 21 days remain.

## Notes

- One reel per day, enforced by idempotency (`last_post_date`), even if cron fires 3 times.
- No `pull_request` triggers anywhere; all workflows hold `contents: write, issues: write`.
- GitHub disables schedules after 60 days of repo inactivity — impossible here thanks to
  the daily state commits. If it ever happens, re-enable in the Actions tab.
- The reel's soundtrack is baked into the MP4 by design (API reels carry original audio only).
