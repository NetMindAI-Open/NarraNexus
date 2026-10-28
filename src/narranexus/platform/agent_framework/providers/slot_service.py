"""
@file_name: slot_service.py
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

from narranexus.platform.agent_framework.providers.cloud_policy import (
    CloudPolicyViolation,
    ensure_slot_provider_allowed,
    framework_allowed_in_cloud,
    framework_locked_detail,
)
from narranexus.platform.agent_framework.providers.model_identity import (
    config_override_wins,
    effective_agent_slot,
    framework_of,
)
from narranexus.platform.agent_framework.providers.user_service import (
    validate_slot_binding,
)
from narranexus.platform.schema.provider_schema import SlotConfig, SlotName


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
        caller may only bind NetMind-source providers and may only pin a
        framework that cannot reach a shared CLI credential file. A trusted internal
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
            # ONE accessor for "which framework, given no explicit choice":
            # a bare "nexus_power" literal here disagreed with the resolver the
            # moment a distribution bound turn.pipeline.act.framework to
            # another plugin — the UI accepted a card this validated against
            # nexus_power and the turn then ran claude_code and rejected it.
            owner_framework = framework_of(owner_agent_slot)
            eff_framework = agent_framework or owner_framework
            # A per-agent pin is the same choice as the user-level switch,
            # so it asks cloud_policy rather than re-deriving a rule. Two
            # conditions, and both matter:
            #
            #   * the TARGET framework must be one cloud permits — keying
            #     off "differs from the owner's" instead locked a cloud
            #     user out of every framework the policy actually allows;
            #   * unless they ALREADY run it. A legacy user whose owner
            #     default is codex_cli gains no new exposure by pinning
            #     codex_cli again, and refusing it would block every
            #     agent-slot edit they make — a model change answered with
            #     a message about frameworks — while closing nothing. The
            #     way out of that state is the user-level switch back to
            #     claude_code, which providers.py deliberately allows.
            if (
                actor_is_staff is not None
                and not framework_allowed_in_cloud(eff_framework, actor_is_staff)
                and eff_framework != owner_framework
            ):
                raise CloudPolicyViolation(framework_locked_detail())
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

    async def _owner_agent_ids(self, owner_id: str) -> list[str]:
        """agent_id list owned by ``owner_id`` (agents.created_by).

        Projects to ``agent_id`` only — the ``agents`` table carries a fat
        ``agent_metadata`` MEDIUMTEXT column we would otherwise ship and
        discard on every call.
        """
        rows = await self.db.get(
            "agents", {"created_by": owner_id}, fields=["agent_id"]
        )
        return [r["agent_id"] for r in rows or [] if r and r.get("agent_id")]

    async def _slot_rows_by_agent(self, agent_ids: list[str]) -> Dict[str, list[dict]]:
        """All ``agent_slots`` rows for these agents, grouped by agent — ONE
        ``IN (...)`` query. ``get_by_ids`` cannot serve this: it builds an
        id → single-row map, and an agent has one row per slot."""
        out: Dict[str, list[dict]] = {aid: [] for aid in agent_ids}
        if not agent_ids:
            return out
        placeholders = ",".join(["%s"] * len(agent_ids))
        rows = await self.db.execute(
            "SELECT agent_id, slot_name, provider_id, model, agent_framework "
            f"FROM agent_slots WHERE agent_id IN ({placeholders})",
            tuple(agent_ids),
        )
        for r in rows or []:
            aid = r.get("agent_id")
            if aid in out:
                out[aid].append(r)
        return out

    async def count_owner_overrides(self, owner_id: str) -> Dict[str, int]:
        """Per-slot count of how many of ``owner_id``'s agents hold an
        *effective* override (a row whose provider_id is set), plus the owner's
        total agent count.

        Returns ``{<slot>: N for each SlotName, "total_agents": T}`` — used by
        the Model-Defaults confirm dialog to show the blast radius before a
        bulk apply. DB cost is 1 (agents) + 1 (agent_slots IN-query).
        """
        agent_ids = await self._owner_agent_ids(owner_id)
        per_slot: Dict[str, int] = {s.value: 0 for s in SlotName}
        by_agent = await self._slot_rows_by_agent(agent_ids)
        for rows in by_agent.values():
            for r in rows:
                slot = r.get("slot_name")
                if slot in per_slot and config_override_wins(r):
                    per_slot[slot] += 1
        return {**per_slot, "total_agents": len(agent_ids)}

    async def clear_owner_agents_slots(
        self, owner_id: str, slot_names: list[str]
    ) -> Dict[str, int]:
        """Clear each of ``slot_names`` for EVERY agent owned by ``owner_id``,
        reverting those agents to inherit the owner default on their next run.
        Returns ``{slot_name: cleared_count}``.

        The whole request is validated fail-closed FIRST (an unknown slot name
        raises ``ValueError`` before any delete, so a bad name never leaves a
        partial clear behind), slot names are order-preserving-deduped, and the
        owner's agent list is fetched ONCE and reused across slots — the
        orchestration lives here, not in the route.

        Fail-closed covers VALIDATION only: a runtime failure mid-loop (a DB
        blip, an audit insert that raises) can still leave an earlier agent
        cleared. That is recoverable — every deleted row was snapshotted to
        ``agent_slot_clear_audit`` before its delete, and re-running is
        idempotent (an already-cleared ``(agent_id, slot_name)`` is a no-op).
        """
        valid = {s.value for s in SlotName}
        slots = list(dict.fromkeys(slot_names))  # order-preserving dedup
        bad = [s for s in slots if s not in valid]
        if bad:
            raise ValueError(f"Invalid slot(s): {bad}")
        agent_ids = await self._owner_agent_ids(owner_id)
        return {s: await self._clear_one_slot(owner_id, s, agent_ids) for s in slots}

    async def _clear_one_slot(
        self, owner_id: str, slot_name: str, agent_ids: list[str]
    ) -> int:
        """Delete ``slot_name`` for the given ``agent_ids``, returning how many
        actually had a row.

        ALL rows for ``(agent_id, slot_name)`` are deleted, including any
        framework-only / empty-provider stub (those exist precisely to be
        cleared — only counting/display treat them as "not an override").
        Every deleted row is snapshotted into ``agent_slot_clear_audit`` BEFORE
        the delete, so an irreversible bulk clear stays recoverable — the
        snapshot passes NULL through verbatim (no ``or ""``) so a source
        ``agent_framework``/``params_json`` of NULL ("inherit" / "all auto")
        stays distinguishable from an empty string on restore. ``db.delete``
        has no IN semantics, so this deletes per agent_id.
        """
        cleared = 0
        for aid in agent_ids:
            existing = await self.db.get_one(
                "agent_slots", {"agent_id": aid, "slot_name": slot_name}
            )
            if not existing:
                continue
            await self.db.insert(
                "agent_slot_clear_audit",
                {
                    "user_id": owner_id,
                    "agent_id": aid,
                    "slot_name": slot_name,
                    "provider_id": existing.get("provider_id"),
                    "model": existing.get("model"),
                    "agent_framework": existing.get("agent_framework"),
                    "params_json": existing.get("params_json"),
                },
            )
            await self.db.delete(
                "agent_slots", {"agent_id": aid, "slot_name": slot_name}
            )
            cleared += 1
        return cleared

    async def owner_agents_overview(
        self, owner_id: str, agent_ids: Optional[list[str]] = None
    ) -> Dict[str, dict]:
        """Effective (agent + helper_llm) model per owned agent.

        Shape: ``{agent_id: {"agent": {"model": str, "inheriting": bool,
        "agent_framework": str}, "helper_llm": {"model": str, "inheriting":
        bool}}}``. ``inheriting`` is True when the agent has no *effective*
        override row for that slot (no row, or a stub with empty provider_id)
        and falls back to the owner default — matching the runtime resolver
        and the per-agent llm-config endpoint.

        ``agent_framework`` follows the identity overlay
        (``model_identity.effective_agent_slot``): an override supplies the
        framework only when it rebinds the slot with BOTH a provider and a
        framework; otherwise the owner's ``user_slots`` row does, and a
        missing/null column reads as the platform default. This is the same
        rule the driver dispatch and the system prompt use, so the directory
        can never show a brand the agent is not running.

        ``agent_ids`` narrows the result to those agents — but ownership is
        ALWAYS re-checked against ``agents.created_by`` here, never trusted
        from the caller: a caller passing someone else's public agent must
        not get that agent's configuration projected. That is the privacy
        boundary ``GET /api/auth/agents`` relies on.

        Feeds ``GET /api/auth/agents`` (the agents directory + the profile
        page), one of the hottest routes in the app, so the cost is flat:
        1 (user_slots) + 1 (agents, owner-scoped) + 1 (agent_slots IN-query),
        whatever the owner's agent count.
        """
        owned = await self._owner_agent_ids(owner_id)
        if agent_ids is not None:
            wanted = set(agent_ids)
            owned = [aid for aid in owned if aid in wanted]
        owner_rows = await self.db.get("user_slots", {"user_id": owner_id})
        owner_by_slot = {r.get("slot_name"): r for r in owner_rows or []}
        by_agent = await self._slot_rows_by_agent(owned)
        out: Dict[str, dict] = {}
        for aid in owned:
            override_by_slot = {r.get("slot_name"): r for r in by_agent.get(aid, [])}
            slots_view: Dict[str, dict] = {}
            for slot in (SlotName.AGENT.value, SlotName.HELPER_LLM.value):
                override = override_by_slot.get(slot)
                owner_default = owner_by_slot.get(slot)
                if config_override_wins(override):
                    view = {"model": (override or {}).get("model") or "", "inheriting": False}
                else:
                    view = {"model": (owner_default or {}).get("model") or "", "inheriting": True}
                if slot == SlotName.AGENT.value:
                    view["agent_framework"] = framework_of(
                        effective_agent_slot(override, owner_default)
                    )
                slots_view[slot] = view
            out[aid] = slots_view
        return out


__all__ = ["AgentSlotService"]
