"""
@file_name: extension_points.py
@author: Bin Liang
@date: 2026-09-04
@description: NexusPower's own extension points — the loop's strategy seats as slots other plugins can fill.

The pipeline has three vertical grains (spec §484): platform stages → the
act-stage framework (a Port) → the seams inside a framework. This module is
the third grain for NexusPower: five of its protocols are declared as slots
UNDER the framework slot it fills (``turn.pipeline.act.framework.nexus_power.<seat>``),
so the slot tree's nesting rule applies — bind ``turn.pipeline.act.framework``
to another framework and these seats are that framework's business, not
orphans in a ``builtin.`` domain. The loop's default implementations are the
default providers; any plugin may provide another implementation
(``provides: {"turn.pipeline.act.framework.nexus_power.stop": [...]}``) or bind
one through any of the six binding layers (distribution, narranexus.toml,
``NX_BIND__turn__pipeline__act__framework__nexus_power__stop=<provider>``).

A provider is ``Callable[[SeatContext], impl]``: it receives what the
assembly knows about the turn and returns the seat implementation.
``resolve_one`` / ``resolve_many`` read the kernel's resolved bindings
(``kernel.plugins.bound``, the same seam every other slot consumer uses) and
build the seat; unknown providers fail loud (a bound name that nobody provides
is a configuration error, never a silent fallback).

Registration happens once: the host boot (every process that runs the loop
boots, the executor included) registers what the manifest names. Third-party
seat providers reach the loop when their plugin is loaded in that process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.slots import Slot

OWNER = "builtin.frameworks.nexus_power"
# the framework slot this plugin fills; its seats hang beneath it
NAMESPACE = "turn.pipeline.act.framework.nexus_power"
STOP = f"{NAMESPACE}.stop"
COMPACTION = f"{NAMESPACE}.compaction"
PROJECTOR = f"{NAMESPACE}.projector"
EXPRESSION = f"{NAMESPACE}.expression"
POLICY = f"{NAMESPACE}.policy"

_PROTOCOLS = "narranexus_plugins.frameworks_nexus_power.core.contracts.protocols"

# (path, arity, contract, default provider, doc) — the manifest `declares` and the slot tree agree on these.
DECLARED_SLOTS: tuple[tuple[str, str, str, Optional[str], str], ...] = (
    (STOP, "one", f"{_PROTOCOLS}:StopPolicy", "no_more_actions", "When the loop ends a turn (default: the model made no more tool calls)."),
    (COMPACTION, "one", f"{_PROTOCOLS}:CompactionPolicy", "tool_result_pruner", "How the ledger is compacted when the context fills up."),
    (PROJECTOR, "one", f"{_PROTOCOLS}:ContextProjector", "passthrough", "How the ledger becomes the provider message list each step."),
    (EXPRESSION, "one", f"{_PROTOCOLS}:ExpressionPolicy", "contract", "Which tools count as the agent speaking (expressive tools) and how their text is tagged."),
    (POLICY, "many", f"{_PROTOCOLS}:PolicyLayer", None, "Tool-call policy layers, checked in registration order (disallowed tools, workspace confinement, shell confinement, ...)."),
)


@dataclass(frozen=True)
class SeatContext:
    """What a seat provider may look at while building its implementation."""

    options: Any  # TurnOptions
    workspace: str
    tool_context: Any = None  # ToolContext
    profile: Any = None  # ProviderProfile
    base_messages: list[Any] = field(default_factory=list)  # projector input (harness-inserted)
    tail: Optional[Callable[[], str]] = None  # projector: the per-step tail renderer


def _stop_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.stop import NoMoreActionsStop

    return NoMoreActionsStop()


def _compaction_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.compaction import ToolResultPruner

    return ToolResultPruner()


def _projector_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.projector import PassthroughProjector

    return PassthroughProjector(ctx.base_messages, ctx.tail or (lambda: ""))


def _expression_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.expression import ExpressionContract

    return ExpressionContract(ctx.options.expressive_tools)


def _disallowed_tools(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import DisallowedToolsLayer

    return DisallowedToolsLayer()


def _workspace_confinement(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import WorkspaceConfinementLayer

    return WorkspaceConfinementLayer()


def _shell_confinement(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import ShellConfinementLayer

    return ShellConfinementLayer()


# ``docs/API_POLICY.md`` §8: a one-arity slot is filled by ``CONTRIBUTION`` and
# a many-arity one by ``CONTRIBUTIONS``. Five seats live in ONE module here, so
# the seat name prefixes the §8 word (``STOP_CONTRIBUTION``, …) — the ``_DEFAULT``
# suffix these carried was a sixth vocabulary that said nothing about arity.
STOP_CONTRIBUTION = Contribution("no_more_actions", lambda: _stop_default)
COMPACTION_CONTRIBUTION = Contribution("tool_result_pruner", lambda: _compaction_default)
PROJECTOR_CONTRIBUTION = Contribution("passthrough", lambda: _projector_default)
EXPRESSION_CONTRIBUTION = Contribution("contract", lambda: _expression_default)
STOP_POLICIES = (STOP_CONTRIBUTION,)
COMPACTION_POLICIES = (COMPACTION_CONTRIBUTION,)
PROJECTORS = (PROJECTOR_CONTRIBUTION,)
EXPRESSION_POLICIES = (EXPRESSION_CONTRIBUTION,)
# Order is the check order; the same order the assembly always used.
POLICY_CONTRIBUTIONS = (
    Contribution("disallowed_tools", lambda: _disallowed_tools),
    Contribution("workspace_confinement", lambda: _workspace_confinement),
    Contribution("shell_confinement", lambda: _shell_confinement),
)

PROVIDERS: dict[str, tuple[Contribution[Any], ...]] = {
    STOP: STOP_POLICIES,
    COMPACTION: COMPACTION_POLICIES,
    PROJECTOR: PROJECTORS,
    EXPRESSION: EXPRESSION_POLICIES,
    POLICY: POLICY_CONTRIBUTIONS,
}


def slots() -> tuple[Slot, ...]:
    return tuple(
        Slot(path=path, arity=arity, contract=contract, owner=OWNER, default=default, doc=doc)  # type: ignore[arg-type]
        for path, arity, contract, default, doc in DECLARED_SLOTS
    )


def _registries(registries: Any):
    if registries is not None:
        return registries
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES


def resolve_one(path: str, ctx: SeatContext, registries: Any = None) -> Any:
    """Build the bound implementation of a one-arity seat through the kernel's resolved bindings (unknown provider → UnknownEntry, fail loud)."""
    from narranexus.kernel.plugins.bound import bound_entry

    return bound_entry(_registries(registries), path).factory()(ctx)


def resolve_many(path: str, ctx: SeatContext, registries: Any = None) -> tuple[Any, ...]:
    """Build every bound implementation of a many-arity seat, in binding order (registration order when unbound)."""
    from narranexus.kernel.plugins.bound import bound_entries

    return tuple(entry.factory()(ctx) for entry in bound_entries(_registries(registries), path))


__all__ = [
    "COMPACTION",
    "COMPACTION_CONTRIBUTION",
    "COMPACTION_POLICIES",
    "DECLARED_SLOTS",
    "EXPRESSION",
    "EXPRESSION_CONTRIBUTION",
    "EXPRESSION_POLICIES",
    "NAMESPACE",
    "OWNER",
    "POLICY",
    "POLICY_CONTRIBUTIONS",
    "PROJECTOR",
    "PROJECTORS",
    "PROJECTOR_CONTRIBUTION",
    "PROVIDERS",
    "STOP",
    "STOP_CONTRIBUTION",
    "STOP_POLICIES",
    "SeatContext",
    "resolve_many",
    "resolve_one",
    "slots",
]
