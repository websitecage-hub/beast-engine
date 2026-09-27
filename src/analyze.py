"""analyze.py — the learning brain (Sunday), v5.0.

Decay w = 0.5 ** (age_days/28); global mean G; per-value adjusted score with
shrinkage k=5: adj = (Sum w*s + 5*G) / (Sum w + 5). Weights = max(adj, 0.001)
normalized per family. Hour learning picks next_post_hour. Experiments enqueue
under-sampled (cluster, topic) combos. Writes data/REPORT.md.

Part 7.2 questions answered in the report + Part 7.3 self-improvement metrics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config, harvest

MIN_POSTS = 5
DECAY_HALFLIFE_D = 28
K = 5
MIN_WEIGHT = 0.001
FAMILIES = ("archetype", "topic", "mood", "bg_type", "loop_technique")
HOURS = ("13:30", "15:00", "16:30", "21:30")


def _parse(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def scored_posts(memory: dict) -> list:
    out = []
    for p in memory.get("posts", []):
        if p.get("score") is None:
            continue
        created = _parse(p.get("created_at"))
        if not created:
            continue
        age_days = max((datetime.now(timezone.utc) - created).total_seconds() / 86400.0, 0.0)
        out.append({
            "score": float(p["score"]),
            "w": 0.5 ** (age_days / DECAY_HALFLIFE_D),
            "dna": p.get("dna") or {},
            "hour": p.get("hour_slot"),
            "exploit": bool((p.get("dna") or {}).get("exploit", True)),
            "trending_ref": (p.get("dna") or {}).get("trending_ref"),
            "hook": p.get("hook") or ((p.get("caption") or "").split("\n")[0]),
            "age_days": age_days,
            # v5.0: the raw insight numbers drive the Part 7.3 metrics section
            "metrics": p.get("metrics") or {},
        })
    return out


def _global_mean(posts: list) -> float:
    wsum = sum(p["w"] for p in posts)
    if wsum <= 0:
        return 0.0
    return sum(p["w"] * p["score"] for p in posts) / wsum


def _adjusted(posts: list, key_fn, value: str, G: float) -> tuple:
    """Returns (adj, n, sum_w). Shrinkage toward the global mean."""
    sel = [p for p in posts if key_fn(p) == value]
    wsum = sum(p["w"] for p in sel)
    if not sel:
        return G, 0, 0.0
    raw = sum(p["w"] * p["score"] for p in sel)
    adj = (raw + K * G) / (wsum + K)
    return adj, len(sel), wsum


def analyze(dry_run: bool = False) -> dict:
    cfg = config.load_config()
    memory = config.load_memory()
    strategy = config.load_strategy()
    posts = scored_posts(memory)

    if len(posts) < MIN_POSTS:
        _write_report(memory, strategy, posts, None, insufficient=True)
        if not dry_run:
            config.save_strategy(strategy)
        return {"insufficient": True, "n": len(posts)}

    G = _global_mean(posts)
    adj_tables, n_tables = {}, {}
    for fam in FAMILIES:
        values = list((strategy.get("weights") or {}).get(fam, {}).keys()) or \
            cfg.get({"archetype": "archetypes", "topic": "topics",
                     "mood": "moods", "bg_type": "bg_types",
                     "loop_technique": "loop_techniques"}[fam]) or []
        adj_row, n_row = {}, {}
        for v in values:
            adj, n, _ = _adjusted(posts, lambda p, f=fam, vv=v: (p["dna"] or {}).get(f), v, G)
            adj_row[v] = adj
            n_row[v] = n
        adj_tables[fam] = adj_row
        n_tables[fam] = n_row

    # hour learning
    hour_adj = {}
    for h in HOURS:
        adj, n, _ = _adjusted(posts, lambda p: p.get("hour"), h, G)
        hour_adj[h] = adj if n else None

    experiments = []
    combos = {}
    for p in posts:
        d = p["dna"] or {}
        key = (d.get("archetype"), d.get("topic"))
        combos[key] = combos.get(key, 0) + 1
    for arch in cfg["archetypes"]:
        for topic in cfg["topics"]:
            if len(experiments) >= 2:
                break
            if combos.get((arch, topic), 0) < 3:
                experiments.append({"archetype": arch, "topic": topic,
                                    "enqueued": config.today_utc().isoformat()})
        if len(experiments) >= 2:
            break

    strategy["adj"] = adj_tables
    strategy["n"] = n_tables
    strategy["weights"] = {
        fam: _normalize({v: max(a, MIN_WEIGHT) for v, a in adj_tables[fam].items()})
        for fam in FAMILIES
    }
    strategy["hour_scores"] = hour_adj
    if any(v is not None for v in hour_adj.values()):
        strategy["next_post_hour"] = max(
            (h for h in HOURS if hour_adj[h] is not None), key=lambda h: hour_adj[h])
    strategy["experiments"] = experiments
    strategy["last_updated"] = datetime.now(timezone.utc).isoformat()

    if not dry_run:
        config.save_strategy(strategy)
    _write_report(memory, strategy, posts, G)
    return {"n": len(posts), "G": G, "weights": strategy["weights"],
            "hour_scores": hour_adj, "next_post_hour": strategy["next_post_hour"],
            "experiments": experiments}


def _normalize(row: dict) -> dict:
    total = sum(row.values())
    if total <= 0:
        return {k: 1.0 for k in row}
    return {k: max(v / total, MIN_WEIGHT) for k, v in row.items()}


def _metrics_section(memory, posts) -> list:
    """Part 7.3 — self-improvement metrics + Part 4.1/7.2 answers."""
    out = ["## Self-improvement metrics (Part 7.3)", ""]
    with_metrics = [p for p in posts if (p.get("dna") is not None)]
    rows = []
    for p in posts:
        m = (p.get("metrics") or {})
        if not m:
            continue
        rows.append((p, m))
    if not rows:
        out += ["_no harvested metrics yet_", ""]
        return out
    spr = [harvest.sends_per_reach(m) for _, m in rows]
    savr = [harvest.saves_per_reach(m) for _, m in rows]
    out += [f"- posts with metrics: **{len(rows)}**",
            f"- sends per reach: mean **{sum(spr)/len(spr)*100:.2f}%** "
            f"(target >{harvest.TARGET_SENDS_PER_REACH*100:.0f}%)",
            f"- saves per reach: mean **{sum(savr)/len(savr)*100:.2f}%**",
            f"- reels above the send target: "
            f"**{sum(1 for x in spr if x >= harvest.TARGET_SENDS_PER_REACH)}/{len(spr)}**",
            ""]
    # SEO §8: search is a compounding channel, so its leading indicators are tracked
    # separately from the 48-hour engagement signals above. Reach from non-followers is
    # the proxy for search discovery; saves/reach is the trust signal the spec names.
    seo_rows = [(p, m) for p, m in rows
                if (p.get("dna") or {}).get("alt_text") or (p.get("dna") or {}).get("hashtags")]
    if seo_rows:
        big_saves = sum(1 for _, m in rows if harvest.saves_per_reach(m) >= 0.05)
        described = sum(1 for p, _ in seo_rows
                        if len((p.get("dna") or {}).get("alt_text") or "") > 40)
        tagged = sum(1 for p, _ in seo_rows
                     if 5 <= len((p.get("dna") or {}).get("hashtags") or []) <= 7)
        kws = {}
        for p, _ in seo_rows:
            kw = (p.get("dna") or {}).get("keyword")
            if kw:
                kws[kw] = kws.get(kw, 0) + 1
        out += ["## SEO: is the search channel compounding? (spec §8)", "",
                "| metric | value | what good looks like |", "|---|---|---|",
                f"| reels shipping alt text | {described}/{len(seo_rows)} | all of them |",
                f"| reels with 5-7 hashtags | {tagged}/{len(seo_rows)} | all of them |",
                f"| reels at >=5% saves/reach | {big_saves}/{len(rows)} | above 5% |",
                "_profile visits from search, reach from non-followers and bio link "
                "clicks live in Instagram Insights — rising month over month means "
                "search discovery is working._",
                ""]
        if kws:
            # The comment keyword is the DM conversion path, so its distribution shows
            # whether comments are being spread across the automation's rules.
            top = sorted(kws.items(), key=lambda kv: kv[1], reverse=True)
            out += ["### comment keywords used (DM automation triggers)", "",
                    "| keyword | reels |", "|---|---|"]
            out += [f"| {k} | {n} |" for k, n in top[:12]]
            out.append("")
    # Part 7.2 Q1: which clusters earn the most saves?
    per_cluster = {}
    for p, m in rows:
        cl = (p.get("dna") or {}).get("cluster") or (p.get("dna") or {}).get("archetype")
        if not cl:
            continue
        per_cluster.setdefault(cl, []).append(harvest.saves_per_reach(m))
    if per_cluster:
        out += ["## Which clusters earn the most saves? (Part 7.2 Q1)", "",
                "| cluster | mean saves/reach | n |", "|---|---|---|"]
        for cl, vals in sorted(per_cluster.items(), key=lambda kv: sum(kv[1]) / len(kv[1]),
                               reverse=True):
            out.append(f"| {cl} | {sum(vals)/len(vals)*100:.2f}% | {len(vals)} |")
        out.append("")
    # Part 7.2 Q3: bg type vs completion proxy
    per_bg = {}
    for p, m in rows:
        bg = (p.get("dna") or {}).get("bg_type")
        plays, reach = float(m.get("plays") or 0), max(float(m.get("reach") or 0), 1.0)
        if not bg or not plays:
            continue
        per_bg.setdefault(bg, []).append(plays / reach)
    if per_bg:
        out += ["## Background type vs completion proxy (Part 7.2 Q3)", "",
                "| bg_type | mean plays/reach | n |", "|---|---|---|"]
        for bg, vals in sorted(per_bg.items(), key=lambda kv: sum(kv[1]) / len(kv[1]),
                               reverse=True):
            out.append(f"| {bg} | {sum(vals)/len(vals):.2f} | {len(vals)} |")
        out.append("")
    # Part 7.2 Q7: loop technique effect
    per_loop = {}
    for p, m in rows:
        lt = (p.get("dna") or {}).get("loop_technique")
        plays, reach = float(m.get("plays") or 0), max(float(m.get("reach") or 0), 1.0)
        if not lt or not plays:
            continue
        per_loop.setdefault(lt, []).append(plays / reach)
    if per_loop:
        out += ["## Loop technique vs rewatch proxy (Part 7.2 Q7)", "",
                "| loop_technique | mean plays/reach | n |", "|---|---|---|"]
        for lt, vals in sorted(per_loop.items(), key=lambda kv: sum(kv[1]) / len(kv[1]),
                               reverse=True):
            out.append(f"| {lt} | {sum(vals)/len(vals):.2f} | {len(vals)} |")
        out.append("")
    # Part 7.2 Q6: trending alignment
    aligned = [harvest.sends_per_reach(m) for p, m in rows
               if (p.get("dna") or {}).get("audio_from_trending")]
    unaligned = [harvest.sends_per_reach(m) for p, m in rows
                 if not (p.get("dna") or {}).get("audio_from_trending")]
    out += ["## Does trending audio help sends? (Part 7.2 Q6)", "",
            f"- trending audio: n={len(aligned)} "
            + (f"mean sends/reach={sum(aligned)/len(aligned)*100:.2f}%" if aligned else ""),
            f"- non-trending: n={len(unaligned)} "
            + (f"mean sends/reach={sum(unaligned)/len(unaligned)*100:.2f}%" if unaligned else ""),
            ""]
    return out


def _write_report(memory, strategy, posts, G, insufficient: bool = False) -> str:
    lines = ["# Beast Engine — weekly brain report", "",
             f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", ""]
    if insufficient:
        lines += [f"**insufficient data (n={len(posts)})** — strategy held steady.", ""]
        lines += ["A minimum of 5 scored posts is required before the learning pass "
                  "adjusts weights.", ""]
        text = "\n".join(lines)
        config.REPORT_PATH.write_text(text, encoding="utf-8")
        return text

    mean_score = G
    lines += [f"Posts scored this cycle: **{len(posts)}**",
              f"Weighted mean score: **{mean_score:.4f}**", "",
              "## Attribute adjustments (value | adj | n)", ""]
    for fam in FAMILIES:
        lines.append(f"### {fam}")
        rows = sorted(((v, a, (strategy.get('n') or {}).get(fam, {}).get(v, 0))
                       for v, a in (strategy.get('adj') or {}).get(fam, {}).items()),
                      key=lambda r: r[1], reverse=True)
        lines.append("| value | adj | n |")
        lines.append("|---|---|---|")
        for v, a, n in rows:
            lines.append(f"| {v} | {a:.4f} | {n} |")
        lines.append("")

    lines += ["## Hour scores", "", "| hour | adj |", "|---|---|"]
    for h, a in (strategy.get("hour_scores") or {}).items():
        lines.append(f"| {h} | {'n/a' if a is None else format(a, '.4f')} |")
    lines += ["", f"Next post hour: **{strategy.get('next_post_hour')}**", ""]

    top = sorted(posts, key=lambda p: p["score"], reverse=True)[:5]
    lines += ["## Top 5 hooks", "", "| hook | score | cluster | topic | mood | exploit |",
              "|---|---|---|---|---|---|"]
    for p in top:
        d = p["dna"] or {}
        lines.append(f"| {p['hook'][:60]} | {p['score']:.4f} | "
                     f"{d.get('cluster') or d.get('archetype','')} | "
                     f"{d.get('topic','')} | {d.get('mood','')} | {p['exploit']} |")
    lines.append("")

    # Part 7.3 — self-improvement metrics (sends-first hierarchy)
    lines += _metrics_section(memory, posts)

    ex = [p["score"] for p in posts if p["exploit"]]
    xp = [p["score"] for p in posts if not p["exploit"]]
    lines += ["## Exploit vs explore",
              f"- exploit: n={len(ex)} mean={sum(ex)/len(ex):.4f}" if ex else "- exploit: n=0",
              f"- explore: n={len(xp)} mean={sum(xp)/len(xp):.4f}" if xp else "- explore: n=0",
              ""]

    aligned = [p["score"] for p in posts if (p.get("trending_ref") or {}).get("trend_score")]
    unaligned = [p["score"] for p in posts if not (p.get("trending_ref") or {}).get("trend_score")]
    lines += ["## Trending alignment (does riding the trend help THIS audience?)", ""]
    lines.append(f"- trending-aligned: n={len(aligned)} "
                 f"mean={sum(aligned)/len(aligned):.4f}" if aligned
                 else "- trending-aligned: n=0")
    lines.append(f"- not aligned: n={len(unaligned)} "
                 f"mean={sum(unaligned)/len(unaligned):.4f}" if unaligned
                 else "- not aligned: n=0")
    lines += ["", "## Next week's experiments", ""]
    for e in strategy.get("experiments") or []:
        lines.append(f"- {e.get('archetype')} x {e.get('topic')}")
    lines.append("")
    text = "\n".join(lines)
    config.REPORT_PATH.write_text(text, encoding="utf-8")
    return text


def main(dry_run: bool = False):
    res = analyze(dry_run=dry_run)
    print(f"analyze: {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
