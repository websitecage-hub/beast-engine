"""Semantic merge for data/template_manifest.json.

Why this exists: the manifest is an append-only list of run entries keyed by run_id,
written by both the CI runner and local work. A textual rebase therefore conflicts,
and a conflicted file that gets committed poisons every later run - a manifest that
does not parse cannot be read, so no run can see its own history. This module turns
that conflict into a union of entries instead.

  * ``strip_markers(text)``  - repair a document that already contains markers;
  * ``union(a, b)``          - union two entry lists by run_id;
  * CLI ``merge_manifest.py <base> <ours> <theirs>`` - the git merge driver contract.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

START_RE = re.compile(r"^<{7}(\s|$)")
MID_RE = re.compile(r"^={7}\s*$")
END_RE = re.compile(r"^>{7}(\s|$)")


def blocks(text: str) -> list[tuple[list[str], list[str]]]:
    """Return (ours, theirs) line lists for every conflict block, in order."""
    out, ours, theirs, mode = [], None, None, "base"
    for line in text.splitlines():
        if START_RE.match(line):
            mode, ours = "ours", []
            continue
        if MID_RE.match(line) and mode == "ours":
            mode, theirs = "theirs", []
            continue
        if END_RE.match(line) and mode == "theirs":
            out.append((ours or [], theirs or []))
            mode = "base"
            continue
        if mode == "ours":
            ours.append(line)
        elif mode == "theirs":
            theirs.append(line)
    return out


def _load(text: str) -> list:
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def union(a: list, b: list) -> list:
    """Union two manifests by run_id (b wins), sorted by run_id."""
    merged: dict[str, dict] = {}
    for source in (a or [], b or []):
        for entry in source:
            if isinstance(entry, dict) and entry.get("run_id"):
                merged[str(entry["run_id"])] = entry
    return sorted(merged.values(), key=lambda e: str(e.get("run_id") or ""))


def entries_in(text: str) -> list:
    """Every manifest entry recoverable from a possibly-conflicted document."""
    recovered = _load(text)
    for ours, theirs in blocks(text):
        for side in (ours, theirs):
            fragment = "[\n" + "\n".join(side).rstrip().rstrip(",") + "\n]"
            recovered = union(recovered, _load(fragment))
    return union(recovered, []) or salvage_entries(text)


def salvage_entries(text: str) -> list:
    """Entries that parse on their own, ignoring anything markers broke.

    A conflict nested INSIDE entries makes both whole-document and per-side parsing
    fail, but the untouched entries around the damage are still readable. This walks
    the document character by character tracking string state and brace depth, then
    keeps every top-level object that parses.
    """
    found: list = []
    depth, start = 0, None
    in_string, escaped = False, False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 1:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 1 and start is not None:
                chunk = text[start:i + 1]
                try:
                    entry = json.loads(chunk)
                except Exception:
                    entry = None
                if isinstance(entry, dict) and entry.get("run_id"):
                    found.append(entry)
                start = None
    return union(found, [])


def strip_markers(text: str) -> str:
    """A valid manifest document, whether or not it contains conflict markers."""
    if "<<<<<<<" not in text and ">>>>>>>" not in text:
        return text
    return json.dumps(entries_in(text), indent=1, sort_keys=True)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: merge_manifest.py <base> <ours> <theirs>", file=sys.stderr)
        return 2
    ours_path, theirs_path = Path(argv[2]), Path(argv[3])
    merged = union(entries_in(ours_path.read_text()),
                   entries_in(theirs_path.read_text()))
    ours_path.write_text(json.dumps(merged, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
