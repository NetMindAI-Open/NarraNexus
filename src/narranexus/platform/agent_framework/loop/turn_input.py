"""
@file_name: turn_input.py
@author: Bin Liang
@date: 2026-07-27
@description: The materialized per-turn input bundle step_3 hands a driver.

step_3 historically passed the turn's inputs piecemeal (messages,
mcp_servers, extra_env, disallowed_tools) — the "materialized layer" of
the turn existed only as loose locals at the call site. TurnInput makes
it one explicit object so every driver demonstrably eats the same thing
and the bundle has a place to grow.

Two-layer design (future direction): this is the materialized half of
the TurnContext split — pre-joined messages and MCP endpoints that the
claude/codex CLI drivers consume as-is. ``refs`` is the reserved
reference layer (serializable IDs/results a self-owned loop would
project context from itself); it stays ``None`` until a driver declares
the corresponding capability and actually consumes it — declaring
fields that nothing implements is exactly the schema-dishonesty trap
this refactor line avoids.

``cancellation`` is deliberately NOT part of TurnInput: it is per-run
control flow owned by the runtime, not turn content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TurnInput:
    """Everything a driver needs to run one turn, minus control flow.

    ``driver_kwargs()`` reproduces the exact keyword shape the step_3
    call site historically used, including the empty→None normalization
    that lets driver-side defaults engage.
    """

    messages: list[dict[str, Any]]
    mcp_servers: dict[str, dict[str, Any]]
    disallowed_tools: tuple[str, ...] = ()
    # Plugin tools reachable only through tool_search (never in the up-front
    # list). Fully-qualified names; frameworks without a search seam ignore it.
    deferred_tools: tuple[str, ...] = ()
    extra_env: dict[str, str] = field(default_factory=dict)
    # The agent this turn belongs to. Drivers that project their own
    # context (NexusPower) stamp it into ToolContext; CLI drivers accept
    # and ignore it via **kwargs.
    agent_id: str = "agent"
    # The turn's delivery surface: fully-qualified reply tools declared
    # by the platform (modules' expressive_tools, priority order —
    # the first entry is the default reply tool). Empty = mute turn.
    expressive_tools: tuple[str, ...] = ()
    # The turn's origin declaration — ONE already-rendered line
    # (channel.message_source_handler.render_origin_declaration) naming where
    # the turn came from and which tool answers it. Rendered at the step layer,
    # where working_source is known, so drivers stay ignorant of the registry
    # and there is exactly one place the sentence is composed. Empty = no
    # declared surface; drivers must then say nothing about replying.
    origin_declaration: str = ""
    # Reserved reference layer (design §8.2): serializable IDs/results
    # for drivers that project their own context. Always None today.
    refs: dict[str, Any] | None = None
    # Per-turn fast-mode knobs (schema.turn_profile.TurnProfile). None =
    # today's behavior everywhere; drivers that never heard of it ignore
    # the kwarg via **kwargs.
    turn_profile: Any | None = None
    # Absolute roots this turn may read IN ADDITION to the agent workspace
    # (e.g. the team shared folder, which is a sibling of every agent
    # workspace so that no single agent owns it). Only drivers that enforce
    # workspace confinement act on it — NexusPower; the CLI drivers run no
    # such layer and ignore the kwarg via **kwargs. Empty = today's behavior.
    extra_accessible_roots: tuple[str, ...] = ()

    def driver_kwargs(self) -> dict[str, Any]:
        """Kwargs for ``AgentLoopDriver.agent_loop`` — legacy-identical.

        messages/mcp_servers pass by reference (no defensive copies —
        step_3 merges into mcp_servers before the call and drivers must
        see the merged dict). Empty extra_env/disallowed_tools become
        None so driver defaults behave exactly as before. An empty
        expressive_tools emits NO key (mute stays the driver default);
        agent_id always rides along.
        """
        kwargs: dict[str, Any] = {
            "messages": self.messages,
            "mcp_servers": self.mcp_servers,
            "extra_env": self.extra_env or None,
            "disallowed_tools": list(self.disallowed_tools) or None,
            "agent_id": self.agent_id,
        }
        if self.expressive_tools:
            kwargs["expressive_tools"] = list(self.expressive_tools)
        if self.deferred_tools:
            kwargs["deferred_tools"] = list(self.deferred_tools)
        if self.turn_profile is not None:
            kwargs["turn_profile"] = self.turn_profile
        if self.origin_declaration:
            kwargs["origin_declaration"] = self.origin_declaration
        if self.extra_accessible_roots:
            # Emitted only when non-empty, like expressive_tools above: a
            # turn that grants nothing must produce the exact legacy kwargs.
            kwargs["extra_accessible_roots"] = list(self.extra_accessible_roots)
        return kwargs
