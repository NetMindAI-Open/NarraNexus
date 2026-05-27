"""
@file_name: manyfold_agents.py
@author: NexusAgent
@date: 2026-05-25
@description: Cross-user agent listing + create endpoint for Manyfold platform

Manyfold needs to:
  - Enumerate all agents in the container regardless of which NarraNexus
    user created them (GET /manyfold/agents).
  - Create a new NarraNexus user + agent when a Manyfold user creates an
    agent via the Manyfold UI (POST /manyfold/agents).

Registered only when ENABLE_MANYFOLD_API=1 (see backend/main.py). The
auth middleware requires a valid MANYFOLD_GATEWAY_TOKEN before the
handler runs.

Owner decision 2026-05-25: container is single-user in practice so the
cross-user concern is mostly cosmetic, but the platform contract still
expects "list everything" semantics — we honor it.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid MANYFOLD_GATEWAY_TOKEN",
        )


@router.get("/manyfold/agents")
async def list_all_agents(request: Request):
    """Return every agent row in the container, cross-user.

    Shape mirrors what Manyfold's frameworkOptions expects (id + name +
    description), plus created_by / created_at for traceability.
    """
    _require_manyfold_auth(request)

    db = await get_db_client()
    rows = await db.get("agents", {}) or []
    return {
        "data": [
            {
                "agent_id": row.get("agent_id"),
                "name": row.get("agent_name"),
                "description": row.get("agent_description"),
                "agent_type": row.get("agent_type"),
                "created_by": row.get("created_by"),
                "created_at": row.get("agent_create_time"),
                "is_public": bool(row.get("is_public", 0)),
            }
            for row in rows
        ],
        "object": "list",
    }


# ---------------------------------------------------------------------------
# POST /manyfold/agents — Manyfold side creates an agent, we auto-create
# the matching NarraNexus user (if missing) and the agent row.
# ---------------------------------------------------------------------------


_USER_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def _normalize_user_id(manyfold_user_id: str) -> str:
    """Turn a Manyfold-side user_id into a NarraNexus-safe one.

    NarraNexus user_id is a TEXT primary key; we prefix with ``mf_`` to
    make it visually obvious where the row came from and to avoid
    collisions with native NarraNexus user_ids (``bin``, ``local-default``
    etc).
    """
    cleaned = _USER_ID_RE.sub("_", manyfold_user_id.strip())[:60]
    if not cleaned:
        raise ValueError("manyfold_user_id normalises to an empty string")
    return cleaned if cleaned.startswith("mf_") else f"mf_{cleaned}"


class ManyfoldCreateAgentRequest(BaseModel):
    """Body Manyfold's NarraNexus adapter sends when provisioning an agent.

    All fields except ``agent_id`` and ``manyfold_user_id`` are optional;
    we pick sensible defaults for the rest.
    """

    agent_id: str
    agent_name: str = ""
    description: Optional[str] = None
    manyfold_user_id: str
    manyfold_user_email: Optional[str] = None
    display_name: Optional[str] = None
    inherit_provider_from: Optional[str] = Field(
        default=None,
        description=(
            "Optional native NarraNexus user_id (e.g. 'bin') to copy "
            "provider rows and slot bindings from. Lets a Manyfold-side "
            "agent inherit a host operator's pre-configured LLM setup "
            "without forcing the end user to re-run /api/providers "
            "themselves on first chat."
        ),
    )


@router.post("/manyfold/agents")
async def create_agent_for_manyfold(
    request: Request,
    body: ManyfoldCreateAgentRequest,
):
    """Idempotent agent + user provisioning called by the Manyfold adapter.

    Behaviour:
      1. Ensure a NarraNexus user row exists for the Manyfold user
         (normalised id: ``mf_<sanitised>``). If the row is new, copy
         provider configuration from ``inherit_provider_from`` (when
         supplied and present) so chat works on the first turn.
      2. Ensure an agent row exists with ``created_by`` set to the user
         from step 1. If it already exists, just update the name /
         description to keep them in sync with Manyfold's side.

    Returns the canonical NarraNexus agent_id + the (possibly newly
    created) NarraNexus user_id so the caller can stash it.
    """
    _require_manyfold_auth(request)
    db = await get_db_client()

    nx_user_id = _normalize_user_id(body.manyfold_user_id)
    user_row = await db.get_one("users", {"user_id": nx_user_id})
    user_created = False
    if not user_row:
        await db.insert("users", {
            "user_id": nx_user_id,
            "user_type": "local",
            "role": "user",
            "display_name": body.display_name
                or body.manyfold_user_email
                or body.manyfold_user_id,
        })
        user_created = True
        logger.info(
            f"[manyfold-create] new NarraNexus user {nx_user_id!r} "
            f"(manyfold_id={body.manyfold_user_id!r})"
        )

    # If asked, mirror provider rows + slot bindings from a template user.
    # Skip on idempotent reruns where the user already had slots configured.
    if user_created and body.inherit_provider_from:
        try:
            await _clone_provider_setup(
                db, src_user_id=body.inherit_provider_from, dst_user_id=nx_user_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[manyfold-create] provider clone {body.inherit_provider_from} "
                f"→ {nx_user_id} failed: {exc}"
            )

    # Agent row — insert or update
    agent_row = await db.get_one("agents", {"agent_id": body.agent_id})
    if agent_row:
        await db.update("agents", {"agent_id": body.agent_id}, {
            "agent_name": body.agent_name or agent_row.get("agent_name") or body.agent_id,
            "agent_description": body.description or agent_row.get("agent_description"),
            "created_by": nx_user_id,
        })
        agent_created = False
    else:
        await db.insert("agents", {
            "agent_id": body.agent_id,
            "agent_name": body.agent_name or body.agent_id,
            "agent_description": body.description or "",
            "agent_type": "general",
            "created_by": nx_user_id,
            "is_public": 0,
        })
        agent_created = True

    logger.info(
        f"[manyfold-create] agent {body.agent_id!r} "
        f"{'created' if agent_created else 'updated'} "
        f"created_by={nx_user_id!r}"
    )

    return {
        "agent_id": body.agent_id,
        "user_id": nx_user_id,
        "user_created": user_created,
        "agent_created": agent_created,
    }


# ---------------------------------------------------------------------------
# PATCH /manyfold/agents/{agent_id} — Manyfold side pushes a rename /
# description edit so NarraNexus's own DB stays in sync with the
# Manyfold UI. See `narranexus/README.md` flow #3 on the Manyfold side
# for the bidirectional design (plan A = push, plan B = reconcile pull).
# ---------------------------------------------------------------------------


class ManyfoldUpdateAgentRequest(BaseModel):
    """Patch body — every field is optional. Absent fields are left untouched.

    NOTE: we deliberately split ``agent_name`` and ``agent_description``
    into separate optional fields rather than a single ``patch`` dict so
    the API contract is type-checked end-to-end (pydantic on this side,
    TypeScript on the caller side). Empty string is meaningful for
    ``agent_description`` (intentional clear) — use ``None`` / omit to
    skip the field.
    """

    agent_name: Optional[str] = Field(
        default=None,
        description="New display name. Omit to leave unchanged.",
        min_length=1,
        max_length=200,
    )
    agent_description: Optional[str] = Field(
        default=None,
        description=(
            "New description. Empty string is honored (clears the field); "
            "omit (None) to leave unchanged."
        ),
        max_length=2000,
    )


@router.patch("/manyfold/agents/{agent_id}")
async def update_agent_for_manyfold(
    agent_id: str,
    request: Request,
    body: ManyfoldUpdateAgentRequest,
):
    """Apply a Manyfold-initiated edit to NarraNexus's agent row.

    Updates only the fields present in the body. Returns the updated
    row (or 404 if the agent doesn't exist). The Manyfold side calls
    this BEFORE committing its own DB update so a failure here aborts
    the entire rename — keeping both sides consistent (vs the prior
    behaviour where Manyfold would silently drift if NarraNexus was
    unreachable).
    """
    _require_manyfold_auth(request)
    db = await get_db_client()

    agent_row = await db.get_one("agents", {"agent_id": agent_id})
    if not agent_row:
        raise HTTPException(
            status_code=404,
            detail=f"agent {agent_id!r} not found",
        )

    patch: dict[str, str] = {}
    if body.agent_name is not None:
        patch["agent_name"] = body.agent_name
    if body.agent_description is not None:
        patch["agent_description"] = body.agent_description

    if not patch:
        # No-op patches are legal — return the current row so the caller
        # can short-circuit without a second GET. Distinct from 4xx
        # because the contract is "absent field = no change", and the
        # empty case is just the degenerate version of that.
        return {
            "agent_id": agent_row.get("agent_id"),
            "name": agent_row.get("agent_name"),
            "description": agent_row.get("agent_description"),
            "updated_fields": [],
        }

    await db.update("agents", {"agent_id": agent_id}, patch)

    logger.info(
        f"[manyfold-update] {agent_id} patched fields={list(patch.keys())}"
    )

    updated = await db.get_one("agents", {"agent_id": agent_id})
    return {
        "agent_id": updated.get("agent_id") if updated else agent_id,
        "name": updated.get("agent_name") if updated else patch.get("agent_name"),
        "description": (
            updated.get("agent_description")
            if updated
            else patch.get("agent_description")
        ),
        "updated_fields": list(patch.keys()),
    }


# ---------------------------------------------------------------------------
# DELETE /manyfold/agents/{agent_id} — Manyfold side asks NarraNexus to fully
# remove a previously-provisioned agent (cascade through its derived data).
# ---------------------------------------------------------------------------


# Tables whose rows are owned by an agent and should disappear with it.
# Order matters: child rows first, then the agents row, so FK-like
# integrity assumptions hold even though SQLite isn't actually enforcing
# FKs on most of these columns. Independent of NarraNexus's schema_registry
# because we want to be explicit about the blast radius.
_AGENT_CASCADE_TABLES = (
    "events",
    "narratives",
    "mcp_urls",
    "agent_messages",
    "module_instances",
    "instance_jobs",
    "cost_records",
    "bus_channel_members",
    "bus_agent_registry",
    "bus_message_failures",
    "lark_credentials",
    "channel_slack_credentials",
    "channel_telegram_credentials",
    "lark_trigger_audit",
    "channel_trigger_audit",
    "team_members",
    "instance_artifacts",
)


@router.delete("/manyfold/agents/{agent_id}")
async def delete_agent_for_manyfold(agent_id: str, request: Request):
    """Cascade-delete a NarraNexus agent + all derived rows.

    Called by the Manyfold-side NarraNexusAgentAdapter.removeAgent when
    a user clicks "Delete agent" in Manyfold UI. We delete everything
    keyed by ``agent_id`` (narratives, events, module_instances, IM
    channel credentials, message bus state, artifacts, etc.) and then
    the agents row itself.

    We do NOT delete the user row even if this was their last agent —
    user_providers / user_slots are user-level and may be reused by
    future agents under the same user.

    Idempotent: missing agent → 404 only if there's also no historical
    data tied to the id; otherwise we cascade through whatever we find
    and return ``{deleted: true}``.
    """
    _require_manyfold_auth(request)

    db = await get_db_client()
    agent_row = await db.get_one("agents", {"agent_id": agent_id})
    deleted_counts: dict[str, int] = {}

    # Wipe child tables first. We don't short-circuit on missing agent
    # row because reconciler / partial-failure may have left orphan
    # rows we still want gone.
    for table in _AGENT_CASCADE_TABLES:
        try:
            existing = await db.get(table, {"agent_id": agent_id})
            if existing:
                await db.delete(table, {"agent_id": agent_id})
                deleted_counts[table] = len(existing)
        except Exception as exc:  # noqa: BLE001
            # Table might not exist in this NarraNexus revision; log
            # and continue — we'd rather over-delete than leave a
            # stale row that breaks listAgents.
            logger.warning(
                f"[manyfold-delete] cascade skipped table {table!r}: {exc}"
            )

    # Finally the agents row itself.
    if agent_row:
        await db.delete("agents", {"agent_id": agent_id})
        deleted_counts["agents"] = 1
    elif not deleted_counts:
        # Nothing at all matched — surface a 404 so callers don't think
        # they accidentally cascade-deleted an unrelated agent's data.
        raise HTTPException(status_code=404, detail=f"agent {agent_id!r} not found")

    logger.info(
        f"[manyfold-delete] {agent_id!r} cascade done: {deleted_counts}"
    )
    return {
        "deleted": True,
        "agent_id": agent_id,
        "cascade": deleted_counts,
    }


async def _clone_provider_setup(db, *, src_user_id: str, dst_user_id: str) -> None:
    """Copy user_providers + user_slots rows from src → dst.

    Critical: NarraNexus enforces per-user provider visibility (the
    provider_resolver refuses to serve a slot whose ``provider_id`` is
    owned by a different user). So we generate fresh ``provider_id``\\s
    for the destination user, build an old→new map, and rewrite the
    cloned slot bindings to point at those new ids. Without this remap,
    the destination's slot references the source's provider_id and the
    very first chat turn fails with ``provider X not visible``.
    """
    import secrets

    src_providers = await db.get("user_providers", {"user_id": src_user_id}) or []
    if not src_providers:
        logger.info(
            f"[manyfold-create] no providers to clone from {src_user_id!r}"
        )
        return

    existing_dst_provider_names = {
        row.get("name")
        for row in (await db.get("user_providers", {"user_id": dst_user_id}) or [])
    }

    pid_remap: dict[str, str] = {}
    for prov in src_providers:
        old_pid = prov.get("provider_id")
        if prov.get("name") in existing_dst_provider_names:
            continue
        new_pid = f"prov_{secrets.token_hex(4)}"
        pid_remap[old_pid] = new_pid
        clone = {k: v for k, v in prov.items() if k != "id"}
        clone["user_id"] = dst_user_id
        clone["provider_id"] = new_pid
        if clone.get("owner_user_id") == src_user_id:
            clone["owner_user_id"] = dst_user_id
        await db.insert("user_providers", clone)

    src_slots = await db.get("user_slots", {"user_id": src_user_id}) or []
    existing_dst_slot_names = {
        row.get("slot_name")
        for row in (await db.get("user_slots", {"user_id": dst_user_id}) or [])
    }
    for slot in src_slots:
        if slot.get("slot_name") in existing_dst_slot_names:
            continue
        clone = {k: v for k, v in slot.items() if k != "id"}
        clone["user_id"] = dst_user_id
        old_pid = clone.get("provider_id")
        clone["provider_id"] = pid_remap.get(old_pid, old_pid)
        await db.insert("user_slots", clone)

    logger.info(
        f"[manyfold-create] cloned {len(src_providers)} providers + "
        f"{len(src_slots)} slot bindings: {src_user_id} → {dst_user_id} "
        f"(pid remap: {pid_remap})"
    )
