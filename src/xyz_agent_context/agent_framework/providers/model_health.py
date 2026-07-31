"""
@file_name: model_health.py
@author:
@date: 2026-07-30
@description: Runtime model-failure feedback — record (source, model, protocol)
tuples whose live calls hit a definitive model rejection, so the next model
sync revalidates them ahead of the TTL queue.

The probe ledger used to learn about dead models only when they vanished from
the upstream catalog; a model that stayed listed but stopped answering lived in
every user's dropdown forever. This module closes the loop: when an agent run
fails with a classified ``model_not_found`` (see [[failure]] — balance/429/5xx
never classify as that), the acting slot's binding is resolved back to its
provider card and stored as a suspect. Suspects only accelerate re-probing
([[model_sync]] treats them as stale immediately); the probe verdict stays
authoritative, so a spurious report costs one probe call and nothing else.
"""
from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

SUSPECTS_TABLE = "model_probe_suspects"

# Only sources the probe engine can actually revalidate. ``system_pool`` shares
# the netmind backend/ledger entry; everything else (OAuth CLIs, custom
# endpoints, the free-tier gateway card) has no probe path, so a report for
# them would sit in the table forever.
_PROBEABLE_SOURCES = {"netmind", "openrouter", "yunwu"}


def _normalize_source(source: str) -> str:
    return "netmind" if source == "system_pool" else source


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def report_model_suspect(
    db, *, source: str, protocol: str, model: str, reason: str
) -> bool:
    """Upsert a suspect row. Returns True when the report was recorded (i.e.
    the source is one the probe engine can revalidate)."""
    src = _normalize_source(source or "")
    if src not in _PROBEABLE_SOURCES or not model or not protocol:
        return False
    filters = {"source": src, "model_id": model, "protocol": protocol}
    now = _now()
    existing = await db.get_one(SUSPECTS_TABLE, filters)
    if existing:
        await db.update(SUSPECTS_TABLE, filters, {
            "occurrences": int(existing.get("occurrences") or 0) + 1,
            "reason": reason,
            "last_seen_at": now,
            "updated_at": now,
        })
    else:
        await db.insert(SUSPECTS_TABLE, {
            **filters,
            "reason": reason,
            "occurrences": 1,
            "last_seen_at": now,
        })
    logger.info(f"[model_health] suspect recorded: {src}/{model} ({protocol}) — {reason}")
    return True


async def load_suspects(db) -> dict[str, set[tuple[str, str]]]:
    """All stored suspects, grouped by source: {source: {(model_id, protocol)}}."""
    rows = await db.get(SUSPECTS_TABLE, filters={})
    out: dict[str, set[tuple[str, str]]] = {}
    for row in rows or []:
        out.setdefault(row["source"], set()).add((row["model_id"], row["protocol"]))
    return out


async def clear_suspects(db, source: str) -> None:
    """Drop every suspect for ``source`` — called after a sync pass revalidated
    them (probe result is now recorded in the ledger, the report served its
    purpose either way)."""
    await db.delete(SUSPECTS_TABLE, {"source": _normalize_source(source)})


async def report_agent_slot_suspect(
    db, *, user_id: str, agent_id: str, reason: str
) -> bool:
    """Resolve the acting agent-slot binding back to (provider source, protocol,
    model) and report it as a suspect.

    Mirrors the resolver's overlay order: an ``agent_slots`` row with a
    non-empty provider_id wins over the ``user_slots`` row. Resolution happens
    at report time (the error path), which can theoretically race a concurrent
    config change — acceptable, because a wrong suspect only triggers one extra
    probe. Best-effort by contract: returns False instead of raising.
    """
    try:
        slot = await db.get_one(
            "agent_slots", {"agent_id": agent_id, "slot_name": "agent"}
        )
        if not slot or not slot.get("provider_id"):
            slot = await db.get_one(
                "user_slots", {"user_id": user_id, "slot_name": "agent"}
            )
        if not slot or not slot.get("provider_id") or not slot.get("model"):
            return False
        prov = await db.get_one(
            "user_providers", {"provider_id": slot["provider_id"]}
        )
        if not prov:
            return False
        return await report_model_suspect(
            db,
            source=prov.get("source") or "",
            protocol=prov.get("protocol") or "",
            model=slot["model"],
            reason=reason,
        )
    except Exception as e:  # noqa: BLE001 — feedback must never break the error path itself
        logger.warning(f"[model_health] suspect report failed: {e!r}")
        return False
