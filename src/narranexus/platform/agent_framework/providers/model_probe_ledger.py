"""
@file_name: model_probe_ledger.py
@author:
@date: 2026-06-24
@description: Read/write the model-probe ledger — the dedup cache that records,
per (provider source, model id), which protocols (openai / anthropic) the model
actually answers on.

The ledger has two carriers. The committed JSON file is the release-time
snapshot: a fresh local install ships with known-good per-protocol model lists
without probing on first run, and ``model_catalog`` can read it synchronously.
The DB table (``model_probe_ledger``, one row per source) is the DURABLE copy
in cloud: the container file resets to the snapshot on every deploy, so probe
history (``tested_at`` clocks, pass->fail flips) must live in the DB to survive.
Writers (the daily runner, the "Update models" button) load DB-first and save
to both; the file stays the fallback seed.

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


def passing_models(
    models_map: dict[str, Any], protocol: str, *, include_extras: bool = False
) -> list[str]:
    """THE read gate for per-protocol pass-lists.

    Every consumer of the ledger (card overwrites, provisioning seeds, sync
    result lists) must come through here so the ``extra`` invariant lives in
    one place: gateway-only ids (probed for the free-tier gate, marked
    ``extra``) are NEVER listed unless the caller opts in — a user's own key
    is not necessarily authorized or priced for them.
    """
    return [
        mid
        for mid, rec in models_map.items()
        if rec.get(protocol) == PASS and (include_extras or not rec.get("extra"))
    ]


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
    return passing_models(models, protocol)


# ---------------------------------------------------------------------------
# DB carrier — the durable copy (cloud containers lose the file on redeploy)
# ---------------------------------------------------------------------------

LEDGER_TABLE = "model_probe_ledger"


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def load_ledger_db(db) -> dict[str, Any] | None:
    """Assemble the ledger from the DB rows, or None when nothing is stored yet
    (first run after this table shipped — caller falls back to the file)."""
    rows = await db.get(LEDGER_TABLE, filters={})
    sources: dict[str, Any] = {}
    generated_at: str | None = None
    for row in rows or []:
        try:
            entry = json.loads(row.get("models_json") or "")
        except (json.JSONDecodeError, TypeError):
            continue  # a corrupt row must not sink the loadable ones
        if not isinstance(entry, dict) or "models" not in entry:
            continue
        sources[row["source"]] = entry
        val = row.get("generated_at")
        if val is not None and not isinstance(val, str):
            # Some DB drivers eagerly parse datetime-looking strings.
            val = val.isoformat() if hasattr(val, "isoformat") else str(val)
        if val:
            generated_at = max(generated_at or "", val)
    if not sources:
        return None
    return {"generated_at": generated_at, "sources": sources}


async def save_ledger_db(db, ledger: dict[str, Any]) -> int:
    """Upsert one row per source. Returns the number of rows written."""
    generated_at = ledger.get("generated_at")
    now = _utcnow_iso()
    written = 0
    for source, entry in (ledger.get("sources") or {}).items():
        payload = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        data = {"models_json": payload, "generated_at": generated_at, "updated_at": now}
        existing = await db.get_one(LEDGER_TABLE, {"source": source})
        if existing:
            await db.update(LEDGER_TABLE, {"source": source}, data)
        else:
            await db.insert(LEDGER_TABLE, {"source": source, **data})
        written += 1
    return written
