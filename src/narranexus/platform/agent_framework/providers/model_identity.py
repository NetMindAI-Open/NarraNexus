"""
@file_name: model_identity.py
@author:
@date: 2026-07-10
@description: Resolve an agent's REAL runtime identity (coding-agent
framework + model) for display in the system prompt.

Why this exists
---------------
The "LLM Model" line in BasicInfoModule's system prompt used to be a
hardcoded literal ("Claude Agent SDK" / "sonnet-4") in
``context_runtime.py``, so every agent — regardless of its actual
configuration — told the user it was Claude Sonnet-4. This module
resolves the truth from the same slot rows the runtime dispatches on,
so the prompt states what the agent actually runs (e.g. "Codex CLI"
/ "gpt-5").

Iron rule #9: this lives in the agent_framework layer, not inside a
Module. BasicInfoModule (a Module) just calls ``resolve_agent_model_
identity`` and renders the strings — it never learns framework names.

This is the SINGLE overlay implementation. The dispatch-side resolver
``agent_runtime._agent_runtime_steps.step_3_agent_loop.
_resolve_agent_framework_name`` delegates here (returns ``.framework``),
so the identity shown in the prompt can never disagree with the driver
that actually runs. The rule: a per-agent ``agent_slots`` override wins
ONLY when it truly rebinds the slot — it carries BOTH a ``provider_id``
AND an ``agent_framework`` (a provider-only or framework-only stub does
NOT win, matching the config resolver, which would otherwise e.g. run
the Codex driver against a Claude config). Otherwise the owner's
``user_slots`` row (keyed by ``agents.created_by``) is authoritative.
Framework and model are read from that SAME winning slot row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

# How the agent names its own runtime INSIDE the system prompt comes from the
# registered framework's ``FrameworkMeta.self_description`` (``_display_for``),
# and WHICH framework a slot row resolves to comes from the ONE accessor
# ``loop.driver.resolve_framework_name`` (explicit > env > the
# ``turn.pipeline.act.framework`` binding > the code default). This module used
# to keep a second ``DEFAULT_AGENT_FRAMEWORK`` literal, which silently
# disagreed with the resolver the moment a distribution bound a different
# default framework.


@dataclass(frozen=True)
class AgentModelIdentity:
    """The agent's runtime identity for prompt display.

    - ``framework``: canonical framework name (e.g. ``"codex_cli"``).
    - ``framework_display``: human label (e.g. ``"Codex CLI"``).
    - ``model``: the configured model string on the agent slot
      (e.g. ``"gpt-5"``); may be empty when the slot lets the CLI pick
      its own default.
    """

    framework: str
    framework_display: str
    model: str


def _display_for(framework: str) -> str:
    """What the agent says its runtime is: the registered framework's
    ``FrameworkMeta.self_description`` (a prompt string owned by the framework
    plugin). Unknown or uninstalled names fall back to the raw canonical
    string — never invent a brand, and never raise (this module's promise)."""
    from narranexus.contracts import UnknownEntry
    from narranexus.platform.agent_framework.loop.driver import (
        FrameworkNotInstalledError,
        framework_meta,
    )

    try:
        return framework_meta(framework).self_description
    except (UnknownEntry, FrameworkNotInstalledError):
        return framework


def config_override_wins(override: dict | None) -> bool:
    """Whether an ``agent_slots`` row replaces the owner default for CONFIG
    resolution — i.e. which provider/model the agent's calls actually go to.

    The PROVIDER rule, deliberately distinct from ``slot_rebinds`` (the
    IDENTITY rule): any row with a non-empty ``provider_id`` wins, whether
    or not it also names an ``agent_framework``. An empty-provider
    (framework-only) stub never shadows the default. This is the rule
    ``driver.resolver._apply_agent_overrides`` applies at resolve time; every
    other "which provider does this agent run on" question must ask it here
    instead of re-deriving it (#394 review I3). Routing such a question
    through ``slot_rebinds`` would be wrong: a provider-only override would
    read as "falls back to the owner default".
    """
    return bool(override and override.get("provider_id"))


async def resolve_agent_config_slot(
    db: Any, *, agent_id: str, user_id: str, slot_name: str = "agent"
) -> dict | None:
    """The slot row ``agent_id``'s ``slot_name`` calls resolve against: the
    per-agent override when ``config_override_wins``, else the owner's
    ``user_slots`` default (``user_id`` is the owner). None when neither
    exists. Raises on DB errors — callers decide their own failure
    direction."""
    override = await db.get_one(
        "agent_slots", {"agent_id": agent_id, "slot_name": slot_name}
    )
    if config_override_wins(override):
        return override
    return await db.get_one("user_slots", {"user_id": user_id, "slot_name": slot_name})


def slot_rebinds(override: dict | None) -> bool:
    """Whether an ``agent_slots`` row rebinds the agent slot for identity.

    True only when the row carries BOTH a ``provider_id`` AND an
    ``agent_framework``. A provider-only or framework-only stub does not win:
    the config resolver skips empty-provider rows, and honouring a
    framework-only stub here would run e.g. the Codex driver against a Claude
    config.
    """
    return bool(override and override.get("provider_id") and override.get("agent_framework"))


def effective_agent_slot(
    override: dict | None, owner_default: dict | None
) -> dict | None:
    """The slot row THIS agent actually runs on — the one place that rule lives.

    Pure, so every projection of "what does this agent run" (the system
    prompt, the driver dispatch, the agents directory) can share it instead of
    re-deriving it against a hand-written query and drifting — which is how the
    directory once showed a framework-only stub's brand for an agent that was
    running on the owner default.
    """
    if slot_rebinds(override):
        return override
    return owner_default


def framework_of(slot: dict | None) -> str:
    """Framework name a slot row resolves to — through the ONE accessor.

    ``loop.driver.resolve_framework_name`` applies the whole precedence
    (explicit > ``AGENT_LOOP_FRAMEWORK`` env > the
    ``turn.pipeline.act.framework`` binding > the code default), so a
    distribution that binds a different default framework cannot make the
    prompt's identity, the slot writer's validation and the driver disagree.

    Never raises — this module's promise (see
    ``resolve_agent_model_identity``): a binding that names a framework no
    plugin provides (``FrameworkNotInstalledError``) or an unregistered name
    degrades to the raw column value, else the code default. The turn path
    still refuses that binding loudly; the system prompt must not fail to
    build over it.
    """
    from narranexus.contracts import UnknownEntry
    from narranexus.platform.agent_framework.loop.driver import (
        DEFAULT_AGENT_LOOP_FRAMEWORK,
        FrameworkNotInstalledError,
        resolve_framework_name,
    )

    raw = (slot or {}).get("agent_framework") or None
    try:
        return resolve_framework_name(raw)
    except (UnknownEntry, FrameworkNotInstalledError):
        return raw or DEFAULT_AGENT_LOOP_FRAMEWORK


async def resolve_agent_model_identity(
    agent_id: str, db: Any
) -> AgentModelIdentity:
    """Resolve THIS agent's real (framework, model) for prompt display.

    Overlay (the authority ``_resolve_agent_framework_name`` delegates to):
      1. Per-agent override — ``agent_slots[agent_id, 'agent']`` wins
         ONLY when it carries BOTH a ``provider_id`` AND an
         ``agent_framework`` (a provider-only or framework-only stub does
         not rebind the slot; the config resolver skips it too).
      2. Owner default — ``user_slots[owner, 'agent']`` where
         ``owner = agents.created_by``.

    Both framework and model come from whichever slot row wins, so the
    displayed identity matches what the driver actually runs.

    Never raises: any missing row / null column / DB error degrades to
    ``(resolve_framework_name(None), "")`` — and even a broken framework
    binding degrades (see ``framework_of``) — so identity resolution can never
    break the system-prompt build. The resolved framework is displayed through
    its own ``FrameworkMeta``, so the prompt still says something
    truthful-by-fallback rather than a wrong brand.
    """
    slot: dict | None = None
    try:
        override = await db.get_one(
            "agent_slots", {"agent_id": agent_id, "slot_name": "agent"}
        )
        owner_default: dict | None = None
        if not slot_rebinds(override):
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            owner = (agent_row or {}).get("created_by")
            if owner:
                owner_default = await db.get_one(
                    "user_slots", {"user_id": owner, "slot_name": "agent"}
                )
        slot = effective_agent_slot(override, owner_default)
    except Exception as e:  # noqa: BLE001 — defensive: any DB hiccup
        logger.warning(
            f"[agent_identity] slot lookup failed for agent={agent_id}: {e}; "
            f"falling back to the bound default framework"
        )
        slot = None

    framework = framework_of(slot)
    model = (slot or {}).get("model") or ""
    return AgentModelIdentity(
        framework=framework,
        framework_display=_display_for(framework),
        model=model,
    )
