# The two cloned formats (preview-only)

Two measured Instagram reel formats, integrated as **data-only** variants of
source-immutable HyperFrames templates. They are registered but **not eligible**:
nothing schedules them, and publishing stays off.

| | A | B |
|---|---|---|
| format id | `caption_list_title_v1` | `social_moment_loop_v1` |
| duration | 12.2 s | 7.0 s |
| canvas | 1080×1920 @ 30 fps | 1080×1920 @ 30 fps |
| structure | one continuous shot; 4-line white Montserrat 700 title at 98 px with a 7 px `#0b0b0c` outline, block centre ≈43 %, visible from frame 0, no scrim, no entrance animation | one continuous POV shot; one static lowercase Inter 700 block in `#f2ebb0` at 76 px, soft shadow, band 30–61 %, visible from frame 0 |
| copy lives in | the title (4 lines max) + the caption (the promised list) | the on-screen block: hook lines then payoff lines, max 8 lines |
| luma / sat target | 105 ±14 / 14.2 ±5 | 78.6 ±20 / 8.34 ±4 |
| grade limits | brightness 0.85–1.32, saturate 0.60–1.45 | brightness 0.80–1.45, saturate 0.60–1.60 |
| template hash (index.html) | `4b6ebd9d470e02060612e6c21a7ef02a80ece68f011f6442be04a5fe1b65f487` | `9d75c1ad71541a59681b97b1d05e97533299c9c0a4f1f722cfda65ca2a25b673` |

Sources: `~/beast-templates/sx8gyu/formats/*/template`, copied byte-for-byte into
`templates/<format_id>/`. The copy is verified against the registry hash on every
render; the source workspace is never written to.

## How a variant is produced

```
payload (JSON, validated)
   └─ templates/<fmt>/  --copy-->  $BEAST_SCRATCH/<run_id>/<fmt>/
        ├─ assets/media/bg.mp4   replaced with the chosen background
        ├─ assets/audio/bgm.m4a  replaced with the chosen track
        ├─ index.html            CONTENT block + one --bg-filter style injected
        └─ npx hyperframes@0.8.212 render -q delivery --crf 18 --fps 30
```

Only two things are injected into the copy:

1. the `const CONTENT = {…}` data block (JSON-encoded; payload strings are
   validated and rejected if they contain `<`, `>`, `{`, `}` or `javascript:`);
2. one CSS custom property, `--bg-filter: brightness() saturate() contrast()`,
   bounded by the format's `grade_limits`.

There is no per-post HTML/CSS editing, and the model cannot reach the DOM.

## Payload contract (`src/formats/template_payloads.py`, schema 1.0)

Both formats require `topic_id`, `caption`, `background_query`, `audio`
(`source`, `rights` = `unknown|preview_only|cleared`), and `provenance`
(`source`, `usage_clearance`).

A additionally requires `headline`, `titleLines` (≤4, ≤34 chars each) and
`list_items`; **the headline's number must equal the list length exactly**, and
every list item must appear in the caption. A mismatch is an error — the type is
never shrunk to hide it.

B additionally requires `hookLines`, `payoffLines` (≤8 lines total, ≤42 chars),
and `loop_hypothesis`.

`rights="cleared"` is refused unless `rights_evidence` is present: a download is
not a licence, and a positive TikTok commercial flag is not Instagram
cross-platform clearance.

## Grading and measurement

The background is measured with `ffmpeg signalstats` (mean `YAVG`/`SATAVG` over
sampled frames), a bounded grade is computed from the measured targets, and the
**rendered output** is measured again. Drift is reported as
`yavg_delta`/`sat_delta`; a stretch pick (one needing a grade outside the
declared limits) is reported as `natural_fit: false` rather than passed off as a
normal pick.

## QA

`src/render_gate.py` is the gate: 1080×1920, square pixels, 30 fps, duration
inside the format's window, valid `moov`, full decode, audio present. A file that
fails is `needs_review`, never `rendered`.

## Manifest and ledger

`src/formats/run_manifest.py` writes `data/template_manifest.json`. Each entry
records the run id, template hash, payload, background/track (path, sha256,
stable id, source, creator, clearance), grade, measurements, QA, output hash and
delivery outcome.

Freshness is **derived** from entries with `status in (rendered, delivered)` and
`qa_ok: true`. A killed run therefore consumes nothing, and re-rendering a run
releases its own previous reservation first. This is the fix for the factory's
ledger bug, where a discarded render still burned a background, a track and a
piece of copy.

## Toolchain

Requires Node 22+ and the pinned `hyperframes@0.8.212` CLI, plus a headless
Chrome. On this box Node lives off-PATH at
`/usr/local/nvm/versions/node/v24.22.1/bin` (also `~/.local/node/bin`), the CLI
is fetched by `npx`, and Chrome was installed by
`npx hyperframes@0.8.212 browser ensure` (~261 MB under
`~/.cache/hyperframes/`). `ffprobe` is not on PATH here; a static 7.0.2 build
lives at `/tmp/beast-run2/bin/ffprobe` for this run.

If the toolchain is not runnable, `render_project()` raises
`TemplateUnavailable`. It never returns a path to a file it did not produce.

## Reproduce

```bash
export PATH="/usr/local/nvm/versions/node/v24.22.1/bin:$PATH"
export BEAST_SCRATCH=/tmp/beast-render
python3 -m pytest tests/test_templates.py tests/test_audio_contract.py -q
python3 tests/_template_preview.py           # both formats, fixture media
```

## Rights

All bundled and downloaded media is `usage_clearance=unknown` /
`rights=preview_only`. **Preview only. Nothing here is publication-cleared, and
`short_publish_enabled` stays `false`.**