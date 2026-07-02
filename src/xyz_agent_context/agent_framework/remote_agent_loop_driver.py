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
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from loguru import logger

from xyz_agent_context.agent_runtime.executor_protocol import (
    build_agent_loop_request,
)


class RemoteAgentLoopDriver:
    """Runs the agent loop on the remote Executor service."""

    def __init__(self, framework: str, working_path: str, executor_url: str):
        self.framework = framework
        self.working_path = str(working_path)
        self._url = executor_url.rstrip("/") + "/agent-loop"

    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_server_urls: dict[str, str],
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
            mcp_server_urls=mcp_server_urls,
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
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._url, json=body) as resp:
                resp.raise_for_status()
                async for raw_line in resp.content:
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
                    line = raw_line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    if "error" in msg:
                        err = msg["error"]
                        # Match local-driver behaviour: the loop raised, so
                        # re-raise here for step_3's except path to capture.
                        raise RuntimeError(
                            f"{err.get('type', 'Error')}: {err.get('message', '')}"
                        )
                    yield msg["event"]
