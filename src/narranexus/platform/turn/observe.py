"""
@file_name: observe.py
@author: Bin Liang
@date: 2026-09-04
@description: Frozen stage contexts (``contracts.agent.stages``) derived from the mutable ``RunContext`` at each boundary — what hooks and snapshots see.

Values only: ids, counts, hashes. Nothing here hands a hook the run
context, a module instance or a database handle.
"""
from __future__ import annotations

import hashlib
from typing import Any

from narranexus.contracts.agent.stages import (
    ActContext,
    AssembleContext,
    CommitContext,
    ComposeContext,
    IngressContext,
    RecallContext,
    ReflectContext,
    ToolSurfaceView,
)


def _ws(value: Any) -> str:
    return str(getattr(value, "value", value))


def ingress_view(ctx: Any) -> IngressContext:
    return IngressContext(
        agent_id=ctx.agent_id,
        user_id=ctx.user_id,
        input_content=ctx.input_content,
        working_source=_ws(ctx.working_source),
        trigger_extra_data=dict(ctx.trigger_extra_data or {}),
        job_instance_id=ctx.job_instance_id,
        forced_narrative_id=ctx.forced_narrative_id,
    )


def recall_view(ctx: Any) -> RecallContext:
    return RecallContext(
        narrative_ids=tuple(n.id for n in (ctx.narrative_list or [])),
        session_id=getattr(ctx.session, "id", None) if ctx.session is not None else None,
        markdown_history=ctx.markdown_history or "",
        no_durable_topic=bool(ctx.no_durable_topic),
    )


def compose_view(ctx: Any) -> ComposeContext:
    instances = list(ctx.active_instances or [])
    execution = getattr(ctx.execution_type, "value", None) or str(ctx.execution_type or "agent_loop")
    return ComposeContext(
        capability_names=tuple(sorted({str(i.module_class) for i in instances})),
        execution_type=str(execution),
        instance_ids=tuple(str(i.instance_id) for i in instances),
    )


def assemble_view(ctx: Any) -> AssembleContext:
    out = ctx.assembled
    if out is None:
        return AssembleContext(system_prompt_sha256="", system_prompt_chars=0, tools=ToolSurfaceView((), (), ()), instruction_sections=())
    system = next((m.get("content", "") for m in out.messages if isinstance(m, dict) and m.get("role") == "system"), "")
    return AssembleContext(
        system_prompt_sha256=hashlib.sha256(str(system).encode("utf-8")).hexdigest(),
        system_prompt_chars=len(str(system)),
        tools=ToolSurfaceView(
            mcp_servers=tuple(sorted(out.mcp_servers)),
            expressive_tools=tuple(out.expressive_tools),
            disallowed_tools=tuple(out.disallowed_tools),
        ),
        instruction_sections=tuple(sorted({str(i.module_class) for i in (ctx.active_instances or [])})),
    )


def act_view(ctx: Any) -> ActContext:
    result = ctx.execution_result
    if result is None:
        return ActContext(final_output="", stop_reason="none")
    steps = getattr(result, "execution_steps", None) or []
    return ActContext(
        final_output=str(getattr(result, "final_output", "") or ""),
        stop_reason="interrupted" if getattr(result, "interrupted", False) else "completed",
        tool_calls=len([s for s in steps if getattr(s, "tool_name", None) or (isinstance(s, dict) and s.get("tool_name"))]),
        usage=dict(getattr(result, "usage", None) or {}),
    )


def commit_view(ctx: Any) -> CommitContext:
    return CommitContext(
        event_id=str(ctx.event.id) if ctx.event is not None else "",
        narrative_ids=tuple(n.id for n in (ctx.narrative_list or [])),
        persisted_capabilities=tuple(sorted({str(m.config.name) for m in (ctx.module_list or []) if getattr(m, "config", None)})),
    )


def reflect_view(ctx: Any) -> ReflectContext:
    return ReflectContext(scheduled=("post_turn_hooks",))


__all__ = ["act_view", "assemble_view", "commit_view", "compose_view", "ingress_view", "recall_view", "reflect_view"]
