"""
@file_name: protocols.py
@author: Bin Liang
@date: 2026-07-29
@description: Every component seam as a Protocol — "interfaces complete on
day one, implementations grow in phases".

``loop.py`` imports only this module and ``events.py``; concrete
implementations arrive via ``LoopAssembly`` injection. Extending the
framework means swapping an implementation or adding a registry entry —
never changing a signature (the test: adding a second implementation
produces zero diff on existing classes).

``CancellationSignal`` re-declares the platform's ``CancellationView``
structurally (``requested() -> bool``) so this layer stays free of
platform imports; the real class satisfies it as-is.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, Sequence, runtime_checkable

from xyz_agent_context.agent_framework.nexus_power.contracts.errors import LoopError
from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    LedgerEntry,
    LoopEvent,
    Usage,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ModelEvent,
    ModelRequest,
    ProviderMessage,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    Decision,
    PolicyContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)


@runtime_checkable
class ModelClient(Protocol):
    """The single abstraction over streaming model calls.

    Default implementation wraps the repo-wide litellm entry point; a
    bypass client is written only when passthrough measurably fails for
    a dialect. Stream failures raise raw exceptions — classification is
    ``ErrorClassifier``'s job (separation of concerns).
    """

    profile: ProviderProfile

    def stream_step(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...

    def estimate_cost_usd(self, usage: Usage, model: str) -> float | None:
        """Turn cost in USD, or None when the model's price is unknown.

        The claude CLI reports its own cost; a self-driven loop must
        price its own tokens, or every NexusPower turn shows $0 on the
        cost surface. Implementations should use a maintained price
        source rather than a hand-kept table.
        """
        ...


@runtime_checkable
class ToolChannel(Protocol):
    """One capability family's tool provider — the framework's most
    important long-term convergence point. Subagents, scheduling and
    capability expansion all arrive as new channels; the dispatcher and
    the loop never change for them."""

    def list_tools(self) -> list[ToolSpec]:
        """Current tool inventory, in a DETERMINISTIC, APPEND-ONLY order
        (constraint C2). The dispatcher concatenates channel lists
        verbatim — no name sort downstream — and the result is the
        provider-visible tool array whose byte prefix the cache keys on.
        A channel that reorders (or registers in a racy order) silently
        invalidates every cached prefix behind the reorder point; new
        tools must append after existing ones."""
        ...

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...

    async def refresh(self) -> bool:
        """Refresh the tool inventory; True when it changed (drives the
        dispatcher's generation cache — the dynamic-expansion seam)."""
        ...


@runtime_checkable
class ToolExecutor(Protocol):
    """The dispatcher abstraction the loop talks to."""

    def visible_tools(self) -> list[ToolSpec]: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...


@runtime_checkable
class PolicyLayer(Protocol):
    """One adjudication layer (pure function; internal exception == deny)."""

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision: ...


@runtime_checkable
class StopPolicy(Protocol):
    """Stop adjudication. v1: NoMoreActionsStop — text alone never
    sustains a turn (monologue semantics)."""

    async def should_stop(self, step_calls: Sequence[ToolCall], ledger: "LedgerView") -> bool: ...


@runtime_checkable
class SteeringInlet(Protocol):
    """Step-boundary injection point. v1: always empty; P4 mounts the
    TriggerInbox. Injection is append-only — never mutate the prefix."""

    async def drain(self) -> list[ProviderMessage]: ...


@runtime_checkable
class EventLogWriter(Protocol):
    """Two-track log sink — every event passes through (append-only,
    ``(thread_id, seq)`` idempotent)."""

    async def append(self, event: LoopEvent) -> None: ...

    async def flush(self) -> None: ...


@runtime_checkable
class ErrorClassifier(Protocol):
    """Raw exception → LoopError normalization (contract A5)."""

    def classify(self, exc: BaseException) -> LoopError: ...


@runtime_checkable
class RetryPolicy(Protocol):
    """Step-level retry seam. v1: NoRetry (an error ends the turn; the
    platform-side helper fallback still applies)."""

    async def should_retry(self, error: LoopError, attempt: int) -> bool: ...


@runtime_checkable
class CancellationSignal(Protocol):
    """Structural view over cancellation tokens (one question only)."""

    def requested(self) -> bool: ...


@runtime_checkable
class ExpressionPolicy(Protocol):
    """Monologue/expression adjudication (R5: a strategy seam).

    The framework owns no expression tools; which calls count as
    outward expression is injected data. Per-agent customization is a
    new implementation — the loop never changes.
    """

    def is_expressive(self, name: str) -> bool: ...

    def tag_text_event(self, event: LoopEvent) -> LoopEvent: ...

    def turn_had_expression(self, tool_calls_seen: Sequence[ToolCall]) -> bool: ...


@runtime_checkable
class LedgerView(Protocol):
    """Read-only ledger surface for strategies (write access stays with
    the loop — read/write separation by type)."""

    def entries(self) -> Sequence[LedgerEntry]: ...

    def open_tool_calls(self) -> Sequence[ToolCall]: ...

    def total_usage(self) -> Usage: ...

    def last_input_tokens(self) -> int: ...


@runtime_checkable
class CompactionPolicy(Protocol):
    """Context compaction — present from v1 (a long-running turn must
    never die on the context wall; iron rule #14).

    Discipline: compaction only APPENDS replacement entries; existing
    entries are immutable; tool_use/result pairs are never split; the
    head (system) and recent tail stay protected.
    """

    def should_compact(self, ledger: LedgerView, profile: ProviderProfile) -> bool: ...

    async def compact(
        self, ledger: LedgerView, profile: ProviderProfile
    ) -> Sequence[LedgerEntry]: ...


@runtime_checkable
class ContextProjector(Protocol):
    """Ledger → this step's messages. v1 passes the materialized input
    through and honours compaction entries; the full projector (per-
    provider dialects, pruning pipeline) is a later implementation swap."""

    def project(self, ledger: LedgerView, profile: ProviderProfile) -> list[ProviderMessage]: ...
