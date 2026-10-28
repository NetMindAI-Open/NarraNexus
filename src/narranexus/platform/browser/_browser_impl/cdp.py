"""
@file_name: cdp.py
@author:
@date: 2026-09-22
@description: CDP request/response plumbing, the screencast loop, and input translation.

Everything here was shaped by the 2026-09-21 spike against a real browser, and
three findings are encoded rather than commented:

**A response only arrives while something is reading the socket.** The first
version of the spike awaited a CDP call before starting the read loop and
deadlocked — the reply was sitting in the socket with nobody to dequeue it.
``start()`` therefore launches the reader first and every call goes through a
future the reader resolves.

**Every screencast frame must be acked.** Without ``Page.screencastFrameAck``
Chromium emits exactly one frame and then stops. That failure is
indistinguishable from "the page is not changing", which is what made it
expensive to find.

**Silence is not a stall.** Screencast only emits on visual change, so a still
page legitimately produces zero frames. Nothing downstream may treat that as
an error — and because the frames simply are not produced, bandwidth drops on
its own without us dropping any content (铁律 #16).

Input translation is a whitelist, not a passthrough: these events arrive from
the frontend over a socket, so anything unrecognised is untrusted and is
dropped rather than reflected into the protocol.
"""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Callable, Optional, Protocol

from loguru import logger

#: Encoding of every panel frame (screencast and seed capture alike). Frames
#: carry ``SCREENCAST_MIME_TYPE`` so the panel decodes by data, not by a
#: format both sides merely agree on.
SCREENCAST_FORMAT = "jpeg"
SCREENCAST_MIME_TYPE = "image/jpeg"

#: Event types we will forward. Anything else is dropped.
_MOUSE_TYPES = frozenset({"mousePressed", "mouseReleased", "mouseMoved"})
_KEY_TYPES = frozenset({"keyDown", "keyUp", "rawKeyDown", "char"})
_BUTTONS = frozenset({"none", "left", "right", "middle", "back", "forward"})
_KEY_CODES = {"Backspace": 8, "Tab": 9, "Enter": 13, "Shift": 16,
              "Control": 17, "Alt": 18, "Escape": 27, " ": 32,
              "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36,
              "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39,
              "ArrowDown": 40, "Insert": 45, "Delete": 46, "Meta": 91}


class CdpSocket(Protocol):
    """The transport: send a frame, iterate incoming frames, close."""

    async def send(self, raw: str) -> None: ...
    def __aiter__(self): ...
    async def __anext__(self) -> str: ...
    async def close(self) -> None: ...


