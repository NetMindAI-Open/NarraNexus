"""
@file_name: session.py
@author:
@date: 2026-09-22
@description: One live browser session — policy, control ownership and CDP, assembled.

MCP tools and the stream route share this session. Ordinary HTTP(S) browsing
needs no approval. Non-web URLs remain unsupported, and arbitrary scripts
require their separate privileged capability.

**A queued agent is a working agent.** During a user takeover the agent's
actions wait (铁律 #14). No deadline.

Every decision writes an audit row. That is not for tidiness: logs rotate and
greps miss things, while "the row that should be there is not" is evidence
(incident lesson #5).
"""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Optional

from loguru import logger

from narranexus.platform.browser._browser_impl.actions import action_expression, key_events
from narranexus.platform.browser._browser_impl.cdp import SCREENCAST_MIME_TYPE, input_events_for
from narranexus.platform.browser._browser_impl.control import ControlArbiter
from narranexus.platform.browser._browser_impl.pages import BrowserPages
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, decide, origin_of
from narranexus.platform.browser._browser_impl.read import (
    DEFAULT_TEXT_LIMIT,
    snapshot_expression,
)
from narranexus.platform.browser._browser_impl.limits import MAX_SCRIPT_CHARS, check_text
from narranexus.platform.browser._browser_impl.visual import (
    VisualObservation, capture_scale, png_dimensions, view_expression,
)

#: A frame as it goes to subscribers.
Frame = dict
#: ``(frame) -> None``
FrameSink = Callable[[Frame], None]


def _agent_action(method):
    """Serialize operations, checking ownership again after acquiring the lock."""
    @wraps(method)
    async def guarded(self, *args, **kwargs):
        try:
            while self.is_open:
                await self.control.wait_for_turn()
                await self.pages.flush()
                async with self._operation_lock:
                    if not self.control.agent_may_act():
                        continue
                    if not self.is_open:
                        break
                    page_id = self.pages.active_id
                    result = await method(self, *args, **kwargs)
                await self.pages.flush()
                return {**result, "page_id": result.get("page_id", page_id), **self.pages.snapshot()}
            return _err("browser session is closed")
        except Exception as exc:
            self._record("error", operation=method.__name__, error=str(exc))
            return _err(str(exc))
    return guarded


