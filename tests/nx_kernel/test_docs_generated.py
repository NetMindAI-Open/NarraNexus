"""
@file_name: test_docs_generated.py
@author: Bin Liang
@date: 2026-09-03
@description: docs/plugins/slots.md is exactly what the generator renders from the code.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_plugin_docs", ROOT / "scripts" / "dev" / "gen_plugin_docs.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_slot_docs_are_up_to_date():
    gen = _load_generator()
    for path, text in gen.render_all().items():
        assert path.is_file(), f"{path} missing: run scripts/dev/gen_plugin_docs.py --write"
        assert path.read_text(encoding="utf-8") == text, f"{path.name} is stale: run scripts/dev/gen_plugin_docs.py --write"
