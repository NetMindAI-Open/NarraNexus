"""
@file_name: remote_agent_loop_driver.py
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
``max_buffer_size`` in ``xyz_claude_agent_sdk``).
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from loguru import logger

from xyz_agent_context.agent_runtime.executor_protocol import (
    build_agent_loop_request,
)
from xyz_agent_context.agent_framework.executor_errors import (
    ExecutorUnreachableError,
)


# Ceiling for a single NDJSON event line pulled from the executor. Chosen
# to match the SDK's ``max_buffer_size`` in ``xyz_claude_agent_sdk`` (50 MiB)
# so that whatever the SDK is willing to hand us upstream, this transport
# can pass through. Experiment 3 of the 2026-07-08 incident
# analysis showed image event lines top out around 400 KiB even for
# 3.4 MB source images (CLI transparently downsamples), so this is a
# generous belt-and-suspenders bound, not a tight fit.
_MAX_STREAM_BYTES = 50 * 1024 * 1024


def _decode_event(raw: bytes) -> dict[str, Any]:
    """Parse one NDJSON line from the executor stream.

    On an ``{"error": ...}`` frame, raises ``RuntimeError`` for
    step_3's except path to capture (same behaviour as the local
    driver's exceptions surface). On an ``{"event": ...}`` frame,
    returns the event dict itself so the caller can yield it.
    """
    msg = json.loads(raw)
    if "error" in msg:
        err = msg["error"]
        raise RuntimeError(
            f"{err.get('type', 'Error')}: {err.get('message', '')}"
        )
    return msg["event"]


class RemoteAgentLoopDriver:
    """Runs the agent loop on the remote Executor service."""

    def __init__(self, framework: str, working_path: str, executor_url: str):
        self.framework = framework
        self.working_path = str(working_path)
        self._url = executor_url.rstrip("/") + "/agent-loop"

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

        body = build_agent_loop_request(
            framework=self.framework,
            working_path=self.working_path,
            messages=messages,
            mcp_servers=mcp_servers,
            extra_env=extra_env,
            streaming=streaming,
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
                        # ``CancellationToken.is_cancelled`` is a bool @property,
                        # not a method — read it, do not call it.
                        if cancellation is not None and getattr(
                            cancellation, "is_cancelled", False
                        ):
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
                            yield _decode_event(line)
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
                        yield _decode_event(tail)
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
            # RuntimeError from ``_decode_event`` on an ``{"error":...}`` frame
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
