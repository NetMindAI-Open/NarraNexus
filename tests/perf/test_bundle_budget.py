"""
@file_name: test_bundle_budget.py
@author: Bin Liang
@date: 2026-09-03
@description: The built frontend's first-load set stays under frontend/budget.json and carries no plugin code (skips when dist is absent).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"
BUDGET = json.loads((ROOT / "frontend" / "budget.json").read_text(encoding="utf-8"))


def _initial_set() -> list[tuple[str, int]]:
    html = (DIST / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)
    return [(r, (DIST / r).stat().st_size) for r in refs if (DIST / r).is_file()]


pytestmark = pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="frontend not built")


def test_initial_set_within_budget_and_free_of_plugin_code():
    files = _initial_set()
    total = sum(s for _, s in files)
    assert total <= BUDGET["initial_total_bytes"], f"initial set {total} bytes over budget"
    entry = next((s for r, s in files if re.search(r"assets/index-[^/]+\.js$", r)), 0)
    assert entry <= BUDGET["entry_script_bytes"]
    for r, _ in files:
        for pat in BUDGET["initial_forbidden_chunk_patterns"]:
            assert pat not in r, f"{r} must not be in the first-load set"


def test_budget_file_is_a_ratchet_with_sane_values():
    assert BUDGET["entry_script_bytes"] < BUDGET["initial_total_bytes"]
    assert "plugin" in BUDGET["initial_forbidden_chunk_patterns"]
