"""
@file_name: framework_binding.py
@author: Bin Liang
@date: 2026-09-07
@description: Which provider protocols a slot accepts, and whether the agent slot's framework can actually redeem a given card.

These are business rules, not schemas: they read the framework registry
(``turn.pipeline.act.framework``) to answer "can this framework drive this
card?". They used to live in ``platform/schema/provider_schema.py`` and
reached UP into ``agent_framework`` through function-body imports, which
inverted the declared one-way stack (``api → runtime → service → impl →
repository → schema``) and hid the inversion from import-linter (a static AST
gate cannot see an import inside a function body). Here the imports are
top-level and the direction is right; ``provider_schema`` keeps only the
enums and pydantic models.

Error contract at this boundary: an unknown OR uninstalled framework raises
``ValueError`` so the provider routes' existing ``ValueError → 400`` mapping
applies. The settings page must stay renderable — it is the ONE place a user
can undo a binding to a framework they since uninstalled, so answering that
page with a 500 would remove the only recovery path. The raw
``FrameworkNotInstalledError`` stays on the TURN path
(``get_agent_loop_driver``), where it is the actionable message. This is NOT
"unknown framework → default": the binding is refused, never substituted.
"""
from __future__ import annotations

from narranexus.contracts import UnknownEntry
from narranexus.platform.agent_framework.loop.driver import (
    FrameworkNotInstalledError,
    framework_for_oauth_source,
    framework_meta,
    resolve_framework_name,
)
from narranexus.platform.schema.provider_schema import (
    SUBSCRIPTION_AUTH_TYPES,
    ProviderProtocol,
    SlotName,
)

SLOT_REQUIRED_PROTOCOLS: dict[SlotName, list[ProviderProtocol]] = {
    SlotName.AGENT: [ProviderProtocol.ANTHROPIC],
    # helper_llm accepts both protocols: the resolver dispatches to the
    # OpenAI helper (Chat Completions) or the Anthropic helper (Messages
    # API) per the assigned provider's protocol. This is what lets a
    # single Claude key serve agent AND helper.
    SlotName.HELPER_LLM: [ProviderProtocol.OPENAI, ProviderProtocol.ANTHROPIC],
}
"""Static protocol requirement per slot.

The AGENT row is the fallback shape only: ``get_slot_required_protocols``
derives the agent slot from the resolved framework's ``FrameworkMeta``. The
other slots have no framework, so this table is their answer.
"""

# ``SUBSCRIPTION_AUTH_TYPES`` is defined next to the ``AuthType`` enum in
# ``provider_schema`` (it is a subset of enum values, not a registry rule) and
# re-exported here so the binding rules keep one import site for callers.


def resolved_framework_name(agent_framework: str | None = None) -> str:
    """The resolved framework name at the CONFIG boundary, or ``ValueError``.

    Same precedence as ``driver.resolve_framework_name`` (the ONE accessor),
    with the boundary's error contract: a binding naming a framework no plugin
    provides is a 400 here, not the ``RuntimeError`` that reaches the client as
    a 500 on the very page that could fix the binding.
    """
    return _resolved_meta(agent_framework).name


def _resolved_meta(agent_framework: str | None):
    """The resolved framework's meta, or ``ValueError`` — the 400 boundary.

    Both failure modes of the resolver land here: ``FrameworkNotInstalledError``
    (the ``turn.pipeline.act.framework`` binding names a plugin that is not
    registered in this process) and ``UnknownEntry`` (the caller named a
    framework nobody provides).
    """
    try:
        name = resolve_framework_name(agent_framework)
    except FrameworkNotInstalledError as exc:
        raise ValueError(str(exc)) from exc
    try:
        return framework_meta(name)
    except UnknownEntry as exc:
        raise ValueError(f"Unknown agent framework {name!r}") from exc


def get_slot_required_protocols(
    slot_name: str,
    *,
    agent_framework: str | None = None,
) -> list[ProviderProtocol]:
    """Return the protocols allowed for a slot in the current framework.

    The agent slot follows the framework: ``FrameworkMeta.agent_protocols`` of
    the registered framework (a CLI-backed framework speaks exactly one
    protocol because its CLI does; a framework that drives the provider API
    itself accepts either). An absent framework means the bound default; a
    framework the registry does not know — or one the binding names but no
    plugin provides — raises ``ValueError``, which the writers and the routes
    surface as a 400 instead of persisting a binding nothing can run.
    Other slots keep their static requirement.
    """
    if slot_name == SlotName.AGENT.value:
        return [ProviderProtocol(p) for p in _resolved_meta(agent_framework).agent_protocols]
    return SLOT_REQUIRED_PROTOCOLS.get(slot_name, [])


def framework_can_drive_provider(
    framework: str | None,
    *,
    source: str,
    auth_type: str,
    protocol: str,
) -> bool:
    """Can ``framework`` actually run the AGENT slot on this provider card?

    Two gates, in order:

    1. Protocol — the framework's ``FrameworkMeta.agent_protocols``
       (:func:`get_slot_required_protocols`).
    2. Subscription credential — a card whose ``auth_type`` is in
       :data:`SUBSCRIPTION_AUTH_TYPES` is redeemable ONLY by the framework
       whose ``FrameworkMeta.oauth_source`` claims its source
       (``driver.framework_for_oauth_source``).

    API-key / bearer-token cards pass gate 2 untouched: whether a given
    endpoint serves a given framework well is the provider's characteristic,
    not something the platform polices at config time (binding rule #15).
    This function is about what is *technically redeemable*, nothing else.

    Frontend twin: ``frontend/src/lib/agentFramework.ts``
    ``providerBacksFramework()`` — keep the two in step so a picker never
    offers a binding the writers reject.
    """
    meta = _resolved_meta(framework)
    if protocol not in list(meta.agent_protocols):
        return False
    if auth_type in SUBSCRIPTION_AUTH_TYPES:
        return meta.name == framework_for_oauth_source(source)
    return True


__all__ = [
    "SLOT_REQUIRED_PROTOCOLS",
    "SUBSCRIPTION_AUTH_TYPES",
    "framework_can_drive_provider",
    "get_slot_required_protocols",
    "resolved_framework_name",
]
