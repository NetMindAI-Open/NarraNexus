"""
@file_name: stream_bridge.py
@author:
@date: 2026-09-22
@description: Authenticated live transport between the module host and backend.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from typing import Any, Awaitable, Callable, Optional

import anyio
from loguru import logger
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from narranexus.platform.browser._browser_impl.stream_auth import HEADER, verify_stream_token

SessionLookup = Callable[[str], Optional[Any]]
SESSION_CHECK_INTERVAL = 0.1
MAX_INPUT_BYTES = 64 * 1024


def stream_routes(
    lookup: SessionLookup,
    *, on_login_control: Callable[[str, Any, str, str], Awaitable[None]] | None = None,
) -> list[WebSocketRoute]:
    """Keep subscribers connected across absent, closed and replaced sessions."""

    async def stream(websocket: WebSocket) -> None:
        agent_id = websocket.path_params["agent_id"]
        # Webpages cannot open this internal socket, even on localhost. This
        # boundary is enforced independently of the MCP HTTP middleware.
        if websocket.headers.get("origin") or not verify_stream_token(
            agent_id, websocket.headers.get(HEADER, "")
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        connection_id = uuid.uuid4().hex
        session = None
        selected_page_id: str | None = None
        chosen_page_id: str | None = None
        last_pages: dict | None = None
        view_lock = asyncio.Lock()
        view_changed = asyncio.Event()
        def unsubscribe() -> None:
            pass

        def unsubscribe_control() -> None:
            pass

        def unsubscribe_pages() -> None:
            pass
        messages: deque[dict] = deque()
        latest_frame: dict | None = None
        wake = asyncio.Event()
        overflow = asyncio.Event()

        def emit(message: dict) -> None:
            if len(messages) >= 128:
                overflow.set()
                return
            messages.append(message)
            wake.set()

        def frame_sink(frame: dict) -> None:
            nonlocal latest_frame
            if frame.get("page_id") != selected_page_id:
                return
            # Whole images supersede stale pending images; control messages
            # have a separate queue and can never be evicted by frames.
            latest_frame = {**frame, "type": "frame"}
            wake.set()

        def state(control: dict) -> dict:
            return {**control, "can_control": control["owner_connection_id"] == connection_id}

        def control_sink(control: dict) -> None:
            emit({"type": "control", "control": state(control)})

        async def login_control(current: Any, event: str) -> None:
            if on_login_control:
                try:
                    await on_login_control(agent_id, current, connection_id, event)
                except Exception:
                    logger.exception("could not record browser login handoff for {}", agent_id)
                    emit({"type": "error", "code": "login_update_failed",
                          "error": "Could not record login handoff; retry the login request", "retryable": True})

        async def detach() -> None:
            nonlocal latest_frame, selected_page_id, chosen_page_id, last_pages
            unsubscribe()
            unsubscribe_control()
            unsubscribe_pages()
            if session is not None:
                if await session.release_control(connection_id):
                    await login_control(session, "disconnect")
                if selected_page_id is not None:
                    await session.stop_stream(selected_page_id)
            latest_frame = None
            selected_page_id = chosen_page_id = None
            last_pages = None

        async def refresh_view(*, hello: bool = False, force: bool = False) -> None:
            nonlocal selected_page_id, chosen_page_id, last_pages, latest_frame, unsubscribe
            async with view_lock:
                if session is None or not session.is_open:
                    return
                if chosen_page_id not in session.pages.items:
                    chosen_page_id = None
                desired = chosen_page_id or session.pages.active_id
                snapshot = {**session.pages.snapshot(), "selected_page_id": desired,
                            "following_active": chosen_page_id is None}
                if hello:
                    emit({"type": "hello", "connection_id": connection_id,
                          "control": state(session.control.to_dict()), **snapshot})
                elif force or snapshot != last_pages:
                    emit({"type": "pages", **snapshot})
                last_pages = snapshot
                if desired == selected_page_id:
                    return
                old_page_id = selected_page_id
                unsubscribe()
                selected_page_id = desired
                latest_frame = None
                if old_page_id is not None:
                    await session.stop_stream(old_page_id)
                try:
                    unsubscribe = session.subscribe(frame_sink, page_id=desired)
                    await session.start_stream(page_id=desired)
                except Exception:
                    selected_page_id = None
                    logger.exception("could not start browser page stream for {}", agent_id)
                    emit({"type": "error", "code": "stream_unavailable",
                          "error": "Browser page stream unavailable", "retryable": True})

        async def follow_session() -> None:
            nonlocal session, unsubscribe_control, unsubscribe_pages
            first = True
            while True:
                found = lookup(agent_id)
                if found is not None and not found.is_open:
                    found = None
                if first or found is not session:
                    first = False
                    await detach()
                    session = found
                    if session is None:
                        emit({"type": "idle", "reason": "awaiting_session"})
                    else:
                        unsubscribe_control = session.control.subscribe(control_sink)
                        unsubscribe_pages = session.pages.subscribe(view_changed.set)
                        await refresh_view(hello=True)
                elif view_changed.is_set():
                    view_changed.clear()
                    await refresh_view()
                try:
                    await asyncio.wait_for(view_changed.wait(), SESSION_CHECK_INTERVAL)
                except TimeoutError:
                    pass

        async def pump_out() -> None:
            nonlocal latest_frame
            while True:
                await wake.wait()
                while messages:
                    await websocket.send_json(messages.popleft())
                if latest_frame is not None:
                    frame, latest_frame = latest_frame, None
                    await websocket.send_json(frame)
                if not messages and latest_frame is None:
                    wake.clear()

        async def pump_in() -> None:
            nonlocal chosen_page_id
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > MAX_INPUT_BYTES:
                    await websocket.close(code=1009)
                    return
                try:
                    message = json.loads(raw)
                except ValueError:
                    message = None
                if not isinstance(message, dict):
                    emit({"type": "error", "code": "invalid_message",
                          "error": "Expected a JSON object", "retryable": False})
                    continue
                kind = message.get("type")
                if kind == "ping":
                    emit({"type": "pong"})
                    continue
                current = session
                if current is None or not current.is_open:
                    if kind != "resize":
                        emit({"type": "idle", "reason": "awaiting_session"})
                    continue
                if kind == "resize":
                    try:
                        if message.get("page_id", selected_page_id) == selected_page_id:
                            await current.resize_viewport(message.get("width"), message.get("height"), connection_id,
                                                          page_id=selected_page_id)
                    except Exception:
                        logger.exception("could not resize browser for {}", agent_id)
                        emit({"type": "error", "code": "resize_failed",
                              "error": "Could not resize browser viewport", "retryable": True})
                elif kind == "input":
                    if message.get("page_id", selected_page_id) == selected_page_id:
                        await current.handle_user_input(message.get("event"), connection_id, page_id=selected_page_id)
                elif kind in {"new_page", "navigate"}:
                    try:
                        if kind == "new_page":
                            page_id = await current.new_user_page(connection_id)
                            if page_id is None:
                                raise ValueError("Take control before opening a browser page")
                        else:
                            page_id, url = message.get("page_id"), message.get("url")
                            if not isinstance(page_id, str) or page_id != selected_page_id:
                                raise ValueError("The selected browser page changed; retry on the current page.")
                            if not isinstance(url, str):
                                raise ValueError("Expected an HTTP or HTTPS address")
                            if not await current.navigate_user_page(page_id, url, connection_id):
                                raise ValueError("Take control before navigating a browser page")
                        chosen_page_id = None
                        await refresh_view(force=True)
                        emit({"type": "page_action", "action": kind, "page_id": page_id})
                    except Exception as exc:
                        emit({"type": "error", "code": "page_action_failed", "error": str(exc), "retryable": True})
                elif kind in {"select_page", "follow_active", "close_page"}:
                    try:
                        if kind == "follow_active":
                            chosen_page_id = None
                        else:
                            page_id = message.get("page_id")
                            if not isinstance(page_id, str):
                                raise ValueError("Expected a page identifier")
                            current.pages.get(page_id)
                            if kind == "close_page":
                                if not await current.close_user_page(page_id, connection_id):
                                    raise ValueError("Take control before closing a browser page")
                            elif await current.select_user_page(page_id, connection_id):
                                chosen_page_id = None
                            else:
                                chosen_page_id = page_id
                        await refresh_view(force=True)
                    except (ValueError, RuntimeError, ConnectionError) as exc:
                        emit({"type": "error", "code": "page_unavailable", "error": str(exc), "retryable": True})
                elif kind == "take_control":
                    if message.get("page_id", selected_page_id) != selected_page_id:
                        continue
                    if not await current.take_control(connection_id, page_id=selected_page_id):
                        emit({"type": "error", "code": "control_busy",
                              "error": "Another connection controls this browser", "retryable": False})
                    else:
                        chosen_page_id = None
                        await login_control(current, "take")
                        await refresh_view()
                elif kind == "release_control":
                    if await current.release_control(connection_id):
                        await login_control(current, "release")
                else:
                    emit({"type": "error", "code": "invalid_message",
                          "error": "Unknown browser message", "retryable": False})

        tasks = [asyncio.create_task(coro()) for coro in (follow_session, pump_out, pump_in, overflow.wait)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("browser stream failed for {}", agent_id)
        finally:
            with anyio.CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await detach()
                try:
                    await websocket.close(code=1013 if overflow.is_set() else 1000)
                except (RuntimeError, WebSocketDisconnect):
                    pass

    return [WebSocketRoute("/browser/{agent_id}/stream", stream)]
