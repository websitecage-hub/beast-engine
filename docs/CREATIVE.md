# Creative Intelligence (writing brain)

Code: `src/formats/content_factory.py`. Routes: `src/run_preview.py`.
The spec this encodes: *BEAST ENGINE — Creative Intelligence System v1*.

## The six passes

| pass | what happens | where |
|---|---|---|
| 1 | audience-moment card: situation, observable behaviour, private worry, tension | `audience_moment()` |
| 2 | concept slate: >= 3 **different** situations, each mapped to A or B | `audience_moment()` |
| 3 | format writing: A = title + exactly N items; B = one loop block | `_prompt()` |
| 4 | independent critic: 9 scored dimensions + hard fails | `critic_verdict()` |
| 5 | targeted rewrite: the failing dimensions are handed back verbatim | `generate()` |
| 6 | structured payload + critic metadata + evidence in the manifest | `generate()` |

Card and slate run in one model call; the card is written to the manifest as
`content.audience_moment` with `card_compiled_by` / `card_degraded`. A card that
fails validation (fewer than three concepts, a repeated situation, a missing
private worry) falls back to a minimal card and is marked degraded — never passed
off as compiled.

## Deterministic gates (they run before the critic)

`creative_errors()` is pure code. A payload that fails here never spends a critic
call and never reaches a render.

* **Specificity ladder** — `ladder_level()` scores the copy 0-4 from the text:
  a concrete anchor (bus, group chat, phone screen, reply, doorway, counter...),
  a private-meaning marker (replaying, wondering, checking, convincing yourself),
  and a turn (but, still, nobody, already, twice). Level 0-1 is rejected
  mechanically; the level and its reasons are recorded per attempt.
* **Distinct list items** — two items sharing most of their content words are one
  behaviour written twice; the promised count is a contract.
* **One thought (B)** — more than 45 words in the block fails.
* **No clinical vocabulary** — diagnosis, disorder, syndrome, symptom, clinical,
  cure, high-functioning, therapy, mental illness/health, in either format.
* **No repeats** — a reused `topic_id`, or a headline overlapping 60% with a
  recent post's wording, is rejected. The recent list is read from the manifest.

## The critic

Nine dimensions, judged on wording only (the format is assigned, never penalised):
`scene_specificity`, `recognition_authenticity`, `natural_voice`,
`emotional_truth_dignity`, `format_fit`, `distinctiveness` (critical, >= 4 each),
`shareability`, `caption_value`, `product_trust` (>= 3 each), plus a `hard_fail`
field for diagnostic claims, fabricated book content, copied wording,
stigmatising portrayal, count mismatch, filler items, multiple scenes and
manipulative pressure.

`passed` requires no hard fail and both minimums. A JSON parse failure is a
**technical** failure: one protocol retry, then `critic_unparseable` and the run
stops — a dead critic never becomes a pass.

## Known limits (do not overstate)

* Writer and critic can be the same service. The manifest records
  `writer_vendor` and `independent_critic`, which is `false` when the DeepSeek
  bridge wrote the copy.
* `ladder_level()` is a lexical estimate, not a judgement of quality. It catches
  the _broad label_ failure mode; it cannot tell a fresh observation from a tired one.
* Nothing here predicts virality. The reference set is survivorship-biased.
* The ebook bridge is only ever written when verified book material is supplied in
  context; the writer is forbidden from inventing it.
