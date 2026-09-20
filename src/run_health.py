"""run_health.py — daily 05:00 UTC health pass (section 7.4).

1. Ping /health on meta, pinterest, audio-scraper.
2. Warm the trending cache (triggers the day's slow live build).
3. GET /me -> 401 = TOKEN DEAD alert.
4. Refresh the IG token inside 21 days of expiry and ROTATE the GitHub secret.
5. Alert if the machine hasn't posted in 36h and is not paused.
6. Commit token_state + log.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

import requests

from . import alerts, config, llm, music, publish, state


def run(dry_run: bool = False) -> int:
    started = time.time()
    cfg = config.load_config()
    log = {"workflow": "health", "started": datetime.now(timezone.utc).isoformat(),
           "dry_run": dry_run, "checks": {}}

    # 1. service health
    checks = {}
    try:
        checks["meta"] = bool(llm.wake(retries=2, wait=5))
    except Exception:  # noqa: BLE001
        checks["meta"] = False
    try:
        from . import pinterest
        checks["pinterest"] = bool(pinterest.wake(retries=2, wait=5))
    except Exception:  # noqa: BLE001
        checks["pinterest"] = False
    api = cfg["music"]["trending_api"]
    try:
        checks["audio_scraper"] = requests.get(f"{api}/health", timeout=30).status_code == 200
    except Exception:  # noqa: BLE001
        checks["audio_scraper"] = False
    log["checks"] = checks
    print(f"[health] services: {checks}")

    # 2. warm the trending cache (non-fatal)
    try:
        r = requests.get(f"{api}/v1/trending/{cfg['music']['trending_niche']}",
                         params={"limit": 5}, timeout=180)
        log["trending_warm"] = {"status": r.status_code,
                                "results": len((r.json() or {}).get("results") or [])}
    except Exception as exc:  # noqa: BLE001
        log["trending_warm"] = {"error": str(exc)[:200]}
    print(f"[health] trending warm: {log.get('trending_warm')}")

    # 3. token check
    token_ok, account = False, {}
    try:
        account = publish.resolve_account()
        token_ok = True
    except PermissionError:
        if not dry_run:
            alerts.fail("TOKEN DEAD — open developers.facebook.com -> your app -> "
                        "Instagram -> API setup with Instagram login -> Generate token "
                        "-> update the IG_ACCESS_TOKEN secret.")
    except Exception as exc:  # noqa: BLE001
        print(f"[health] /me failed: {exc}")
    log["token"] = {"ok": token_ok, "account": account.get("username")}
    print(f"[health] token: {log['token']}")

    # 4. refresh + rotate when close to expiry
    ts = config.load_token_state()
    try:
        expires = config.date.fromisoformat(str(ts.get("expires_at"))[:10])
    except Exception:  # noqa: BLE001
        expires = config.today_utc()
    days_left = (expires - config.today_utc()).days
    log["token_expiry"] = {"expires_at": str(ts.get("expires_at")), "days_left": days_left}
    if days_left < 21 and token_ok:
        refreshed = publish.refresh_token()
        if refreshed and refreshed.get("access_token"):
            new_tok = refreshed["access_token"]
            rotated = publish._set_secret("IG_ACCESS_TOKEN", new_tok) if not dry_run else False
            ts["expires_at"] = (config.today_utc() +
                                timedelta(seconds=int(refreshed.get("expires_in", 5184000)))).isoformat()
            ts["last_refresh"] = datetime.now(timezone.utc).isoformat()
            if not dry_run:
                config.save_token_state(ts)
            log["token_refresh"] = {"rotated": rotated, "expires_at": ts["expires_at"]}
            if not rotated and not dry_run:
                alerts.fail("IG token refreshed but GitHub secret rotation FAILED — "
                            "update IG_ACCESS_TOKEN manually with a fresh token from "
                            "developers.facebook.com -> Instagram -> API setup with Instagram login.")
            print(f"[health] token refreshed; rotation={rotated}")
        else:
            if not dry_run:
                alerts.fail("IG token refresh request failed — token may expire soon. "
                            "Regenerate manually in the dashboard.")
            log["token_refresh"] = {"error": "refresh request failed"}

    # 5. staleness check
    memory = config.load_memory()
    last = memory.get("last_post_date")
    stale = False
    if last:
        try:
            stale = (config.today_utc() -
                     config.date.fromisoformat(str(last)[:10])) > timedelta(hours=36)
        except Exception:  # noqa: BLE001
            stale = False
    else:
        stale = True
    log["staleness"] = {"last_post_date": last, "stale": stale,
                        "paused": config.paused()}
    if stale and not config.paused() and not dry_run:
        alerts.fail(f"machine hasn't posted — last_post_date={last}")
    print(f"[health] staleness: {log['staleness']}")

    log["seconds"] = round(time.time() - started, 2)
    state.prune_logs()
    state.write_log("health", log)
    state.commit_all("health: token + service check", dry_run=dry_run)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
