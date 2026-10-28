"""
@file_name: agent_events.py
@author: Bin Liang
@date: 2026-09-03
@description: The agent-loop event-dict contract (what every AgentLoopDriver yields).

Moved verbatim from ``narranexus.platform.agent_framework.loop.events`` in
batch 1 of the plugin platform; that module is gone and every producer and
consumer imports from here.

Every ``AgentLoopDriver`` yields event dicts in exactly two top-level
families; the platform's ResponseProcessor (and, through it, the frontend WS
stream, the billing chain, the helper-LLM fallback and the event_stream replay
table) consumes them. The values are wire protocol, streamed verbatim through
the remote executor's NDJSON channel. Changing any value is a breaking
protocol change across orchestrator/executor versions — bump
``API_VERSIONS["agent_events"]``, never "just refactor".

Shapes (all optional fields marked ``total=False``):
  {"type": "raw_response_event", "data": {...}} where data.type is one of
    - "response.text.delta"  {delta}
    - "response.done"        {usage?, stop_reason?, model?, total_cost_usd?,
                              num_turns?, session_id?, superseded_by_retry?}
    - "response.error"       {error_message, error_type, self_serviceable?}
    - "response.usage"       {usage}
    - "response.reply.delta" {delta, call_id?, tool_name?}
    - "response.retry"       {error_type, attempt, max_attempts, delay_seconds}
  {"type": "run_item_stream_event", "item": {...}} where item.type is one of
    - "thinking_item"          {content}
    - "tool_call_item"         {tool_call_id, tool_name, arguments}
    - "tool_call_output_item"  {output, tool_call_id?, tool_name?, status?}

Load-bearing consumer facts (why these exact fields):
  - ``response.done``'s usage is the ONLY data source of the billing
    chain (cost_records → quota deduction).
  - ``tool_call_item.tool_name`` keeps the ``mcp__{server}__{tool}``
    namespace; three consumers substring-match it (organic-reply
    detection, frontend reply bubble, IM reply extraction).
  - ``error_type`` drives fallback skip decisions, the agent circuit
    breaker and frontend actionable badges.
  - ``self_serviceable`` (optional bool) says whether the USER can clear
    the error alone (wait out / upgrade a rate limit, re-login, top up)
    — set by drivers that classify (claude), absent otherwise, and absent
    too when the CLI itself did not classify the error (``unknown``).
"""
from __future__ import annotations

from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Top-level event families
# ---------------------------------------------------------------------------

TYPE_RAW_RESPONSE_EVENT = "raw_response_event"
TYPE_RUN_ITEM_STREAM_EVENT = "run_item_stream_event"

# ---------------------------------------------------------------------------
# data.type values (raw_response_event)
# ---------------------------------------------------------------------------

DATA_TYPE_TEXT_DELTA = "response.text.delta"
DATA_TYPE_DONE = "response.done"
DATA_TYPE_ERROR = "response.error"
# Per-turn token usage harvested from the streaming events (Anthropic
# message_start carries input tokens, message_delta carries output tokens).
# Used for cost/quota accounting when the CLI's terminal ResultMessage.usage is
# unreliable — e.g. a proxied non-Anthropic model via the LiteLLM gateway, where
# ResultMessage.usage comes back 0 but the streamed message_delta has real
# tokens. Accumulated across turns by response_processor.
DATA_TYPE_USAGE = "response.usage"
# The user-facing reply, streamed as the model writes it (NexusPower
# only). Under the monologue/expression contract the reply lives in an
# expression tool's argument, so the argument text IS the reply — this
# shape carries it live, ahead of the completed tool_call_item that
# repeats the same final text. Drivers that do not stream tool
# arguments simply never emit it, so every existing consumer is
# unaffected; consumers that do not know it can ignore it safely (the
# tool_call_item remains the authoritative record).
DATA_TYPE_REPLY_DELTA = "response.reply.delta"
# The adapter is about to retry the model call after a transient provider
# error (claude_code + subscription auth only: the CLI never retries a 429 for
# a claude.ai subscription, so the adapter resumes the same CLI session). Data:
# ``error_type`` (the CLI enum), ``attempt`` (1-based), ``max_attempts``,
# ``delay_seconds``. Rendered as a step-panel progress row; consumers that do
# not know it may ignore it — the retry's own events follow either way.
DATA_TYPE_RETRY = "response.retry"
# Optional boolean on a raw event (``response.done`` / ``response.usage``)
# emitted by a run the transient retry replaced. The event is still
# delivered — its usage is what the billing chain sums — but it no longer
# means "this turn produced output": the claude adapter's resume-refused
# gate skips it. Written and read by adapters/claude/sdk.py only.
DATA_TYPE_DONE_SUPERSEDED_KEY = "superseded_by_retry"


# ---------------------------------------------------------------------------
# item.type values (run_item_stream_event)
# ---------------------------------------------------------------------------

ITEM_TYPE_THINKING = "thinking_item"
ITEM_TYPE_TOOL_CALL = "tool_call_item"
ITEM_TYPE_TOOL_CALL_OUTPUT = "tool_call_output_item"
# Non-streaming collection shape only (output_transfer's new_items list);
# never yielded on the streaming path.
ITEM_TYPE_MESSAGE_OUTPUT = "message_output_item"
# The agent's current plan snapshot (NexusPower only): full replace on
# every update — {"steps": [{"step", "status"}], "note"}.
ITEM_TYPE_PLAN = "plan_item"

