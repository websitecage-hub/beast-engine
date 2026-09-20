"""state.py — git state commits and log bookkeeping.

commit_all: configure bot identity, pull --rebase --autostash, add data/,
commit, push; retry once on push failure then exit 0 (next run recovers).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import config

BOT_NAME = "beast-engine[bot]"
BOT_EMAIL = "beast-engine[bot]@users.noreply.github.com"


def _run(args, cwd=None, check=True, timeout=180):
    """Non-interactive git. Never let a credential prompt hang a job."""
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env["GIT_ASKPASS"] = ""
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o StrictHostKeyChecking=no"
    try:
        return subprocess.run(args, cwd=str(cwd or config.ROOT), capture_output=True,
                              text=True, check=check, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        print(f"[state] {' '.join(args[:3])} timed out after {timeout}s")
        if check:
            raise subprocess.CalledProcessError(124, args)
        return subprocess.CompletedProcess(args, 124, "", "timeout")


def configure_identity():
    _run(["git", "config", "user.name", BOT_NAME], check=False)
    _run(["git", "config", "user.email", BOT_EMAIL], check=False)


def commit_all(message: str, dry_run: bool = False) -> bool:
    """Commit data/ (+ regenerated report/content) and push. Never raises."""
    if dry_run:
        print("[state] dry-run: skipping commit")
        return False
    configure_identity()
    try:
        _run(["git", "pull", "--rebase", "--autostash"], check=False)
        _run(["git", "add", "data/"])
        st = _run(["git", "status", "--porcelain"])
        if not st.stdout.strip():
            print("[state] nothing to commit")
            return False
        _run(["git", "commit", "-m", message[:200]])
    except subprocess.CalledProcessError as exc:
        print(f"[state] commit failed: {exc.stderr}")
        return False
    for attempt in range(2):
        try:
            _run(["git", "push"])
            print(f"[state] pushed: {message}")
            return True
        except subprocess.CalledProcessError as exc:
            print(f"[state] push attempt {attempt + 1} failed: {exc.stderr}")
            if attempt == 0:
                _run(["git", "pull", "--rebase", "--autostash"], check=False)
    print("[state] push failed twice — exiting 0, next run recovers")
    return False


def write_log(workflow: str, payload: dict) -> Path:
    config.LOGS.mkdir(parents=True, exist_ok=True)
    path = config.LOGS / f"{config.today_utc().isoformat()}-{workflow}.json"
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = [existing]
        except Exception:  # noqa: BLE001
            existing = []
    existing.append(payload)
    config.save_json(path, existing)
    return path


def prune_logs(keep: int = 30) -> int:
    """Keep only the newest `keep` files in data/logs/."""
    if not config.LOGS.exists():
        return 0
    files = sorted([p for p in config.LOGS.iterdir() if p.is_file() and p.name != ".gitkeep"],
                   reverse=True)
    removed = 0
    for p in files[keep:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed
