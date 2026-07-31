"""
@file_name: model_sync.py
@author:
@date: 2026-06-24
@description: Auto-discover provider models — fetch each aggregator's catalog,
probe which models actually answer per protocol (openai / anthropic), and
overwrite the per-(source, protocol) model lists. Dedup via the ledger
([[model_probe_ledger]]): NEW models are probed each run; models that
previously FAILED are re-probed (they can flip when the backend adds support);
models that PASSED are trusted only within a TTL — stale entries and
runtime-reported suspects are revalidated (capped per run, oldest first) and
flip pass -> fail ONLY on a definitive model error, never on transient noise.

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
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
from loguru import logger

from xyz_agent_context.agent_framework.providers.model_probe_ledger import (
    FAIL,
    PASS,
    load_ledger,
    passing_models,
    save_ledger,
    source_models,
)

# Concurrency cap for probe calls (keeps the initial seed sane without
# hammering the upstream). Steady-state runs probe only a handful of new models.
_PROBE_CONCURRENCY = 8
_PROBE_TIMEOUT = 60.0
_CATALOG_TIMEOUT = 30.0

# Stale-PASS revalidation: a pass verdict is trusted for this long, then the
# model re-enters the probe queue (oldest first, capped per run so a daily pass
# spreads the load instead of re-probing the whole catalog at once).
_REPROBE_TTL = timedelta(days=7)
_REVALIDATE_CAP = 80

# Probe verdicts. Only a definitive model-level rejection may flip an
# established PASS to FAIL — transient noise (rate limits, upstream blips,
# key/balance trouble) must never empty user dropdowns.
PROBE_OK = "ok"
PROBE_MODEL_ERROR = "model_error"
PROBE_TRANSIENT = "transient"

# Statuses that mean "this model does not exist / is not served here" as
# opposed to "the backend or key is having a moment". 402 (billing) and 429
# are deliberately excluded: NetMind answers 400-family codes for balance
# exhaustion on some routes, which is why the mass-failure guard below exists.
_MODEL_ERROR_STATUSES = {400, 404, 422}

# A revalidation pass with zero OK verdicts and this many definitive failures
# is treated as a key-/backend-wide outage (e.g. an overdrawn platform key
# turning every call into a 400): nothing is flipped.
_MASS_FLIP_GUARD_MIN = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(tested_at: Any) -> bool:
    """True when a ledger timestamp is missing, unparseable, or past the TTL."""
    if not isinstance(tested_at, str):
        return True
    try:
        ts = datetime.fromisoformat(tested_at)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > _REPROBE_TTL


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
) -> str:
    """Minimal completion against ``model`` -> a PROBE_* verdict.

    HTTP 200 = PROBE_OK; a definitive model rejection (400/404/422) =
    PROBE_MODEL_ERROR; anything else (429, 5xx, auth/billing, transport
    errors) = PROBE_TRANSIENT — unreachable is not evidence the model is gone.
    """
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
    except Exception as e:  # noqa: BLE001 — any transport error = not reachable
        logger.debug(f"probe {protocol} {model} failed: {e}")
        return PROBE_TRANSIENT
    if r.status_code == 200:
        return PROBE_OK
    if r.status_code in _MODEL_ERROR_STATUSES:
        return PROBE_MODEL_ERROR
    return PROBE_TRANSIENT


# ---------------------------------------------------------------------------
# Sync engine
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    source: str
    lists: dict[str, list[str]] = field(default_factory=dict)  # protocol -> passing model ids
    probed: int = 0        # how many new/failed (model, protocol) probes ran this pass
    revalidated: int = 0   # how many stale/suspect PASS entries were re-probed
    added: list[str] = field(default_factory=list)    # new model ids seen
    removed: list[str] = field(default_factory=list)  # model ids dropped from catalog
    flipped: list[str] = field(default_factory=list)  # "model:protocol" pass->fail flips


async def sync_source(
    source: str,
    *,
    keys: dict[str, str],
    yunwu_key: str | None = None,
    reprobe_failed: bool = True,
    ledger: dict[str, Any] | None = None,
    suspects: set[tuple[str, str]] | None = None,
    extra_models: dict[str, dict] | None = None,
) -> SyncResult:
    """Fetch ``source``'s catalog, diff against the ledger, probe new + (optionally)
    previously-failed models, revalidate stale/suspect PASS entries, drop models
    gone from the catalog, persist the ledger, and return the passing
    per-protocol lists.

    ``keys`` maps protocol -> api key used to probe that protocol (same key works
    for both on these aggregators). ``suspects`` is a set of (model_id, protocol)
    pairs reported as failing at runtime — they jump the TTL queue.

    ``extra_models`` are ids the backend serves that its public catalog does
    not list (the free-tier gateway routes several such NetMind models). They
    are probed and TTL-revalidated exactly like catalog models but marked
    ``extra`` in the ledger: never listed on the catalog cards (``res.lists``),
    only consulted by the free-tier gate. ``None`` means "caller doesn't know
    about extras" (the manual Update-models button) — existing extra entries
    are left untouched rather than treated as gone.
    """
    cs = catalog_source(source, yunwu_key=yunwu_key)
    owns_ledger = ledger is None
    if ledger is None:
        ledger = load_ledger()
    led = source_models(ledger, cs.name)
    suspects = suspects or set()

    catalog = await cs.fetch()
    if not catalog:
        # An empty catalog is an upstream fault (API shape change, outage), not
        # "every model was retired" — proceeding would wipe every user's lists.
        raise RuntimeError(f"{cs.name} catalog returned no models — refusing to wipe lists")
    extras = {m: meta for m, meta in (extra_models or {}).items() if m not in catalog}
    universe: dict[str, dict] = {**catalog, **extras}
    res = SyncResult(source=cs.name)

    # Build the list of (model_id, protocol) pairs that need a probe this pass:
    # ``to_probe`` = new + previously-failed (non-OK => FAIL, as before);
    # ``revalidate`` = PASS entries past the TTL or reported as runtime
    # suspects (flip rules are stricter — see below).
    to_probe: list[tuple[str, str]] = []
    revalidate: list[tuple[str, str]] = []
    for mid, meta in universe.items():
        is_new = mid not in led
        if is_new:
            res.added.append(mid)
            led[mid] = {**meta, "tested_at": _now()}
            to_probe += [(mid, p) for p in cs.protocols]
        elif mid in extras:
            for k, v in meta.items():  # bare-id extras meta only fills gaps
                led[mid].setdefault(k, v)
        else:
            led[mid].update(meta)  # refresh display/context — no call
        # The catalog wins: a model it lists sheds any extra marker; a
        # gateway-only id carries one so card lists can exclude it.
        if mid in extras:
            led[mid]["extra"] = True
        else:
            led[mid].pop("extra", None)
        if is_new:
            continue
        if reprobe_failed:
            to_probe += [
                (mid, p) for p in cs.protocols if led[mid].get(p) in (FAIL, None)
            ]
        stale = _is_stale(led[mid].get("tested_at"))
        revalidate += [
            (mid, p) for p in cs.protocols
            if led[mid].get(p) == PASS and (stale or (mid, p) in suspects)
        ]

    # Suspects first (a live user already hit them), then oldest first so the
    # daily cap drains the backlog in bounded, fair slices.
    revalidate.sort(
        key=lambda pair: (pair not in suspects, str(led[pair[0]].get("tested_at") or ""))
    )
    revalidate = revalidate[:_REVALIDATE_CAP]

    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        async def run(mid: str, proto: str) -> tuple[str, str, str]:
            async with sem:
                verdict = await _probe(client, cs.base(proto), proto, mid, keys[proto])
                return mid, proto, verdict

        probe_results = await asyncio.gather(*(run(m, p) for m, p in to_probe))
        reval_results = await asyncio.gather(*(run(m, p) for m, p in revalidate))

    for mid, proto, verdict in probe_results:
        # Transient trouble writes NOTHING: a missing verdict is "unknown",
        # which the free gate keeps and the next pass re-probes. Persisting it
        # as FAIL would hide the model for a day over one 429/timeout.
        if verdict == PROBE_OK:
            led[mid][proto] = PASS
        elif verdict == PROBE_MODEL_ERROR:
            led[mid][proto] = FAIL
        led[mid]["tested_at"] = _now()
    res.probed = len(to_probe)
    res.revalidated = len(revalidate)

    # Apply revalidation verdicts. Guard first: a pass with zero OK and a pile
    # of definitive failures looks like a key-/backend-wide outage (an
    # overdrawn key 400s on EVERY model) — flip nothing, keep the old clocks so
    # the next run retries.
    definitive = [r for r in reval_results if r[2] == PROBE_MODEL_ERROR]
    any_ok = any(v == PROBE_OK for _, _, v in reval_results)
    if not any_ok and len(definitive) >= _MASS_FLIP_GUARD_MIN:
        logger.error(
            f"model_sync[{cs.name}]: revalidation returned {len(definitive)} definitive "
            f"failures and 0 OK — treating as backend/key outage, flipping nothing"
        )
    else:
        for mid, proto, verdict in reval_results:
            if verdict == PROBE_MODEL_ERROR:
                led[mid][proto] = FAIL
                res.flipped.append(f"{mid}:{proto}")
            # PROBE_TRANSIENT: keep PASS -> retried next run.
        # ``tested_at`` is per-MODEL, so refresh it only when every revalidated
        # protocol answered decisively — one transient verdict keeps the old
        # clock and the whole model re-enters the queue next run. (A flipped
        # protocol is FAIL now and re-probed daily regardless of the clock.)
        transient_mids = {m for m, _, v in reval_results if v == PROBE_TRANSIENT}
        for mid in {m for m, _, _ in reval_results} - transient_mids:
            led[mid]["tested_at"] = _now()

    # Overwrite: drop models no longer in the catalog (or, when the caller
    # provided the authoritative extras set, no longer served as an extra
    # either). extra_models=None preserves existing extras — the button path
    # doesn't know the gateway list and must not purge the runner's entries.
    keep = set(universe)
    if extra_models is None:
        keep |= {m for m, r in led.items() if r.get("extra")}
    for mid in [m for m in led if m not in keep]:
        res.removed.append(mid)
        del led[mid]

    if owns_ledger:
        ledger["generated_at"] = _now()
        save_ledger(ledger)

    # Card lists carry catalog models only; extras are free-tier routing facts.
    res.lists = {p: sorted(passing_models(led, p)) for p in cs.protocols}
    return res


# ---------------------------------------------------------------------------
# Apply the ledger to the DB (cloud daily job overwrites every user's lists)
# ---------------------------------------------------------------------------

async def apply_ledger_to_db(
    db, *, sources: list[str] | None = None, ledger: dict[str, Any] | None = None
) -> dict[str, dict[str, int]]:
    """Overwrite ``user_providers.models`` for EVERY row of the in-scope sources
    with the current ledger's per-protocol pass-lists.

    One bulk, dialect-safe ``db.update`` per (db-source, protocol) — the probe
    result is a backend property, so all users share it. ``system_pool`` rows
    are overwritten from the ``netmind`` ledger entry (same backend).

    ``netmind_free`` is deliberately NOT in scope here: the free tier reaches
    NetMind through OUR gateway, which only routes (and only prices) the models
    it was configured with. Giving it the upstream's full catalogue would put
    choices in the dropdown that 400 on first use. Its list comes from the
    gateway itself — see ``refresh_free_tier_models``.

    Returns {db_source: {protocol: rows_updated}}.
    """
    import json

    if ledger is None:
        # Callers holding a fresher in-memory/DB ledger pass it in — the file
        # copy can be stale on a read-only cloud rootfs.
        ledger = load_ledger()
    now = _now()
    out: dict[str, dict[str, int]] = {}
    for key in sources or ["netmind", "openrouter", "yunwu"]:
        models_map = ledger.get("sources", {}).get(key, {}).get("models", {})
        if not models_map:
            continue
        db_sources = [key] + (["system_pool"] if key == "netmind" else [])
        for proto in ("openai", "anthropic"):
            passing = sorted(passing_models(models_map, proto))
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


def _free_tier_wallet_client():
    """Indirection seam so tests (and future transports) can swap the client."""
    from xyz_agent_context.integrations.free_tier.wallet_client import WalletClient

    return WalletClient.from_settings()


def apply_free_tier_gate(
    ledger: dict[str, Any], gateway_models: list[str]
) -> dict[str, list[str]]:
    """Gate the gateway's catalogue by the netmind probe verdicts and persist
    the result as the ledger's ``netmind_free`` source entry.

    Per protocol a gateway model is listed unless its netmind verdict is FAIL.
    An UNKNOWN verdict keeps the model: the gateway routes it today, and a
    probe outage must never empty the free dropdown — the extras union gets it
    probed on the next pass anyway. Writing the entry into the ledger makes
    the gate readable by the sync consumers too (``get_default_models`` seeds
    new free cards from it), so every writer agrees on one list.
    """
    netmind = source_models(ledger, "netmind")
    free = source_models(ledger, "netmind_free")
    free.clear()
    lists: dict[str, list[str]] = {"openai": [], "anthropic": []}
    for mid in gateway_models:
        verdicts = netmind.get(mid, {})
        entry: dict[str, Any] = {
            "display_name": verdicts.get("display_name") or mid,
            "context": verdicts.get("context"),
            "tested_at": verdicts.get("tested_at") or _now(),
        }
        for proto in ("openai", "anthropic"):
            entry[proto] = FAIL if verdicts.get(proto) == FAIL else PASS
            if entry[proto] == PASS:
                lists[proto].append(mid)
        free[mid] = entry
    return lists


def compute_drift(ledger: dict[str, Any], gateway_models: list[str]) -> dict[str, list[str]]:
    """What a human must reconcile between the gateway config and upstream.

    - ``gateway_failing``: gateway-served models whose netmind verdict is FAIL
      on some protocol — dead weight in the gateway config (already hidden
      from free cards by the gate; the config entry itself needs a human).
    - ``catalog_pass_not_in_gateway``: catalog models passing probes that the
      gateway does not serve — candidates to add, which is a pricing decision
      and therefore never automated.

    Transient trouble is invisible here by construction: verdicts only flip
    on definitive model errors, so overload/429/5xx never shows up as drift.
    """
    netmind = source_models(ledger, "netmind")
    gateway = set(gateway_models)
    # Only an id that fails on EVERY protocol is dead config. A lone-protocol
    # FAIL (most OSS models have no Anthropic endpoint) is normal and would
    # otherwise make this alert fire on every single pass — a permanently-lit
    # alarm is an alarm turned off (incident lesson #3).
    failing = sorted(
        mid
        for mid in gateway
        if all(
            netmind.get(mid, {}).get(proto) == FAIL
            for proto in ("openai", "anthropic")
        )
    )
    missing = sorted(
        mid
        for mid, r in netmind.items()
        if mid not in gateway
        and not r.get("extra")
        and (r.get("openai") == PASS or r.get("anthropic") == PASS)
    )
    return {"gateway_failing": failing, "catalog_pass_not_in_gateway": missing}


async def refresh_free_tier_models(db, *, ledger: dict[str, Any] | None = None) -> int:
    """Overwrite every free-tier card's model list: the GATEWAY's catalogue
    gated by the netmind probe verdicts (see ``apply_free_tier_gate``).

    Separate from ``apply_ledger_to_db`` on purpose: the free tier's routable
    set is whatever the gateway was configured with (which is also the set it
    can price), not whatever the upstream provider happens to sell. Runs in the
    same daily pass so a gateway config change reaches existing cards without a
    manual step.

    Returns the number of rows updated; 0 when the free tier is not configured.
    """
    import json

    from xyz_agent_context.agent_framework.providers.free_tier import (
        FREE_TIER_SOURCE,
    )
    from xyz_agent_context.integrations.free_tier.wallet_client import WalletError

    client = _free_tier_wallet_client()
    if client is None:
        return 0
    try:
        models = await client.served_models()
    except WalletError as e:  # noqa: BLE001 — never break the whole sync pass
        logger.warning(f"[model_sync] free-tier catalogue lookup failed: {e!r}")
        return 0
    if not models:
        return 0

    if ledger is None:
        from xyz_agent_context.agent_framework.providers.model_probe_ledger import (
            load_ledger_db,
        )

        ledger = await load_ledger_db(db) or load_ledger()
    lists = apply_free_tier_gate(ledger, models)

    now = _now()
    updated = 0
    for proto in ("openai", "anthropic"):
        updated += await db.update(
            "user_providers",
            {"source": FREE_TIER_SOURCE, "protocol": proto},
            {"models": json.dumps(lists[proto]), "updated_at": now},
        ) or 0
    logger.info(
        f"[model_sync] free-tier catalogue: {len(models)} gateway models -> "
        f"openai={len(lists['openai'])} anthropic={len(lists['anthropic'])} "
        f"-> {updated} row(s)"
    )
    return updated


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(_cli()))
