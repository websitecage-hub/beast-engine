"""analyze.py — the learning brain (Sunday).

Decay w = 0.5 ** (age_days/28); global mean G; per-value adjusted score with
shrinkage k=5: adj = (Sum w*s + 5*G) / (Sum w + 5). Weights = max(adj, 0.001)
normalized per family. Hour learning picks next_post_hour. Experiments enqueue
under-sampled (archetype, topic) combos. Writes data/REPORT.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config

MIN_POSTS = 5
DECAY_HALFLIFE_D = 28
K = 5
MIN_WEIGHT = 0.001
FAMILIES = ("archetype", "topic", "mood", "bg_type")
HOURS = ("13:30", "15:00", "16:30")


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
                     "mood": "moods", "bg_type": "bg_types"}[fam]) or []
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
    lines += ["## Top 5 hooks", "", "| hook | score | archetype | topic | mood | exploit |",
              "|---|---|---|---|---|---|"]
    for p in top:
        d = p["dna"] or {}
        lines.append(f"| {p['hook'][:60]} | {p['score']:.4f} | {d.get('archetype','')} | "
                     f"{d.get('topic','')} | {d.get('mood','')} | {p['exploit']} |")
    lines.append("")

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
