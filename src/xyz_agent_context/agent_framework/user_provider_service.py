"""
@file_name: user_provider_service.py
@author: NexusAgent
@date: 2026-04-08
@description: Per-user LLM provider configuration service

Manages provider and slot configurations per user in the database.
Replaces the global llm_config.json for multi-tenant cloud deployments.

In local mode (SQLite), falls back to llm_config.json for backward compatibility.
In cloud mode (MySQL), all provider configs are stored in user_providers and user_slots tables.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger
from pydantic import ValidationError

from xyz_agent_context.agent_framework.cloud_policy import (
    ensure_slot_provider_allowed,
)
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
    SlotName,
    get_slot_required_protocols,
)


def _is_cloud_mode() -> bool:
    """Deprecated local name; routes to the single source of truth in
    ``utils.deployment_mode``. Kept as a thin adapter so any external
    callers that import this symbol keep working."""
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode
    return is_cloud_mode()


# Canonical curated model list for OpenAI's OWN codex backend (the
# ``codex_oauth`` ChatGPT-account login). Codex CLI's interactive picker
# decides which models its subprocess accepts on api.openai.com — same set
# for ChatGPT-account OAuth AND paid-API-key tier (verified 2026-06-02).
#
# Scope is deliberately narrow: this list is the source of truth ONLY for
# ``codex_oauth`` (OpenAI's backend gates by account tier). A third-party
# openai-protocol provider (netmind / yunwu / openrouter / custom base_url)
# exposes ITS OWN model catalogue and is not constrained to this list — the
# user picks whatever that endpoint serves.
#
# One consumer: ``UserProviderService.get_user_config`` overrides the stored
# ``models`` column on a ``codex_oauth`` row at read time, so updating the
# constant propagates without a DB migration. The frontend mirrors the same
# codex_oauth-only scoping in ``agentFramework.ts::getModelsForSlot``.
#
# Verified 2026-06-02 by running interactive ``codex`` and reading
# "Select Model and Effort" menu.
CODEX_CURATED_MODELS = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"]


def validate_slot_binding(
    prov: Dict[str, Any], slot_name: str, agent_framework: Optional[str]
) -> None:
    """Validate that provider ``prov`` (a ``user_providers`` row) can back
    ``slot_name`` under ``agent_framework``. Raises ``ValueError`` on mismatch.

    Single source of truth for the three binding rules, shared by the
    user-level slot writer (``UserProviderService.set_slot``) and the per-agent
    override writer (``AgentSlotService.set_agent_slot``) — the same rules must
    guard a per-agent override, or the misbinding only surfaces at agent-loop
    time as a cryptic NotImplementedError / runtime failure.

    Rules:
      1. Protocol — the agent slot follows the framework (claude_code →
         anthropic, codex_cli → openai); other slots keep their static
         requirement. This is the ONLY gate on the codex agent slot: any
         openai-protocol provider (codex_oauth / user / netmind / yunwu /
         openrouter) is accepted. Codex targets OpenAI's Responses API
         (wire_api="responses"); whether a given endpoint actually serves it
         is the provider's characteristic, not something the platform polices
         at config time (binding rule #15). A mismatch surfaces at agent-loop
         time — the same accepted cost as any user-chosen endpoint.
      2. helper_llm — ACCEPTS OAuth providers (claude_oauth / codex_oauth): a
         subscription login covers both slots. The OAuth credential can't make
         DIRECT Messages / Chat-Completions calls, but the resolver routes an
         OAuth helper to a CliHelperConfig and CliHelperSDK runs the helper's
         structured calls one-shot through the same CLI as the agent
         (build_cli_helper_config) — so no reject here.
    """
    required = get_slot_required_protocols(slot_name, agent_framework=agent_framework)
    if required and prov["protocol"] not in [p.value for p in required]:
        raise ValueError(
            f"Slot '{slot_name}' requires protocol {[p.value for p in required]}, "
            f"got '{prov['protocol']}'"
        )

    # helper_llm ACCEPTS OAuth (claude_oauth / codex_oauth) — no reject: the
    # resolver routes an OAuth helper to a CliHelperConfig and CliHelperSDK runs
    # its structured calls one-shot through the same CLI as the agent, so a
    # single subscription covers both slots (2026-07 "subscription covers
    # helper"). Removing the guard here also lets a per-agent override
    # (AgentSlotService, which shares this validator) bind an OAuth helper.


def _generate_provider_id() -> str:
    return f"prov_{uuid4().hex[:8]}"


def _generate_group_id() -> str:
    return f"grp_{uuid4().hex[:8]}"


class UserProviderService:
    """
    Per-user provider management via database.

    Methods mirror provider_registry's API but are scoped to a user_id.
    """

    def __init__(self, db_client):
        self.db = db_client

    # =========================================================================
    # Read
    # =========================================================================

    async def get_user_config(self, user_id: str) -> LLMConfig:
        """Load a user's provider config from DB, returning an LLMConfig object."""
        # Get providers
        rows = await self.db.get("user_providers", filters={"user_id": user_id})
        providers = {}
        for row in rows:
            # supports_anthropic_server_tools is a newer column. Old rows
            # pre-dating the migration won't have it; default False so we
            # err on the side of disabling WebSearch rather than hanging it.
            _server_tools = row.get("supports_anthropic_server_tools", 0)
            # ``codex_oauth`` provider's model list is NOT user-customizable —
            # codex CLI's interactive picker is the source of truth. Always
            # override the stored ``models`` column with the current
            # ``CODEX_CURATED_MODELS`` constant so a code-side update
            # propagates to existing rows on the next Settings reload, no
            # DB migration needed.
            if row.get("source") == "codex_oauth":
                stored_models = list(CODEX_CURATED_MODELS)
            elif row.get("models"):
                stored_models = json.loads(row["models"])
            else:
                stored_models = []
            prov = ProviderConfig(
                provider_id=row["provider_id"],
                name=row["name"],
                source=row["source"],
                protocol=row["protocol"],
                auth_type=row.get("auth_type", "api_key"),
                api_key=row.get("api_key", ""),
                base_url=row.get("base_url", ""),
                models=stored_models,
                linked_group=row.get("linked_group", ""),
                is_active=bool(row.get("is_active", 1)),
                supports_anthropic_server_tools=bool(_server_tools),
            )
            providers[prov.provider_id] = prov

        # Get slots
        slot_rows = await self.db.get("user_slots", filters={"user_id": user_id})
        slots = {}
        for row in slot_rows:
            # params_json holds the framework-neutral per-slot params
            # (thinking, reasoning_effort, ...). Pre-migration rows have
            # NULL; corrupt or out-of-vocabulary content degrades to auto
            # ("") rather than failing the whole config load — a broken
            # tuning knob must never take the agent offline.
            params: dict = {}
            raw_params = row.get("params_json")
            if raw_params:
                try:
                    parsed = json.loads(raw_params)
                    if isinstance(parsed, dict):
                        params = parsed
                except (ValueError, TypeError):
                    logger.warning(
                        f"user_slots.params_json for user={user_id} "
                        f"slot={row['slot_name']} is not valid JSON; "
                        f"falling back to auto params"
                    )
            try:
                slot = SlotConfig(
                    provider_id=row["provider_id"],
                    model=row["model"],
                    thinking=params.get("thinking", ""),
                    reasoning_effort=params.get("reasoning_effort", ""),
                )
            except ValidationError:
                logger.warning(
                    f"user_slots.params_json for user={user_id} "
                    f"slot={row['slot_name']} has out-of-vocabulary values "
                    f"{params}; falling back to auto params"
                )
                slot = SlotConfig(
                    provider_id=row["provider_id"], model=row["model"]
                )
            slots[row["slot_name"]] = slot

        return LLMConfig(providers=providers, slots=slots)

    # =========================================================================
    # Add Provider
    # =========================================================================

    async def add_provider(
        self,
        user_id: str,
        card_type: str,
        name: str = "",
        api_key: str = "",
        base_url: str = "",
        auth_type: str = "api_key",
        models: Optional[List[str]] = None,
        replace: bool = False,
        inference_base: Optional[str] = None,
    ) -> tuple[LLMConfig, list[str]]:
        """Add a provider for a user. Returns (updated_config, new_provider_ids).

        ``replace=True`` skips the per-source uniqueness guard for aggregator
        card types so the key-rotation (replace) flow can insert a fresh
        provider pair alongside the old one before deleting it (expand-contract
        — see ``onboard_one_key``). New rows get fresh random provider_ids, so
        there is no primary-key collision with the rows being replaced.
        """

        new_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        if card_type in ("netmind", "yunwu", "openrouter"):
            # Check uniqueness (unless the caller is mid-replace).
            if not replace:
                existing = await self.db.get("user_providers", filters={"user_id": user_id, "source": card_type})
                if existing:
                    raise ValueError(f"A {card_type} provider already exists for this user")

            group_id = _generate_group_id()
            configs = _build_dual_providers(
                card_type, api_key, group_id, models, inference_base=inference_base
            )
            for cfg in configs:
                await self._insert_provider(user_id, cfg, now)
                new_ids.append(cfg["provider_id"])

        elif card_type == "claude_oauth":
            existing = await self.db.get("user_providers", filters={"user_id": user_id, "source": "claude_oauth"})
            if existing:
                raise ValueError("Claude OAuth provider already exists for this user")

            pid = _generate_provider_id()
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": "Claude Code (OAuth)",
                "source": "claude_oauth",
                "protocol": "anthropic",
                "auth_type": "oauth",
                "api_key": "",
                "base_url": "",
                # CLI family aliases → auto-track the latest Claude release (the
                # OAuth path runs `claude --model opus|sonnet|haiku`), so no
                # manual version bump on each new model. See model_catalog.py.
                "models": json.dumps(["opus", "sonnet", "haiku"]),
                # OAuth funnels through official Anthropic → server tools OK.
                "supports_anthropic_server_tools": True,
            }, now)
            new_ids.append(pid)

        elif card_type == "codex_oauth":
            from xyz_agent_context.agent_framework.provider_driver.derive import (
                CODEX_CLI_CREDENTIALS_REF,
            )

            # Mirror of claude_oauth: a single row representing the
            # host's ``codex login`` credential. The CodexSDK reads
            # the token directly from ~/.codex/auth.json via its
            # own OAuth fallback; this row exists primarily so
            # CodexOAuthDriver.probe() has a card to probe and the
            # Settings page can surface ✓ / ✗ status.
            #
            # protocol="openai" because Codex's underlying API surface
            # is OpenAI-compatible (Responses API). This makes the row
            # technically eligible for the helper_llm slot too — but
            # OAuth credentials can't actually serve chat-completions
            # calls, so users shouldn't pin it there. We don't gate
            # it at insert time; the resolver / driver layers reject
            # non-agent-slot uses with NotImplementedError.
            existing = await self.db.get(
                "user_providers", filters={"user_id": user_id, "source": "codex_oauth"}
            )
            if existing:
                raise ValueError("Codex OAuth provider already exists for this user")

            pid = _generate_provider_id()
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": "Codex CLI (OAuth)",
                "source": "codex_oauth",
                "protocol": "openai",
                "auth_type": "oauth",
                "api_key": "",
                "base_url": "",
                # Seed the column for completeness, but the read path
                # in ``get_user_config`` overrides this with the
                # current ``CODEX_CURATED_MODELS`` constant —
                # so the column value is effectively cached, not
                # canonical. Updating the constant updates the
                # dropdown for every existing user on next reload.
                "models": json.dumps(list(CODEX_CURATED_MODELS)),
                # Codex is OpenAI's product — Anthropic server tools
                # (WebSearch etc.) are not applicable.
                "supports_anthropic_server_tools": False,
                "driver_type": "codex_oauth",
                "billing_policy": "external_oauth",
                "auth_ref": CODEX_CLI_CREDENTIALS_REF,
            }, now)
            new_ids.append(pid)

        elif card_type in ("anthropic", "openai"):
            pid = _generate_provider_id()
            display_name = name or f"Custom {card_type.title()}"
            if not models:
                from xyz_agent_context.agent_framework.model_catalog import get_default_models
                models = get_default_models("user", card_type)
            else:
                models = list(models)
            # Auto-detect: only the official api.anthropic.com host serves
            # the server-side tool suite (WebSearch etc.). User can flip
            # this later via the edit-provider flow if they front official
            # with a transparent proxy.
            server_tools = (
                card_type == "anthropic"
                and "api.anthropic.com" in (base_url or "").lower()
            )
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": display_name,
                "source": "user",
                "protocol": card_type,
                "auth_type": auth_type,
                "api_key": api_key,
                "base_url": base_url,
                "models": json.dumps(models or []),
                "supports_anthropic_server_tools": server_tools,
            }, now)
            new_ids.append(pid)
        else:
            raise ValueError(f"Unknown card_type: {card_type}")

        # Subscription (OAuth) login covers BOTH slots in one step. The OAuth
        # credential drives the agent CLI AND, via CliHelperSDK, the helper's
        # one-shot calls — so bind agent + helper_llm to this provider now
        # instead of forcing a second manual helper config (2026-07 P0).
        #
        # Fill EMPTY slots only: a fresh subscription login becomes fully
        # runnable, but adding an OAuth provider on top of an existing working
        # setup never clobbers the user's chosen agent/helper. Order mirrors
        # onboard_one_key: framework first (set_slot's agent-protocol check
        # reads it), then agent, then helper.
        if card_type in ("claude_oauth", "codex_oauth") and new_ids:
            pid = new_ids[0]
            if card_type == "claude_oauth":
                framework, agent_model, helper_model = "claude_code", "opus", "haiku"
            else:
                curated = list(json.loads((await self.db.get_one(
                    "user_providers", {"user_id": user_id, "provider_id": pid}
                ) or {}).get("models") or "[]"))
                # Agent on the flagship (curated[0] = gpt-5.5), helper on the
                # cheap mini — mirrors claude's opus/haiku split. The helper
                # does small structured jobs, so pin gpt-5.4-mini (in
                # CODEX_CURATED_MODELS, accepted by a ChatGPT-account
                # subscription; verified 2026-07-08) instead of reusing
                # curated[0], which wrongly put the flagship gpt-5.5 on the
                # helper slot.
                framework = "codex_cli"
                agent_model = curated[0] if curated else ""
                helper_model = "gpt-5.4-mini"

            def _slot_empty(row) -> bool:
                return not row or not row.get("provider_id")

            agent_slot = await self.db.get_one(
                "user_slots", {"user_id": user_id, "slot_name": "agent"}
            )
            if _slot_empty(agent_slot):
                await self.set_user_agent_framework(user_id, framework)
                # actor_is_staff=None — explicit policy bypass: OAuth cards
                # are staff-only on cloud at the add_provider route, so a
                # non-staff cloud caller never reaches this auto-bind.
                await self.set_slot(
                    user_id, "agent", pid, agent_model, actor_is_staff=None
                )
            helper_slot = await self.db.get_one(
                "user_slots", {"user_id": user_id, "slot_name": "helper_llm"}
            )
            if _slot_empty(helper_slot):
                await self.set_slot(
                    user_id, "helper_llm", pid, helper_model, actor_is_staff=None
                )

        config = await self.get_user_config(user_id)
        return config, new_ids

    async def _insert_provider(self, user_id: str, data: dict, now: str):
        row = {
            "user_id": user_id,
            "provider_id": data["provider_id"],
            "name": data["name"],
            "source": data["source"],
            "protocol": data["protocol"],
            "auth_type": data.get("auth_type", "api_key"),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", ""),
            "models": data.get("models", "[]"),
            "linked_group": data.get("linked_group", ""),
            "is_active": 1,
            "supports_anthropic_server_tools": 1 if data.get("supports_anthropic_server_tools") else 0,
            "created_at": now,
            "updated_at": now,
        }
        for optional_key in ("driver_type", "owner_user_id", "billing_policy", "auth_ref"):
            if optional_key in data:
                row[optional_key] = data[optional_key]
        await self.db.insert("user_providers", row)

    # =========================================================================
    # Remove Provider
    # =========================================================================

    async def remove_provider(self, user_id: str, provider_id: str) -> LLMConfig:
        """Remove a provider (and its linked group). Clears affected slots."""
        row = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not row:
            raise ValueError(f"Provider {provider_id} not found")

        # If linked group, delete all in group
        linked_group = row.get("linked_group", "")
        if linked_group:
            group_rows = await self.db.get("user_providers", {"user_id": user_id, "linked_group": linked_group})
            for r in group_rows:
                await self.db.delete("user_providers", {"user_id": user_id, "provider_id": r["provider_id"]})
                # Clear any slots using this provider — both the user-level
                # defaults and any per-agent overrides (agent_slots is keyed by
                # agent_id, not user_id; provider_id is globally unique, so a
                # provider_id filter only ever hits this owner's rows). Without
                # this, a deleted provider leaves a dangling override that
                # fails at resolve time.
                await self.db.delete("user_slots", {"user_id": user_id, "provider_id": r["provider_id"]})
                await self.db.delete("agent_slots", {"provider_id": r["provider_id"]})
        else:
            await self.db.delete("user_providers", {"user_id": user_id, "provider_id": provider_id})
            await self.db.delete("user_slots", {"user_id": user_id, "provider_id": provider_id})
            await self.db.delete("agent_slots", {"provider_id": provider_id})

        return await self.get_user_config(user_id)

    # =========================================================================
    # Slots
    # =========================================================================

    async def set_slot(
        self,
        user_id: str,
        slot_name: str,
        provider_id: str,
        model: str,
        thinking: str = "",
        reasoning_effort: str = "",
        *,
        actor_is_staff: Optional[bool],
    ) -> LLMConfig:
        """Assign a provider + model (+ neutral reasoning params) to a slot.

        PUT semantics: every call writes the FULL param set. Omitted params
        reset to "" (auto) — the UI always sends the current dropdown values.

        ``actor_is_staff`` (required keyword): the caller's role for the
        cloud netmind-only policy (see cloud_policy). Routes pass the real
        role; a trusted internal caller (onboard, OAuth auto-bind — policy
        already decided upstream) must write ``actor_is_staff=None``
        EXPLICITLY. Deliberately no default: a silent-bypass default is
        exactly how the manyfold clone gap happened, so a bypass has to be
        visible at the call site. Raises ``CloudPolicyViolation`` (→ 403 at
        the route) on a forbidden binding.
        """
        # Validate slot name
        if slot_name not in [s.value for s in SlotName]:
            raise ValueError(f"Invalid slot: {slot_name}")

        # Validate the neutral params through the schema (rejects dialect
        # words like "adaptive"/"minimal" before they reach storage).
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

        # Validate provider exists for this user
        prov = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not prov:
            raise ValueError(f"Provider {provider_id} not found for user {user_id}")

        # Cloud netmind-only policy (single source of truth: cloud_policy).
        ensure_slot_provider_allowed(prov, actor_is_staff)

        # Validate the provider↔slot binding (protocol / codex-source /
        # helper-OAuth). The agent slot is framework-dependent, so resolve the
        # user's current framework first. Shared with AgentSlotService via
        # validate_slot_binding so both writers enforce identical rules.
        agent_framework = None
        if slot_name == SlotName.AGENT.value:
            existing_slot = await self.db.get_one(
                "user_slots", {"user_id": user_id, "slot_name": slot_name}
            )
            agent_framework = (existing_slot or {}).get("agent_framework") or "claude_code"
        validate_slot_binding(prov, slot_name, agent_framework)

        # Upsert slot
        existing = await self.db.get_one("user_slots", {"user_id": user_id, "slot_name": slot_name})
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            await self.db.update("user_slots",
                {"user_id": user_id, "slot_name": slot_name},
                {"provider_id": provider_id, "model": model,
                 "params_json": params_json, "updated_at": now}
            )
        else:
            await self.db.insert("user_slots", {
                "user_id": user_id,
                "slot_name": slot_name,
                "provider_id": provider_id,
                "model": model,
                "params_json": params_json,
                "updated_at": now,
            })

        return await self.get_user_config(user_id)

    # =========================================================================
    # One-key onboarding
    # =========================================================================

    async def onboard_one_key(
        self,
        user_id: str,
        api_key: str,
        provider_type: Optional[str] = None,
        replace: bool = False,
        inference_base: Optional[str] = None,
        activate: bool = True,
    ) -> tuple[LLMConfig, list[str], dict]:
        """Wire a complete runnable config from a single API key.

        Detects the key's protocol (sk-ant- prefix → anthropic, else
        openai) unless ``provider_type`` overrides it, then:

          1. Persists the matching agent framework (anthropic →
             claude_code, openai → codex_cli). MUST happen before the
             agent slot — set_slot validates the agent provider's
             protocol against the framework.
          2. Creates the provider (card_type = the protocol; the
             provider gets the catalog's suggested model list).
          3. Assigns BOTH slots to that same provider with the
             catalog's onboarding defaults (strongest agent model of
             the family + the cheap helper model).

        Returns (config, new_provider_ids, meta) where meta carries
        {provider_type, agent_framework, agent_model, helper_model}
        for the API response. Raises ValueError on bad input or any
        step failure (duplicate provider, protocol mismatch, ...).
        """
        from xyz_agent_context.agent_framework.model_catalog import (
            get_default_agent_model,
            get_default_helper_model,
        )

        key = (api_key or "").strip()
        if not key:
            raise ValueError("api_key is required")

        # Aggregator keys (netmind/yunwu/openrouter) have no recognisable
        # prefix — they are only reachable via an explicit provider_type
        # from the UI's provider picker.
        ptype = provider_type or (
            "anthropic" if key.startswith("sk-ant-") else "openai"
        )
        allowed = ("anthropic", "openai", "netmind", "yunwu", "openrouter")
        if ptype not in allowed:
            raise ValueError(
                f"provider_type must be one of {allowed}, got {ptype!r}"
            )
        # Only a pure-OpenAI key runs the codex_cli agent; every
        # aggregator's anthropic endpoint serves claude_code like an
        # official Claude key does.
        framework = "codex_cli" if ptype == "openai" else "claude_code"
        agent_model = get_default_agent_model(ptype)
        helper_model = get_default_helper_model(ptype)

        # Key rotation: aggregator cards (netmind/yunwu/openrouter) are guarded
        # one-per-source, so a second onboard is a REPLACE, not an add. If the
        # user already has one and hasn't confirmed, don't mutate — report
        # needs_replace so the UI can prompt "you already have <masked>, replace?".
        # Official anthropic/openai cards use source="user" (unguarded, users
        # may hold several) so they never need this.
        old_rows: list[dict] = []
        if ptype in ("netmind", "yunwu", "openrouter"):
            old_rows = await self.db.get(
                "user_providers", filters={"user_id": user_id, "source": ptype}
            )
            if old_rows and not replace:
                config = await self.get_user_config(user_id)
                existing_key = str(old_rows[0].get("api_key") or "")
                masked = ("***" + existing_key[-4:]) if len(existing_key) > 4 else "***"
                meta = {
                    "provider_type": ptype,
                    "needs_replace": True,
                    "existing_masked": masked,
                }
                return config, [], meta

        # Verify the key BEFORE writing anything — a typo'd key should
        # fail here with a clear message, not at the first chat turn.
        # Definitive auth rejections (401/403) raise; transient failures
        # (network, 5xx) do NOT block — we proceed and report
        # key_check="unverified (...)" so the UI can surface a warning.
        key_check = await self._verify_onboard_key(
            ptype, key, agent_model, inference_base=inference_base
        )

        # Register-only mode (activate=False): create the provider(s) but do NOT
        # touch the user's framework or slots. Used when the user already has an
        # active config — a NetMind card is made AVAILABLE (auto-registered on
        # login), not force-activated over their own provider.
        if activate:
            await self.set_user_agent_framework(user_id, framework)
        # Expand-contract replace: create the NEW provider(s) and repoint slots
        # to them BEFORE deleting the old ones (replace=True skips the source
        # guard). If any step fails the old, working provider is still in place —
        # the user is never left without a runnable config (safer than the
        # delete-then-add the user would otherwise do by hand).
        config, new_ids = await self.add_provider(
            user_id=user_id, card_type=ptype, api_key=key, replace=bool(old_rows),
            inference_base=inference_base,
        )
        if activate:
            # Official anthropic/openai cards create ONE provider that
            # serves both slots. The netmind card creates TWO linked rows
            # (anthropic + openai); route each slot to its protocol's row.
            agent_pid = helper_pid = new_ids[0]
            if len(new_ids) > 1:
                by_protocol = {
                    config.providers[pid].protocol.value: pid
                    for pid in new_ids
                    if pid in config.providers
                }
                agent_pid = by_protocol.get("anthropic", new_ids[0])
                helper_pid = by_protocol.get("openai", new_ids[0])
            # actor_is_staff=None — explicit policy bypass: the onboard route
            # already decided (cloud non-staff gets activate=False and never
            # reaches these binds).
            config = await self.set_slot(
                user_id, "agent", agent_pid, agent_model, actor_is_staff=None
            )
            config = await self.set_slot(
                user_id, "helper_llm", helper_pid, helper_model, actor_is_staff=None
            )

        # Contract step: drop the old pair now that slots point at the new one.
        # Slots for the old provider_ids were already overwritten above, so their
        # deletion here is a harmless no-op — this only removes stale rows.
        new_id_set = set(new_ids)
        for r in old_rows:
            old_pid = r.get("provider_id")
            if not old_pid or old_pid in new_id_set:
                continue
            await self.db.delete("user_providers", {"user_id": user_id, "provider_id": old_pid})
            await self.db.delete("user_slots", {"user_id": user_id, "provider_id": old_pid})
        if old_rows:
            config = await self.get_user_config(user_id)

        meta = {
            "provider_type": ptype,
            "agent_framework": framework,
            "agent_model": agent_model,
            "helper_model": helper_model,
            "key_check": key_check,
            # False = register-only (framework/slots untouched) — the UI must
            # not claim "you're now running on <model>" in that case.
            "activated": activate,
        }
        return config, new_ids, meta

    async def _verify_onboard_key(
        self, ptype: str, api_key: str, agent_model: str,
        inference_base: Optional[str] = None,
    ) -> str:
        """Live-probe the key against its provider before persisting.

        Builds a transient ProviderConfig (never stored) and reuses
        ``provider_registry.test_provider`` — GET /models on official
        endpoints (zero token cost), a max_tokens=1 call on proxies.
        Aggregators are probed on their anthropic endpoint (the agent's
        critical path).

        Returns "ok" on success, or "unverified (<reason>)" when the
        probe failed for a NON-auth reason (network, 5xx, timeout) —
        we don't block a good key because our egress hiccuped.

        Raises ValueError when the provider definitively rejected the
        credential (401/403).
        """
        from xyz_agent_context.agent_framework.provider_registry import (
            provider_registry,
        )

        if ptype in _DUAL_PROVIDER_CONFIGS:
            info = _DUAL_PROVIDER_CONFIGS[ptype]["anthropic"]
            # Probe the SAME base the provider will be created with — otherwise a
            # dev-minted netmind key gets probed against prod inference and 401s,
            # failing onboarding before it ever writes.
            base_url = info["base_url"]
            if ptype == "netmind" and inference_base:
                base_url = _netmind_base_for("anthropic", inference_base)
            probe_cfg = ProviderConfig(
                provider_id="_onboard_verify",
                name="onboard verify",
                source=ptype,
                protocol=ProviderProtocol.ANTHROPIC,
                auth_type=info["auth_type"],
                api_key=api_key,
                base_url=base_url,
                models=[agent_model],
            )
        else:
            probe_cfg = ProviderConfig(
                provider_id="_onboard_verify",
                name="onboard verify",
                source=ProviderSource.USER,
                protocol=ptype,
                auth_type=AuthType.API_KEY,
                api_key=api_key,
                base_url="",
                models=[agent_model],
            )

        ok, msg = await provider_registry.test_provider(probe_cfg)
        if ok:
            return "ok"
        low = msg.lower()
        if (
            "authentication failed" in low
            or "access denied" in low
            or "invalid api key" in low
            or "http 401" in low
            or "http 403" in low
        ):
            raise ValueError(f"API key rejected by {ptype}: {msg}")
        logger.warning(
            f"[onboard] key probe inconclusive for {ptype}: {msg} — "
            f"proceeding unverified"
        )
        return f"unverified ({msg})"

    # ---- agent_framework: per-user coding-agent SDK choice ---------
    # The ``user_slots[slot_name='agent'].agent_framework`` column is
    # read by step_3_agent_loop._resolve_agent_framework_sdk to pick
    # ClaudeAgentSDK vs CodexSDK. Reading defaults to "claude_code"
    # for any null/missing row so existing users are untouched.

    # Coding-agent framework names ``set_user_agent_framework`` accepts.
    # MUST stay in sync with ``agent_framework/__init__.py``
    # registrations and resolver's ``_KNOWN_AGENT_FRAMEWORKS`` (route
    # layer imports this constant directly — single source of truth).
    _SUPPORTED_AGENT_FRAMEWORKS: tuple[str, ...] = (
        "claude_code",
        "codex_cli",
    )

    async def get_user_agent_framework(self, user_id: str) -> str:
        """Return the user's chosen coding-agent framework.

        Returns ``"claude_code"`` when:
          - The user has no agent slot row yet (new user)
          - The column is null (rows from before the column was added)
        Anything other than the supported set still returns the raw
        value; the caller (step_3 resolver) is responsible for
        falling back to claude_code on unknown values.
        """
        row = await self.db.get_one(
            "user_slots", {"user_id": user_id, "slot_name": "agent"}
        )
        if not row:
            return "claude_code"
        return (row.get("agent_framework") or "claude_code")

    async def set_user_agent_framework(self, user_id: str, framework: str) -> None:
        """Persist the user's coding-agent framework choice.

        Upserts ``user_slots[user_id, slot_name='agent'].agent_framework``.
        If the user has no agent slot row yet (provider_id/model not
        set), a stub row is inserted with empty provider_id+model so
        the framework choice is preserved until they wire the slot.
        provider_resolver still rejects the call at agent_loop time
        when provider_id is empty — same as today.

        Raises ``ValueError`` for unknown framework values.
        """
        if framework not in self._SUPPORTED_AGENT_FRAMEWORKS:
            raise ValueError(
                f"Unknown agent_framework {framework!r}. "
                f"Supported: {self._SUPPORTED_AGENT_FRAMEWORKS}"
            )

        existing = await self.db.get_one(
            "user_slots", {"user_id": user_id, "slot_name": "agent"}
        )
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            await self.db.update(
                "user_slots",
                {"user_id": user_id, "slot_name": "agent"},
                {"agent_framework": framework, "updated_at": now},
            )
        else:
            # Stub row: framework choice survives even if the agent
            # slot's provider/model is not yet wired. provider_resolver
            # will reject usage with empty provider_id at agent_loop
            # time, which is correct UX (forces the user to finish
            # slot setup).
            await self.db.insert(
                "user_slots",
                {
                    "user_id": user_id,
                    "slot_name": "agent",
                    "provider_id": "",
                    "model": "",
                    "agent_framework": framework,
                    "updated_at": now,
                },
            )

    async def validate_slots(self, user_id: str) -> list[str]:
        """Validate all slots are configured."""
        config = await self.get_user_config(user_id)
        errors = []
        for slot in SlotName:
            if slot.value not in config.slots:
                errors.append(f"Slot '{slot.value}' not configured")
        return errors

    # =========================================================================
    # Update Models
    # =========================================================================

    async def update_models(self, user_id: str, provider_id: str, models: list[str]) -> LLMConfig:
        """Update available models for a provider."""
        now = datetime.now(timezone.utc).isoformat()
        affected = await self.db.update("user_providers",
            {"user_id": user_id, "provider_id": provider_id},
            {"models": json.dumps(models), "updated_at": now}
        )
        if affected == 0:
            raise ValueError(f"Provider {provider_id} not found")
        return await self.get_user_config(user_id)

    # =========================================================================
    # Test
    # =========================================================================

    async def test_provider(self, user_id: str, provider_id: str) -> tuple[bool, str]:
        """Test connectivity to a provider."""
        row = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not row:
            return False, "Provider not found"

        if row.get("auth_type") == "oauth":
            return True, "OAuth provider (managed by Claude Code CLI)"

        from xyz_agent_context.agent_framework.provider_registry import provider_registry
        prov = ProviderConfig(
            provider_id=row["provider_id"],
            name=row["name"],
            source=row["source"],
            protocol=row["protocol"],
            auth_type=row.get("auth_type", "api_key"),
            api_key=row.get("api_key", ""),
            base_url=row.get("base_url", ""),
            models=json.loads(row["models"]) if row.get("models") else [],
        )
        return await provider_registry.test_provider(prov)


