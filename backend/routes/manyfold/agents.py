"""
@file_name: agents.py
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

from narranexus.platform.schema import (
    AGENT_TEXT_MAX_LENGTH,
    StrippedText,
    normalize_agent_row_text,
    normalize_agent_text,
)
from narranexus.platform.agent_profile import apply_agent_profile_change
from narranexus.platform.message_bus.agent_discovery_sync import sync_agent_discovery

from narranexus.platform.agent_framework.providers.cloud_policy import (
    NETMIND_SOURCE,
    netmind_slots_only,
)
from narranexus.platform.utils.db.db_factory import get_db_client
from backend.auth_errors import GATEWAY_TOKEN_INVALID, AuthError


router = APIRouter()


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise AuthError(
            GATEWAY_TOKEN_INVALID,
            "missing or invalid MANYFOLD_GATEWAY_TOKEN",
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
    # Capped at the shared agent-text ceiling — this body raw-writes the
    # `agents` row, so an over-long value would be unreadable through the
    # Agent model (see AGENT_TEXT_MAX_LENGTH).
    # StrippedText: the cap measures the value that will be STORED, so this
    # path and PUT /api/auth/agents answer the same for the same input.
    agent_name: StrippedText = Field(default="", max_length=AGENT_TEXT_MAX_LENGTH)
    description: Optional[StrippedText] = Field(
        default=None, max_length=AGENT_TEXT_MAX_LENGTH
    )
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


def _manyfold_failure(result) -> HTTPException:
    """Turn a transaction refusal into this endpoint's own answer.

    `result.error` is composed for a MODEL to read; forwarding it puts sentences
    like "Error: the update did not apply; nothing was changed" into a
    cross-service response body, where the other side has to pattern-match prose
    to learn what happened. auth.py maps on error_kind for the same reason.

    `not_found` keeps the documented 404. The rest are 400, and deliberately not
    409 for `not_applied`: 409 invites a retry and this contract is that a
    failure aborts the rename. Whether Manyfold retries a 409 is their policy.
    """
    detail = {
        "not_found": "agent not found",
        "not_applied": "the update did not persist (concurrent overwrite)",
        "too_long": f"name and description are limited to {AGENT_TEXT_MAX_LENGTH} characters",
        "empty_name": "agent name cannot be empty",
        "nothing_to_update": "no fields to update",
    }.get(result.error_kind or "", "the update could not be applied")
    return HTTPException(
        status_code=404 if result.error_kind == "not_found" else 400,
        detail={"error_kind": result.error_kind, "message": detail},
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
    # Normalized BEFORE the `or` fallbacks, not after: "   " is truthy, so a
    # fallback on the raw value is skipped and whitespace lands as the name.
    # And the row must hold normalized text or it can never be renamed —
    # the update path compares normalized values (see agent_field_matches).
    wanted_name = normalize_agent_text(body.agent_name)
    wanted_desc = normalize_agent_text(body.description)
    if agent_row:
        # An idempotent rerun on an EXISTING agent overwrites agent_name, which
        # is a rename however it is spelled — so it owes the same two things the
        # PATCH below does (identity correction + peer directory), and used to
        # do neither. Manyfold can push a new name through either verb; covering
        # only one leaves Shenzhen round 2 reproducible through the other.
        #
        # The `or` fallbacks stay HERE on purpose: this body defaults agent_name
        # to "", and the shared transaction REFUSES an empty name rather than
        # falling back, so passing "" through would fail provisioning outright.
        # It resolves to a non-empty value first, then delegates.
        #
        # created_by rides along as an extra to keep the row write single. Note
        # it is written in the same call whose name-clash check read the PREVIOUS
        # owner — that note is advisory and this path discards it, so the stale
        # scope has no consequence here.
        result = await apply_agent_profile_change(
            db,
            body.agent_id,
            new_name=(
                wanted_name or agent_row.get("agent_name") or body.agent_id
            ),
            new_description=(
                wanted_desc or agent_row.get("agent_description")
            ),
            created_by=nx_user_id,
        )
        if not result.ok:
            # Same error_kind → same status code as the PATCH below, so the
            # Manyfold side needs one mapping rather than one per verb. Not 409
            # for not_applied for the reason spelled out there: 409 invites a
            # retry and this contract is that a failure aborts the rename.
            raise _manyfold_failure(result)
        agent_created = False
    else:
        await db.insert("agents", normalize_agent_row_text({
            "agent_id": body.agent_id,
            "agent_name": wanted_name or body.agent_id,
            "agent_description": wanted_desc,
            "agent_type": "general",
            "created_by": nx_user_id,
            "is_public": 0,
        }))
        agent_created = True
        # Publish it to the peer directory NOW. This branch reached neither
        # `sync_agent_discovery` nor the shared transaction, so a
        # Manyfold-provisioned agent nobody had talked to yet did not exist in
        # `bus_agent_registry` at all — peers could
        # neither list it nor send to it until its first turn created instances
        # (InstanceFactory syncs there). "Its next turn will fix it" is exactly
        # what P1 section 02 rejected: an idle agent is the case that cannot
        # self-heal. Best-effort, like every other caller — provisioning must
        # not fail because discovery metadata could not be refreshed.
        #
        # NOT the last such writer: `bundle/importer.py` raw-inserts agents and
        # their instances without ever syncing, and renames on import (dedupe
        # suffix / clamp / empty-name fallback) while copying instance_awareness
        # verbatim. Tracked in
        # reference/self_notebook/todo/2026-08-18-bundle-import-identity-gap.md
        #
        # Deliberately NOT routed through apply_agent_profile_change: that
        # function's precondition is that the row already exists, and a brand
        # new agent has no previous name, so it has no identity correction to
        # record either.
        if not await sync_agent_discovery(db, body.agent_id):
            # sync swallows its own failures and returns False, so an unchecked
            # call here fails in total silence — and this branch exists BECAUSE
            # an idle agent has no second chance: nothing re-runs until its
            # first turn. Same wording as the transaction's _refresh_peer_directory.
            logger.warning(
                f"[manyfold-create] peer-directory sync failed for "
                f"{body.agent_id}; peers cannot discover it until its first turn"
            )

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

    # Both capped at the shared agent-text ceiling (patch raw-updates the
    # `agents` row; a longer value would deserialize-fail on read). Previously
    # 200 / 2000 — the 2000 was the fourth uncapped write path that could
    # re-create the #71 unreadable-row bug.
    # `min_length=1` is KEPT here, unlike CreateAgentBody in the
    # social-network route where it was removed. Different reason, different
    # answer: there, a 422 pre-empted a refusal whose exact wording an LLM
    # reads, so the two twins had to reach the same shared string. This
    # endpoint's consumer is the Manyfold service, for which a 422 IS the
    # contract — and since StrippedText normalizes first, "   " arrives as ""
    # and is rejected rather than stored as a whitespace name. Removing the
    # constraint by analogy would also let "" through into `patch` (built with
    # `is not None`) and silently blank the name.
    agent_name: Optional[StrippedText] = Field(
        default=None,
        description="New display name. Omit to leave unchanged.",
        min_length=1,
        max_length=AGENT_TEXT_MAX_LENGTH,
    )
    agent_description: Optional[StrippedText] = Field(
        default=None,
        description=(
            "New description. Empty string is honored (clears the field); "
            "omit (None) to leave unchanged."
        ),
        max_length=AGENT_TEXT_MAX_LENGTH,
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

    # The shared rename transaction, not a raw column write. A rename owes the
    # agent two more things than a row update: the identity correction inside
    # its Awareness profile (which is injected into the system prompt verbatim,
    # so a stale one makes the agent introduce itself by the old name — Shenzhen
    # round 2, P1) and a refreshed peer-discovery row. This path had NEITHER: it
    # was the only writer of agents.agent_name that never touched discovery at
    # all, so a Manyfold rename left every peer looking at the previous name
    # until the agent happened to take a turn. Normalization comes along with
    # it — the transaction stores normalized text by construction.
    result = await apply_agent_profile_change(
        db,
        agent_id,
        new_name=patch.get("agent_name"),
        new_description=patch.get("agent_description"),
    )
    if not result.ok:
        # Manyfold commits its own DB update only after this call succeeds, so
        # a refusal here must surface as a failure rather than a 200 whose body
        # happens to hold the old values — otherwise the two sides drift.
        #
        # not_found keeps this endpoint's documented 404 (the row can be deleted
        # between the check above and the write). Everything else is 400 —
        # deliberately NOT 409 for `not_applied`, though a concurrent overwrite
        # is what it means: 409 invites a retry, and this endpoint's contract is
        # that a failure ABORTS the whole rename. Whether Manyfold retries a 409
        # is that service's policy, not ours to assume from here.
        raise _manyfold_failure(result)

    logger.info(
        f"[manyfold-update] {agent_id} patched fields={list(patch.keys())}"
    )

    updated = await db.get_one("agents", {"agent_id": agent_id})
    return {
        # Same collision report the UI and the agent's own tool get: applied,
        # never blocked, never silent.
        "name_clash_with": result.name_clash_with,
        "identity_record_updated": result.identity_record_updated,
        "agent_id": updated.get("agent_id") if updated else agent_id,
        "name": updated.get("agent_name") if updated else patch.get("agent_name"),
        "description": (
            updated.get("agent_description")
            if updated
            else patch.get("agent_description")
        ),
        # What the caller REQUESTED, unchanged. Reporting what was written
        # instead reads better, and it was changed to that for one round — but
        # it is a semantic change on a contract the other side owns half of, and
        # its failure mode is the one this whole change exists to remove: an
        # idempotent resend of the same name would come back as [], and a
        # Manyfold that checks this field to confirm the rename landed would
        # read "did not happen" and retry, forever. #320's loop, moved to the
        # service boundary. Tidiness is not worth shipping that unilaterally;
        # if the other side wants written-fields, that is a change they agree to.
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

    # Cloud netmind-only policy: mf_* users are ordinary non-staff cloud
    # users, so this clone must not hand them a binding the gated slot
    # writers would refuse — keep NetMind-source rows only (slots pointing
    # at dropped providers are skipped below via the pid_remap check).
    # Without this, a template user holding a bring-your-own key would leak
    # an ACTIVE non-netmind binding past the entire policy.
    policy_active = netmind_slots_only(actor_is_staff=False)
    if policy_active:
        dropped = sum(1 for p in src_providers if p.get("source") != NETMIND_SOURCE)
        if dropped:
            logger.info(
                f"[manyfold-create] netmind-only policy dropped {dropped} "
                f"non-netmind provider(s) from the {src_user_id!r} clone"
            )
        src_providers = [
            p for p in src_providers if p.get("source") == NETMIND_SOURCE
        ]

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
        # str-normalized on BOTH sides (write here, read in the slot loop
        # below) so the remap lookup never misses on a type mismatch.
        old_pid = str(prov.get("provider_id") or "")
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
        old_pid = str(clone.get("provider_id") or "")
        if policy_active and old_pid not in pid_remap:
            # The provider this slot pointed at was dropped by the
            # netmind-only filter — cloning the binding would leave a
            # dangling (and policy-violating) reference.
            continue
        clone["provider_id"] = pid_remap.get(old_pid, old_pid)
        await db.insert("user_slots", clone)

    logger.info(
        f"[manyfold-create] cloned {len(src_providers)} providers + "
        f"{len(src_slots)} slot bindings: {src_user_id} → {dst_user_id} "
        f"(pid remap: {pid_remap})"
    )
