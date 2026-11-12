# Two-format reference analysis — measured, with the numbers

This is the analysis document. It reports what was **measured** on the 14 curated
reference reels, what the measurements imply about each format's writing mechanics, and
where the engine currently diverges from them.

It supersedes the earlier narrative "Creative Analysis v1" note: everything below is
backed by a frame measurement or an OCR line on a clip that exists on disk
(`~/beast-engine/reference-videos/`), not by recollection of a summary.

---

## 1. Method, and what it can and cannot prove

**Sample:** the 14 references in `BEAST_ENGINE_Two_Format_Viral_Reference_Library_v2.csv`
(3 Format A, 11 Format B), downloaded as MP4 at 1080×1920.

**Per clip, measured programmatically:**

- container geometry and duration (ffmpeg probe)
- 6 frames sampled at 0.15 s, 0.8 s, 1.6 s, 45 %, 75 % and near-end
- mean luma (0–255) and mean saturation (0–100) per frame, averaged
- **motion**: mean absolute frame difference between consecutive samples, normalised
- **text band**: the brightest large horizontal block — its top/bottom as % of frame
  height, its height in px, and the mean RGB of its brightest pixels
- **OCR** (RapidOCR) on the first four sampled frames: exact text, per-line top
  position and pixel height

**What this can prove:** where the words sit, how big they are, what colour they are,
whether they are present at frame 0, how much the footage moves, how bright and saturated
it is, and exactly what the on-screen copy says.

**What it cannot prove:** that any element caused the reach. The view counts are
third-party snapshots (vidIQ, UGC Growth Engineer), not Instagram Insights. The sample is
14 clips. Captions were not captured for most references, so the "list delivered in the
caption" reading rests on the library's recorded notes plus the on-screen text being only
a promise (measured: 14–15 words on screen in all three Format A clips, no list).

---

## 2. The 14 references, measured

| # | format | creator | dur | luma | sat | motion | text at 0 s | text band (top–bottom) | text colour | on-screen text (OCR) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | A | @darewithdrea | 7.10 s | 111.1 | 34.9 | 0.128 | yes | 40.7–54.6 % | #F0AE9F | "10 things you think are normal but are actually signs of high functioning anxiety" |
| 2 | A | @lifeliftman | 12.13 s | 75.7 | 33.8 | **0.012** | yes | 27.7–36.1 % | #F4F1F2 | "You can spot a man who grew up emotionally alone by these 8 habits" |
| 3 | A | @tamannah_a_wish | 7.67 s | **16.9** | 13.5 | 0.036 | **no** | — | — | "How to study 12 hours" + "(If you're a lazy student)" + "Read Caption" |
| 4 | B | @colleen.at.home | 6.29 s | 112.1 | 38.7 | 0.083 | yes | 61.6–74.9 % | #DECAB1 | "Corporate millennials after delegating the task instead of just doing it themselves" |
| 5 | B | @dalmatean | 9.13 s | **142.7** | 19.3 | 0.028 | yes | 17.0–30.5 % | #CED4D6 | "Me awkwardly staring at someone i want to be friends with bc idk how to make friends normally" |
| 6 | B | @grishu01 | 7.41 s | 127.5 | 30.2 | 0.131 | yes | 25.5–59.0 % | #E3C4A5 | "i will be too embarrassed to ask for a glass of water but if you're also too ask then i will ask for you…" |
| 7 | B | @jeneral.reels | 7.22 s | 76.7 | 26.9 | **0.282** | yes | 40.6–48.0 % | #ECDBDC | "When im scrolling through our old chats and I accidentally press the CALL button" |
| 8 | B | @lifewfarzy | **27.47 s** | 110.5 | 24.0 | 0.237 | yes | 59.2–63.2 % | #DFDAD0 | "When you overthink every single interaction" |
| 9 | B | @ratul_xyx | 9.64 s | **27.5** | 15.6 | 0.090 | **no** | — | — | "Me after having panic attack at night" |
| 10 | B | @rock.s.w (Ddc5qG7) | 6.06 s | 38.4 | 56.6 | 0.045 | yes | 51.2–79.0 % | **#F5EEA3** | "Some people miss their exes / I just miss my money that I've wasted…" |
| 11 | B | @rock.s.w (Ddc6JmB) | 6.06 s | 25.2 | 81.2 | 0.018 | yes | 50.6–79.6 % | **#F4EBA2** | "how it feels when you see an old picture of the friend group back when everyone was together" |
| 12 | B | @rock.s.w (Ddc6WLa) | 6.06 s | 61.2 | 69.2 | 0.057 | yes | 52.2–80.1 % | **#F6ECA1** | "i will never in my entire life forget the day i fell down the stairs lol" |
| 13 | B | @rock.s.w (Ddwm1XU) | 6.73 s | 63.9 | 22.1 | 0.061 | yes | 39.8–79.0 % | **#F3F1A7** | "i hate when people expect me to open up to them. like i just met you 4 years ago give me some time" |
| 14 | B | @rock.s.w (De4-9BK) | 6.30 s | 25.3 | 40.8 | 0.024 | yes | 49.4–74.5 % | **#F5EFA4** | "sometimes the ache of missing you hits so hard it feels like it's all i'm made of" |