# =============================================================================
# Dual-protocol provider builder (NetMind, Yunwu, OpenRouter)
# =============================================================================

_DUAL_PROVIDER_CONFIGS = {
    "netmind": {
        "anthropic": {"name": "NetMind (Anthropic)", "base_url": "https://api.netmind.ai/inference-api/anthropic", "auth_type": "bearer_token"},
        "openai": {"name": "NetMind (OpenAI)", "base_url": "https://api.netmind.ai/inference-api/openai/v1", "auth_type": "api_key"},
    },
    "yunwu": {
        "anthropic": {"name": "Yunwu (Anthropic)", "base_url": "https://api.yunwuai.cloud/v1/messages", "auth_type": "api_key"},
        "openai": {"name": "Yunwu (OpenAI)", "base_url": "https://api.yunwuai.cloud/v1", "auth_type": "api_key"},
    },
    "openrouter": {
        "anthropic": {"name": "OpenRouter (Anthropic)", "base_url": "https://openrouter.ai/api/v1/messages", "auth_type": "api_key"},
        "openai": {"name": "OpenRouter (OpenAI)", "base_url": "https://openrouter.ai/api/v1", "auth_type": "api_key"},
    },
}


# NetMind's two inference sub-endpoints hang off one base prefix
# (prod: https://api.netmind.ai/inference-api). ONLY netmind's base is
# environment-swappable — a key minted on dev NetMind must hit dev inference
# (https://test.api.netmind.ai/inference-api). yunwu/openrouter are single-env.
_NETMIND_INFERENCE_SUBPATHS = {"anthropic": "/anthropic", "openai": "/openai/v1"}


def _netmind_base_for(protocol: str, inference_base: str) -> str:
    """Derive netmind's per-protocol inference URL from a base prefix override."""
    return inference_base.rstrip("/") + _NETMIND_INFERENCE_SUBPATHS[protocol]


def _build_dual_providers(
    card_type: str,
    api_key: str,
    group_id: str,
    models: Optional[list] = None,
    inference_base: Optional[str] = None,
) -> list[dict]:
    from xyz_agent_context.agent_framework.model_catalog import get_default_models
    cfg = _DUAL_PROVIDER_CONFIGS[card_type]
    result = []
    for protocol, info in cfg.items():
        proto_models = models or get_default_models(card_type, protocol)
        # Override the base ONLY for netmind + when a caller opted in
        # (use-subscription). Everything else keeps the hardcoded prod base.
        base_url = info["base_url"]
        if card_type == "netmind" and inference_base:
            base_url = _netmind_base_for(protocol, inference_base)
        result.append({
            "provider_id": _generate_provider_id(),
            "name": info["name"],
            "source": card_type,
            "protocol": protocol,
            "auth_type": info["auth_type"],
            "api_key": api_key,
            "base_url": base_url,
            "models": json.dumps(proto_models),
            "linked_group": group_id,
        })
    return result
