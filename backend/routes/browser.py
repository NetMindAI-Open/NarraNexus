"""
@file_name: browser.py
@author:
@date: 2026-09-22
@description: HTTP + WebSocket surface for the in-app browser.

Endpoints (mounted under /api/browser, plus one WS route):
  GET  /runtime                  — runtime status for the install card
  PUT  /runtime/source|mode      — local-only runtime preferences
  POST /runtime/install          — start (or attach to) the one-time install (local only)
  POST /runtime/install/cancel   — cooperative cancel, partial download kept (local only)
  GET  /notices/{agent_id}       — open login / verification requests
  GET|PUT /policy/{agent_id}     — the owner's arbitrary-script permissions
  WS   /ws/browser/{agent_id}    — authenticate, frames out, input in

The browser runs on the host that serves the API, so runtime installation and
selection are local-mode operations: in cloud mode the runtime would land in
one container while sessions launch in another, and any signed-in user could
trigger a machine-wide download.

Why the runtime endpoints are not per-agent: the Chromium runtime is one
per-machine install, shared by every agent. Scoping it to an agent would
prompt the user to install it again for the next one.

The WS route is the transport validated on 2026-09-21 (27–38 fps, 42 KB per
frame, ~1 ms). It carries frames the browser produced and input the user
typed, nothing else — the input side is whitelisted in ``cdp.input_events_for``
before it reaches the protocol.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, Optional
from urllib.parse import quote, urlparse

import anyio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from backend.auth import _is_cloud_mode, auth_middleware
from backend.routes._ownership import assert_owned
from narranexus.platform.browser.browser_service import BrowserService
from narranexus.platform.browser._browser_impl.install import manual_install_help
from narranexus.platform.browser._browser_impl.policy_store import PolicyValidationError
from narranexus.platform.browser._browser_impl.selection import save_mode, save_source, source_view
from narranexus.platform.browser._browser_impl.stream_auth import HEADER, stream_token
from narranexus.platform.browser.stream_bridge import MAX_INPUT_BYTES
from narranexus.platform.module_system import mcp_base_url

router = APIRouter()
ws_router = APIRouter()

#: Pending frames per panel. Small on purpose: a screencast frame supersedes
#: the one before it, so a deep queue would only buy latency.
FRAME_QUEUE_MAX = 4

#: One service per process: the runtime is a machine-wide install and the
#: install coordinator's "one download no matter how many askers" guarantee
#: only holds if everyone shares the same instance.
_service: Optional[BrowserService] = None


def _require_identity(request: Request) -> str:
    identity = getattr(request.state, "user_id", None)
    if not identity:
        raise HTTPException(status_code=401, detail="Authentication required")
    return identity


async def _require_owner(request: Request, agent_id: str) -> None:
    _require_identity(request)
    await assert_owned(request, agent_id)

#: Sessions live in the service, not here. A second registry would drift the
#: moment one side closed a browser the other still believed in.


def get_service() -> BrowserService:
    global _service
    if _service is None:
        _service = BrowserService()
    return _service


def register_session(session_id: str, session: Any) -> None:
    """Make a session reachable by the panel (tests and in-process callers)."""
    get_service().register_session(session_id, session)


def drop_session(session_id: str) -> None:
    get_service().forget_session(session_id)


# ── runtime ──────────────────────────────────────────────────────────────────


@router.get("/runtime")
async def runtime_status(request: Request) -> dict[str, Any]:
    """Status plus live install progress, for the install card."""
    _require_identity(request)
    svc = get_service()
    status = svc.status().to_dict()
    progress = svc.install_progress
    status["progress"] = (
        {
            "phase": progress.phase,
            "bytes_done": progress.bytes_done,
            "bytes_total": progress.bytes_total,
            "percent": progress.percent,
        }
        if progress is not None
        else None
    )
    status["manual_install"] = manual_install_help()
    try:
        status["selection"] = source_view()
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Could not read browser runtime preferences") from exc
    return status


class BrowserSourceDecision(BaseModel):
    """Choose an installed vendor runtime, never an arbitrary executable."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["managed", "system"]


@router.put("/runtime/source")
async def select_runtime(body: BrowserSourceDecision, request: Request) -> dict:
    _require_identity(request)
    if _is_cloud_mode():
        raise HTTPException(403, "Browser source selection is available only in local mode")
    if get_service().installing:
        raise HTTPException(409, "Wait for the browser installation to finish")
    try:
        await asyncio.to_thread(save_source, body.source)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Could not save browser source preference") from exc
    return await runtime_status(request)


