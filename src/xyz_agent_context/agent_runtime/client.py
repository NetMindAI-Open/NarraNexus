"""
@file_name: client.py
@author:
@date: 2026-06-17
@description: AgentRuntimeClient — the single seam every trigger uses to
run an agent, instead of constructing AgentRuntime in-process.

Why this exists
---------------
Today every trigger (channels, jobs, message-bus, chat A2A) and the
backend WS route build ``AgentRuntime()`` and drive ``.run()`` directly.
That means the agent_loop — and the claude/codex subprocess it spawns —
runs inside *every* trigger container, so the 2026-06-17 security
exposure (env dump / cross-workspace read) has 8 attack surfaces, not
one.

This module introduces one interface, ``AgentRuntimeClient``, with two
methods that cover the two consumer shapes already present in the code:

* ``run_and_collect`` — drive a run to completion and return a
  ``RunCollection`` (the ``run_collector.collect_run`` consumers:
  lark / slack / telegram / job / message-bus / chat-A2A-sync).
* ``run_stream`` — yield runtime events live (the streaming consumers:
  backend WS via BackgroundRun, chat-A2A-SSE).

Transports:

* ``InProcessAgentRuntimeClient`` — calls ``AgentRuntime`` in the same
  process. Behaviour-identical to today. Used by local / desktop
  (binding rule #7: bash run.sh and the DMG must not change), and by
  cloud until the dedicated agent-runtime service exists.
* (future) ``HttpAgentRuntimeClient`` — calls the extracted
  agent-runtime service over the network. Selecting it for cloud is the
  next step; this file is the seam so triggers never change again
  (binding rule #9: the transport underneath can be swapped without
  rewriting callers).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncGenerator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from xyz_agent_context.agent_runtime.run_collector import RunCollection


@runtime_checkable
class AgentRuntimeClient(Protocol):
    """The contract triggers depend on (never the concrete AgentRuntime)."""

    async def run_and_collect(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Any,
        **extra_kwargs: Any,
    ) -> "RunCollection":
        """Drive one run to completion, return its grouped output."""
        ...

    def run_stream(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Any = None,
        **extra_kwargs: Any,
    ) -> AsyncGenerator:
        """Yield runtime events live (caller iterates with ``async for``)."""
        ...


class InProcessAgentRuntimeClient:
    """In-process transport — constructs AgentRuntime and drives it here.

    Imports are kept lazy (inside the methods) so this module is safe to
    import at the top of any trigger without re-introducing the
    channel/__init__ ↔ AgentRuntime circular import the lazy-import
    pattern was added to avoid.
    """

    async def run_and_collect(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Any,
        **extra_kwargs: Any,
    ) -> "RunCollection":
        from xyz_agent_context.agent_runtime.admission import (
            get_admission_controller,
        )
        from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
        from xyz_agent_context.agent_runtime.run_collector import collect_run

        # Two-level concurrency gate (no-op locally; enforced in cloud).
        # Queues the START only, never interrupts (binding rule #14).
        async with get_admission_controller().slot(user_id):
            return await collect_run(
                AgentRuntime(),
                agent_id=agent_id,
                user_id=user_id,
                input_content=input_content,
                working_source=working_source,
                **extra_kwargs,
            )

    async def run_stream(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Any = None,
        **extra_kwargs: Any,
    ) -> AsyncGenerator:
        from xyz_agent_context.agent_runtime.admission import (
            get_admission_controller,
        )
        from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime

        # working_source is optional for the streaming consumers (chat
        # A2A SSE never set it); only forward it when provided so we
        # preserve AgentRuntime.run's own default.
        if working_source is not None:
            extra_kwargs["working_source"] = working_source
        # Admission gate held for the lifetime of the stream (rule #14:
        # delays start; the slot frees when the stream is exhausted).
        async with get_admission_controller().slot(user_id):
            async for event in AgentRuntime().run(
                agent_id=agent_id,
                user_id=user_id,
                input_content=input_content,
                **extra_kwargs,
            ):
                yield event


def get_agent_runtime_client() -> AgentRuntimeClient:
    """Return the client for the current deployment.

    Transport seam: cloud will select ``HttpAgentRuntimeClient`` once the
    extracted agent-runtime service exists. Until then every mode runs
    in-process — zero behaviour change vs. constructing AgentRuntime
    directly (binding rule #7). When the HTTP transport lands, only this
    function changes; no trigger does.
    """
    return InProcessAgentRuntimeClient()
