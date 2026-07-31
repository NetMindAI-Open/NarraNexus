"""
@file_name: options.py
@author: Bin Liang
@date: 2026-07-29
@description: TurnOptions — the framework's external invocation surface.

Deep-aligned with the claude-agent-sdk ``ClaudeAgentOptions`` and the
``codex exec`` flag surface so adopters keep their mental model:

  cwd/env                <- claude sdk cwd / codex --cd
  model settings         <- both
  allowed/disallowed     <- claude sdk tool filters
  permission_mode        <- claude sdk permission_mode / codex approvals
                            (mapped onto PolicyEngine layers, not a new system)
  output_mode            <- stream-json / codex --json
  include_arg_deltas     <- claude sdk include_partial_messages
  output_schema          <- codex --output-schema (structured final output;
                            callers may pass ``SomeModel.model_json_schema()``)
  expandables            <- ours: dynamic capability catalog
  subagents              <- claude sdk ``agents``
  session/resume         <- P4 seats; v1 keeps them None (contract-tested)

Deliberately absent: ``max_turns`` or any turn/duration ceiling. Long
runs are a first-class scenario (iron rule #14); the framework will
never grow such a parameter.

Validation runs once at the driver boundary (pydantic); inside the
framework the object is frozen data.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SubagentSpec(BaseModel):
    """One predefined subagent (mirrors a claude sdk ``agents`` entry).

    Capability-intersection discipline: ``tools`` intersects with the
    parent turn's visible surface; subagents derive a MINIMAL prompt
    mode and are born mute (no expressive tools) — results speak
    through the parent turn.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    prompt: str
    tools: tuple[str, ...] = ()
    model: str | None = None
    background: bool = False


class ExpandableSpec(BaseModel):
    """Wire-safe form of ``Expandable`` (everything JSON-serializable).

    ``local_tools`` intentionally has no wire form — in-process handlers
    exist only for same-process embedding and are attached at assembly
    time, never transported.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    card: str
    instructions: str = ""
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    skill_dirs: tuple[str, ...] = ()
    extra_env: dict[str, str] = Field(default_factory=dict)
    # Delivery tools this capability contributes (fully-qualified names);
    # expansion grants them to the turn's expression contract.
    expressive_tools: tuple[str, ...] = ()


class TurnOptions(BaseModel):
    """Everything configurable about one turn (all inputs except the
    conversation itself)."""

    model_config = ConfigDict(frozen=True)

    # --- runtime environment ---
    cwd: str
    agent_id: str = "agent"
    env: dict[str, str] = Field(default_factory=dict)

    # --- model ---
    model: str
    provider: str | None = None
    api_key: str = ""
    base_url: str = ""
    thinking: bool = False
    llm_extra: dict[str, Any] = Field(default_factory=dict)
    # ^ passthrough params for the model call (litellm kwargs — e.g.
    #   extra_headers for bearer-token providers). Dialect content only;
    #   the framework never inspects it.

    # --- tool surface ---
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()      # empty = no allowlist
    disallowed_tools: tuple[str, ...] = ()
    expressive_tools: tuple[str, ...] = ()   # monologue/expression contract
    marker_tools: tuple[str, ...] = ()       # label tools declared out-of-band
    builtin_groups: tuple[str, ...] = ("files", "shell", "context")
    permission_mode: Literal["default", "plan", "accept_edits", "bypass"] = "default"

    # --- capability expansion ---
    expandables: tuple[ExpandableSpec, ...] = ()
    initial_expansions: frozenset[str] = frozenset()

    # --- output control ---
    output_mode: Literal["legacy_dict", "loop_events"] = "legacy_dict"
    include_arg_deltas: bool = True
    output_schema: dict[str, Any] | None = None

    # --- subagents (P4 execution; declared surface is v1) ---
    subagents: dict[str, SubagentSpec] = Field(default_factory=dict)

    # --- session seats (P4; v1 keeps these None — contract-tested) ---
    session_id: str | None = None
    resume_from: str | None = None
