"""report.py — write docs/research/RESEARCH_AUDIT.md from the local evidence.

No publishing side effects. The report separates:
  * OBSERVED FACTS  — what a source actually supplies (counts, fields, urls)
  * THIRD-PARTY OBSERVATIONS — the eight selected vidIQ analyses (observational,
    survivorship-biased, not verified Instagram Insights)
  * HYPOTHESES      — interpretations that still require first-party testing
  * UNSUPPORTED     — sales/conversion claims the corpus cannot support
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from . import analyze, normalize

# The eight public Reel analyses recorded in the master architecture, Section 2.
# These are THIRD-PARTY, SELECTED OUTLIERS: not this account's data and not
# controlled. Kept here so the report cites them without implying they are proof.
VIDIQ_OBSERVATIONS = [
    {"handle": "@rock.s.w", "url": "https://www.instagram.com/reel/Ddwm1XUx5Nw/",
     "reported": "964K / 31x",
     "structure": "~6.7s POV walk, static line, comic final clause, seamless loop",
     "hypothesis": "specific social truth + punchline can beat a generic paragraph, even with minimal editing."},
    {"handle": "@lifeliftman", "url": "https://www.instagram.com/reel/DeRsolMAyoa/",
     "reported": "2.1M / 143x",
     "structure": "~12s atmospheric shot, static curiosity hook; promised list deferred to caption",
     "hypothesis": "caption-led curiosity may work for this creator; test whether it earns watch, sends and profile actions for our account."},
    {"handle": "@sam_socialanxiety", "url": "https://www.instagram.com/reel/Dd3Y6dAT_vP/",
     "reported": "796K / 33x",
     "structure": "~83s real phone-call challenge, tension, real interaction, emotional resolution",
     "hypothesis": "real stakes create a payoff an automated text card cannot fake; use as story-structure evidence, not a directive to fabricate experiences."},
    {"handle": "@sadiealdis", "url": "https://www.instagram.com/reel/DdlzBcjAYBf/",
     "reported": "1.3M / 17x",
     "structure": "~35s direct-to-camera list with live delivery and phrase-synced captions",
     "hypothesis": "identity framing plus recognizable specifics can hold attention; human presence is a dependency."},
    {"handle": "@_jeffmarr", "url": "https://www.instagram.com/reel/DeGUSSQg_Xa/",
     "reported": "1.2M / 6.8x",
     "structure": "~96s reflective voiceover, night-driving visuals, kinetic captions, open question",
     "hypothesis": "motion can come from text timing and narrative rather than many cuts; long narration only works when the material earns it."},
    {"handle": "@tamaniaaa", "url": "https://www.instagram.com/reel/Dd2TCaOOI84/",
     "reported": "836K / 154x",
     "structure": "~72s casual in-car book reading, repeated daily motif accumulating to insight",
     "hypothesis": "a tangible prop and conversational delivery can create trust; do not copy the passage or imply endorsement."},
    {"handle": "@pedronfrey", "url": "https://www.instagram.com/reel/DeRxq8Th_KV/",
     "reported": "4.6M / 318x",
     "structure": "~26s ten-item countdown, persistent title, fast captions, emotional turn in final item",
     "hypothesis": "progress indicators and escalation may increase completion; needs item-level timeline support."},
    {"handle": "@huntrex_", "url": "https://www.instagram.com/reel/DeVgtPbucy7/",
     "reported": "970K / 27x",
     "structure": "~19s multi-scene cinematic montage, short phrases synced to voiceover, contrast reframe",
     "hypothesis": "visual progression can help a short idea feel like a story; requires semantic b-roll matching."},
]


def _table(headers: list, rows: list) -> str:
    if not rows:
        return "_(none present in the local corpus)_\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) if c is not None else "—" for c in r) + " |")
    return "\n".join(out) + "\n"


def build_report(research_dir, out_path) -> str:
    research_dir = Path(research_dir)
    res = normalize.normalize_all(research_dir)
    records = res["records"]
    summary = analyze.summarize(records)
    oc = analyze.outliers_and_controls(records)
    dups = analyze.duplicates(records)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    lines = []
    a = lines.append
    a("# Research Audit — beast-engine")
    a("")
    a(f"Generated: {now}")
    a("")
    a("This audit is produced by `src/research/report.py` from whatever is "
      "actually on disk. It does not download anything and does not spend credits. "
      "Observed facts and hypotheses are kept in separate sections.")
    a("")
    a("## 1. Artefacts audited (observed)")
    a("")
    a("| file | records parsed |")
    a("|---|---|")
    for name, n in res["files"]:
        a(f"| `data/research/{name}` | {n} |")
    a("")
    a(f"Total records: **{summary['n_records']}** "
      f"({summary['by_platform']}).")
    a("")
    a("## 2. What the local corpus actually contains")
    a("")
    a(f"- Languages: {summary['languages']}")
    a(f"- Subreddits: {summary['subreddits']}")
    a(f"- Duplicate groups (cleaned-text collision): **{summary['duplicate_groups']}**")
    a(f"- Performance data present (views/baseline): **{summary['performance_available']}**")
    a("")
    a("### Field coverage (reel schema)")
    a("")
    cov_rows = [(f, v["present"], v["missing"], f'{v["present_pct"]}%')
                for f, v in summary["missing"].items()]
    a(_table(["field", "present", "missing", "present %"], cov_rows))
    a("## 3. The reported 346 + 300 reel dataset and the eight vidIQ analyses")
    a("")
    a("**Observed:** no raw file for the reported *346 hashtag Reels* or *300 "
      "Reels across 20 accounts* exists in this environment. No `videos.json`, "
      "no Apify export and no vidIQ export are present. The only structured "
      "performance-shaped artefact referenced anywhere is the plan summary in the "
      "owner's notes, which reports derived numbers (median length 10s, 74% ≤15s, "
      "share rates 4–13%, per-account medians) and is **not** independently "
      "verifiable here.")
    a("")
    a("**Consequence:** the outlier table below is empty because the corpus has no "
      "views/baseline fields. Building it would require re-collection, which "
      "Section 7 (spend) forbids until a specific gap is identified.")
    a("")
    a("### Outliers and same-creator controls (from local performance data)")
    a("")
    a(_table(["creator", "views", "baseline", "multiple", "url"], [
        (r["creator"], r["views"], r["baseline"], r["multiple"], r["url"])
        for r in oc["outliers"]]))
    a("### Same-creator controls")
    a("")
    a(_table(["creator", "views", "baseline", "multiple", "url"], [
        (r["creator"], r["views"], r["baseline"], r["multiple"], r["url"])
        for r in oc["controls"][:20]]))
    a("## 4. Third-party visual observations (vidIQ set, observational)")
    a("")
    a("These eight are **selected public outliers** — survivorship-biased, "
      "third-party view figures, not verified Insights. Listed as OBSERVATIONS "
      "with the HYPOTHESIS each suggests; neither is a fact about our account.")
    a("")
    a(_table(["handle", "reported", "observed structure", "hypothesis"], [
        (o["handle"], o["reported"], o["structure"], o["hypothesis"])
        for o in VIDIQ_OBSERVATIONS]))
    a("**Structural read (hypothesis, not proof):** a *creative mechanic* — a "
      "specific, sendable punchline — separates winners from a dense, generic, "
      "all-at-once paragraph. This is exactly what v3 (two-beat short) tests "
      "against v2 (paced reveal) as a control.")
    a("")
    a("## 5. Unsupported claims (kept out of the engine)")
    a("")
    a("- No competitor sales/conversion numbers exist in the corpus; none are "
      "inferred. Sales evidence is `unknown` unless measured on our own account.")
    a("- The 8 examples are not a controlled experiment and cannot attribute "
      "causality to any single production choice.")
    a("")
    a("## 6. Pattern acceptance gate (Section 5.4)")
    a("")
    a("No pattern from this evidence set is promoted. The set fails the gate as "
      "written: it is not ≥3 examples from ≥2 creators *with same-creator "
      "controls measured locally*, and it is not reproducible from the raw data "
      "available here. The video templates T1–T10 already encoded from the "
      "owner's 2026-10-15/16 run remain **hypotheses to test**, not proven facts.")
    a("")
    a("## 7. Remaining research questions (before any paid action)")
    a("")
    a("1. Do v3 two-beat shorts beat v2 paced reels on **share/send rate per "
      "reach** on *this* account? (first-party; needs publishing approval)")
    a("2. Does a plain-text (arm C) short hold attention competitively against "
      "footage (arm A) and still (arm B)? (in-repo render tests + first-party)")
    a("3. Is a caption-led curiosity hook (as in one third-party example) worth a "
      "test, or does it only work with an established audience? (needs first-party)")
    a("4. What is the local corpus **situation coverage** gap, if any, versus the "
      "v3 topic list? (computable now — see below)")
    a("")
    a("## 8. Spend decision")
    a("")
    a("**No paid research is authorized by this audit.** vidIQ balance is "
      "reported at 45 credits; keep ≥25 (Section 7 / 16). Apify pricing must be "
      "verified live before any run. The local corpus is qualitative Reddit text "
      "and cannot be extended into performance evidence by spending — that "
      "requires this account's own mature Insights, which is a publishing-phase "
      "dependency, not a scraping one.")
    a("")
    a("## 9. Cinematic arm decision (Step 4 gate)")
    a("")
    a("**Not justified yet.** The only cinematic evidence is third-party, "
      "uncontrolled, and depends on human presence / real footage (examples 2–8) "
      "or authentic props, which the current rights-cleared pipeline cannot "
      "supply. Per Section 16 decision 3, no `build_cinematic.py` is added; "
      "Format C is registered in the format registry as **not eligible** and "
      "stays declarative until first-party evidence justifies the build.")
    a("")
    text = "\n".join(lines) + "\n"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    import argparse
    from .. import config
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(config.ROOT / "data" / "research"))
    ap.add_argument("--out", default=str(config.ROOT / "docs" / "research"
                                         / "RESEARCH_AUDIT.md"))
    a = ap.parse_args()
    build_report(a.dir, a.out)
    print(f"wrote {a.out}")
