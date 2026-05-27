"""
@file_name: ws_client.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: WebSocket driver for /ws/agent/run — sends one turn, records
              every server event until completion or timeout

The local backend's chat WebSocket protocol (see
backend/routes/websocket.py):

  1. Client connects to /ws/agent/run
  2. Client sends JSON: {agent_id, user_id, input_content,
                         working_source="chat", token? (cloud only)}
  3. Server streams JSON messages with a ``type`` field:
       progress | agent_response | agent_thinking | tool_call | error
       run_started | run_ended | reconnect_warning | stopping |
       run_reconnect | replay | heartbeat
  4. Connection closes naturally when the agent finishes.

This client only handles the fresh-run case (Phase A). Reconnect /
replay are out of scope for the e2e suite; we always exercise the
happy fresh-run path so cases stay readable.

Per turn we yield an in-memory list of every server message, plus the
``run_id`` extracted from the ``run_started`` event (used downstream by
log_grep for backend log correlation).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import websockets


# Default cap before we treat a stream as wedged. Per iron rule #14 we
# never impose a hard limit on the agent loop itself — this is the
# *driver's* patience, not a fix-via-cap. If a turn legitimately needs
# longer the case overrides via turn_timeout_seconds.
DEFAULT_TURN_TIMEOUT = 180


@dataclass
class WSTurn:
    """All events received during one user→agent turn."""
    input_content: str
    started_at: float
    ended_at: Optional[float] = None
    run_id: Optional[str] = None
    events: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False
    timed_out: bool = False
    transport_error: Optional[str] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    @property
    def final_reply(self) -> str:
        """The text the *user actually saw*.

        On the runtime wire, a tool call lands as a ``progress`` event
        whose ``details.tool_name`` carries the MCP name. Only the
        message sent via ``mcp__chat_module__send_message_to_user_directly``
        ever reaches the user — inline assistant ``agent_response``
        deltas are model self-talk per the prompt rules and are NOT
        visible. We extract from the ``status=='running'`` events to
        avoid double-counting completions.

        Multiple tool calls in the same turn (the agent decided to send
        more than one message) are joined by a blank line.
        """
        return "\n\n".join(extract_user_visible_messages(self.events))


SEND_TOOL_NAME = "mcp__chat_module__send_message_to_user_directly"


def extract_user_visible_messages(events: list[dict]) -> list[str]:
    """Helper reused by programmatic phase + ws transcripts."""
    out: list[str] = []
    for evt in events:
        if evt.get("type") != "progress" or evt.get("status") != "running":
            continue
        details = evt.get("details") or {}
        if details.get("tool_name") != SEND_TOOL_NAME:
            continue
        args = details.get("arguments") or {}
        msg = args.get("content")
        if isinstance(msg, str) and msg:
            out.append(msg)
    return out


async def drive_turn(
    ws_url: str,
    agent_id: str,
    user_id: str,
    input_content: str,
    *,
    working_source: str = "chat",
    token: Optional[str] = None,
    turn_timeout_seconds: int = DEFAULT_TURN_TIMEOUT,
) -> WSTurn:
    """Open a fresh WS, send one turn, drain until the server hangs up
    or our budget expires. The agent stays alive in the backend either
    way — we never call /stop, because doing so would mask cases where
    the agent legitimately runs long."""

    target = ws_url.rstrip("/") + "/ws/agent/run"
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "user_id": user_id,
        "input_content": input_content,
        "working_source": working_source,
    }
    if token:
        payload["token"] = token

    turn = WSTurn(input_content=input_content, started_at=time.time())

    try:
        async with websockets.connect(
            target,
            max_size=None,         # backend streams large tool outputs
            ping_interval=20,
            ping_timeout=60,
            open_timeout=15,
        ) as ws:
            await ws.send(json.dumps(payload))
            try:
                await _drain(ws, turn, turn_timeout_seconds)
            except asyncio.TimeoutError:
                turn.timed_out = True
    except Exception as exc:
        turn.transport_error = f"{type(exc).__name__}: {exc}"
    finally:
        turn.ended_at = time.time()

    return turn


async def _drain(ws, turn: WSTurn, timeout_seconds: int) -> None:
    """Read messages until the WS closes or we hit the per-turn cap.

    We treat a closed socket OR a ``run_ended`` event as completion.
    ``error`` does not end the turn by itself — the backend may emit a
    recoverable error then keep going — but the WS will close if the
    error is terminal, so we just keep reading.
    """
    deadline = time.time() + timeout_seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"turn exceeded {timeout_seconds}s")
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except websockets.ConnectionClosed:
            turn.completed = True
            return

        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            event = {"type": "_parse_error", "raw": raw}

        turn.events.append(event)

        et = event.get("type")
        if et == "run_started":
            turn.run_id = event.get("run_id") or turn.run_id
        elif et == "run_ended":
            turn.completed = True
            return
