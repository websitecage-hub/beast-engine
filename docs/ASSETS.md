# Asset Intelligence (footage, sound, pairing)

Code: `src/formats/asset_intent.py`, `asset_probe.py`, `asset_scoring.py`,
`asset_ledger.py`, `asset_broker.py`. Runner: `src/run_preview.py`.
Spec: *BEAST ENGINE — Asset Intelligence & Acquisition Pipeline v1*.

Core principle: **semantic relevance first, emotional tone second, aesthetic
polish third.** A beautiful clip that does not belong to the story is the wrong clip.

## Pipeline per reel

1. **AssetIntent** (`asset_intent.compile_intent`) — compiled from the *final
   approved copy*: the literal scene, entities, action, setting, time of day,
   camera perspective, motion intent, valence/arousal/tension/warmth/humour,
   text-safe zone, negative visuals, plus an audio intent. Validated: bounded
   numeric scales, no medical framing, every query must name something visible.
   Mood slogans ("dark aesthetic", "motivational", "cinematic", "mindset") are
   **rejected by validation** and never searched.
2. **Three bounded lanes** — literal -> adjacent -> emotional, each clipped to 55
   characters (the deployed `/search/videos` answers 422 above ~60). Lanes are
   deduplicated, and a repeated query cannot shift later queries into the wrong lane.
3. **Search** — `/search/videos`, `page_size=25`, results deduplicated by pin id
   and media url, each result remembering the lane and query that found it.
4. **Metadata prefilters** (`asset_scoring.hard_reject`) — no bandwidth is spent on
   a candidate that cannot win: promoted pins, advertising CTAs (shop now, link in
   bio, subscribe, % off...), branded source domains, non-video, wrong shape. Soft
   signals (links in the description, verified creators) are recorded, not hidden.
5. **Bounded downloads** — at most 6 candidates, 12 MB each
   (`pinterest.download(..., max_bytes=)`), so a CI runner is never filled. The
   downloaded file is re-hashed and rejected if it is a duplicate of one used
   inside the no-repeat window.
6. **Local measurement** (`asset_probe.analyze`) — probe, luma/saturation with the
   same implementation as the style profile, motion profile (mean frame difference,
   max jump, hard scene-cut count), text-safe busyness in the template's band, and
   burned-in-text suspicion from persistent high-edge bands and uniform overlay bars.
7. **Candidate score, 100 points** — semantic scene fit 30, emotional fit 15,
   text-safe composition 20, motion compatibility 10, format profile fit 10,
   technical quality 10, cleanliness/provenance 5. Minimum **75**. Every dimension
   stores a number *and* the evidence drawn from the measurement.
8. **Audio** — trend keywords from the audio intent (slogans dropped), the live
   `/v1/trending` async contract (202 -> `poll_with` -> `status=ready`, rows under
   `data`), immediate download of the signed `play_link`, decode, normalise to
   about -20 LUFS, fingerprint, then a 100-point score (semantic/emotional fit 30,
   energy/tempo 20, texture 15, rhythm 10, loudness 10, trend 5, freshness 5,
   duration 5). A track that cannot be measured fails closed — it never becomes
   "the first unused one".
9. **Pair-fit, 100 points** — text-to-scene 35, combined emotional interpretation
   25, text legibility 15, pacing 10, tone coherence or declared contrast 10,
   novelty 5, with a written rationale stored in the manifest.

## Fail-closed rules

* An unmeasured property scores **0** and forces `needs_human_review`. It can
  never silently become a pass.
* The minimum score is **never lowered** to make a run finish. If nothing reaches
  75, the best candidate is returned marked `passes_minimum: false` +
  `needs_human_review`, and the run's status is `needs_review`.
* No unresolved clip is ever replaced with generic filler: the run stops with the
  rejection list instead.
* Grade limits are fixed. A clip that cannot be brought into the visual band is
  reported as out of band, never tolerated by widening the limits.

## Freshness (`asset_ledger.py`)

Found != used. Searching or downloading never consumes an asset. The chosen clip
and track are **reserved**, and committed only after a valid artifact is delivered;
a run that dies anywhere releases them. The ledger is synced from the delivered
manifest at the start of every acquisition, is written atomically (temp file +
`os.replace`), and a corrupt file is treated as "nothing is known" rather than
"everything is reusable". No-repeat window: 30 days, separate ledgers for visual
assets and audio, configurable via `BEAST_LEDGER`.

## Environment (GitHub Actions)

| variable | purpose |
|---|---|
| `BEAST_SCRATCH` | render + working files (runner temp) |
| `BEAST_ASSET_CACHE` | candidate downloads (runner temp) |
| `BEAST_LEDGER` | durable freshness state, committed back with `data/` |
| `BEAST_WRITER_TIMEOUT` | writer call budget in seconds |

`.github/workflows/preview.yml` is `workflow_dispatch` only — no schedule — and it
refuses to start if `short_publish_enabled` is true.
