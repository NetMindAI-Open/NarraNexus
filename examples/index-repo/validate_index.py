"""
@file_name: validate_index.py
@author: Bin Liang
@date: 2026-09-04
@description: Metadata check for the plugin index repo (`protagolabs/narranexus-plugins`): every entry has the IndexEntry fields, a `<publisher>.<name>` id, an `owner/repo` GitHub repo, no duplicate ids; the blocklist maps plugin id -> version -> reason. Inclusion is a metadata check, not a code review.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:  # the engine's grammar when narranexus is installed; the same expression otherwise
    from narranexus.contracts.distribution import PLUGIN_ID_RE as ID_RE
except ImportError:  # pragma: no cover
    ID_RE = re.compile(r"^[a-z0-9]+([_-][a-z0-9]+)*(\.[a-z0-9]+([_-][a-z0-9]+)*)+$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FIELDS = {"id", "repo", "author", "description", "tags", "kinds"}


def check(root: Path) -> list[str]:
    problems: list[str] = []
    entries = json.loads((root / "index.json").read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        return ["index.json must be a list"]
    seen: set[str] = set()
    for i, e in enumerate(entries):
        where = f"index.json[{i}]"
        if not isinstance(e, dict) or set(e) - FIELDS or not {"id", "repo"} <= set(e):
            problems.append(f"{where}: fields must be a subset of {sorted(FIELDS)} with id and repo")
            continue
        if not ID_RE.match(e["id"]) or e["id"].startswith("builtin."):
            problems.append(f"{where}: bad id {e['id']!r}")
        if not REPO_RE.match(e["repo"]):
            problems.append(f"{where}: repo must be owner/repo, got {e['repo']!r}")
        if e["id"] in seen:
            problems.append(f"{where}: duplicate id {e['id']!r}")
        seen.add(e["id"])
        for key in ("tags", "kinds"):
            if key in e and not (isinstance(e[key], list) and all(isinstance(x, str) for x in e[key])):
                problems.append(f"{where}: {key} must be a list of strings")
    blocked_path = root / "blocked_versions.json"
    if blocked_path.is_file():
        blocked = json.loads(blocked_path.read_text(encoding="utf-8"))
        if not isinstance(blocked, dict):
            problems.append("blocked_versions.json must be an object")
        else:
            for pid, versions in blocked.items():
                if not ID_RE.match(pid):
                    problems.append(f"blocked_versions.json: bad id {pid!r}")
                if not isinstance(versions, dict) or not all(isinstance(v, str) and v for v in versions.values()):
                    problems.append(f"blocked_versions.json[{pid}]: must map version -> reason")
    return problems


if __name__ == "__main__":
    found = check(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
    for p in found:
        print(f"PROBLEM: {p}")
    print("ok" if not found else f"{len(found)} problem(s)")
    raise SystemExit(1 if found else 0)
