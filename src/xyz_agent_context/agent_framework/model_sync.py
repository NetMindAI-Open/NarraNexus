"""
@file_name: model_sync.py
@author:
@date: 2026-06-24
@description: Auto-discover provider models — fetch each aggregator's catalog,
probe which models actually answer per protocol (openai / anthropic), and
overwrite the per-(source, protocol) model lists. Dedup via the committed
ledger ([[model_probe_ledger]]): only NEW models are probed each run; models
that already PASSED are trusted; models that previously FAILED are re-probed
(they can flip when the backend adds support).

Design + scope: see reference/self_notebook/specs/2026-06-24-power-models-auto-sync-design.md
In scope (catalog + dual-protocol probe): netmind (+ system_pool, same backend),
openrouter, yunwu. Out of scope: claude_oauth / codex_oauth (CLI, self-track),
custom_* (arbitrary endpoint).

The probe result is a property of the provider BACKEND, not the user's key, so
one probe pass (with any valid key for that source) is applied to every provider
row of that source.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
from loguru import logger

from xyz_agent_context.agent_framework.model_probe_ledger import (
    FAIL,
    PASS,
    load_ledger,
    save_ledger,
    source_models,
)

# Concurrency cap for probe calls (keeps the initial seed sane without
# hammering the upstream). Steady-state runs probe only a handful of new models.
_PROBE_CONCURRENCY = 8
_PROBE_TIMEOUT = 60.0
_CATALOG_TIMEOUT = 30.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Catalog sources
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogSource:
    """How to discover + probe one provider's models."""
    name: str
    protocols: tuple[str, ...]
    openai_base: str
    anthropic_base: str
    fetch: Callable[[], Awaitable[dict[str, dict]]]  # () -> {model_id: meta}

    def base(self, protocol: str) -> str:
        return self.openai_base if protocol == "openai" else self.anthropic_base


async def _get_json(url: str, headers: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_CATALOG_TIMEOUT) as client:
        r = await client.get(url, headers=headers or {})
        r.raise_for_status()
        return r.json()


async def _fetch_netmind_catalog() -> dict[str, dict]:
    """NetMind public catalog → Chat models only. id = model_name."""
    data = await _get_json("https://api.netmind.ai/v1/model")
    out: dict[str, dict] = {}
    for m in data.get("models", []):
        if m.get("model_type") != "Chat":
            continue
        cfg = m.get("model_exhibition_config") or {}
        mid = m.get("model_name")
        if not mid:
            continue
        out[mid] = {
            "display_name": cfg.get("title") or mid,
            "context": cfg.get("context"),
        }
    return out


async def _fetch_openrouter_catalog() -> dict[str, dict]:
    """OpenRouter public catalog → text->text (chat) models. id = data[].id."""
    data = await _get_json("https://openrouter.ai/api/v1/models")
    out: dict[str, dict] = {}
    for m in data.get("data", []):
        arch = m.get("architecture") or {}
        ins = arch.get("input_modalities") or []
        outs = arch.get("output_modalities") or []
        if "text" not in ins or "text" not in outs:
            continue  # skip image/audio-only endpoints
        mid = m.get("id")
        if not mid:
            continue
        out[mid] = {
            "display_name": m.get("name") or mid,
            "context": m.get("context_length"),
        }
    return out