class BrowserModeDecision(BaseModel):
    """Choose whether new local browser sessions have a native window."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["headless", "headed"]


@router.put("/runtime/mode")
async def select_runtime_mode(body: BrowserModeDecision, request: Request) -> dict:
    _require_identity(request)
    if _is_cloud_mode():
        raise HTTPException(403, "Browser mode selection is available only in local mode")
    try:
        await asyncio.to_thread(save_mode, body.mode)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Could not save browser mode preference") from exc
    return await runtime_status(request)


@router.post("/runtime/install")
async def install_runtime(request: Request) -> dict[str, Any]:
    """Install the runtime, or attach to an install already running.

    Never returns an error status for an install failure: the card needs to
    render the reason, and an HTTP 500 would give it nothing to show.
    """
    _require_identity(request)
    if _is_cloud_mode():
        raise HTTPException(403, "Browser installation is available only in local mode")
    outcome = await get_service().install()
    status = await runtime_status(request)
    return {
        "ok": outcome.ok,
        "error": outcome.error,
        "status": status,
        "manual_install": status["manual_install"],
    }


@router.post("/runtime/install/cancel")
async def cancel_install(request: Request) -> dict[str, Any]:
    _require_identity(request)
    if _is_cloud_mode():
        raise HTTPException(403, "Browser installation is available only in local mode")
    get_service().cancel_install()
    return {"ok": True}


# ── login notices ────────────────────────────────────────────────────────────


@router.get("/notices/{agent_id}")
async def list_notices(agent_id: str, request: Request) -> dict[str, Any]:
    """Owned login / verification requests, visible across processes.

    Read through the database: the request is raised in the MCP host process,
    so an in-process registry here would always be empty.
    """
    await _require_owner(request, agent_id)
    try:
        return {"pending": await get_service().pending_notices(agent_id)}
    except Exception as exc:
        logger.exception("could not read browser notices")
        raise HTTPException(status_code=503, detail="Could not read browser notices; retry") from exc


class PolicyTarget(BaseModel):
    """Only implemented permissions are editable through browser settings."""

    model_config = ConfigDict(extra="forbid")
    origin: str = Field(min_length=1, max_length=2048)
    capability: Literal["full_cdp_access"]


class PolicyRule(PolicyTarget):
    verdict: Literal["allow", "deny"]


@router.get("/policy/{agent_id}")
async def read_policy(agent_id: str, request: Request) -> dict:
    await _require_owner(request, agent_id)
    try:
        return await get_service().policy_view(agent_id)
    except Exception as exc:
        logger.exception("could not read browser policy")
        raise HTTPException(503, "Could not read browser permissions; retry") from exc


async def _write_policy(agent_id: str, origin: str, capability: str, verdict: str) -> dict:
    try:
        return await get_service().set_policy_rule(agent_id, origin=origin, capability=capability, verdict=verdict)
    except PolicyValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("could not update browser policy")
        raise HTTPException(503, "Could not save browser permissions; retry") from exc


@router.put("/policy/{agent_id}")
async def update_policy(agent_id: str, body: PolicyRule, request: Request) -> dict:
    await _require_owner(request, agent_id)
    return await _write_policy(agent_id, body.origin, body.capability, body.verdict)


@router.post("/policy/{agent_id}/revoke")
async def revoke_policy(agent_id: str, body: PolicyTarget, request: Request) -> dict:
    await _require_owner(request, agent_id)
    return await _write_policy(agent_id, body.origin, body.capability, "deny")


# ── the panel stream ─────────────────────────────────────────────────────────


def _stream_url(agent_id: str) -> str:
    base = urlparse(mcp_base_url())
    if base.scheme not in ("http", "https") or not base.netloc or base.query or base.fragment:
        raise ValueError("Invalid MCP base URL")
    scheme = "wss" if base.scheme == "https" else "ws"
    return f"{scheme}://{base.netloc}{base.path.rstrip('/')}/browser/{quote(agent_id, safe='')}/stream"


def _allowed_origin(websocket: WebSocket) -> bool:
    raw = websocket.headers.get("origin")
    if not raw:
        return True  # Non-browser clients still authenticate below.
    from backend.config import settings

    if raw in settings.cors_origins:
        return True
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.netloc == websocket.headers.get("host"):
        return True
    return not _is_cloud_mode() and raw in ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")


async def _authenticate_socket(websocket: WebSocket, agent_id: str) -> None:
    """Run the existing HTTP identity policy on the WS auth envelope."""
    raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
    if len(raw.encode()) > MAX_INPUT_BYTES:
        raise HTTPException(status_code=401, detail="Authentication message too large")
    message = json.loads(raw)
    if not isinstance(message, dict) or message.get("type") != "auth":
        raise HTTPException(status_code=401, detail="Send an auth message first")
    user_id = message.get("user_id")
    token = message.get("token")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=401, detail="Missing user identity")
    if not _is_cloud_mode() and websocket.query_params.get("x_user_id") != user_id:
        raise HTTPException(status_code=401, detail="User identity does not match connection")
    if token is not None and not isinstance(token, str):
        raise HTTPException(status_code=401, detail="Invalid credential")
    headers = [(k, v) for k, v in websocket.scope.get("headers", [])
               if k.lower() not in (b"authorization", b"x-user-id")]
    headers.append((b"x-user-id", user_id.encode("utf-8")))
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode("utf-8")))
    scope = {**websocket.scope, "type": "http", "method": "GET", "path": "/api/browser/runtime",
             "scheme": "https" if websocket.url.scheme == "wss" else "http",
             "headers": headers, "state": {}}
    request = Request(scope)

    async def authenticated(_request: Request) -> Response:
        return Response(status_code=204)

    result = await auth_middleware(request, authenticated)
    if result.status_code != 204 or getattr(request.state, "user_id", None) != user_id:
        raise HTTPException(status_code=401, detail="Authentication failed")
    await _require_owner(request, agent_id)
    websocket.state.user_id = user_id


@ws_router.websocket("/ws/browser/{agent_id}")
async def browser_stream(websocket: WebSocket, agent_id: str) -> None:
    """Relay the panel's socket to the process that owns the browser.

    A pass-through rather than a reimplementation: the stream's behaviour
    (frames push without client traffic, oldest frame dropped under pressure,
    control handed back on disconnect) is defined once, in the process that
    holds the session. Two copies of those rules would drift, and the drift
    would show up as a panel that works in one deployment and freezes in
    another.

    The panel connects HERE, not to the MCP host directly, so it stays behind
    the backend's identity checks — the MCP port is not a public surface.
    """
    if not _allowed_origin(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        await _authenticate_socket(websocket, agent_id)
    except WebSocketDisconnect:
        return
    except (HTTPException, ValueError, asyncio.TimeoutError) as exc:
        await websocket.send_json({"type": "error", "code": "auth_failed",
                                   "error": getattr(exc, "detail", "Authentication failed"), "retryable": False})
        await websocket.close(code=1008)
        return
    except Exception:
        logger.exception("browser stream authorization unavailable")
        await websocket.send_json({"type": "error", "code": "auth_unavailable",
                                   "error": "Authorization unavailable", "retryable": True})
        await websocket.close(code=1011)
        return

    try:
        import websockets
    except Exception:
        await websocket.send_json({"type": "error", "code": "stream_unavailable",
                                   "error": "Websocket client unavailable", "retryable": True})
        await websocket.close()
        return

    try:
        upstream = await websockets.connect(
            _stream_url(agent_id), max_size=16 * 1024 * 1024, max_queue=4,
            additional_headers={HEADER: stream_token(agent_id)},
            open_timeout=10, close_timeout=5, proxy=None,
        )
    except Exception:
        # The module host may be down, or the agent may have no browser open.
        # Either way the panel needs a reason, not a silent empty canvas.
        logger.exception("browser stream host unavailable")
        await websocket.send_json({"type": "error", "code": "stream_unavailable",
                                   "error": "Browser stream unavailable", "retryable": True})
        await websocket.close()
        return

    async def to_panel() -> None:
        async for message in upstream:
            await websocket.send_text(message)

    async def to_browser() -> None:
        while True:
            message = await websocket.receive_text()
            if len(message.encode()) > MAX_INPUT_BYTES:
                await websocket.close(code=1009)
                return
            await upstream.send(message)

    tasks = [asyncio.create_task(to_panel()), asyncio.create_task(to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("browser stream relay failed for {}", agent_id)
    finally:
        with anyio.CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await upstream.close()
            except Exception:
                logger.exception("could not close browser upstream")
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass
