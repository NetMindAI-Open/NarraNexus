"""Provider Driver abstraction.

Single registration point for the four kinds of LLM provider that
coexist in NarraNexus — user-custom (anthropic/openai), aggregator
quick-add (NetMind / Yunwu / OpenRouter), Claude OAuth (via Claude
Code CLI on the host) and the cloud-only system free-tier pool.

The driver pattern collapses what used to be two parallel resolve
paths (`ProviderResolver` for HTTP traffic + `_get_user_llm_configs_strict`
for background triggers) into one. Each row of `user_providers` carries
a `driver_type` column; this package dispatches on that column to a
single `Driver` implementation that knows how to build the three
LLM config dataclasses (`ClaudeConfig` / `OpenAIConfig`)
for that row.

Public entry points
-------------------
* :func:`resolve_user_llm_configs` — the single replacement for the
  old two-path resolver. Used by HTTP middleware and background triggers
  alike.
* :func:`backfill_provider_metadata` — one-shot startup job that fills
  `driver_type` / `owner_user_id` / `billing_policy` / `auth_ref` on
  rows created before this abstraction shipped.
* :class:`Driver` and :func:`register` — the registration API. New
  provider types add a class and decorate it.

See ``reference/self_notebook/specs/2026-05-13-provider-unification-design.md``
for the design rationale.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.provider_driver.base import (
    CallContext,
    Driver,
    DriverHealth,
    ProviderCard,
)
from xyz_agent_context.agent_framework.provider_driver.registry import (
    DRIVER_REGISTRY,
    get_driver_class,
    register,
)
from xyz_agent_context.agent_framework.provider_driver.derive import (
    derive_driver_type,
    derive_billing_policy,
    derive_auth_ref,
    resolve_codex_credentials_path,
    is_slot_broken,
    pick_default_model,
)
from xyz_agent_context.agent_framework.provider_driver.backfill import (
    backfill_provider_metadata,
)
from xyz_agent_context.agent_framework.provider_driver.self_heal import (
    self_heal_if_broken,
)
from xyz_agent_context.agent_framework.provider_driver.resolver import (
    resolve_user_llm_configs,
    resolve_user_runtime_llm_configs,
)

# Import drivers/ to trigger registration via @register decorators.
# The order doesn't matter — every Driver self-registers on import.
from xyz_agent_context.agent_framework.provider_driver import drivers  # noqa: F401


__all__ = [
    "CallContext",
    "Driver",
    "DriverHealth",
    "ProviderCard",
    "DRIVER_REGISTRY",
    "get_driver_class",
    "register",
    "derive_driver_type",
    "derive_billing_policy",
    "derive_auth_ref",
    "resolve_codex_credentials_path",
    "is_slot_broken",
    "pick_default_model",
    "backfill_provider_metadata",
    "self_heal_if_broken",
    "resolve_user_llm_configs",
    "resolve_user_runtime_llm_configs",
]