# ---------------------------------------------------------------------------
# Error taxonomy (claude-agent-sdk vocabulary, adopted platform-wide)
# ---------------------------------------------------------------------------

# response_processor's severity triage, the circuit breaker and the
# fallback skip decision all branch on these values. Driver-synthesized
# errors must pick from this set (or the platform-side extensions below).
CLI_ERROR_TYPES = frozenset(
    {
        "authentication_failed",
        "billing_error",
        "rate_limit",
        "invalid_request",
        "server_error",
        "unknown",
    }
)

# Which CLI enums the USER can clear on their own — wait out / upgrade a
# rate limit, re-login, top up — as opposed to a provider outage or a request
# the platform built wrong. Keyed on the enum only (message text is free-form
# and provider-specific); every CLI_ERROR_TYPES value is placed explicitly.
# ``unknown`` (the CLI did not classify it either) and a missing type have NO
# verdict -> None, never a fabricated False: it may well be a quota or
# credential problem. Platform-side extensions (``no_output``) are False.
# Consumed by the claude driver's ``response.error`` builders; carried to the
# frontend / inbox as ``ErrorMessage.self_serviceable`` so the UI can tell
# "you can fix this" from "the platform broke" without parsing the text.
_SELF_SERVICEABLE_CLI_ERROR_TYPES = frozenset(
    {"rate_limit", "authentication_failed", "billing_error"}
)


_UNCLASSIFIED_CLI_ERROR_TYPES = frozenset({"", "unknown"})


def cli_error_self_serviceable(error_type: "str | None") -> "bool | None":
    """True when ``error_type`` names a failure the user can clear alone,
    False when it names one they cannot, None when nobody classified it."""
    key = error_type or ""
    if key in _UNCLASSIFIED_CLI_ERROR_TYPES:
        return None
    return key in _SELF_SERVICEABLE_CLI_ERROR_TYPES


# ---------------------------------------------------------------------------
# Usage field vocabulary (dual: Anthropic vs OpenAI/codex spellings)
# ---------------------------------------------------------------------------

# ExecutionState.accumulate_usage reads cache-read tokens by checking
# these keys in this order. Producers may emit either spelling.
USAGE_CACHE_READ_KEYS = ("cache_read_input_tokens", "cached_input_tokens")
USAGE_CACHE_CREATION_KEY = "cache_creation_input_tokens"

# ---------------------------------------------------------------------------
# TypedDict shapes (documentation-grade typing; dicts remain plain dicts)
# ---------------------------------------------------------------------------


class RawResponseData(TypedDict, total=False):
    type: str  # DATA_TYPE_TEXT_DELTA | DATA_TYPE_DONE | DATA_TYPE_ERROR
    delta: str
    usage: dict[str, Any]
    stop_reason: str
    model: str
    total_cost_usd: float
    num_turns: int
    error_message: str
    error_type: str
    self_serviceable: bool


class RawResponseEvent(TypedDict):
    type: str  # TYPE_RAW_RESPONSE_EVENT
    data: RawResponseData


class RunItem(TypedDict, total=False):
    type: str  # ITEM_TYPE_THINKING | ITEM_TYPE_TOOL_CALL | ITEM_TYPE_TOOL_CALL_OUTPUT
    content: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    output: str
    status: str


class RunItemStreamEvent(TypedDict):
    type: str  # TYPE_RUN_ITEM_STREAM_EVENT
    item: RunItem


# ---------------------------------------------------------------------------
# Constructors for the most commonly synthesized shapes
# ---------------------------------------------------------------------------


def raw_text_delta_event(delta: str) -> RawResponseEvent:
    """A user-visible/monologue text increment."""
    return {
        "type": TYPE_RAW_RESPONSE_EVENT,
        "data": {"type": DATA_TYPE_TEXT_DELTA, "delta": delta},
    }


def raw_error_event(error_message: str, error_type: str) -> RawResponseEvent:
    """A stream error; ``error_type`` should come from ``CLI_ERROR_TYPES``
    (or a platform-side classifier extension consumers already know)."""
    return {
        "type": TYPE_RAW_RESPONSE_EVENT,
        "data": {
            "type": DATA_TYPE_ERROR,
            "error_message": error_message,
            "error_type": error_type,
        },
    }


__all__ = [
    "cli_error_self_serviceable",
    "TYPE_RAW_RESPONSE_EVENT",
    "TYPE_RUN_ITEM_STREAM_EVENT",
    "DATA_TYPE_TEXT_DELTA",
    "DATA_TYPE_DONE",
    "DATA_TYPE_ERROR",
    "DATA_TYPE_USAGE",
    "DATA_TYPE_REPLY_DELTA",
    "DATA_TYPE_RETRY",
    "DATA_TYPE_DONE_SUPERSEDED_KEY",
    "ITEM_TYPE_THINKING",
    "ITEM_TYPE_TOOL_CALL",
    "ITEM_TYPE_TOOL_CALL_OUTPUT",
    "ITEM_TYPE_MESSAGE_OUTPUT",
    "ITEM_TYPE_PLAN",
    "CLI_ERROR_TYPES",
    "USAGE_CACHE_READ_KEYS",
    "USAGE_CACHE_CREATION_KEY",
    "RawResponseData",
    "RawResponseEvent",
    "RunItem",
    "RunItemStreamEvent",
    "raw_text_delta_event",
    "raw_error_event",
]
