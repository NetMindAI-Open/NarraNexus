"""
@file_name: remote_driver.py
@author:
@date: 2026-06-17
@description: AgentLoopDriver that delegates the loop to the Executor service.

Conforms to the ``AgentLoopDriver`` Protocol (same ``agent_loop``
async-generator contract as the local claude/codex drivers), but instead
of spawning the CLI in-process it POSTs to the Executor service and
streams the raw event dicts back. This is the network transport behind
the existing step-3 seam — the mirror of ``HttpAgentRuntimeClient`` one
layer down.

Selected by ``get_agent_loop_driver`` when ``AGENT_EXECUTOR_URL`` is set
(cloud orchestrator). Unset → the local driver runs in-process, so
``bash run.sh`` and the desktop build are unchanged (binding rule #7).

The scoped provider configs travel in the request body (see
``executor_protocol.build_agent_loop_request``) because they normally
ride a ContextVar that does not survive the network hop.

This driver owns NO wire-format knowledge beyond calling
``build_agent_loop_request`` and POSTing what it returns — which is why the
resume-capability HMAC (2026-07-28) needed no change here: the token and its
``issued_at`` are minted inside that builder and ride the same body dict. Keep
it that way; do not hand-assemble the body in this module.

Stream reader (2026-07-09 fix): uses ``resp.content.iter_any()`` +
manual line-splitting rather than aiohttp's line iterator. The line
iterator has an unmovable 128 KiB per-line ceiling (aiohttp's
``StreamReader.readuntil`` raises ``LineTooLong`` once the buffer
crosses ``_high_water = limit * 2 = 131072`` without seeing a newline),
which is BELOW the size of a single NDJSON event carrying a base64
image (tool_result events run 150-400 KiB). The multimodal-large-file
incident (2026-07-08) traces to exactly that: any Read on an image
>~90 KiB crashed the transport, killing the executor connection, and
the fallback helper LLM covered it up with a fake reply. The fix lifts
the ceiling to ``_MAX_STREAM_BYTES`` (aligned with the SDK's own
``max_buffer_size`` in ``adapters.claude.sdk``).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from typing import Any, AsyncGenerator

from loguru import logger

from narranexus.platform.agent_runtime.executor_protocol import (
    build_agent_loop_request,
    build_steer_request,
)
from narranexus.platform.agent_framework.loop.cancellation_view import (
    CancellationView,
)
from narranexus.platform.agent_framework.loop.driver import framework_capabilities
from narranexus.platform.agent_framework.loop.executor_errors import (
    ExecutorUnreachableError,
)

#: How often the steer pump re-checks cancellation while waiting on the run's
#: queue — bounds how long after turn-end the pump lingers, without busy-spin.
_STEER_POLL_S = 0.2

#: Per-POST timeout for /steer. The shared session has total=None (the
#: /agent-loop stream runs for hours), so a /steer POST would otherwise inherit
#: NO timeout and could hang the pump forever on a stalled executor. A steer is
#: small and same-network, so a tight bound is safe; a timeout is treated as
#: transient (the pump keeps draining) rather than terminal.
_STEER_POST_TIMEOUT_S = 30.0

#: What this remote shell can carry ACROSS the executor hop. A framework's own
#: capabilities are its ``FrameworkMeta.capabilities`` (registry-derived, so a
#: third-party framework that drains a steering inlet gets live steering the
#: moment it registers); this set is the property of the TRANSPORT, not of any
#: framework: the executor exposes ``POST /steer`` + ``steer_consumed`` frames
#: and nothing else, so every other capability the wrapped driver ships is not
#: reachable through this shell and must not be claimed here.
_REMOTE_CARRIED_CAPABILITIES = frozenset({"steering"})


# Ceiling for a single NDJSON event line pulled from the executor. Chosen
# to match the SDK's ``max_buffer_size`` in ``adapters.claude.sdk`` (50 MiB)
# so that whatever the SDK is willing to hand us upstream, this transport
# can pass through. Experiment 3 of the 2026-07-08 incident
# analysis showed image event lines top out around 400 KiB even for
# 3.4 MB source images (CLI transparently downsamples), so this is a
# generous belt-and-suspenders bound, not a tight fit.
_MAX_STREAM_BYTES = 50 * 1024 * 1024


class RemoteAgentLoopDriver:
    """Runs the agent loop on the remote Executor service."""

    def __init__(self, framework: str, working_path: str, executor_url: str):
        self.framework = framework
        self.working_path = str(working_path)
        base = executor_url.rstrip("/")
        self._url = base + "/agent-loop"
        self._steer_url = base + "/steer"

    def capabilities(self) -> set[str]:
        """The wrapped framework's declared capabilities, narrowed to what this
        hop can carry — ``{"steering"}`` when the framework's
        ``FrameworkMeta.capabilities`` says it drains a steering inlet, else the
        base contract (empty).

        Framework-AWARE, not a blanket yes: ``RemoteAgentLoopDriver`` is the one
        remote shell for EVERY framework, so a steer POSTed to a framework whose
        in-container driver never drains one would queue with nothing to read it.
        The answer now comes from the framework REGISTRY rather than a frozenset
        of builtin names, so a third-party framework that implements steering
        gets it, and one that does not is refused — the silent-downgrade class
        this replaced.

        Read statically rather than probed over HTTP: the executor image and
        this code deploy in lockstep (submodule pin), so a running orchestrator's
        remote executor always has ``/steer``; a delivery that still fails (a
        version-skew window, the run already ended) degrades visibly — the
        injection is logged undelivered and, never acked, resurfaces as a fresh
        turn (iron rule #16). ``framework_capabilities`` is fail-closed on an
        unregistered name, which is the conservative direction here: no steering
        claimed means the injection resurfaces as a turn, rather than being
        swallowed by an executor that cannot drain it. The ``/capabilities``
        endpoint stays the explicit probe if a framework's answer ever needs
        runtime confirmation; per-turn probing is pure latency.
        """
        return set(framework_capabilities(self.framework) & _REMOTE_CARRIED_CAPABILITIES)

    async def _handle_frame(self, line: bytes, steer_channel: Any) -> dict[str, Any] | None:
        """One NDJSON frame from the executor → the event dict to yield, or
        ``None`` for a control frame handled here.

        Three frame types cross the wire (executor_service): ``{"event": …}``
        (yield it), ``{"error": …}`` (raise ``RuntimeError``, the same surface as
        the local driver's relayed error), and ``{"steer_consumed": [ids]}`` — the
        remote twin of the local runner's steer_consumed stdout line. The loop in
        the executor reported which steer_inbox rows it drained; forward them to
        the orchestrator's real ``SteerChannel`` so the producer advances its
        cursor on CONSUMPTION (not on push), then swallow the frame — it is a
        transient control signal, never turn output."""
        frame = json.loads(line)
        if "error" in frame:
            err = frame["error"]
            raise RuntimeError(f"{err.get('type', 'Error')}: {err.get('message', '')}")
        if "steer_consumed" in frame:
            if steer_channel is not None:
                await steer_channel.deliver_consumed(list(frame["steer_consumed"]))
            return None
        return frame["event"]

    async def _pump_steer(
        self, session: Any, run_id: str, steer_channel: Any, cancel_view: CancellationView
    ) -> None:
        """Drain the run's ``SteerChannel`` and POST each injection to ``/steer``
        until the turn ends (cancelled) or delivery breaks — the remote twin of
        the local stdin steer pump.

        A failed delivery is LOGGED, never raised: the injection was not consumed
        (no ``steer_consumed`` comes back for it), so the producer never acks it
        and it resurfaces as a fresh turn — never lost, and never taking the turn
        down (iron rule #16). EVERY per-POST failure is treated as transient — a
        non-200 (e.g. a per-run 404, the run just ended), a per-POST timeout, or a
        connection-level error — and the pump keeps draining rather than stopping:
        unlike the local pump's ``BrokenPipeError`` (which means the runner PROCESS
        is gone, so the turn is over), a ``/steer`` POST rides its OWN short-lived
        connection, separate from the long-running ``/agent-loop`` stream, so a
        connector blip / transient refuse there does NOT imply the run ended. The
        pump therefore stops only on cancellation (turn end, via the caller's
        ``finally``); when the executor really is gone the ``/agent-loop`` stream
        ends and cancels this pump anyway."""
        import aiohttp

        while not cancel_view.requested():
            try:
                msg = await asyncio.wait_for(steer_channel.queue.get(), timeout=_STEER_POLL_S)
            except asyncio.TimeoutError:
                continue
            try:
                async with session.post(
                    self._steer_url,
                    json=build_steer_request(run_id=run_id, steer_msg=msg),
                    timeout=aiohttp.ClientTimeout(total=_STEER_POST_TIMEOUT_S),
                ) as r:
                    if r.status != 200:
                        logger.warning(
                            f"[RemoteAgentLoop] /steer {r.status} run={run_id} — "
                            f"injection not delivered (resurfaces as a fresh turn)"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Transient (a per-POST timeout or a connection blip on the
                # separate /steer connection). Keep pumping — this injection
                # resurfaces; later ones may still land. The pump ends only when
                # the turn does (cancellation), never on a single failed POST.
                logger.warning(
                    f"[RemoteAgentLoop] /steer POST failed run={run_id}: "
                    f"{type(e).__name__}: {e} — injection not delivered (resurfaces); "
                    f"pump continues"
                )
                continue

    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],  # {name: {"url": str, "headers": {str: str}?}}
        *,
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        import aiohttp

        _cancel_view = CancellationView(cancellation)
        # A live SteerChannel is carried over the HTTP hop: this run gets an
        # unguessable id in the request body, a pump POSTs each injection to the
        # executor's /steer under that id, and the executor's steer_consumed
        # frames are forwarded back to this channel below. The id is minted with
        # `secrets` (not a guessable counter) because /steer is unauthenticated
        # like /agent-loop — an unguessable handle is what stops a direct caller
        # from injecting into a run they never started. None channel → no id →
        # a non-steerable request, byte-for-byte the old body.
        steer_channel = kwargs.get("steering")
        # Steer this run only if BOTH a channel was handed in AND this framework's
        # in-container driver can actually drain it (nexus_power). If the
        # orchestrator handed a channel for a non-steer-capable framework
        # (claude_code / codex_cli), do NOT mint a run_id or pump — the executor
        # would queue steers nothing reads. Left un-pumped, those injections are
        # never consumed → never acked → resurface as a fresh turn (never lost).
        steerable = steer_channel is not None and "steering" in self.capabilities()
        if steer_channel is not None and not steerable:
            # A channel was handed in but THIS framework's in-container driver
            # cannot drain it (claude_code / codex_cli). Make the degradation
            # VISIBLE — the un-pumped injections are never consumed → never acked
            # → resurface as a fresh turn (never lost), but ops must be able to
            # see "cloud steering is a no-op for this framework" rather than only
            # notice a growing in-flight queue. Replaces the pre-framework-gate
            # blanket warning this driver used to log.
            logger.warning(
                f"[RemoteAgentLoop] steering supplied for framework "
                f"{self.framework!r}, whose executor driver does not drain it — "
                f"injections will not be delivered (resurface as fresh turns)"
            )
        run_id = secrets.token_hex(16) if steerable else None
        # turn_profile arrives as the in-process pydantic model; the wire
        # wants its dict form (JSON body).
        _profile = kwargs.get("turn_profile")
        if _profile is not None and hasattr(_profile, "model_dump"):
            _profile = _profile.model_dump()
        body = build_agent_loop_request(
            framework=self.framework,
            working_path=self.working_path,
            messages=messages,
            mcp_servers=mcp_servers,
            extra_env=extra_env,
            streaming=streaming,
            disallowed_tools=kwargs.get("disallowed_tools"),
            agent_id=str(kwargs.get("agent_id") or "agent"),
            expressive_tools=kwargs.get("expressive_tools"),
            deferred_tools=kwargs.get("deferred_tools"),
            turn_profile=_profile,
            extra_accessible_roots=kwargs.get("extra_accessible_roots"),
            origin_declaration=kwargs.get("origin_declaration") or "",
            run_id=run_id,
        )

        # No total timeout: agent loops can run for hours (binding rule
        # #14). sock_read is also unbounded — gaps between events during
        # long tool calls must not abort the stream.
        timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
        logger.info(
            f"[RemoteAgentLoop] → {self._url} framework={self.framework!r}"
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self._url, json=body) as resp:
                    resp.raise_for_status()
                    # A steerable run pumps injections to /steer for the life of
                    # the stream. Started only AFTER the POST is accepted (so a
                    # rejected turn never pumps), and cancelled in the finally
                    # BEFORE the session closes (so no put outlives its session).
                    pump = (
                        asyncio.create_task(
                            self._pump_steer(session, run_id, steer_channel, _cancel_view)
                        )
                        if steer_channel is not None and run_id is not None
                        else None
                    )
                    try:
                        # Manual line accumulation on ``iter_any()``: aiohttp's line
                        # iterator (``async for raw_line in resp.content``) hits
                        # ``LineTooLong`` at 131 KiB, which is BELOW a single
                        # base64-image event line (150-400 KiB). ``iter_any`` yields
                        # whatever bytes have arrived without any parsing, so we own
                        # the line boundary and can raise up to ``_MAX_STREAM_BYTES``.
                        buf = bytearray()
                        async for chunk in resp.content.iter_any():
                            # Cooperative cancellation: if the orchestrator's token
                            # fired, stop pulling — exiting the `async with` aborts
                            # the request, which the executor observes as disconnect.
                            if _cancel_view.requested():
                                logger.info("[RemoteAgentLoop] cancelled — aborting stream")
                                return
                            if not chunk:
                                continue
                            buf.extend(chunk)
                            while True:
                                nl = buf.find(b"\n")
                                if nl < 0:
                                    break
                                line = bytes(buf[:nl]).strip()
                                del buf[: nl + 1]
                                if not line:
                                    continue
                                event = await self._handle_frame(line, steer_channel)
                                if event is not None:
                                    yield event
                            if len(buf) > _MAX_STREAM_BYTES:
                                # Preserve the aiohttp-style failure mode (raise
                                # rather than silently truncate) but at a ceiling
                                # aligned with the SDK, so a genuinely malformed
                                # stream still fails fast.
                                raise RuntimeError(
                                    f"[RemoteAgentLoop] event line exceeded "
                                    f"{_MAX_STREAM_BYTES} bytes without a newline "
                                    f"(buf={len(buf)})"
                                )
                        # Trailing bytes without a final newline: the executor
                        # should terminate its NDJSON stream cleanly, but tolerate
                        # a missing trailing "\n" rather than losing the last event.
                        tail = bytes(buf).strip()
                        if tail:
                            event = await self._handle_frame(tail, steer_channel)
                            if event is not None:
                                yield event
                    finally:
                        if pump is not None:
                            pump.cancel()
                            # Settle the pump here so its cancellation never
                            # surfaces as a "Task was destroyed but it is pending"
                            # warning — and so a pump that died on an UNEXPECTED
                            # error (not the ClientError/TimeoutError it handles)
                            # is logged, never re-raised into the turn: a steer
                            # transport bug must not take the whole turn down.
                            try:
                                await pump
                            except asyncio.CancelledError:
                                pass  # expected — cancelled at turn end
                            except Exception as e:  # noqa: BLE001 — pump is best-effort
                                logger.warning(f"[RemoteAgentLoop] steer pump died: {e!r}")
        except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError) as e:
            # The executor connection failed — either it could not be
            # established (container down / not yet up → ClientConnectorError,
            # ClientOSError) OR it dropped mid-run (container killed / network
            # reset → ServerDisconnectedError, ClientPayloadError). Both mean
            # the executor is unreachable; convert to the typed exception so
            # step_3 surfaces an actionable ``infra_transient`` error and skips
            # the fabricating fallback (issue ②) — even mid-run, which would
            # otherwise be masked by a helper-LLM reply — and so it is never
            # mistaken for a retry-forever transient (its class name is not in
            # the circuit breaker's set).
            #
            # Deliberately NOT caught here (so they flow as before): the
            # RuntimeError from ``_handle_frame`` on an ``{"error":...}`` frame
            # (a USER LLM error the executor relayed) and the ``_MAX_STREAM_BYTES``
            # RuntimeError — both are RuntimeError, not aiohttp ClientError, so
            # this except never touches them. ``ClientResponseError`` from
            # ``raise_for_status`` (executor reachable but returned 5xx) is also
            # not a ClientConnectionError, so it too flows through unchanged.
            raise ExecutorUnreachableError(
                f"executor unreachable at {self._url}: "
                f"{type(e).__name__}: {e}",
                target=self._url,
            ) from e
