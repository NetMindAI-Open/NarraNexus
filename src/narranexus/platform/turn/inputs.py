"""
@file_name: inputs.py
@author: Bin Liang
@date: 2026-09-04
@description: ``TurnServices`` + ``StageInputs`` — the platform's private inputs to a stage strategy (alpha; not a public contract).

A strategy receives the mutable ``RunContext`` (the legacy carrier every
step already knows) and the services the runtime built for this turn. The
frozen ``contracts.agent.stages`` values are derived from the same context
at each boundary (``observe.py``) for hooks and snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from narranexus.contracts.agent.pipeline import PipelineProfile


@dataclass
class TurnServices:
    db_client: Any
    event_service: Any
    session_service: Any
    narrative_service: Any
    markdown_manager: Any
    trajectory_recorder: Any
    hook_manager: Any
    response_processor: Any
    execute_callback_instance: Callable[..., Awaitable[Any]]
    # wall-clock marks the Commit stage prints in the [turn-timing] line
    timings: dict[str, float] = field(default_factory=dict)
    # set by Ingress when owner LLM config resolution fails: the pipeline stops after Ingress
    aborted: bool = False
    # Called by the pipeline exactly once, the moment ``ctx.event`` exists (after
    # the stage that created it — Ingress by default). The runtime uses it to
    # bind the trace + cost scopes so every helper LLM call from Recall onward
    # is booked to THIS turn, never to the ambient (parent / previous) event.
    bind_event: Optional[Callable[[str], None]] = None


@dataclass
class StageInputs:
    ctx: Any  # RunContext
    services: TurnServices
    profile: PipelineProfile
    silent: bool = False


__all__ = ["StageInputs", "TurnServices"]
