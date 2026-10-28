"""
@file_name: stages.py
@author: Bin Liang
@date: 2026-09-03
@description: The seven turn stages and the frozen value objects that flow between them.

Order is fixed by the platform — it is the skeleton that makes "one agent,
one user, one chat" run — but each stage's *strategy* is a replaceable slot
(``turn.pipeline.<stage>``) and each stage has ``onWill``/``onDid`` hooks.
The value objects are the approval-snapshot cut points: a stage strategy is
a pure function from the previous value to the next, plus side effects the
platform owns.

Field sets are the subset of today's ``RunContext`` that crosses a stage
boundary; anything a stage keeps to itself stays out of the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Stage(str, Enum):
    INGRESS = "ingress"
    RECALL = "recall"
    COMPOSE = "compose"
    ASSEMBLE = "assemble"
    ACT = "act"
    COMMIT = "commit"
    REFLECT = "reflect"


STAGES: tuple[Stage, ...] = tuple(Stage)


@dataclass(frozen=True)
class Budgets:
    """Per-turn limits the runtime enforces on plugin participation.

    ``None`` means "no limit" (binding rule #14: the platform never caps the
    agent loop itself; these bound *plugin* work around it).
    """

    sync_hook_timeout_s: float = 0.2
    sync_hook_total_s: float = 0.5
    context_tokens_hint: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class IngressContext:
    """Output of Ingress: the accepted input and where it came from."""

    agent_id: str
    user_id: str
    input_content: str
    working_source: str
    trigger_extra_data: Mapping[str, Any] = field(default_factory=dict)
    job_instance_id: str | None = None
    forced_narrative_id: str | None = None


@dataclass(frozen=True)
class RecallContext:
    """Output of Recall: which narratives are in play and what history was read."""

    narrative_ids: tuple[str, ...]
    session_id: str | None
    markdown_history: str = ""
    no_durable_topic: bool = False


@dataclass(frozen=True)
class ComposeContext:
    """Output of Compose: which capabilities participate and how the turn executes."""

    capability_names: tuple[str, ...]
    execution_type: str  # "agent_loop" | "direct_trigger" | "silent"
    instance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSurfaceView:
    """The tool surface a turn ran with (names only; definitions stay with the framework)."""

    mcp_servers: tuple[str, ...]
    expressive_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]


@dataclass(frozen=True)
class AssembleContext:
    """Output of Assemble: the prompt and tool surface, by digest, not by content.

    Digests keep the value small and byte-stable; the platform can always
    re-derive the full prompt from the same inputs.
    """

    system_prompt_sha256: str
    system_prompt_chars: int
    tools: ToolSurfaceView
    instruction_sections: tuple[str, ...]  # capability names that contributed, in order


@dataclass(frozen=True)
class ActContext:
    """Output of Act: what the loop produced."""

    final_output: str
    stop_reason: str
    tool_calls: int = 0
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CommitContext:
    """Output of Commit: the durable identifiers written this turn."""

    event_id: str
    narrative_ids: tuple[str, ...]
    persisted_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReflectContext:
    """Output of Reflect: what background work was scheduled."""

    scheduled: tuple[str, ...] = ()


__all__ = [
    "STAGES",
    "ActContext",
    "AssembleContext",
    "Budgets",
    "CommitContext",
    "ComposeContext",
    "IngressContext",
    "RecallContext",
    "ReflectContext",
    "Stage",
    "ToolSurfaceView",
]