def _make_yunwu_catalog(api_key: str) -> Callable[[], Awaitable[dict[str, dict]]]:
    """Yunwu exposes an OpenAI-style /v1/models (needs the key)."""
    async def _fetch() -> dict[str, dict]:
        data = await _get_json(
            "https://yunwu.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        out: dict[str, dict] = {}
        for m in data.get("data", []):
            mid = m.get("id")
            if mid:
                out[mid] = {"display_name": mid, "context": None}
        return out
    return _fetch


# Static (catalog-independent) wiring per source. ``fetch`` for sources whose
# catalog needs a key (yunwu) is bound at sync time via ``catalog_source``.
_OPENAI_BASE = {
    "netmind": "https://api.netmind.ai/inference-api/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "yunwu": "https://yunwu.ai/v1",
}
_ANTHROPIC_BASE = {
    "netmind": "https://api.netmind.ai/inference-api/anthropic",
    "openrouter": "https://openrouter.ai/api",
    "yunwu": "https://yunwu.ai",
}


def catalog_source(source: str, *, yunwu_key: str | None = None) -> CatalogSource:
    """Build the CatalogSource for an in-scope source. ``system_pool`` maps to
    netmind (same backend)."""
    key = "netmind" if source in ("netmind", "system_pool") else source
    if key == "netmind":
        fetch = _fetch_netmind_catalog
    elif key == "openrouter":
        fetch = _fetch_openrouter_catalog
    elif key == "yunwu":
        if not yunwu_key:
            raise ValueError("yunwu catalog fetch requires a yunwu_key")
        fetch = _make_yunwu_catalog(yunwu_key)
    else:
        raise ValueError(f"source {source!r} is not in scope for model_sync")
    return CatalogSource(
        name=key,
        protocols=("openai", "anthropic"),
        openai_base=_OPENAI_BASE[key],
        anthropic_base=_ANTHROPIC_BASE[key],
        fetch=fetch,
    )


SUPPORTED_SOURCES = ("netmind", "system_pool", "openrouter", "yunwu")


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

async def _probe(
    client: httpx.AsyncClient, base: str, protocol: str, model: str, key: str
) -> bool:
    """A model `passes` a protocol iff a minimal completion returns HTTP 200."""
    base = base.rstrip("/")
    if protocol == "openai":
        url = f"{base}/chat/completions"
        payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    else:  # anthropic
        url = f"{base}/v1/messages"
        payload = {"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "hi"}]}
        headers = {
            "Authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    try:
        r = await client.post(url, json=payload, headers=headers, timeout=_PROBE_TIMEOUT)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001 — any transport error = not reachable
        logger.debug(f"probe {protocol} {model} failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Sync engine
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    source: str
    lists: dict[str, list[str]] = field(default_factory=dict)  # protocol -> passing model ids
    probed: int = 0     # how many (model, protocol) probes ran this pass
    added: list[str] = field(default_factory=list)    # new model ids seen
    removed: list[str] = field(default_factory=list)  # model ids dropped from catalog


async def sync_source(
    source: str,
    *,
    keys: dict[str, str],
    yunwu_key: str | None = None,
    reprobe_failed: bool = True,
    ledger: dict[str, Any] | None = None,
) -> SyncResult:
    """Fetch ``source``'s catalog, diff against the ledger, probe new + (optionally)
    previously-failed models, drop models gone from the catalog, persist the
    ledger, and return the passing per-protocol lists.

    ``keys`` maps protocol -> api key used to probe that protocol (same key works
    for both on these aggregators).
    """
    cs = catalog_source(source, yunwu_key=yunwu_key)
    owns_ledger = ledger is None
    if ledger is None:
        ledger = load_ledger()
    led = source_models(ledger, cs.name)

    catalog = await cs.fetch()
    res = SyncResult(source=cs.name)

    # Build the list of (model_id, protocol) pairs that need a probe this pass.
    to_probe: list[tuple[str, str]] = []
    for mid, meta in catalog.items():
        if mid not in led:
            res.added.append(mid)
            led[mid] = {**meta, "tested_at": _now()}
            to_probe += [(mid, p) for p in cs.protocols]
        else:
            led[mid].update(meta)  # refresh display/context — no call
            if reprobe_failed:
                to_probe += [(mid, p) for p in cs.protocols if led[mid].get(p) == FAIL]

    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        async def run(mid: str, proto: str) -> tuple[str, str, bool]:
            async with sem:
                ok = await _probe(client, cs.base(proto), proto, mid, keys[proto])
                return mid, proto, ok
        for mid, proto, ok in await asyncio.gather(*(run(m, p) for m, p in to_probe)):
            led[mid][proto] = PASS if ok else FAIL
            led[mid]["tested_at"] = _now()
    res.probed = len(to_probe)

    # Overwrite: drop models no longer in the catalog.
    for mid in [m for m in led if m not in catalog]:
        res.removed.append(mid)
        del led[mid]

    if owns_ledger:
        ledger["generated_at"] = _now()
        save_ledger(ledger)

    res.lists = {p: sorted(m for m, r in led.items() if r.get(p) == PASS) for p in cs.protocols}
    return res


# ---------------------------------------------------------------------------
# Apply the ledger to the DB (cloud daily job overwrites every user's lists)
# ---------------------------------------------------------------------------

async def apply_ledger_to_db(db, *, sources: list[str] | None = None) -> dict[str, dict[str, int]]:
    """Overwrite ``user_providers.models`` for EVERY row of the in-scope sources
    with the current ledger's per-protocol pass-lists.

    One bulk, dialect-safe ``db.update`` per (db-source, protocol) — the probe
    result is a backend property, so all users share it. ``system_pool`` rows
    are overwritten from the ``netmind`` ledger entry (same backend).

    Returns {db_source: {protocol: rows_updated}}.
    """
    import json

    ledger = load_ledger()
    now = _now()
    out: dict[str, dict[str, int]] = {}
    for key in sources or ["netmind", "openrouter", "yunwu"]:
        models_map = ledger.get("sources", {}).get(key, {}).get("models", {})
        if not models_map:
            continue
        db_sources = [key] + (["system_pool"] if key == "netmind" else [])
        for proto in ("openai", "anthropic"):
            passing = sorted(m for m, r in models_map.items() if r.get(proto) == PASS)
            payload = json.dumps(passing)
            for ds in db_sources:
                n = await db.update(
                    "user_providers",
                    {"source": ds, "protocol": proto},
                    {"models": payload, "updated_at": now},
                )
                out.setdefault(ds, {})[proto] = n
    return out


# ---------------------------------------------------------------------------
# CLI — refresh the committed ledger (used by the release pipeline + cron + dev)
# ---------------------------------------------------------------------------

async def _cli() -> int:
    """Refresh the ledger for every source we have a key for in the env.

    Keys (any that are present are synced):
      NETMIND_API_KEY  -> netmind (+ system_pool, same backend)
      OPENROUTER_API_KEY -> openrouter
      YUNWU_API_KEY    -> yunwu
    """
    import os

    ledger = load_ledger()
    plan = [
        ("netmind", os.environ.get("NETMIND_API_KEY"), None),
        ("openrouter", os.environ.get("OPENROUTER_API_KEY"), None),
        ("yunwu", os.environ.get("YUNWU_API_KEY"), os.environ.get("YUNWU_API_KEY")),
    ]
    any_run = False
    for source, key, yunwu_key in plan:
        if not key:
            logger.info(f"model_sync: no key for {source}, skipping")
            continue
        any_run = True
        res = await sync_source(
            source, keys={"openai": key, "anthropic": key},
            yunwu_key=yunwu_key, ledger=ledger,
        )
        logger.info(
            f"model_sync[{source}]: probed={res.probed} added={len(res.added)} "
            f"removed={len(res.removed)} openai={len(res.lists.get('openai', []))} "
            f"anthropic={len(res.lists.get('anthropic', []))}"
        )
    if not any_run:
        logger.warning("model_sync: no provider keys in env — nothing to sync")
        return 1
    ledger["generated_at"] = _now()
    save_ledger(ledger)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(_cli()))
