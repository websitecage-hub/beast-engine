# Research Audit — beast-engine

Generated: 2026-10-17T12:44:26.488576+00:00

This audit is produced by `src/research/report.py` from whatever is actually on disk. It does not download anything and does not spend credits. Observed facts and hypotheses are kept in separate sections.

## 1. Artefacts audited (observed)

| file | records parsed |
|---|---|
| `data/research/AvPD_top_year.xml` | 0 |
| `data/research/avpd_top_year.xml` | 0 |
| `data/research/introvert_top_year.xml` | 0 |
| `data/research/lonely_top_year.xml` | 25 |
| `data/research/sa_new.xml` | 0 |
| `data/research/sa_top_all.xml` | 25 |
| `data/research/sa_top_year.xml` | 25 |
| `data/research/socialskills_top_year.xml` | 0 |
| `data/research/sw_top.xml` | 0 |
| `data/research/corpus.json` | 75 |

Total records: **150** ({'reddit': 150}).

## 2. What the local corpus actually contains

- Languages: {'en': 108, 'unknown': 42}
- Subreddits: {'lonely': 50, 'socialanxiety': 100}
- Duplicate groups (cleaned-text collision): **43**
- Performance data present (views/baseline): **False**

### Field coverage (reel schema)

| field | present | missing | present % |
|---|---|---|---|
| platform | 150 | 0 | 100.0% |
| canonical_url | 75 | 75 | 50.0% |
| creator_handle | 0 | 150 | 0.0% |
| content_id | 150 | 0 | 100.0% |
| posted_at | 75 | 75 | 50.0% |
| captured_at | 150 | 0 | 100.0% |
| views | 0 | 150 | 0.0% |
| likes | 0 | 150 | 0.0% |
| comments | 0 | 150 | 0.0% |
| shares | 0 | 150 | 0.0% |
| saves | 0 | 150 | 0.0% |
| reach | 0 | 150 | 0.0% |
| followers | 0 | 150 | 0.0% |
| creator_baseline_views | 0 | 150 | 0.0% |
| outlier_multiple | 0 | 150 | 0.0% |
| duration_s | 0 | 150 | 0.0% |
| language | 150 | 0 | 100.0% |
| niche | 150 | 0 | 100.0% |
| hook_text | 0 | 150 | 0.0% |
| format_family | 0 | 150 | 0.0% |

## 3. The reported 346 + 300 reel dataset and the eight vidIQ analyses

**Observed:** no raw file for the reported *346 hashtag Reels* or *300 Reels across 20 accounts* exists in this environment. No `videos.json`, no Apify export and no vidIQ export are present. The only structured performance-shaped artefact referenced anywhere is the plan summary in the owner's notes, which reports derived numbers (median length 10s, 74% ≤15s, share rates 4–13%, per-account medians) and is **not** independently verifiable here.

**Consequence:** the outlier table below is empty because the corpus has no views/baseline fields. Building it would require re-collection, which Section 7 (spend) forbids until a specific gap is identified.

### Outliers and same-creator controls (from local performance data)

_(none present in the local corpus)_

### Same-creator controls

_(none present in the local corpus)_

## 4. Third-party visual observations (vidIQ set, observational)

These eight are **selected public outliers** — survivorship-biased, third-party view figures, not verified Insights. Listed as OBSERVATIONS with the HYPOTHESIS each suggests; neither is a fact about our account.

| handle | reported | observed structure | hypothesis |
|---|---|---|---|
| @rock.s.w | 964K / 31x | ~6.7s POV walk, static line, comic final clause, seamless loop | specific social truth + punchline can beat a generic paragraph, even with minimal editing. |
| @lifeliftman | 2.1M / 143x | ~12s atmospheric shot, static curiosity hook; promised list deferred to caption | caption-led curiosity may work for this creator; test whether it earns watch, sends and profile actions for our account. |
| @sam_socialanxiety | 796K / 33x | ~83s real phone-call challenge, tension, real interaction, emotional resolution | real stakes create a payoff an automated text card cannot fake; use as story-structure evidence, not a directive to fabricate experiences. |
| @sadiealdis | 1.3M / 17x | ~35s direct-to-camera list with live delivery and phrase-synced captions | identity framing plus recognizable specifics can hold attention; human presence is a dependency. |
| @_jeffmarr | 1.2M / 6.8x | ~96s reflective voiceover, night-driving visuals, kinetic captions, open question | motion can come from text timing and narrative rather than many cuts; long narration only works when the material earns it. |
| @tamaniaaa | 836K / 154x | ~72s casual in-car book reading, repeated daily motif accumulating to insight | a tangible prop and conversational delivery can create trust; do not copy the passage or imply endorsement. |
| @pedronfrey | 4.6M / 318x | ~26s ten-item countdown, persistent title, fast captions, emotional turn in final item | progress indicators and escalation may increase completion; needs item-level timeline support. |
| @huntrex_ | 970K / 27x | ~19s multi-scene cinematic montage, short phrases synced to voiceover, contrast reframe | visual progression can help a short idea feel like a story; requires semantic b-roll matching. |

**Structural read (hypothesis, not proof):** a *creative mechanic* — a specific, sendable punchline — separates winners from a dense, generic, all-at-once paragraph. This is exactly what v3 (two-beat short) tests against v2 (paced reveal) as a control.

## 5. Unsupported claims (kept out of the engine)

- No competitor sales/conversion numbers exist in the corpus; none are inferred. Sales evidence is `unknown` unless measured on our own account.
- The 8 examples are not a controlled experiment and cannot attribute causality to any single production choice.

## 6. Pattern acceptance gate (Section 5.4)

No pattern from this evidence set is promoted. The set fails the gate as written: it is not ≥3 examples from ≥2 creators *with same-creator controls measured locally*, and it is not reproducible from the raw data available here. The video templates T1–T10 already encoded from the owner's 2026-10-15/16 run remain **hypotheses to test**, not proven facts.

## 7. Remaining research questions (before any paid action)

1. Do v3 two-beat shorts beat v2 paced reels on **share/send rate per reach** on *this* account? (first-party; needs publishing approval)
2. Does a plain-text (arm C) short hold attention competitively against footage (arm A) and still (arm B)? (in-repo render tests + first-party)
3. Is a caption-led curiosity hook (as in one third-party example) worth a test, or does it only work with an established audience? (needs first-party)
4. What is the local corpus **situation coverage** gap, if any, versus the v3 topic list? (computable now — see below)

## 8. Spend decision

**No paid research is authorized by this audit.** vidIQ balance is reported at 45 credits; keep ≥25 (Section 7 / 16). Apify pricing must be verified live before any run. The local corpus is qualitative Reddit text and cannot be extended into performance evidence by spending — that requires this account's own mature Insights, which is a publishing-phase dependency, not a scraping one.

## 9. Cinematic arm decision (Step 4 gate)

**Not justified yet.** The only cinematic evidence is third-party, uncontrolled, and depends on human presence / real footage (examples 2–8) or authentic props, which the current rights-cleared pipeline cannot supply. Per Section 16 decision 3, no `build_cinematic.py` is added; Format C is registered in the format registry as **not eligible** and stays declarative until first-party evidence justifies the build.

