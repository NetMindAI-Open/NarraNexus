"""
@file_name: agent_slot_service.py
@author: rujing.yan
@date: 2026-07-09
@description: Per-agent LLM slot OVERRIDES (agent_slots table).

An agent inherits its owner's user-level slots (``user_slots``) by default.
This service writes/reads the optional per-agent overrides that let a single
agent pin its own coding-agent framework + model (agent slot) and its own
helper model (helper_llm slot), independent of the owner default and of the
owner's other agents.

The overlay itself lives in ``provider_driver.resolver`` (a per-agent row wins
over the user default at resolve time); this service is only the writer/reader
for the ``agent_slots`` rows. The binding rules (protocol / codex-source /
helper-OAuth) are enforced through the SAME ``validate_slot_binding`` the
user-level writer uses, so a per-agent override can never bind an
incompatible provider.

Scope note: only the own-provider resolution path honours these overrides;
the cloud SYSTEM free-tier pool is a fixed one-model config and ignores them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from xyz_agent_context.agent_framework.cloud_policy import (
    FRAMEWORK_LOCKED_DETAIL,
    CloudPolicyViolation,
    ensure_slot_provider_allowed,
    netmind_slots_only,
)
from xyz_agent_context.agent_framework.user_provider_service import (
    validate_slot_binding,
)
from xyz_agent_context.schema.provider_schema import SlotConfig, SlotName


class AgentSlotService:
    """CRUD for per-agent slot overrides (``agent_slots``)."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def get_agent_slots(self, agent_id: str) -> Dict[str, dict]:
        """Return this agent's override rows keyed by slot_name (may be empty)."""
        rows = await self.db.get("agent_slots", {"agent_id": agent_id})
        return {r["slot_name"]: r for r in rows or [] if r.get("slot_name")}

    async def get_agent_slot(
        self, agent_id: str, slot_name: str
    ) -> Optional[dict]:
        return await self.db.get_one(
            "agent_slots", {"agent_id": agent_id, "slot_name": slot_name}
        )

    async def _owner_of(self, agent_id: str) -> str:
        agent_row = await self.db.get_one("agents", {"agent_id": agent_id})
        owner = (agent_row or {}).get("created_by")
        if not owner:
            raise ValueError(f"Agent {agent_id!r} not found or has no owner.")
        return owner

    async def set_agent_slot(
        self,
        agent_id: str,
        slot_name: str,
        provider_id: str,
        model: str,
        thinking: str = "",
        reasoning_effort: str = "",
        agent_framework: Optional[str] = None,
        *,
        actor_is_staff: Optional[bool],
    ) -> dict:
        """Upsert a per-agent override for ``slot_name``.

        PUT semantics (mirrors ``UserProviderService.set_slot``): every call
        writes the full param set; omitted reasoning params reset to "" (auto).

        The provider must belong to the agent's OWNER (providers are
        user-scoped). For the agent slot, ``agent_framework`` is the per-agent
        framework being pinned; if omitted it defaults to the owner's current
        framework. Validation reuses ``validate_slot_binding``.

        ``actor_is_staff`` (required keyword): the caller's role for the
        cloud netmind-only policy (see cloud_policy) — a non-staff cloud
        caller may only bind NetMind-source providers and may not pin a
        framework that differs from the owner default. A trusted internal
        caller must write ``actor_is_staff=None`` EXPLICITLY (deliberately
        no default — a bypass has to be visible at the call site). Raises
        ``CloudPolicyViolation`` (→ 403 at the route) on a violation.
        """
        if slot_name not in [s.value for s in SlotName]:
            raise ValueError(f"Invalid slot: {slot_name}")

        # Validate neutral params through the schema (rejects dialect words).
        params_model = SlotConfig(
            provider_id=provider_id,
            model=model,
            thinking=thinking,  # type: ignore[arg-type]
            reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
        )
        params_json = json.dumps(
            {
                "thinking": params_model.thinking,
                "reasoning_effort": params_model.reasoning_effort,
            },
            sort_keys=True,
        )

        owner = await self._owner_of(agent_id)
        prov = await self.db.get_one(
            "user_providers", {"user_id": owner, "provider_id": provider_id}
        )
        if not prov:
            raise ValueError(
                f"Provider {provider_id!r} not found for the agent's owner."
            )

        # Cloud netmind-only policy (single source of truth: cloud_policy).
        ensure_slot_provider_allowed(prov, actor_is_staff)

        # Resolve the framework the binding is validated against. Only the
        # agent slot carries a framework; for the agent slot, a per-agent
        # framework (if given) wins, else fall back to the owner default.
        eff_framework: Optional[str] = None
        if slot_name == SlotName.AGENT.value:
            owner_agent_slot = await self.db.get_one(
                "user_slots", {"user_id": owner, "slot_name": "agent"}
            )
            owner_framework = (
                (owner_agent_slot or {}).get("agent_framework") or "claude_code"
            )
            eff_framework = agent_framework or owner_framework
            # Framework changes are staff-only on cloud (the user-level
            # switch is gated in providers.py); a per-agent pin to a
            # DIFFERENT framework would be the same change through the
            # side door, so it follows the same policy.
            if (
                actor_is_staff is not None
                and eff_framework != owner_framework
                and netmind_slots_only(actor_is_staff)
            ):
                raise CloudPolicyViolation(FRAMEWORK_LOCKED_DETAIL)
        validate_slot_binding(prov, slot_name, eff_framework)

        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "provider_id": provider_id,
            "model": model,
            "params_json": params_json,
            "updated_at": now,
        }
        if slot_name == SlotName.AGENT.value and eff_framework:
            payload["agent_framework"] = eff_framework

        # Check-then-write upsert — mirrors UserProviderService.set_slot. Not
        # atomic: two concurrent PUTs on the same (agent_id, slot_name) could
        # both miss the row and race the unique index (idx_as_agent_slot),
        # surfacing as a 500 on the loser. The UI saves a single slot at a
        # time so it doesn't fire in practice; kept consistent with the
        # existing user-slot writer rather than introducing a dialect-specific
        # upsert here.
        existing = await self.db.get_one(
            "agent_slots", {"agent_id": agent_id, "slot_name": slot_name}
        )
        if existing:
            await self.db.update(
                "agent_slots",
                {"agent_id": agent_id, "slot_name": slot_name},
                payload,
            )
        else:
            await self.db.insert(
                "agent_slots",
                {
                    "agent_id": agent_id,
                    "slot_name": slot_name,
                    "created_at": now,
                    **payload,
                },
            )
        return await self.get_agent_slot(agent_id, slot_name)  # type: ignore[return-value]

    async def clear_agent_slot(
        self, agent_id: str, slot_name: Optional[str] = None
    ) -> None:
        """Delete one override (``slot_name`` given) or all of the agent's
        overrides (``slot_name=None``) — reverting the affected slot(s) to
        inherit the owner default on the next run."""
        filters: Dict[str, Any] = {"agent_id": agent_id}
        if slot_name:
            filters["slot_name"] = slot_name
        await self.db.delete("agent_slots", filters)


__all__ = ["AgentSlotService"]
