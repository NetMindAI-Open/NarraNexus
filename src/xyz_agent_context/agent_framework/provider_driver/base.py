"""
@file_name: base.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver Protocol + ProviderCard dataclass + shared types

The Driver abstraction is the heart of the Provider Unification work
(spec 2026-05-13-provider-unification-design.md). Each row of
``user_providers`` maps to exactly one Driver instance via the
``driver_type`` column; the Driver knows how to talk to that specific
kind of LLM endpoint (NetMind, Yunwu, custom OpenAI, Claude OAuth via
CLI, the cloud-only system free-tier pool, ...).

We deliberately use ``typing.Protocol`` instead of an ABC so that:

* Third-party drivers (future) can be duck-typed without inheriting
  from anything in this codebase.
* Stub drivers in tests can stay simple — no boilerplate ``__init__``
  forwarding.
* Optional methods (e.g. ``estimate_cost``, ``on_call_completed``) can
  carry default implementations on the Protocol while keeping the
  classes flat.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    OpenAIConfig,
)


# =============================================================================
# ProviderCard — in-memory view of one user_providers row
# =============================================================================

@dataclass(frozen=True)
class ProviderCard:
    """In-memory snapshot of a single ``user_providers`` row.

    Frozen so a Driver instance can be cached / passed around without
    risk of mutation from the call site. Use :meth:`from_row` to build
    one from a raw ``db.get_one`` dict — that helper handles JSON
    decoding of ``models`` and normalises None defaults.
    """

    provider_id: str
    user_id: str
    name: str
    source: str
    protocol: str
    auth_type: str
    api_key: str
    base_url: str
    models: list[str] = field(default_factory=list)
    linked_group: str = ""
    is_active: bool = True
    supports_anthropic_server_tools: bool = False

    # Provider Unification additions (Phase 0)
    driver_type: Optional[str] = None
    owner_user_id: Optional[str] = None
    billing_policy: str = "user_pays"
    auth_ref: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> "ProviderCard":
        """Build a ProviderCard from a raw ``db.get_one`` result.

        Tolerates legacy rows where the new columns are still null —
        callers in the resolve path should ensure backfill has run, but
        unit tests construct cards directly without going through DB.
        """
        models_raw = row.get("models") or "[]"
        if isinstance(models_raw, list):
            models_list = models_raw
        else:
            try:
                models_list = json.loads(models_raw)
            except (ValueError, TypeError):
                models_list = []

        return cls(
            provider_id=row["provider_id"],
            user_id=row.get("user_id", ""),
            name=row.get("name", ""),
            source=row.get("source", "user"),
            protocol=row.get("protocol", "openai"),
            auth_type=row.get("auth_type") or "api_key",
            api_key=row.get("api_key") or "",
            base_url=row.get("base_url") or "",
            models=models_list,
            linked_group=row.get("linked_group") or "",
            is_active=bool(row.get("is_active", 1)),
            supports_anthropic_server_tools=bool(
                row.get("supports_anthropic_server_tools", 0)
            ),
            driver_type=row.get("driver_type"),
            owner_user_id=row.get("owner_user_id"),
            billing_policy=row.get("billing_policy") or "user_pays",
            auth_ref=row.get("auth_ref"),
        )


# =============================================================================
# DriverHealth — return type for Driver.probe()
# =============================================================================

@dataclass(frozen=True)
class DriverHealth:
    """Result of a Driver probe.

    Producers populate ``ok`` (True if the credential is usable) plus
    optional ``detail`` and ``expires_at`` for UI surfacing. Drivers
    that can't perform a real probe (e.g. SystemDriver) should still
    return a meaningful summary.
    """

    ok: bool
    detail: str = ""
    expires_at: Optional[str] = None  # ISO-8601 string when known (OAuth)


# =============================================================================
# CallContext — extra info passed to on_call_completed
# =============================================================================

@dataclass(frozen=True)
class CallContext:
    """Per-call metadata passed to :meth:`Driver.on_call_completed`.

    Drivers consult this to decide what to do post-call. The system
    driver uses ``user_id`` to credit the quota table; other drivers
    typically ignore it.
    """

    user_id: str
    agent_id: Optional[str] = None
    event_id: Optional[str] = None
    call_type: str = "llm_function"  # llm_function / llm_stream / agent_loop


# =============================================================================
# Driver Protocol
# =============================================================================

@runtime_checkable
class Driver(Protocol):
    """One driver per LLM provider type. Stateless except for the
    ``ProviderCard`` snapshot it captures at construction.

    Implementations should be cheap to instantiate — each LLM call
    builds a new Driver from a freshly-read card row. Caching belongs
    to the resolver layer, not here.
    """

    card: ProviderCard

    # ----- class-level metadata ----------------------------------------------

    @classmethod
    def driver_type(cls) -> str:
        """Returns the key under which this driver registers in
        :data:`DRIVER_REGISTRY`. Must match the value written into
        ``user_providers.driver_type``.
        """
        ...

    # ----- config construction ----------------------------------------------

    def build_claude_config(self, model: str) -> ClaudeConfig:
        """Build a ``ClaudeConfig`` for the AGENT slot.

        Raises NotImplementedError on drivers that don't speak
        anthropic protocol (e.g. CustomOpenAIDriver).
        """
        ...

    def build_openai_config(self, model: str) -> OpenAIConfig:
        """Build an ``OpenAIConfig`` for the HELPER_LLM slot.

        Raises NotImplementedError on drivers that don't speak openai
        protocol (e.g. CustomAnthropicDriver, ClaudeOAuthDriver).
        """
        ...

    # ----- diagnostics + lifecycle hooks ------------------------------------

    async def probe(self) -> DriverHealth:
        """Active credential + endpoint reachability check.

        Default: assume healthy if api_key/auth_ref is populated. Drivers
        that can actually call a /models or /me endpoint should override.
        """
        ...

    async def on_call_completed(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        ctx: CallContext,
    ) -> None:
        """Hook fired after a successful LLM call. SystemDriver uses
        this to deduct from ``user_quotas``; other drivers no-op.
        """
        ...

    def models(self) -> list[str]:
        """Return the list of model IDs the user has marked usable on
        this card. Self-heal compares against this list.
        """
        ...


# =============================================================================
# Mixins to make Driver implementations terse
# =============================================================================

class _DriverBase:
    """Common helper boilerplate so concrete drivers stay short.

    Concrete drivers can inherit from this *or* duck-type. The Protocol
    check is satisfied by either path.
    """

    def __init__(self, card: ProviderCard) -> None:
        self.card = card

    def models(self) -> list[str]:
        return list(self.card.models or [])

    async def probe(self) -> DriverHealth:
        if self.card.api_key or self.card.auth_ref:
            return DriverHealth(ok=True, detail="credential present")
        return DriverHealth(ok=False, detail="no api_key or auth_ref")

    async def on_call_completed(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        ctx: CallContext,
    ) -> None:
        # Default: do nothing. Cost is already logged in cost_records by
        # the caller; only SystemDriver overrides this to deduct quota.
        return None

    def build_claude_config(self, model: str) -> ClaudeConfig:
        raise NotImplementedError(
            f"{type(self).__name__} does not support agent (anthropic) slot"
        )

    def build_openai_config(self, model: str) -> OpenAIConfig:
        raise NotImplementedError(
            f"{type(self).__name__} does not support helper_llm (openai) slot"
        )


__all__ = [
    "ProviderCard",
    "DriverHealth",
    "CallContext",
    "Driver",
    "_DriverBase",
]