class BrowserSession:
    """A browser the agent drives and the user can take over."""

    def __init__(
        self,
        *,
        cdp: Any,
        policy: BrowserPolicy,
        audit: Callable[[dict], None],
        turn_id: str,
        thread_id: str,
        policy_provider: Optional[Callable[[], Any]] = None,
        page_id: str = "main",
        browser: Any = None,
    ) -> None:
        self._policy = policy
        # Privileged scripts read current settings across the MCP/API boundary.
        self._policy_provider = policy_provider
        self._audit = audit
        self._scope: ContextVar[tuple[str, str]] = ContextVar(
            "browser_scope", default=(turn_id, thread_id)
        )
        self.control = ControlArbiter()
        self._closed = False
        self._frames = 0
        self._operation_lock = asyncio.Lock()
        self.pages = BrowserPages(cdp, page_id=page_id, browser=browser, lock=self._operation_lock)
        self._close_task: asyncio.Task | None = None
        self._pressed_keys: dict[str, dict] = {}
        self._pressed_buttons: dict[str, dict] = {}
        self._input_page_id: str | None = None
        self._visual_observation: VisualObservation | None = None

    @property
    def _cdp(self) -> Any:
        return self.pages.current.cdp

    def bind_scope(self, *, turn_id: str, thread_id: str) -> None:
        """Bind the runtime-declared turn/conversation ids in this task, never session-global state.

        Self-declared like agent_id (see ``_mcp_identity.THREAD_ID_HEADER``):
        they attribute audit rows and bind visual observations to the calling
        turn; they authorize nothing.
        """
        self._scope.set((turn_id, thread_id))

    @property
    def _turn_id(self) -> str:
        return self._scope.get()[0]

    @property
    def _thread_id(self) -> str:
        return self._scope.get()[1]

    @property
    def is_open(self) -> bool:
        return not self._closed and self.pages.is_open

    @property
    def frames_delivered(self) -> int:
        return self._frames

    # ── audit ────────────────────────────────────────────────────────────

    def _record(self, event: str, **fields: Any) -> None:
        from narranexus.platform.browser._browser_impl.policy import origin_of

        row = {"event": event, "ts": time.time(), "turn_id": self._turn_id, "thread_id": self._thread_id}
        # Audit metadata must never retain URLs with query secrets, scripts,
        # selectors, input values, or exception text containing page content.
        origin = origin_of(fields.get("origin") or fields.get("url") or "")
        if origin:
            row["origin"] = origin
        allowed = {
            "verdict": {"allow", "ask", "deny"},
            "action": {"click", "fill", "select", "press", "scroll"},
            "operation": {"navigate", "read_page", "act", "run_script", "capture_screenshot", "select_page", "close_page"},
        }
        for name, values in allowed.items():
            value = fields.get(name)
            if isinstance(value, str) and value in values:
                row[name] = value
        if type(fields.get("frames")) is int:
            row["frames"] = fields["frames"]
        if event == "error":
            row["error_code"] = "operation_failed"
        try:
            self._audit(row)
        except Exception:
            # Losing an audit row must not fail the operation it describes,
            # but it must not be silent either.
            logger.exception("browser audit sink raised")

    async def _fresh_policy(self) -> BrowserPolicy:
        """The agent's permissions as they are RIGHT NOW.

        Script settings are written in the API process. Failed refreshes fail
        the privileged operation; a cached allow could bypass a recent revoke.
        """
        if self._policy_provider is None:
            return self._policy
        fresh = self._policy_provider()
        return await fresh if inspect.isawaitable(fresh) else fresh

    # ── navigation ───────────────────────────────────────────────────────

    @_agent_action
    async def navigate(self, url: str, *, new_page: bool = False) -> dict:
        """Navigate to any HTTP(S) URL while the agent holds control."""
        if self._closed:
            return _err("browser session is closed")

        refusal = self._validate_web_url(url, operation="navigate")
        if refusal is not None:
            return refusal
        try:
            await self.control.wait_for_turn()
        except Exception as exc:
            return _err(str(exc))

        if self._closed:
            return _err("browser session is closed")

        if new_page:
            await self.pages.create()
        result = await self._cdp.call("Page.navigate", {"url": url})
        if result and result.get("errorText"):
            return _err(result["errorText"])
        return {"ok": True, "outcome": "OK", "url": url, "page_id": self.pages.active_id}

    @_agent_action
    async def select_page(self, page_id: str) -> dict:
        self.pages.select(page_id)
        return {"ok": True, "outcome": "OK", "page_id": page_id}

    @_agent_action
    async def close_page(self, page_id: str) -> dict:
        await self.pages.close_page(page_id)
        return {"ok": True, "outcome": "OK", "page_id": self.pages.active_id}

    # ── reading ──────────────────────────────────────────────────────────

    async def _current_url(self) -> str:
        """Where the page actually is right now."""
        res = await self._cdp.call(
            "Runtime.evaluate", {"expression": "location.href", "returnByValue": True}
        )
        return str(((res or {}).get("result") or {}).get("value") or "")

    def _validate_web_url(self, url: str, *, operation: str) -> Optional[dict]:
        """Limit browser tools to web pages without consulting site policies."""
        supported = origin_of(url) is not None
        self._record(operation, url=url, verdict="allow" if supported else "deny")
        if not supported:
            return {"ok": False, "outcome": "REJECTED", "message": "Only HTTP and HTTPS pages are supported."}
        return None

    async def _validate_current_page(self) -> Optional[dict]:
        return self._validate_web_url(await self._current_url(), operation="read")

    async def _evaluate(self, expression: str) -> dict:
        """Run one expression in the page. Never raises."""
        res = await self._cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        details = (res or {}).get("exceptionDetails")
        if details:
            text = details.get("text") or "script failed"
            exc = (details.get("exception") or {}).get("description")
            return {"ok": False, "outcome": "ERROR", "message": exc or text}
        return {"ok": True, "outcome": "OK", "data": ((res or {}).get("result") or {}).get("value")}

    @_agent_action
    async def read_page(
        self, *, selector: str | None = None, offset: int = 0, limit: int = DEFAULT_TEXT_LIMIT,
    ) -> dict:
        """Read actionable targets and a Unicode text page within an optional CSS scope."""
        expression = snapshot_expression(selector=selector, offset=offset, limit=limit)
        if self._closed:
            return _err("browser session is closed")
        try:
            await self.control.wait_for_turn()
        except Exception as exc:
            return _err(str(exc))

        refusal = await self._validate_current_page()
        if refusal is not None:
            return refusal

        result = await self._evaluate(expression)
        if not result["ok"]:
            return result

        data = result.get("data")
        if not isinstance(data, dict):
            return _err("browser returned an invalid snapshot")
        if data.get("error"):
            return _err(str(data["error"]))
        if data.get("next_offset") is not None or offset:
            result["truncated"] = (
                f"partial text: offset {offset} of {data.get('total')} characters; "
                f"continue browser_read with the same selector and offset={data.get('next_offset')}"
                if data.get("next_offset") is not None else
                f"partial text: final page starting at offset {offset}; earlier characters are not included"
            )
        return result

    @_agent_action
    async def act(
        self, action: str, *, selector: str | None = None, text: str | None = None,
        key: str | None = None, value: str | None = None, x: float | None = None,
        y: float | None = None, delta_x: float | None = None, delta_y: float | None = None,
        observation_id: str | None = None,
    ) -> dict:
        """Perform a fixed gesture on a web page without accepting scripts.

        Normal page handlers may initiate their own requests and navigation.
        """
        if observation_id is not None:
            observation = self._visual_observation
            if observation is None or observation.id != observation_id:
                return _err("Visual observation is stale; call browser_look again")
            if action not in ("click", "scroll") or selector is not None:
                return _err("Use observation_id with coordinate click or scroll only")
            current = await self._evaluate(view_expression())
            if not current.get("ok") or not observation.matches(
                current.get("data") or {}, self.pages.active_id, self._scope.get(),
            ):
                return _err("Page, viewport or caller changed; call browser_look again")
            x, y = observation.point(x, y)
        expression = action_expression(
            action, selector=selector, text=text, key=key, value=value,
            x=x, y=y, delta_x=delta_x, delta_y=delta_y,
        )
        refusal = await self._validate_current_page()
        if refusal is not None:
            return refusal
        self._visual_observation = None
        result = await self._evaluate(expression)
        if not result['ok']:
            return result
        data = result.get('data')
        if not isinstance(data, dict):
            return _err('browser returned an invalid action result')
        if data.get('error'):
            return _err(str(data['error']))
        if action in ('click', 'scroll'):
            point = {'x': data.get('x'), 'y': data.get('y')}
            if action == 'scroll':
                events = input_events_for({
                    'kind': 'wheel', **point, 'deltaX': delta_x or 0, 'deltaY': delta_y or 0,
                })
            else:
                events = [
                    item for event_type, buttons in (
                        ('mouseMoved', 0), ('mousePressed', 1), ('mouseReleased', 0),
                    ) for item in input_events_for({
                        'kind': 'mouse', 'type': event_type, **point, 'buttons': buttons,
                    })
                ]
            if not events:
                return _err('browser returned invalid target coordinates')
        elif action == 'press':
            events = key_events(key)
        else:
            events = []
        # Complete the release even if cancellation or a protocol failure happens
        # during a press, so the next caller does not inherit a held input.
        release = events[-1] if action in ('click', 'press') else None
        try:
            for method, params in events[:-1] if release else events:
                await self._cdp.call(method, params)
        finally:
            if release:
                await self._cdp.call(*release)
        self._record('action', action=action)
        return {'ok': True, 'outcome': 'OK', 'action': action}

    @_agent_action
    async def run_script(self, expression: str) -> dict:
        """Run the agent's own expression in the page and return its value.

        Privileged escape hatch for operations not covered by fixed actions.

        Arbitrary JavaScript can submit forms, fetch other origins, or navigate.
        Require deliberately configured full_cdp_access independently of
        unrestricted navigation, reading, screenshots and fixed actions.
        """
        if self._closed:
            return _err("browser session is closed")
        try:
            check_text("script", expression, MAX_SCRIPT_CHARS)
        except ValueError as exc:
            return _err(str(exc))
        try:
            await self.control.wait_for_turn()
        except Exception as exc:
            return _err(str(exc))

        refusal = await self._validate_current_page()
        if refusal is not None:
            return refusal
        verdict = decide(await self._fresh_policy(), url=await self._current_url(),
                         capability="full_cdp_access")
        self._record("script", verdict=verdict.verdict, origin=verdict.origin)
        if verdict.verdict != "allow":
            return {"ok": False, "outcome": "REJECTED", "message": (
                "Arbitrary page scripts require explicitly configured full_cdp_access. "
                "Navigation, reading, screenshots and fixed browser actions need no permission."
            )}
        return await self._evaluate(expression)

    @_agent_action
    async def capture_screenshot(self) -> dict:
        """Capture evidence only while the caller may read the current page."""
        refusal = await self._validate_current_page()
        if refusal is not None:
            return refusal
        result = await self._cdp.call("Page.captureScreenshot", {"format": "png"})
        data = (result or {}).get("data")
        if not isinstance(data, str) or not data:
            return _err("browser returned an empty screenshot")
        self._record("screenshot")
        return {"ok": True, "outcome": "OK", "data": data, "mime_type": "image/png"}

    @_agent_action
    async def observe_page(
        self, *, selector: str | None = None, x: float | None = None,
        y: float | None = None, width: float | None = None,
        height: float | None = None, scale: float = 1,
    ) -> dict:
        """Return native screenshot bytes and a scoped image-coordinate observation."""
        expression = view_expression(selector=selector, x=x, y=y, width=width, height=height, scale=scale)
        self._visual_observation = None
        refusal = await self._validate_current_page()
        if refusal is not None:
            return refusal
        page = self.pages.current
        # Match stream startup/resize ordering: Chrome can restore capture metrics.
        async with page.stream_lock:
            inspected = await self._evaluate(expression)
            state = inspected.get("data")
            if not inspected.get("ok") or not isinstance(state, dict):
                return _err(inspected.get("message", "Could not inspect the viewport"))
            if state.get("error"):
                return _err(state["error"])
            region, viewport = state["region"], state["viewport"]
            capture = await page.cdp.call("Page.captureScreenshot", {
                "format": "png", "fromSurface": True, "captureBeyondViewport": False,
                "clip": {"x": viewport["scroll_x"] + region["x"],
                         "y": viewport["scroll_y"] + region["y"],
                         "width": region["width"], "height": region["height"],
                         "scale": capture_scale(region, viewport["dpr"], scale)},
            })
            data = (capture or {}).get("data")
            image_width, image_height = png_dimensions(data)
            observation = VisualObservation(
                id=uuid.uuid4().hex, page_id=page.id, scope=self._scope.get(),
                state=state, width=image_width, height=image_height,
            )
            after = await self._evaluate(view_expression())
            if not after.get("ok") or not observation.matches(
                after.get("data") or {}, page.id, self._scope.get(),
            ):
                return _err("Page changed during capture; call browser_look again")
        self._visual_observation = observation
        self._record("screenshot")
        return {
            "ok": True, "outcome": "OK", "data": data, "observation_id": observation.id,
            "url": state["url"], "title": state["title"], "viewport": viewport, "region": region,
            "image": {"width": image_width, "height": image_height, "mime_type": "image/png"},
            "coverage": "visible region only",
            "coordinates": "Use image pixel x/y with this observation_id in browser_act. "
                           "Observe again after actions, scrolling, resizing, navigation or human takeover.",
        }

    # ── user input ───────────────────────────────────────────────────────

    async def resize_viewport(self, width: int, height: int, connection_id: str, *, page_id: str | None = None) -> bool:
        """Fit the panel without changing layout under another human driver."""
        if type(width) is not int or type(height) is not int:
            return False

        def permitted() -> bool:
            return self.is_open and (
                self.control.agent_may_act() or self.control.accept_user_input(connection_id)
            )

        # Spectators must not queue behind a human's takeover. Recheck after
        # acquiring the action lock in case ownership changed while waiting.
        if not permitted():
            return False
        size = (max(240, min(width, 1920)), max(240, min(height, 1440)))
        async with self._operation_lock:
            if not permitted():
                return False
            page = self.pages.get(page_id or self.pages.active_id)
            # Chrome's initial screenshot can restore earlier device metrics.
            # Serialize it with resize without blocking streams on agent work.
            async with page.stream_lock:
                if size != page.viewport:
                    await page.cdp.call("Emulation.setDeviceMetricsOverride", {
                        "width": size[0], "height": size[1], "deviceScaleFactor": 1, "mobile": False,
                    })
                    page.viewport = size
                    if page.streaming:
                        await page.cdp.capture_frame()
            return True

    async def take_control(self, connection_id: str, *, page_id: str | None = None) -> bool:
        async with self._operation_lock:
            if not self.is_open:
                return False
            page = self.pages.get(page_id or self.pages.active_id)
            if not self.control.user_take_control(connection_id):
                return False
            self._visual_observation = None
            await self._reset_inputs()
            self.pages.select(page.id)
            return True

    async def select_user_page(self, page_id: str, connection_id: str) -> bool:
        async with self._operation_lock:
            if not self.is_open or not self.control.accept_user_input(connection_id):
                return False
            self.pages.get(page_id)
            await self._reset_inputs()
            self.pages.select(page_id)
            return True

    async def close_user_page(self, page_id: str, connection_id: str) -> bool:
        async with self._operation_lock:
            if not self.is_open or not self.control.accept_user_input(connection_id):
                return False
            await self._reset_inputs()
            await self.pages.close_page(page_id)
            return True

    async def new_user_page(self, connection_id: str) -> str | None:
        async with self._operation_lock:
            if not self.is_open or not self.control.accept_user_input(connection_id):
                return None
            await self._reset_inputs()
            page = await self.pages.create()
            self._record("page_created")
            return page.id

    async def navigate_user_page(self, page_id: str, url: str, connection_id: str) -> bool:
        async with self._operation_lock:
            if not self.is_open or not self.control.accept_user_input(connection_id):
                return False
            if page_id != self.pages.active_id:
                raise ValueError("The selected browser page changed; retry on the current page.")
            page = self.pages.get(page_id)
            refusal = self._validate_web_url(url, operation="navigate")
            if refusal is not None:
                raise ValueError(refusal["message"])
            await self._reset_inputs()
            result = await page.cdp.call("Page.navigate", {"url": url})
            if result and result.get("errorText"):
                raise RuntimeError(result["errorText"])
            return True

    async def _reset_inputs(self) -> None:
        try:
            page = self.pages.items.get(self._input_page_id or "")
            if page is not None and page.cdp.is_open:
                for params in self._pressed_keys.values():
                    await page.cdp.call("Input.dispatchKeyEvent", {
                        **params, "type": "keyUp", "text": "", "modifiers": 0,
                    })
                for params in self._pressed_buttons.values():
                    await page.cdp.call("Input.dispatchMouseEvent", {
                        **params, "type": "mouseReleased", "buttons": 0, "modifiers": 0,
                    })
        except Exception:
            logger.exception("could not reset browser inputs")
        finally:
            self._pressed_keys.clear()
            self._pressed_buttons.clear()
            self._input_page_id = None

    async def release_control(self, connection_id: str) -> bool:
        async with self._operation_lock:
            if not self.control.accept_user_input(connection_id):
                return False
            await self._reset_inputs()
            self.control.user_release_control(connection_id)
            return True

    async def handle_user_input(self, event: dict, connection_id: str = "local", *, page_id: str | None = None) -> None:
        """Forward one panel input event, if the user currently holds control.

        Silently dropped otherwise — this runs on every mouse move over the
        panel, and a rejection per event would be noise, not information.
        """
        async with self._operation_lock:
            if not self.is_open or not self.control.accept_user_input(connection_id):
                return
            if page_id is not None and page_id != self.pages.active_id:
                return
            if self._input_page_id != self.pages.active_id:
                await self._reset_inputs()
                self._input_page_id = self.pages.active_id
            for method, params in input_events_for(event):
                if method == "Input.dispatchKeyEvent":
                    key = params.get("code") or params["key"]
                    if params["type"] in ("keyDown", "rawKeyDown"):
                        self._pressed_keys[key] = params
                    elif params["type"] == "keyUp":
                        self._pressed_keys.pop(key, None)
                elif method == "Input.dispatchMouseEvent":
                    if params["type"] == "mousePressed":
                        self._pressed_buttons[params["button"]] = params
                    elif params["type"] == "mouseReleased":
                        self._pressed_buttons.pop(params["button"], None)
                    elif params["type"] == "mouseMoved":
                        for held in self._pressed_buttons.values():
                            held.update(x=params["x"], y=params["y"])
                await self._cdp.call(method, params)

    # ── frames ───────────────────────────────────────────────────────────

    async def start_stream(self, *, page_id: str | None = None, **kwargs: Any) -> None:
        """Begin screencast; frames fan out to whoever subscribed."""
        page = self.pages.get(page_id or self.pages.active_id)
        async with page.stream_lock:
            if not self.is_open:
                raise ConnectionError("browser session is closed")
            if not page.streaming:
                await page.cdp.start_screencast(on_frame=lambda data, meta: self._on_frame(page.id, data, meta), **kwargs)
                page.streaming = True

    async def stop_stream(self, page_id: str) -> None:
        page = self.pages.items.get(page_id)
        if page is None:
            return
        async with page.stream_lock:
            if page.streaming and not page.sinks and page.cdp.is_open:
                await page.cdp.stop_screencast()
                page.streaming = False
                page.last_frame = None

    def _on_frame(self, page_id: str, data: str, meta: dict) -> None:
        page = self.pages.items.get(page_id)
        if page is None:
            return
        self._frames += 1
        frame = {"data": data, "mime_type": SCREENCAST_MIME_TYPE, "meta": meta, "n": self._frames,
                 "ts": time.time(), "page_id": page_id}
        page.last_frame = frame
        for sink in list(page.sinks):
            try:
                sink(frame)
            except Exception:
                # One dead panel must not end the stream for the others, and
                # must not end the agent's session either.
                logger.exception("browser frame sink raised")

    def subscribe(self, sink: FrameSink, *, page_id: str | None = None) -> Callable[[], None]:
        """Register a frame sink. Returns an unsubscribe callable."""
        page = self.pages.get(page_id or self.pages.active_id)
        page.sinks.append(sink)
        if page.last_frame is not None:
            try:
                sink(page.last_frame)
            except Exception:
                logger.exception("browser initial frame sink raised")

        def unsubscribe() -> None:
            try:
                page.sinks.remove(sink)
            except ValueError:
                pass

        return unsubscribe

    # ── lifecycle ────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Stop streaming, release queued actions, close the protocol."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
            self._close_task.add_done_callback(lambda task: task.cancelled() or task.exception())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await self._close_task
            raise

    async def _close(self) -> None:
        self._closed = True
        self._record("closed", frames=self._frames)
        self.control.close()
        # Closing the CDP transport ends screencasting too. Sending a stop
        # command first can strand shutdown behind an unresponsive renderer.
        await self.pages.close()


def _err(message: str) -> dict:
    return {"ok": False, "outcome": "ERROR", "message": message}


def make_session(
    *,
    cdp: Any,
    policy: Optional[BrowserPolicy] = None,
    audit: Optional[Callable[[dict], None]] = None,
    turn_id: str = "",
    thread_id: str = "",
) -> BrowserSession:
    """Convenience constructor with safe defaults for the audit sink."""
    return BrowserSession(
        cdp=cdp,
        policy=policy or BrowserPolicy(),
        audit=audit or (lambda _row: None),
        turn_id=turn_id,
        thread_id=thread_id,
    )
