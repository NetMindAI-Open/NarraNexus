"""
@file_name: model.py
@author: Bin Liang
@date: 2026-07-29
@description: Model-side contracts — "dialect is data, not code".

``ProviderProfile`` describes a provider's behavioural dialect as a data
row: onboarding a new provider means adding a row, never adding code
branches (iron rules #9/#15 — every user model is a first-class
citizen). A bypass ``ModelClient`` implementation is written only when
litellm passthrough measurably fails for a dialect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# v1 consumes materializer-built provider messages (OpenAI-style dicts).
ProviderMessage = dict[str, Any]


@dataclass(frozen=True)
class ProviderProfile:
    """One provider's dialect descriptor.

    Fields are filled from measured behaviour (the four litellm
    passthrough probes), never guessed. Unknown providers resolve to a
    conservative default row — any user model runs; it just forgoes the
    optimizations.
    """

    name: str
    cache_style: Literal["breakpoints", "prefix_auto", "none"] = "none"
    thinking_replay: Literal["keep", "strip"] = "strip"
    supports_arg_delta: bool = False   # streamed tool-argument fragments
    max_breakpoints: int = 4           # Anthropic-style cache_control budget
    context_window: int = 128_000      # tokens; compaction thresholds key off this
    max_output_tokens: int = 8_192
    # The provider's HARD ``input + max_tokens`` limit, which is a
    # different number from ``context_window`` above: that one is the
    # budget we choose to manage against (and compact against), this one
    # is the wall the request 400s at. Keeping them apart lets the
    # output clamp use the real wall without moving compaction's
    # trigger.
    #
    # ``None`` means unmeasured — read it through ``output_wall``, never
    # directly. A literal default here silently diverged from
    # ``context_window`` the moment a row set only the latter, and the
    # clamp then sized against a wall SHORTER than the window we manage,
    # cutting the free tier's default model from 8_192 to 1_024.
    vendor_context_window: int | None = None

    @property
    def output_wall(self) -> int:
        """The limit the output clamp sizes against.

        Unmeasured providers fall back to the managed budget, so "no
        measurement" can never mean "a wall we invented" — the two are
        equal by construction rather than by a matching literal.
        """
        return self.vendor_context_window or self.context_window


@dataclass(frozen=True)
class ModelParams:
    """Resolved model settings for one turn (chosen by the caller — the
    framework never picks models; iron rule #15)."""

    model: str
    provider: str | None = None
    api_key: str = ""
    base_url: str = ""
    thinking: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpServerSpec:
    """One MCP server endpoint: ``{url, headers?}`` (per-agent headers)."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CachePlan:
    """Pure-data output of the prompt-cache policy.

    ``ModelClient`` translates it per ``profile.cache_style``:
    ``breakpoints`` injects cache_control markers at the given message
    indices; ``prefix_auto``/``none`` only rely on deterministic ordering.
    """

    breakpoint_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class ModelRequest:
    """The full input bundle for one model call."""

    messages: list[ProviderMessage]
    tools: list[dict[str, Any]]        # OpenAI function-tool schema dicts
    params: ModelParams
    cache_plan: CachePlan = field(default_factory=CachePlan)
    # What this request's input is expected to cost, so the client can
    # leave room for it under the provider's input+output wall. Zero
    # means "unknown" and the client asks for the full ceiling.
    input_tokens_estimate: int = 0


# R3: a closed kind vocabulary. Providers may evolve freely — we always
# *translate into* these kinds; adding one is an explicit contract change.
ModelEventKind = Literal[
    "text_delta",
    "thinking_delta",
    "tool_use_start",  # tool name arrives before arguments (policy can veto early)
    "arg_delta",       # raw partial-JSON argument fragment
    "tool_use",        # complete call (model-track accounting)
    "done",            # step finished; carries usage + stop reason
]

MODEL_EVENT_KINDS: frozenset[str] = frozenset(
    ("text_delta", "thinking_delta", "tool_use_start", "arg_delta", "tool_use", "done")
)


@dataclass(frozen=True)
class ModelEvent:
    """One atomic event from a streaming model call.

    ``content_index`` aligns fragments across interleaved content blocks
    (pi discipline: block events are not guaranteed contiguous).
    """

    kind: ModelEventKind
    content_index: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Fail at the construction site — a typo must never silently
        # drop events downstream.
        if self.kind not in MODEL_EVENT_KINDS:
            raise ValueError(f"unknown ModelEvent kind: {self.kind!r}")
