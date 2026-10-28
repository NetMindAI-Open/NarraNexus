"""
@file_name: pipeline.py
@author: Bin Liang
@date: 2026-09-03
@description: Stage strategies (the vertical slots) and the per-agent pipeline profile that selects them.

Today fast mode, voice mode, job turns and silent IM ingestion are boolean
knobs scattered across the runtime steps. A ``PipelineProfile`` names them:
one strategy per stage, a budget, and an optional capability filter. Built-in
profiles are ``default``, ``fast``, ``voice``, ``job`` and ``silent``; plugins
contribute more; an agent binds one; a turn may override it.

``StageStrategy`` is structural: a strategy for stage S is any object with an
``async run(inputs) -> S's context value``. The platform's turn runtime is the
only caller, so the argument shape is the platform's ``StageInputs`` (not part
of the public contract yet — alpha).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, Protocol, runtime_checkable

from narranexus.contracts.agent.stages import Budgets, Stage


@runtime_checkable
class StageStrategy(Protocol):
    """One stage's behaviour; bound through the ``turn.pipeline.<stage>`` slot."""

    stage: Stage

    async def run(self, inputs: Any) -> Any: ...


@runtime_checkable
class TurnPipeline(Protocol):
    """The whole turn runtime (slots ``turn`` / ``turn.pipeline``).

    ``run`` executes the seven stages for one turn under ``profile`` and
    yields the platform's progress/response messages until Commit has
    finished (Reflect runs in the background). The message vocabulary is the
    platform's (alpha), like ``StageStrategy.run``'s inputs.
    """

    # An async generator: declared with plain ``def`` so the annotation is the
    # iterator itself, not a coroutine resolving to one.
    def run(self, ingress: Any, profile: "PipelineProfile") -> AsyncIterator[Any]: ...


# Decision (batch 1 review): every stage slot (``turn.pipeline.<stage>``) shares
# the ONE ``StageStrategy`` contract; the stage a strategy serves is its
# ``stage`` field, checked when the slot is bound, not encoded in the type.
# ``ActStrategy`` is an alias — not a subtype — that gives the Act slot a
# path-readable contract name. No further per-stage aliases: the other stage
# slots point at ``StageStrategy`` directly.
ActStrategy = StageStrategy


# --- the ``when`` clause grammar -------------------------------------------
#
# A profile says WHEN it applies as data, so selecting one is not an if-chain
# in the platform (which no plugin profile could ever reach). Deliberately
# tiny and closed — a predicate language, not an expression language: no
# arbitrary attribute access, no calls, no numbers, no eval. The frontend's
# slot-point predicates (``frontend/src/platform/registries/when.ts``) are the
# same IDEA with a different vocabulary (``conversationKind:`` / ``agentHas:``
# / ``setting:``); that grammar is keyed on UI facts and cannot express a turn
# fact like ``source == 'discord'``, so this is a sibling, not a copy. Both
# stay closed vocabularies for the same reason: a typo must be a parse error,
# never "always true".
#
#   clause  := term (" or " term)*          -- lowest precedence
#   term    := atom (" and " atom)*
#   atom    := "!"? NAME                    -- truthiness of a context key
#            | NAME ("==" | "!=") VALUE     -- string comparison
#   VALUE   := "'...'" | '"..."' | bare word
#
# Unknown keys are falsy (absent context is not a match), never an error: the
# host decides what facts a turn exposes and may add more over time.
_ATOM_RE = re.compile(
    r"""^(?P<neg>!)?\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*
        (?:(?P<op>==|!=)\s*(?P<value>'[^']*'|"[^"]*"|[A-Za-z0-9_.:@/-]+))?$""",
    re.VERBOSE,
)


class WhenSyntaxError(ValueError):
    """A ``when`` clause that does not parse. Loud at selection time rather
    than silently never (or always) matching."""


def _eval_atom(atom: str, ctx: Mapping[str, Any]) -> bool:
    m = _ATOM_RE.match(atom.strip())
    if not m:
        raise WhenSyntaxError(
            f"invalid when clause {atom!r}: expected NAME, !NAME, NAME == 'value' or NAME != 'value'"
        )
    key, op, raw = m.group("key"), m.group("op"), m.group("value")
    value = ctx.get(key)
    if op is None:
        holds = bool(value)
    else:
        wanted = raw[1:-1] if raw and raw[0] in "'\"" else (raw or "")
        holds = (str(value) if value is not None else "") == wanted
        if op == "!=":
            holds = not holds
    return not holds if m.group("neg") else holds


def evaluate_when(clause: str, ctx: Mapping[str, Any]) -> bool:
    """Whether ``clause`` holds for the facts in ``ctx``.

    An EMPTY clause is ``False``: a profile with no ``when`` does not take part
    in automatic selection at all (it is reachable by being named explicitly,
    or by being the fallback ``default``). That is deliberately the opposite of
    the frontend's "empty always holds" — here "always holds" would mean a
    profile that silently wins every turn.
    """
    if not clause or not clause.strip():
        return False
    return any(
        all(_eval_atom(atom, ctx) for atom in term.split(" and "))
        for term in clause.split(" or ")
    )


@dataclass(frozen=True)
class CapabilityFilter:
    """Which capabilities take part under a profile (``None`` = all enabled)."""

    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()

    def allows(self, name: str) -> bool:
        if name in self.exclude:
            return False
        return self.include is None or name in self.include


@dataclass(frozen=True)
class PipelineProfile:
    """A named selection of stage strategies plus budgets, a capability filter,
    and the condition under which the host picks it.

    ``when`` is the profile's own answer to "when do I apply?" (grammar above),
    and ``order`` breaks ties — LOWEST first, so a specific profile outranks a
    broad one. Together they replace the platform-side if-chain that used to
    decide this: a plugin profile can now say ``when="source == 'discord'"``
    and be selected without a platform edit. An empty ``when`` means the
    profile is only reachable by being named explicitly (or by being the
    fallback ``default``).
    """

    id: str
    strategies: Mapping[Stage, str] = field(default_factory=dict)  # stage -> strategy name; missing = default
    budgets: Budgets = field(default_factory=Budgets)
    capability_filter: CapabilityFilter = field(default_factory=CapabilityFilter)
    narrative_persistence: str = "durable"  # "durable" | "ephemeral"
    when: str = ""
    order: int = 100

    def strategy_for(self, stage: Stage, default: str = "default") -> str:
        return self.strategies.get(stage, default)

    def with_override(self, override: "TurnOverride") -> "PipelineProfile":
        strategies = dict(self.strategies)
        strategies.update(override.strategies)
        return PipelineProfile(
            id=self.id,
            strategies=strategies,
            budgets=override.budgets or self.budgets,
            capability_filter=override.capability_filter or self.capability_filter,
            narrative_persistence=override.narrative_persistence or self.narrative_persistence,
            when=self.when,
            order=self.order,
        )


@dataclass(frozen=True)
class TurnOverride:
    """Per-turn overlay on the agent's profile (the TURN binding layer)."""

    strategies: Mapping[Stage, str] = field(default_factory=dict)
    budgets: Budgets | None = None
    capability_filter: CapabilityFilter | None = None
    narrative_persistence: str | None = None


BUILTIN_PROFILE_IDS: tuple[str, ...] = ("default", "fast", "voice", "job", "silent")

__all__ = [
    "BUILTIN_PROFILE_IDS",
    "ActStrategy",
    "CapabilityFilter",
    "PipelineProfile",
    "StageStrategy",
    "TurnOverride",
    "TurnPipeline",
    "WhenSyntaxError",
    "evaluate_when",
]
