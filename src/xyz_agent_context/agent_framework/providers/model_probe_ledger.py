"""
@file_name: model_probe_ledger.py
@author:
@date: 2026-06-24
@description: Read/write the model-probe ledger — the dedup cache that records,
per (provider source, model id), which protocols (openai / anthropic) the model
actually answers on.

The ledger is a committed JSON file (the release-time snapshot) so a fresh local
install ships with known-good per-protocol model lists without probing on first
run. The cloud daily job and the local "Update models" button both UPDATE it via
``model_sync`` (which owns the probing); this module is the pure read/write layer
so ``model_catalog`` can read it without importing the probing code.

Shape:
    {
      "generated_at": "<iso>",
      "sources": {
        "<source>": {"models": {"<model_id>": {
            "openai": "pass"|"fail", "anthropic": "pass"|"fail",
            "display_name": str, "context": str|None, "tested_at": "<iso>"
        }}}
      }
    }

A model is offered for (source, protocol) iff its ledger entry has
``entry[protocol] == "pass"``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Committed next to model_catalog.py so it ships in the package / DMG build.
LEDGER_PATH = Path(__file__).with_name("model_probe_ledger.json")

PASS = "pass"
FAIL = "fail"


def load_ledger() -> dict[str, Any]:
    """Load the ledger, returning the empty skeleton if the file is absent."""
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"generated_at": None, "sources": {}}
    data.setdefault("sources", {})
    return data


def save_ledger(ledger: dict[str, Any]) -> bool:
    """Persist the ledger (pretty-printed, stable key order for clean diffs).

    Best-effort: a read-only container rootfs (cloud) makes the write fail — that
    only loses the cross-run dedup cache (the DB rows are the durable output, and
    the next run just re-probes), so we log and carry on rather than crash the
    sync. Returns True on success.
    """
    try:
        LEDGER_PATH.write_text(
            json.dumps(ledger, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def source_models(ledger: dict[str, Any], source: str) -> dict[str, Any]:
    """The per-model dict for a source, creating it if missing."""
    return ledger["sources"].setdefault(source, {"models": {}})["models"]


def ledger_models(source: str, protocol: str) -> list[str]:
    """Model ids that PASS ``protocol`` for ``source`` in the committed ledger.

    Pure read — used by ``model_catalog.get_default_models``. ``system_pool``
    reuses the ``netmind`` entry (same backend / platform key). Returns [] when
    the ledger has nothing for the source (caller falls back to hardcoded
    defaults).
    """
    ledger = load_ledger()
    key = "netmind" if source in ("netmind", "system_pool") else source
    models = ledger.get("sources", {}).get(key, {}).get("models", {})
    return [mid for mid, rec in models.items() if rec.get(protocol) == PASS]
