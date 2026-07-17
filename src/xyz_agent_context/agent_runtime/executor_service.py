"""
@file_name: executor_service.py
@author:
@date: 2026-06-17
@description: The agent-loop Executor service.

This is the ONLY tier that spawns the claude/codex CLI. It is a thin,
near-stateless FastAPI app: given an assembled prompt + the resolved
(scoped) provider configs + the workspace path, it runs the LOCAL
agent-loop driver and streams the raw event dicts back as NDJSON.

Security shape (why this exists):
  * It holds NO platform master secrets (no JWT/DB/admin keys). Its
    container is started WITHOUT the platform .env; the only credential
    it sees is the per-run scoped LLM key, which arrives in the request
    body and is applied to a ContextVar for the duration of the loop.
  * It needs NO database — the orchestrator did all DB work (steps
    0-2.5) and ships the assembled messages + configs.
  * Because the executor process does NOT set ``AGENT_EXECUTOR_URL``,
    ``get_agent_loop_driver`` here resolves to the LOCAL claude/codex
    driver (no self-recursion).

Per-agent / per-user workspace isolation is a deployment concern layered
on top (mount only that user's workspace into the container) — out of
scope for this module, which just runs the loop it is handed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger

# Importing the package registers the built-in agent-loop drivers
# (claude_code / codex_cli) into the registry.
import xyz_agent_context.agent_framework  # noqa: F401
from xyz_agent_context.agent_framework.agent_loop_driver import (
    get_agent_loop_driver,
)
from xyz_agent_context.agent_runtime.executor_protocol import (
    apply_provider_configs,
)

app = FastAPI(title="NarraNexus Agent-Loop Executor")


@app.get("/health")
def health() -> dict:
    return {"status": "healthy"}


@app.post("/watch/ensure")
async def watch_ensure(request: Request) -> JSONResponse:
    """Start (or reuse) an `officecli watch` server INSIDE this container and
    return the port ALLOCATED to the file.

    In cloud the workspace + the agent's officecli edits live in this executor
    container, so the watch MUST run here (the orchestrator can't spawn a
    resident-sharing process into it). The orchestrator's `/office-watch/open`
    calls this before minting the proxy URL, then proxies to the returned port.
    This container owns port allocation (one dedicated port per file), so the
    port is decided here — the orchestrator never guesses it. Reuses a running
    watch if this file already has one.

    Body: {agent_id, user_id, file}. No auth (internal-trust, same as
    /agent-loop); the port allowlist + workspace confinement in ensure_watch are
    the guard.
    """
    import asyncio

    from xyz_agent_context.utils.office_watch import ensure_watch

    body = await request.json()
    port = await asyncio.get_running_loop().run_in_executor(
        None,
        ensure_watch,
        body["agent_id"],
        body["user_id"],
        body["file"],
    )
    if port is None:
        return JSONResponse({"ok": False}, status_code=503)
    return JSONResponse({"ok": True, "port": port}, status_code=200)


@app.get("/watch/version")
async def watch_version(request: Request) -> JSONResponse:
    """Return the mtime+size of an office file INSIDE this container.

    The orchestrator's `/office-watch/version` (cloud branch) calls this so the
    frontend's mtime-poll fallback works when the workspace lives in the
    container. Query: agent_id, user_id, file. No auth (internal-trust).
    """
    from xyz_agent_context.utils.office_watch import resolve_watch_file
    from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace

    agent_id = request.query_params.get("agent_id", "")
    user_id = request.query_params.get("user_id", "")
    file = request.query_params.get("file", "")
    try:
        rel = resolve_watch_file(agent_id, user_id, file)
        abs_path = resolve_existing_workspace(agent_id, user_id) / rel
        st = os.stat(abs_path)
    except (ValueError, OSError) as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse({"mtime": st.st_mtime, "size": st.st_size}, status_code=200)


@app.get("/watch/{port}/{path:path}")
async def watch_passthrough(port: int, path: str, request: Request) -> Response:
    """Reverse-proxy to an `officecli watch` server running INSIDE this
    container (bound to 127.0.0.1:{port}).

    This is the only bridge from the orchestrator into the watch port: the
    broker exposes only this executor's API port, not arbitrary container
    ports. The orchestrator's own /api/office-watch-proxy route forwards to
    here; the browser never reaches this directly.

    No auth (internal-trust, same as /agent-loop — the container holds no
    secrets), but the port allowlist is still enforced as defense-in-depth so
    this can't be turned into an SSRF into other in-container ports.
    """
    import aiohttp

    from xyz_agent_context.utils.office_watch import is_watch_port

    if not is_watch_port(port):
        return JSONResponse({"error": f"port {port} not allowed"}, status_code=403)

    upstream = f"http://127.0.0.1:{port}/{path}"
    if request.url.query:
        upstream += f"?{request.url.query}"
    fwd = {k: v for k, v in request.headers.items() if k.lower() in ("accept", "cache-control", "last-event-id")}
    timeout = aiohttp.ClientTimeout(total=None, sock_read=None, sock_connect=10)
    session = aiohttp.ClientSession(timeout=timeout)
    try:
        resp = await session.get(upstream, headers=fwd)
    except aiohttp.ClientError as e:
        await session.close()
        logger.warning(f"[Executor] watch passthrough upstream failed ({upstream}): {e}")
        return JSONResponse({"error": "watch server unavailable"}, status_code=502)

    async def _body():
        try:
            async for chunk in resp.content.iter_any():
                yield chunk
        finally:
            resp.release()
            await session.close()

    media_type = resp.headers.get("Content-Type", "application/octet-stream")
    return StreamingResponse(
        _body(),
        status_code=resp.status,
        media_type=media_type,
        headers={"X-Accel-Buffering": "no"},
    )


@app.post("/agent-loop")
async def agent_loop(request: Request) -> StreamingResponse:
    """Run one agent turn and stream raw event dicts back as NDJSON.

    One JSON object per line:
      {"event": {...}}            a raw agent-loop event
      {"error": {"type","message"}}  the loop raised
    """
    body = await request.json()
    framework = body["framework"]
    working_path = body["working_path"]

    # Re-apply the orchestrator's resolved (scoped) provider configs onto
    # THIS task's ContextVars, so the CLI authenticates with the right key.
    apply_provider_configs(body.get("provider_configs") or {})

    # AGENT_EXECUTOR_URL is unset in the executor container → local driver.
    driver = get_agent_loop_driver(framework, working_path=working_path)

    async def _stream():
        logger.info(f"[Executor] agent-loop start framework={framework!r} workspace={working_path}")
        try:
            async for event in driver.agent_loop(
                messages=body["messages"],
                mcp_servers=body.get("mcp_servers") or {},
                streaming=bool(body.get("streaming", True)),
                extra_env=body.get("extra_env") or None,
                cancellation=None,  # cancellation = orchestrator aborts the stream
            ):
                yield json.dumps({"event": event}, default=str) + "\n"
        except Exception as e:  # noqa: BLE001 — surface to caller, never crash the service
            logger.exception(f"[Executor] agent-loop failed: {e}")
            yield json.dumps({"error": {"type": type(e).__name__, "message": str(e)}}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


def _resolve_executor_log_dir(base_working_path: str) -> Path:
    """Where the executor writes its logs: under the (single) mounted user
    workspace dir, so each user's executor logs land in THEIR directory and
    persist on the host volume — not in a shared sink.

    The broker mounts exactly one user subtree at
    ``{base}/{user_id}``, so ``base`` has a single non-hidden subdir = the
    user. Falls back to ``{base}/.executor_logs`` if it can't be uniquely
    determined (so logging never hard-fails).
    """
    base = Path(base_working_path)
    try:
        subdirs = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        subdirs = []
    user_dir = subdirs[0] if len(subdirs) == 1 else base
    return user_dir / ".executor_logs"


def main() -> None:
    import os

    import uvicorn

    base = os.environ.get("BASE_WORKING_PATH", "/opt/narranexus/workspaces")
    log_dir = _resolve_executor_log_dir(base)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_dir / "executor_{time:YYYY-MM-DD}.log"),
            rotation="50 MB",
            retention="14 days",
            enqueue=True,
        )
        logger.info(f"[Executor] file logging at {log_dir}")
    except OSError as e:  # noqa: BLE001 — file logging is best-effort
        logger.warning(f"[Executor] could not set up file logging at {log_dir}: {e}")

    port = int(os.environ.get("AGENT_EXECUTOR_PORT", "8020"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