class CdpSession:
    """A browser transport or flattened page channel with correlated calls."""

    def __init__(self, *, socket: CdpSocket, parent: CdpSession | None = None, session_id: str | None = None) -> None:
        self._socket = socket
        self._parent = parent
        self._session_id = session_id
        self._channels: dict[str, CdpSession] = {}
        self._pending_channels: dict[int, str | None] = {}
        self._listeners: list[Callable[[str, dict], None]] = []
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self._on_frame: Optional[Callable[[str, dict], None]] = None
        self._frame_quality = 60
        self._closed = False
        self.frames_seen = 0
        self._acks: set[asyncio.Task] = set()
        self._disconnected = asyncio.Event()

    @property
    def is_open(self) -> bool:
        return not self._closed and (self._parent is None or self._parent.is_open)

    def channel(self, session_id: str) -> CdpSession:
        """An attached flattened target shares transport, never browser lifetime."""
        if session_id not in self._channels:
            self._channels[session_id] = CdpSession(socket=self._socket, parent=self, session_id=session_id)
        return self._channels[session_id]

    def subscribe_events(self, sink: Callable[[str, dict], None]) -> Callable[[], None]:
        self._listeners.append(sink)

        def unsubscribe() -> None:
            if sink in self._listeners:
                self._listeners.remove(sink)

        return unsubscribe

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the read loop. Must happen before any ``call()``."""
        if self._parent is not None:
            await self._parent.start()
            return
        if self._reader is None:
            self._reader = asyncio.create_task(self._read_loop())
            # A fire-and-forget task whose exception nobody retrieves is a
            # buried mine (incident lesson #2); surface it instead.
            self._reader.add_done_callback(self._reader_finished)

    def _reader_finished(self, task: asyncio.Task) -> None:
        self._closed = True
        self._disconnected.set()
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("cdp read loop died: {}", exc)
        self._fail_pending(exc or ConnectionError("cdp socket closed"))
        for channel in self._channels.values():
            channel._disconnect()

    def _disconnect(self) -> None:
        self._closed = True
        self._on_frame = None
        self._disconnected.set()
        if self._parent is not None:
            for msg_id, channel in list(self._parent._pending_channels.items()):
                future = self._parent._pending.get(msg_id)
                if channel == self._session_id and future is not None and not future.done():
                    future.set_exception(ConnectionError("cdp target closed"))

    async def close(self) -> None:
        if self._parent is not None:
            self._disconnect()
            tasks = list(self._acks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._parent._channels.pop(self._session_id, None)
            return
        self._closed = True
        self._on_frame = None
        self._fail_pending(ConnectionError("cdp session closed"))
        tasks = list(self._acks)
        if self._reader is not None:
            tasks.append(self._reader)
        for task in tasks:
            task.cancel()
        try:
            for channel in list(self._channels.values()):
                await channel.close()
            await self._socket.close()
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._disconnected.set()

    async def wait_closed(self) -> None:
        await self._disconnected.wait()

    def _fail_pending(self, exc: BaseException) -> None:
        """Nobody may be left awaiting a promise that can never resolve."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # ── calls ────────────────────────────────────────────────────────────

    async def call(
        self, method: str, params: Optional[dict] = None, *, timeout: float = 30.0,
        session_id: str | None = None,
    ) -> Any:
        """Send a CDP command and await its result.

        Raises:
            RuntimeError: The protocol answered with an error.
            ConnectionError: The socket closed while waiting.
            asyncio.TimeoutError: No answer within ``timeout``.
        """
        if self._closed:
            raise ConnectionError("cdp session closed")
        if self._parent is not None:
            return await self._parent.call(method, params, timeout=timeout, session_id=self._session_id)
        await self.start()
        self._next_id += 1
        msg_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        self._pending_channels[msg_id] = session_id
        try:
            await self._socket.send(
                json.dumps({"id": msg_id, "method": method, "params": params or {},
                            **({"sessionId": session_id} if session_id else {})})
            )
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)
            self._pending_channels.pop(msg_id, None)
            if not fut.done():
                fut.cancel()
            elif not fut.cancelled():
                fut.exception()

    def _notify(self, method: str, params: dict) -> None:
        if method == "Target.detachedFromTarget":
            channel = self._channels.get(params.get("sessionId"))
            if channel is not None:
                channel._disconnect()
        for sink in list(self._listeners):
            try:
                sink(method, params)
            except Exception:
                logger.exception("cdp event subscriber raised")
        if method != "Page.screencastFrame":
            return
        self.frames_seen += 1
        session_id = params.get("sessionId")
        if session_id is not None:
            # Ack first, never behind the handler: the stream stops dead
            # without it, and a slow subscriber must not throttle the browser.
            task = asyncio.create_task(self._ack(session_id))
            self._acks.add(task)
            task.add_done_callback(self._acks.discard)
        handler = self._on_frame
        if handler is None:
            return
        try:
            handler(params.get("data", ""), params.get("metadata") or {})
        except Exception:
            # One bad subscriber must not end the session for everyone.
            logger.exception("screencast frame handler raised")

    async def _ack(self, session_id: Any) -> None:
        try:
            await self.call("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception as exc:
            logger.debug("screencast ack failed (session likely closing): {}", exc)

    async def _read_loop(self) -> None:
        async for raw in self._socket:
            try:
                msg = json.loads(raw)
            except Exception:
                logger.warning("cdp: undecodable frame dropped")
                continue
            if not isinstance(msg, dict):
                logger.warning("cdp: non-object frame dropped")
                continue
            msg_id = msg.get("id")
            if msg_id is not None:
                fut = self._pending.get(msg_id)
                if fut is None or fut.done():
                    continue
                if "error" in msg:
                    err = msg["error"] or {}
                    fut.set_exception(RuntimeError(f"cdp error: {err.get('message', err)}"))
                else:
                    fut.set_result(msg.get("result"))
                continue
            destination = self._channels.get(msg.get("sessionId")) if msg.get("sessionId") else self
            if destination is not None:
                destination._notify(msg.get("method", ""), msg.get("params") or {})

    # ── screencast ───────────────────────────────────────────────────────

    async def start_screencast(
        self,
        *,
        on_frame: Callable[[str, dict], None],
        max_width: int = 1920,
        max_height: int = 1440,
        quality: int = 60,
    ) -> None:
        """Begin streaming. Frames are base64 ``SCREENCAST_FORMAT``, as CDP delivers them."""
        self._on_frame = on_frame
        self._frame_quality = quality
        # Watched background tabs keep painting without moving the agent's
        # native foreground tab or changing its logical operation target.
        await self.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
        # A background/static tab may not emit an initial compositor frame.
        # Seed its view from the same renderer before subscribing to changes.
        await self.capture_frame()
        await self.call(
            "Page.startScreencast",
            {
                "format": SCREENCAST_FORMAT,
                "quality": quality,
                "maxWidth": max_width,
                "maxHeight": max_height,
                "everyNthFrame": 1,
            },
        )

    async def capture_frame(self) -> None:
        """Refresh a watched static page after startup or a viewport change."""
        if self._on_frame is None:
            return
        metrics = await self.call("Page.getLayoutMetrics")
        viewport = (metrics or {}).get("cssVisualViewport", {})
        snapshot = await self.call("Page.captureScreenshot", {"format": SCREENCAST_FORMAT, "quality": self._frame_quality})
        if self._on_frame is not None and (snapshot or {}).get("data"):
            self._on_frame(snapshot["data"], {"deviceWidth": viewport.get("clientWidth"),
                                             "deviceHeight": viewport.get("clientHeight")})

    async def stop_screencast(self) -> None:
        self._on_frame = None
        try:
            await self.call("Page.stopScreencast")
            await self.call("Emulation.setFocusEmulationEnabled", {"enabled": False})
        except Exception as exc:
            logger.debug("stopScreencast failed (session likely closing): {}", exc)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bounded_int(value: Any, minimum: int, maximum: int, default: int = 0) -> int:
    return value if type(value) is int and minimum <= value <= maximum else default


def input_events_for(event: dict) -> list[tuple[str, dict]]:
    """Translate one frontend input event into CDP commands.

    A whitelist, deliberately. These events come off a socket the renderer
    writes to, so an unrecognised shape is untrusted input and is dropped
    rather than forwarded — reflecting arbitrary dicts into
    ``Input.dispatch*`` would turn the takeover channel into a way to drive
    the protocol directly.

    Returns:
        ``[(method, params), ...]`` — empty when the event is not accepted.
    """
    if not isinstance(event, dict):
        return []
    kind = event.get("kind")
    modifiers = _bounded_int(event.get("modifiers"), 0, 15)
    for flag, bit in (("altKey", 1), ("ctrlKey", 2), ("metaKey", 4), ("shiftKey", 8)):
        if event.get(flag) is True:
            modifiers |= bit

    if kind in ("mouse", "wheel"):
        x, y = event.get("x"), event.get("y")
        if not (_is_number(x) and _is_number(y)):
            return []
        if kind == "wheel":
            dx = event.get("deltaX", 0)
            dy = event.get("deltaY", 0)
            return [
                (
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseWheel",
                        "x": x,
                        "y": y,
                        "deltaX": dx if _is_number(dx) else 0,
                        "deltaY": dy if _is_number(dy) else 0,
                        "modifiers": modifiers,
                    },
                )
            ]
        etype = event.get("type")
        if not isinstance(etype, str) or etype not in _MOUSE_TYPES:
            return []
        button = event.get("button")
        return [
            (
                "Input.dispatchMouseEvent",
                {
                    "type": etype,
                    "x": x,
                    "y": y,
                    "button": button if isinstance(button, str) and button in _BUTTONS else (
                        "none" if etype == "mouseMoved" else "left"
                    ),
                    "buttons": _bounded_int(event.get("buttons"), 0, 31),
                    "clickCount": _bounded_int(event.get("clickCount"), 0, 3,
                                               0 if etype == "mouseMoved" else 1),
                    "modifiers": modifiers,
                },
            )
        ]

    if kind == "key":
        etype = event.get("type")
        if not isinstance(etype, str) or etype not in _KEY_TYPES:
            return []
        key = event.get("key")
        if not isinstance(key, str) or not key:
            return []
        # Chromium needs carriage-return text to perform Enter's native form
        # submission/newline action; a key code alone only dispatches keydown.
        default_text = "\r" if key == "Enter" else key if len(key) == 1 else ""
        text = event.get("text", default_text)
        if not isinstance(text, str) or etype not in ("keyDown", "char") or modifiers & 7:
            text = ""
        params = {"type": etype, "key": key, "text": text, "modifiers": modifiers}
        code = event.get("code")
        if isinstance(code, str) and len(code) <= 64:
            params["code"] = code
        vk = _bounded_int(event.get("windowsVirtualKeyCode"), 0, 255,
                          _KEY_CODES.get(key, ord(key.upper()) if len(key) == 1 and key.isascii() else 0))
        if vk:
            params["windowsVirtualKeyCode"] = vk
        params["autoRepeat"] = event.get("repeat") is True
        params["location"] = _bounded_int(event.get("location"), 0, 3)
        return [("Input.dispatchKeyEvent", params)]

    if kind == "text" and isinstance(event.get("text"), str) and event["text"]:
        return [("Input.insertText", {"text": event["text"]})]

    return []
