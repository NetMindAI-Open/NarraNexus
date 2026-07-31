"""
@file_name: client.py
@author:
@date: 2026-06-17
@description: AgentRuntimeClient — the single seam every trigger uses to
run an agent, instead of constructing AgentRuntime in-process.

Why this exists
---------------
Every trigger (channels, jobs, message-bus, chat A2A) runs agents
through this one interface, ``AgentRuntimeClient``, with two methods
that cover the two consumer shapes present in the code:

* ``run_and_collect`` — drive a run to completion and return a
  ``RunCollection`` (the ``run_collector.collect_run`` consumers:
  lark / slack / telegram / job / message-bus / chat-A2A-sync).
* ``run_stream`` — yield runtime events live (the streaming consumers:
  chat-A2A-SSE, matrix). The backend WS path uses BackgroundRun, not
  this client.

Both methods forward ``**extra_kwargs`` verbatim to ``AgentRuntime.run``,
so newer optional parameters (e.g. ``silent=True`` for skip-agent-loop
memory-only writes; ``trigger_extra_data`` for per-turn context) reach
the runtime without a signature bump here. Callers pass them as regular
keyword arguments to ``run_and_collect`` / ``run_stream``.

Run observability (2026-07-31)
------------------------------
This seam is also where every trigger run becomes OBSERVABLE: a
``RunRecorder`` taps the event stream (decorator around the runtime —
``_RecordedRuntime`` — so ``collect_run`` stays untouched) and persists
the same live trace the WS path produces via BackgroundRun:
``event_stream`` rows + the ``events`` row state machine + heartbeat.
Any read-side surface (chat reconnect endpoint's tail-follow, the team
roster, a future dashboard) can then observe a trigger run exactly like
a chat run. The recorder is an observer — its failures never break the
run — and can be disabled with ``NARRANEXUS_RUN_RECORDING_DISABLED``.

Transports:

* ``InProcessAgentRuntimeClient`` — calls ``AgentRuntime`` in the same
  process. Used by local / desktop (binding rule #7: bash run.sh and
  the DMG must not change), and by cloud until the dedicated
  agent-runtime service exists.
* (future) ``HttpAgentRuntimeClient`` — calls the extracted
  agent-runtime service over the network. The recorder moves with the
  transport (server-side), so triggers never change again (binding
  rule #9: the transport underneath can be swapped without rewriting
  callers).
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional, Protocol, runtime_checkable

from loguru import logger

if TYPE_CHECKING:
    from xyz_agent_context.agent_runtime.run_collector import RunCollection
    from xyz_agent_context.agent_runtime.run_recorder import RunRecorder


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


class _RecordedRuntime:
    """Decorator over a runtime: forwards ``.run()`` and feeds every
    event to the recorder on the side. ``collect_run`` (and any other
    consumer of the runtime protocol) needs no changes — it keeps
    receiving the original typed messages.

    The tap is guarded: a recorder bug must never break the run it
    observes (binding rule #14 — the platform is not the interruption
    source). Individual DB write failures are already swallowed inside
    RunRecorder; this guard covers programming errors.
    """

    def __init__(self, runtime: Any, recorder: "RunRecorder") -> None:
        self._runtime = runtime
        self._recorder = recorder

    async def run(self, **kwargs: Any) -> AsyncGenerator:
        from xyz_agent_context.agent_runtime.run_recorder import normalise_event

        async for event in self._runtime.run(**kwargs):
            try:
                await self._recorder.record(normalise_event(event))
            except Exception:  # noqa: BLE001 — observer never breaks observed
                logger.opt(exception=True).warning(
                    "[RunRecorder] record failed; run continues unrecorded frame"
                )
            yield event


async def _new_recorder() -> "Optional[RunRecorder]":
    """Build a recorder for one trigger run, or None when recording is
    off (kill switch) or the DB client cannot be obtained — a run must
    start regardless of observability."""
    from xyz_agent_context.agent_runtime.run_recorder import (
        RunRecorder,
        recording_enabled,
    )

    if not recording_enabled():
        return None
    try:
        from xyz_agent_context.utils.db.db_factory import get_db_client
        return RunRecorder(db=await get_db_client())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RunRecorder] unavailable for this run: {e}")
        return None


def _spawn_finalize(recorder: "RunRecorder", state: str, **kwargs: Any) -> None:
    """Finalize on a task of its own — for contexts that cannot await
    (GeneratorExit unwinding, host-task cancellation). Paired with a
    done-callback so a failure is logged, never silently GC'd
    (incident lesson #2)."""
    try:
        task = asyncio.get_running_loop().create_task(
            recorder.finalize(state, **kwargs)
        )
    except RuntimeError:  # loop already closing — nothing we can do
        return

    def _log_failure(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            logger.warning(
                f"[RunRecorder {recorder.run_id}] deferred finalize failed: "
                f"{t.exception()}"
            )

    task.add_done_callback(_log_failure)


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
        from xyz_agent_context.agent_runtime.cancellation import CancelledByUser
        from xyz_agent_context.agent_runtime.run_collector import collect_run
        from xyz_agent_context.agent_runtime.run_recorder import (
            STATE_CANCELLED,
            STATE_COMPLETED,
            STATE_FAILED,
            TERMINAL_STATES,
        )

        recorder = await _new_recorder()
        runtime: Any = AgentRuntime()
        if recorder is not None:
            runtime = _RecordedRuntime(runtime, recorder)

        try:
            # Two-level concurrency gate: queues the START only, never
            # interrupts a running loop (binding rule #14).
            async with get_admission_controller().slot(user_id):
                result = await collect_run(
                    runtime,
                    agent_id=agent_id,
                    user_id=user_id,
                    input_content=input_content,
                    working_source=working_source,
                    **extra_kwargs,
                )
            if recorder is not None:
                await recorder.finalize(STATE_COMPLETED)
            return result
        except CancelledByUser as e:
            if recorder is not None:
                with suppress(Exception):
                    await recorder.finalize(STATE_CANCELLED, cancel_reason=e.reason)
            raise
        except Exception as e:
            if recorder is not None:
                with suppress(Exception):
                    await recorder.finalize(
                        STATE_FAILED,
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
            raise
        finally:
            # Host task cancelled mid-run (deploy restart, trigger
            # shutdown): no except-branch ran and awaiting here would be
            # re-cancelled. Settle the row on a task of its own instead
            # of leaving it 'running' until the stale sweep finds it.
            if recorder is not None and recorder.state not in TERMINAL_STATES:
                _spawn_finalize(
                    recorder, STATE_FAILED,
                    error_message="run interrupted (host task cancelled)",
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
        from xyz_agent_context.agent_runtime.cancellation import CancelledByUser
        from xyz_agent_context.agent_runtime.run_recorder import (
            STATE_CANCELLED,
            STATE_COMPLETED,
            STATE_FAILED,
            TERMINAL_STATES,
            normalise_event,
        )

        # working_source is optional for the streaming consumers (chat
        # A2A SSE never set it); only forward it when provided so we
        # preserve AgentRuntime.run's own default.
        if working_source is not None:
            extra_kwargs["working_source"] = working_source

        recorder = await _new_recorder()
        # Set when a terminal handler already scheduled a DEFERRED finalize
        # (a task, not awaited) — the recorder's state is still non-terminal
        # at that instant, so the finally-net below must not double-spawn a
        # second, competing terminal state.
        finalize_deferred = False
        try:
            # Admission gate held for the lifetime of the stream (rule #14:
            # delays start; the slot frees when the stream is exhausted).
            async with get_admission_controller().slot(user_id):
                async for event in AgentRuntime().run(
                    agent_id=agent_id,
                    user_id=user_id,
                    input_content=input_content,
                    **extra_kwargs,
                ):
                    if recorder is not None:
                        try:
                            await recorder.record(normalise_event(event))
                        except Exception:  # noqa: BLE001 — observer never breaks observed
                            logger.opt(exception=True).warning(
                                "[RunRecorder] record failed; run continues "
                                "unrecorded frame"
                            )
                    yield event
            if recorder is not None:
                await recorder.finalize(STATE_COMPLETED)
        except CancelledByUser as e:
            if recorder is not None:
                with suppress(Exception):
                    await recorder.finalize(STATE_CANCELLED, cancel_reason=e.reason)
            raise
        except GeneratorExit:
            # Consumer closed the stream mid-run; the underlying run dies
            # with the generator. Awaiting inside GeneratorExit unwinding
            # is forbidden, so finalize on a task of its own.
            if recorder is not None:
                _spawn_finalize(
                    recorder, STATE_CANCELLED,
                    cancel_reason="stream consumer closed",
                )
                finalize_deferred = True
            raise
        except Exception as e:
            if recorder is not None:
                with suppress(Exception):
                    await recorder.finalize(
                        STATE_FAILED,
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
            raise
        finally:
            # Host task cancelled at a suspension point (deploy restart,
            # trigger shutdown): CancelledError is a BaseException, so no
            # except-branch above ran. Settle the row on a task of its own
            # — same net run_and_collect carries.
            if (
                recorder is not None
                and not finalize_deferred
                and recorder.state not in TERMINAL_STATES
            ):
                _spawn_finalize(
                    recorder, STATE_FAILED,
                    error_message="run interrupted (host task cancelled)",
                )


def get_agent_runtime_client() -> AgentRuntimeClient:
    """Return the client for the current deployment.

    Transport seam: cloud will select ``HttpAgentRuntimeClient`` once the
    extracted agent-runtime service exists. Until then every mode runs
    in-process — zero behaviour change vs. constructing AgentRuntime
    directly (binding rule #7). When the HTTP transport lands, only this
    function changes; no trigger does.
    """
    return InProcessAgentRuntimeClient()
