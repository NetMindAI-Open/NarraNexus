"""
@file_name: events.py
@author: Bin Liang
@date: 2026-07-29
@description: Typed loop events and ledger entries — the two-track schema.

Two-track discipline (day-1 structural constraint C1):
  - ``model`` track: events needed to rebuild LLM context (complete
    tool_use / tool_result / assistant text blocks);
  - ``ui`` track: increments replayed to frontends only (text deltas,
    thinking deltas, tool-argument deltas); never used for context
    rebuilding.

"Entry is schema": ``LedgerEntry`` is shaped like a future
``nexus_events`` table row, so persisting the log is a writer swap,
never a class rewrite. Compaction is an *appended* replacement entry
(``TYPE_COMPACTION``) — history is append-only; projection substitutes
the replaced span, the log keeps everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Literal, TypedDict

Track = Literal["model", "ui"]

# Internal event vocabulary. The mapping to the legacy dict contract
# (agent_framework.loop.events) is owned exclusively by LegacyEventAdapter.
TYPE_TEXT_DELTA = "text_delta"
TYPE_THINKING_DELTA = "thinking_delta"
TYPE_TOOL_USE = "tool_use"
# ui track only: the tool's name is known before its arguments finish
# streaming. The UI shows "using X" immediately; the completed TYPE_TOOL_USE
# then carries the full arguments and replaces it, keyed by call_id.
TYPE_TOOL_USE_START = "tool_use_start"
TYPE_TOOL_ARG_DELTA = "tool_arg_delta"  # ui track only: streamed argument field text
TYPE_TOOL_RESULT = "tool_result"
TYPE_COMPACTION = "compaction"          # replacement entry; see CompactionPayload
TYPE_PLAN = "plan"                      # ui track: full plan snapshot
TYPE_STEP_DONE = "step_done"
TYPE_TURN_DONE = "turn_done"
TYPE_ERROR = "error"

VALID_EVENT_TYPES = frozenset(
    {
        TYPE_TEXT_DELTA,
        TYPE_THINKING_DELTA,
        TYPE_TOOL_USE,
        TYPE_TOOL_USE_START,
        TYPE_TOOL_ARG_DELTA,
        TYPE_TOOL_RESULT,
        TYPE_COMPACTION,
        TYPE_PLAN,
        TYPE_STEP_DONE,
        TYPE_TURN_DONE,
        TYPE_ERROR,
    }
)


class Phase(Enum):
    """The five phases of one turn's state machine.

    DRAIN_STEERING and STOP_CHECK call sites exist from day one; v1
    mounts ``NullSteeringInlet`` (always empty) and ``NoMoreActionsStop``
    (stop when the model takes no action). Upgrading either is an
    assembly swap — ``loop.py`` never changes.

    Deliberate decision (R4): the phase set is framework anatomy, not an
    extension point. Pseudo-phase needs (e.g. a pre-execution preview)
    belong in hooks or dispatcher wrapping, never in new enum members.
    """

    PROJECT = auto()
    MODEL_STREAM = auto()
    DISPATCH = auto()
    DRAIN_STEERING = auto()
    STOP_CHECK = auto()


class EndReason(Enum):
    """Why a turn ended.

    Platform semantics: assistant text is inner monologue; reaching the
    user requires a tool call. A turn therefore ends when the model
    takes *no more actions* (``NO_MORE_ACTIONS``) — not when it stops
    talking. ``SUSPENDED`` is the P4 sleep/wake seat: v1 never produces
    it and a contract test locks that.
    """

    NO_MORE_ACTIONS = auto()
    INTERRUPTED = auto()
    ERROR = auto()
    SUSPENDED = auto()


@dataclass(frozen=True)
class Usage:
    """Real token usage for one or more model calls (additive).

    Anchored to provider-reported usage, never character estimates
    (constraint C3). The cache fields feed the cache-hit-rate metrics
    and the cost-transparency surface.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens
            + other.cache_creation_tokens,
        )

    def as_legacy_dict(self) -> dict[str, int]:
        """Both spellings of the legacy usage vocabulary (see
        ``loop.events.USAGE_CACHE_READ_KEYS``): Anthropic-style keys are
        authoritative; consumers probing OpenAI-style keys also succeed."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_tokens,
            "cache_creation_input_tokens": self.cache_creation_tokens,
        }


@dataclass(frozen=True)
class LoopEvent:
    """A typed event produced by the loop (pre-translation form).

    ``(thread_id, seq)`` is the idempotency key; ``seq`` is allocated
    exclusively by ``TurnLedger`` — no other component mints one.
    """

    track: Track
    seq: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    usage: Usage | None = None


@dataclass(frozen=True)
class LedgerEntry:
    """One ledger row — shaped like a future ``nexus_events`` table row."""

    seq: int
    track: Track
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    usage: Usage | None = None


# ---------------------------------------------------------------------------
# Payload shapes (R2): runtime stays dict for the grey-release adapters,
# but each type has exactly one authoritative shape, locked by golden tests.
# ---------------------------------------------------------------------------


class TextDeltaPayload(TypedDict):
    """``text_delta`` / ``thinking_delta``: a ui-track text increment."""

    text: str
    monologue: bool  # stamped by ExpressionPolicy.tag_text_event


class ToolUsePayload(TypedDict):
    """``tool_use``: one complete tool call on the model track."""

    call_id: str
    tool_name: str  # MCP tools keep the mcp__{server}__{tool} namespace
    args: dict[str, Any]


class ToolArgDeltaPayload(TypedDict):
    """``tool_arg_delta``: a streamed argument-field fragment (ui track).

    ``expressive`` marks fragments of an EXPRESSION tool's argument —
    those are the user-facing reply being written live; everything else
    is an internal presentation detail.
    """

    call_index: int
    call_id: str
    tool_name: str
    field_path: str
    text: str
    expressive: bool


class PlanPayload(TypedDict):
    """``plan``: the agent's full plan snapshot (replace-on-write)."""

    steps: list[dict[str, Any]]
    note: str


class ToolResultPayload(TypedDict):
    """``tool_result``: one tool outcome on the model track."""

    call_id: str
    ok: bool
    content: Any
    error: str | None
    synthetic: bool  # True when synthesized by interruption handling


class CompactionPayload(TypedDict):
    """``compaction``: a replacement entry (also the narrative feed)."""

    replaces_from_seq: int
    replaces_to_seq: int
    summary: str
    retained_tail_seq: int


class StepDonePayload(TypedDict):
    """``step_done``: one model call finished (per-step usage rides
    ``LoopEvent.usage``)."""

    step_index: int
    stop_reason: str


class TurnDonePayload(TypedDict):
    """``turn_done``: turn closure — the billing chain's sole source
    (cumulative usage rides ``LoopEvent.usage``)."""

    end_reason: str  # EndReason.name
    model: str
    num_steps: int
    structured_output: Any  # per TurnOptions.output_schema; None otherwise
    cost_usd: float | None  # priced by the model client; None = unknown price


class ErrorPayload(TypedDict):
    """``error``: a classified failure (drives fallback / breaker / badges)."""

    error_type: str  # ErrorType.value
    message: str
    retryable: bool