Bold marks the values that contradict a common assumption.

### Aggregate

| | Format A (n=3) | Format B (n=11) |
|---|---|---|
| duration, median | 7.67 s | 6.73 s |
| duration range | 7.10–12.13 s | 6.06–27.47 s |
| words on screen, median | 14 | 18 |
| words on screen, range | 14–15 | 9–56 |
| visible lines, median | 4 | 7 |
| line height, median | 58 px | 86 px |
| mean luma, median | 75.7 | 63.9 |
| mean luma, range | 16.9–111.1 | 25.2–142.7 |
| motion, median | 0.036 | 0.061 |
| text present at frame 0 | 2 of 3 | 9 of 11 |

---

## 3. Format A — the promise, held still

**What the three clips actually do**

All three are a single static statement over one nearly static shot. The measured motion
on @lifeliftman is **0.012** — the second lowest of all 14 — and its luma is 75.7. The
image is doing almost nothing; the sentence is the whole event. That is not an accident of
that clip: @darewithdrea is at 0.128 and @tamannah at 0.036, and none of them cuts.

**The three promise shapes are different, and the engine only writes one of them.**

1. **Identity callout** — "@lifeliftman": *"You can spot a man who grew up emotionally
   alone by these 8 habits."* Second person, a group is named, a number is attached. The
   reader is invited to check someone (or themselves) against it.
2. **Count listicle** — "@darewithdrea": *"10 things you think are normal but are
   actually signs of high functioning anxiety."* The number is the hook and the
   reassurance is inverted ("normal but actually").
3. **How-to with a qualification** — "@tamannah_a_wish": *"How to study 12 hours (If
   you're a lazy student)"*. The parenthetical does the work: it pre-empts the objection
   and names the reader.

All three put the **number or the outcome in the first line** and never show the list
itself: 14–15 words on screen in every case, and the count is a promise about the caption.
That is the format's actual mechanism — **the reel is the advertisement for the caption**,
which is also why the on-screen text can be short enough to read in under two seconds.

**Placement is high.** @lifeliftman sets the block at 27.7–36.1 % of frame height,
@darewithdrea at 40.7–54.6 %. This is above the vertical centre — the block sits in the
upper third, where a thumb resting on the screen does not cover it and where the eye lands
first. Text is **static**: it does not animate on, and it is fully present at frame 0 in
two of three clips (@tamannah fades in within the first second).

**Typography is smaller than Format B.** Median line height 58 px against 86 px. On a
1080-wide canvas that is roughly 5.4 % of frame height per line — the text is legible but
subordinate to the image, which is the opposite of Format B's proportion.

