"""
@file_name: model_sync_runner.py
@author:
@date: 2026-06-24
@description: Background runner for the model auto-discovery sync ([[model_sync]]).

Cloud: runs daily at 05:00 UTC — refresh the ledger (probe new/failed models with
the platform key) and OVERWRITE every user's provider model lists from it.
Release/CI & dev: run once (no --loop) to refresh + commit the ledger snapshot.

Run modes:
    python -m xyz_agent_context.services.model_sync_runner          # one pass, exit
    python -m xyz_agent_context.services.model_sync_runner --loop   # daily 05:00 UTC

Keys are read from env (only sources with a key are synced):
    NETMIND_API_KEY -> netmind (+ system_pool)   OPENROUTER_API_KEY   YUNWU_API_KEY
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

from loguru import logger

from xyz_agent_context.agent_framework.providers import model_health, model_sync
from xyz_agent_context.agent_framework.providers.model_probe_ledger import (
    load_ledger,
    load_ledger_db,
    save_ledger,
    save_ledger_db,
)

DAILY_HOUR_UTC = 5


def _seconds_until(hour_utc: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_once() -> dict:
    """One full pass: refresh the ledger for every keyed source, then overwrite
    every user's provider lists in the DB. Returns a summary dict."""
    from xyz_agent_context.utils.db.db_factory import get_db_client
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    plan = [
        ("netmind", os.environ.get("NETMIND_API_KEY"), None),
        ("openrouter", os.environ.get("OPENROUTER_API_KEY"), None),
        ("yunwu", os.environ.get("YUNWU_API_KEY"), os.environ.get("YUNWU_API_KEY")),
    ]
    db = await get_db_client()
    # This runner is its own process/container — make sure the ledger/suspects
    # tables exist even when it wins the startup race against the backend.
    await auto_migrate(db._backend)

    # DB carrier first (durable across deploys); committed file only seeds the
    # very first pass after this table ships.
    ledger = await load_ledger_db(db) or load_ledger()
    suspects = await model_health.load_suspects(db)
    synced: list[str] = []
    logger.info("model_sync_runner: pass START")

    # The free-tier gateway's catalogue, fetched once: it feeds the netmind
    # probe pass (gateway-only models get probed as extras), the free-card
    # gate, and the drift report. Unreachable gateway -> None extras, so
    # existing extra entries survive untouched.
    gateway_models: list[str] = []
    try:
        wallet = model_sync._free_tier_wallet_client()
        if wallet is not None:
            gateway_models = list(await wallet.served_models() or [])
    except Exception as e:  # noqa: BLE001 — gateway trouble must not kill the pass
        logger.warning(f"model_sync_runner: free-tier gateway catalogue unavailable: {e!r}")

    for source, key, yunwu_key in plan:
        if not key:
            continue
        extras = (
            {m: {"display_name": m} for m in gateway_models}
            if source == "netmind" and gateway_models
            else None
        )
        try:
            res = await model_sync.sync_source(
                source, keys={"openai": key, "anthropic": key},
                yunwu_key=yunwu_key, ledger=ledger,
                suspects=suspects.get(source),
                extra_models=extras,
            )
            synced.append(source)
            logger.info(
                f"model_sync_runner[{source}]: probed={res.probed} "
                f"revalidated={res.revalidated} flipped={res.flipped or '[]'} "
                f"added={len(res.added)} removed={len(res.removed)} "
                f"openai={len(res.lists.get('openai', []))} "
                f"anthropic={len(res.lists.get('anthropic', []))}"
            )
        except Exception as e:  # noqa: BLE001 — one source failing must not abort the rest
            logger.exception(f"model_sync_runner[{source}]: FAILED: {e}")

    if not synced:
        logger.warning("model_sync_runner: no provider keys in env — nothing synced")
        return {"synced": [], "applied": {}}

    # Free cards first: refresh_free_tier_models runs the gate, which ALSO
    # writes the netmind_free entry into this in-memory ledger — doing it
    # before the saves means both carriers ship the gated entry (provisioning
    # seeds read it) in the same pass that computed the verdicts.
    free_tier = await model_sync.refresh_free_tier_models(db, ledger=ledger)

    ledger["generated_at"] = model_sync._now()
    save_ledger(ledger)          # best-effort container-file copy
    await save_ledger_db(db, ledger)
    for source in synced:
        await model_health.clear_suspects(db, source)

    applied = await model_sync.apply_ledger_to_db(db, sources=synced, ledger=ledger)

    # Drift between the gateway config and the upstream catalog is a human
    # decision (pricing) — surface it, never act on it. The [MODEL-DRIFT]
    # WARNING is the deploy-side watcher's alert signature; the durable record
    # rides the pass heartbeat below (incident lesson #5) — drift is routine
    # reconciliation output, not an error event.
    drift: dict = {}
    if gateway_models and "netmind" in synced:
        drift = model_sync.compute_drift(ledger, gateway_models)
        if drift["gateway_failing"] or drift["catalog_pass_not_in_gateway"]:
            logger.warning(
                "[MODEL-DRIFT] netmind vs free-tier gateway: "
                f"gateway_failing={drift['gateway_failing']} "
                f"catalog_pass_not_in_gateway={drift['catalog_pass_not_in_gateway']}"
            )

    logger.info(
        f"model_sync_runner: pass DONE synced={synced} applied={applied} "
        f"free_tier_rows={free_tier}"
    )
    return {
        "synced": synced,
        "applied": applied,
        "free_tier_rows": free_tier,
        "drift": drift,
    }


async def run_loop() -> None:
    # L2 lifecycle (incident lesson #4): started/heartbeat/stopped in
    # service_audit so "the container is up but hasn't synced in days" is a
    # SQL query, not a guess. One heartbeat per pass — its detail carries the
    # pass summary INCLUDING drift, which is routine reconciliation output and
    # must not pollute event_type='error' rows.
    from xyz_agent_context.services.service_audit import ServiceAuditor

    audit = ServiceAuditor("model_sync")
    logger.info(f"model_sync_runner: loop mode, firing daily at {DAILY_HOUR_UTC:02d}:00 UTC")
    await audit.started({"daily_hour_utc": DAILY_HOUR_UTC})
    try:
        while True:
            delay = _seconds_until(DAILY_HOUR_UTC)
            logger.info(f"model_sync_runner: next run in {delay/3600:.1f}h")
            await asyncio.sleep(delay)
            try:
                summary = await run_once()
                await audit.heartbeat(summary, force=True)
            except Exception as e:  # noqa: BLE001 — loop must survive any single failure
                logger.exception(f"model_sync_runner: pass crashed: {e}")
                await audit.error({"error": str(e)[:500]})
    finally:
        await audit.stopped()


def main() -> int:
    import sys

    if "--loop" in sys.argv:
        asyncio.run(run_loop())
        return 0
    asyncio.run(run_once())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
