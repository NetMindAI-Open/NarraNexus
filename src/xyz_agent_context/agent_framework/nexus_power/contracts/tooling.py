"""
@file_name: tooling.py
@author: Bin Liang
@date: 2026-07-29
@description: Tool-surface contracts: specs, calls, results, annotations,
and policy decisions.

Two disciplines live here:
  - a tool's description travels WITH its spec (single source of truth —
    the prompt's tool-guideline section derives from specs, so "what the
    model sees" can never drift from "what is registered");
  - ``ToolAnnotations.marker_only`` encodes label tools: the call itself
    is the signal; the dispatcher short-circuits execution and the whole
    meaning lives in the event stream (streamed argument fields become
    the user-visible reply, delivered by the event consumer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ToolAnnotations:
    """Behavioural annotations driving dispatch and presentation."""

    read_only: bool = False            # eligible for parallel execution
    destructive: bool = False          # candidate for interceptable preview (E3)
    expressive: bool = False           # an outward-expression channel
    streamable_fields: tuple[str, ...] = ()  # arg fields projected as ui deltas
    marker_only: bool = False          # label tool: execution is a no-op success


@dataclass(frozen=True)
class ToolSpec:
    """One tool's full declaration (description included — single source).

    MCP tools keep the ``mcp__{server}__{tool}`` namespace (three legacy
    consumers substring-match it); builtin tools use bare names.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: ToolAnnotations = field(default_factory=ToolAnnotations)

    def as_openai_tool(self) -> dict[str, Any]:
        """The OpenAI function-tool wire shape litellm consumes."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    """One model-initiated call (arguments complete).

    ``parse_error`` is set when the argument JSON never parsed (typically
    a stream cut by the output-token limit). Such a call must be ANSWERED,
    never executed — running it with partial args produced misleading
    downstream failures (a write_file without ``path`` resolved to the
    workspace root and surfaced ``Is a directory``, 2026-07-30 incident).

    ``truncated`` distinguishes the two ways that parse can fail, because
    they need opposite advice: arguments severed mid-stream must be sent
    again SMALLER, while genuinely malformed JSON must be sent again
    CORRECTED. It is decided from the received bytes, not from the
    provider's ``stop_reason`` — gateways misreport that field.
    """

    id: str
    name: str
    args: dict[str, Any]
    parse_error: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ToolResult:
    """One tool outcome. Denials and failures are error-shaped results —
    they never escape as exceptions through the loop."""

    call_id: str
    ok: bool
    content: Any = None
    error: str | None = None
    synthetic: bool = False

    def as_text(self) -> str:
        """The string fed back to the model as the tool message body."""
        if self.ok:
            content = self.content
            return content if isinstance(content, str) else _compact_json(content)
        return f"ERROR: {self.error or 'tool failed'}"


def _compact_json(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True)
class ToolContext:
    """Execution context injected by the assembly (read-only to tools)."""

    agent_id: str
    workspace: str
    extra_env: dict[str, str] = field(default_factory=dict)


class PolicyVerdict(Enum):
    """Decision outcome. Deny always wins; a layer's internal exception
    counts as DENY (fail-closed — a deliberate multi-tenant choice with
    no industry precedent to lean on)."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    verdict: PolicyVerdict
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict is PolicyVerdict.ALLOW


ALLOW = Decision(PolicyVerdict.ALLOW)


@dataclass(frozen=True)
class PolicyContext:
    """What policy layers may see when judging a call."""

    tool_ctx: ToolContext
    disallowed_tools: frozenset[str] = frozenset()