**Colour is not fixed.** @lifeliftman is white (#F4F1F2) on a mid-dark frame (75.7);
@darewithdrea is warm (#F0AE9F) on a bright frame (111.1); @tamannah is white on a very
dark frame (16.9). The rule the data supports is **contrast against the footage**, not a
brand colour — which is exactly what `text_style.for_background()` does.

---

## 4. Format B — the confession, stacked

**The dominant grammar, in 9 of 11 clips:** one second-person or first-person present-tense
thought, broken into 4–8 stacked lines, all visible at once, ending on a turn. No list, no
number, no title.

The turn is the mechanism, and it is measurable in the copy:

| clip | setup | turn |
|---|---|---|
| @rock.s.w (Ddc5qG7) | "Some people miss their exes" | "I just miss my money that I've wasted" |
| @rock.s.w (Ddc6WLa) | "i will never in my entire life forget the day" | "i fell down the stairs lol" |
| @rock.s.w (Ddwm1XU) | "i hate when people expect me to open up to them" | "like i just met you 4 years ago give me some time" |
| @rock.s.w (De4-9BK) | "sometimes the ache of missing you hits so hard" | "it feels like it's all i'm made of" |
| @colleen.at.home | "Corporate millennials after delegating the task" | "instead of just doing it themselves" |
| @dalmatean | "Me awkwardly staring at someone i want to be friends with" | "bc idk how to make friends normally" |

Two structures carry almost all of them: **reversal** ("some people X, I just Y") and
**anticlimax** ("…and then I fell down the stairs lol"). The reversal is the stronger of
the two because the first line can stand alone as a sentiment and the second line
recontextualises it — which is what makes a 6-second loop rewatchable.

**Typography is the format's identity.** Median line height 86 px, and the largest
references run 100–131 px per line: on a 1080×1920 canvas that is **7.7–12 % of frame
height per line**, block occupying roughly 50–80 % of the height. This is display type, not
subtitle type. The block is centred, 5–8 lines, tight leading (the measured top positions
advance ~6–7 % of height per line, i.e. line-height ≈ 1.15–1.2× the glyph height).

**Colour splits by creator, not by format.** All five @rock.s.w clips use the same warm
yellow — measured **#F5EEA3 / #F4EBA2 / #F6ECA1 / #F3F1A7 / #F5EFA4**, a spread of four
RGB points — on dark or mid footage (luma 25–64). The other creators use white or warm
white on brighter footage (#DECAB1 at luma 112, #CED4D6 at luma 143, #ECDBDC at 77). So:
**yellow is a dark-footage treatment**, and it is remarkably consistent where it is used.
The engine's template value is #feffac — within ~10 RGB of the measured cluster.

**Case is a signal.** @rock.s.w writes all lowercase, including "i" — that reads as a
written confession, not a caption. @colleen, @dalmatean, @jeneral and @grishu use sentence
case or mixed case, which reads as a *situational* joke. Both work, but they are different
voices and should not be mixed inside one account.

**Motion is not a rule.** Measured motion ranges from 0.018 (@rock.s.w, nearly still) to
0.282 (@jeneral.reels, restless handheld). Both are in the same format with comparable
reach. The format tolerates a still frame and a busy one; what it does not tolerate is a
frame that competes with the text.

**Luma is not a rule either.** The Format B set runs from 25.2 to **142.7** — @dalmatean
is the brightest clip in the whole sample and still a Format B reference. The "dark
aesthetic" is a tendency of the biggest @rock.s.w clips, not a constraint. Text colour
follows the footage; the footage does not have to be dark.

**Duration is tighter than Format A.** Median 6.73 s, and the cluster of @rock.s.w clips is
**6.06 s** — remarkably consistent. Two outliers (9.13 s and 27.47 s) are longer
narratives; @lifewfarzy at 27.47 s is effectively a different use of the same shell and
should be treated as a control, not a template.

---

## 5. The number system, measured against the engine's templates

| property | measured A | measured B | engine A | engine B |
|---|---|---|---|---|
| canvas | 1080×1920 | 1080×1920 | 1080×1920 | 1080×1920 |
| duration | 7.1–12.1 s | 6.06–9.6 s (cluster 6.06) | 12.2 s | 7.0 s |
| line height | 48–91 px (med 58) | 83–131 px (med 86) | 81 px | 86 px |
| text band | 27–55 % | 40–80 % (one at 17–30 %) | 42–58 % | 51–84 % |
| text colour | white or warm, per footage | yellow #F5EEA3 on dark; white on bright | yellow below 92 luma, white above 122 | #feffac |
| text at frame 0 | 2 of 3 | 9 of 11 | yes | yes |
| visible lines | 4 | 5–8 | 4 | 4–6 |

**Where the engine is faithful:** canvas, frame-0 text, A's duration and line count, the
yellow-on-dark decision, and the block's vertical band in both formats.

**Where it diverges, in order of likely impact:**

1. **B's type is undersized.** Engine 86 px against a reference median of 86 px sounds
   right, but the *reach leaders* in the set run 100–131 px. The engine sits at the
   bottom of the measured range, not the middle.
2. **B's word cap is tight.** Engine caps at 45 words; the measured median is 18 and the
   largest reference is 56. The cap is not the binding constraint — but the engine's
   *line count* (4–6) is lower than the references' 5–8, which changes how the block sits
   in frame.
3. **A has one promise shape, the data has three.** The engine writes "N things you do…"
   (@darewithdrea's shape). The identity callout (@lifeliftman) and the how-to with a
   qualification (@tamannah) are both CORE references and neither is generated.
4. **A's type is oversized relative to its references.** 81 px sits above the measured
   median of 58 px; A's text should be the *smaller* of the two formats, and in the
   references it is.
5. **The engine assumes dark footage for B.** The measured range is 25–143, and the
   brightest clip in the sample is a Format B reference. Nothing should gate on darkness.

---

## 6. What this changes about the writing rules

Grounded in the above, the rules that follow are:

- **A is a promise, not a list.** The reel advertises the caption; the on-screen text
  should carry the number, the identity and the inversion, and stop. 14–16 words.
- **A's three shapes should all be reachable:** identity callout ("You can spot a…"),
  count listicle ("N things you…"), how-to with a qualification ("How to X (if you're
  Y)"). The qualification is where the recognition lives.
- **B is one thought with a turn.** The turn is a reversal or an anticlimax, and it must
  land on the last line so a replay re-reads the first line differently.
- **B's block is 5–8 lines, display size, centred, 50–80 % of height.** Fewer, larger
  lines beat more, smaller ones.
- **Yellow belongs to dark footage; white belongs to bright footage.** #F5EEA3 measured,
  #feffac shipped — close enough, and decided by luma, not by brand.
- **Case is a voice decision.** All-lowercase reads as confession; sentence case reads as
  situation comedy. One account should pick one.

---

## 7. Limits

- 14 clips, 3 of them Format A. Nothing here is a controlled test; it is a description of
  a small curated set.
- View counts are third-party snapshots. The one clip with a directly measured outlier
  multiple in the library is @lifeliftman (143×). Treat reach as a discovery signal.
- OCR produced occasional errors on stylised type ("bcidk how to" for "bc idk how to",
  "someonei want tobe" for "someone i want to be"); the quotes above are corrected only
  where the intended word is unambiguous, and are otherwise verbatim.
- Text band and colour are measured on a 270×480 downsample; positions are accurate to
  about ±0.5 % of height, colours to about ±3 RGB.
- Captions were not captured for most references, so the caption mechanics in the library
  CSV remain recorded notes rather than measurements — including the "list is in the
  caption" reading for Format A, which the on-screen word counts support but do not prove.