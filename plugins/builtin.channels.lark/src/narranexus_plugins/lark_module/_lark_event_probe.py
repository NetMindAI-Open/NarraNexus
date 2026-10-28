"""
@file_name: _lark_event_probe.py
@date: 2026-05-27
@description: 5-second WebSocket probe to verify event subscription is
actually working for a freshly-bound Lark app — catches the highest-
frequency real-world bind failure: "user forgot to enable Event
Subscription on the developer console, bot silently never replies".

Why this exists: the bind flow has historically declared success after
`lark-cli auth status` confirmed the credentials work. That's
necessary but not sufficient — the bot also needs:

  1. Event subscription **mode enabled** in the developer console.
  2. WebSocket mode selected (not Webhook).
  3. encrypt_key / verification_token to match (if WS mode requires).
  4. Correct platform (Feishu vs Lark — only enforced at WS connect).

None of those are observable from any REST endpoint. The ONLY way to
verify event delivery actually works is to open the WS subscription
and see if the server accepts it. That's what this probe does.

Probe strategy: spawn `lark-cli event +subscribe`, wait up to
PROBE_TIMEOUT_SEC seconds for the subscriber to either (a) print
something indicating an established stream, or (b) die with an error
code we recognise. Kill the subprocess and return a structured result.

We DO NOT keep the subscriber alive — that's the trigger's job
(`lark_trigger.py`). The probe is intentionally cheap and self-bounded
so it can't hang the bind flow.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, asdict
from typing import Any

from loguru import logger

from ._lark_workspace import get_home_env


# How long to wait for the subscriber to either establish or fail. 5s is
# the tuning chosen by Owner: long enough to clear a normal handshake
# round-trip (≈1-2s observed), short enough that users don't perceive
# a "stuck" bind button.
PROBE_TIMEOUT_SEC: float = 5.0

# Specific error code emitted by the lark_oapi SDK when the WebSocket
# subscriber is told to connect to a domain that doesn't match the
# app's actual brand. Documented in _lark_service.py docstring.
ERROR_BRAND_MISMATCH = "1000040351"


@dataclass
class EventProbeResult:
    """Outcome of the event-subscription health probe."""

    # True iff the probe ran to a definite conclusion (success OR a
    # specific failure). False on probe-tooling errors (lark-cli not
    # found, OS process spawn fail, etc.) — we then SKIP enforcement.
    probe_ran: bool = False
    # True iff WS appeared healthy within the timeout (stream opened,
    # no immediate fatal error).
    healthy: bool = False
    # Specific failure category — populated when probe_ran=True and
    # healthy=False. One of: '', 'brand_mismatch', 'event_sub_disabled',
    # 'timeout', 'connect_failed', 'other'.
    failure_kind: str = ""
    # Raw error text from lark-cli stderr (preserved for diagnostics).
    raw_error: str = ""
    # Probe duration in milliseconds (for monitoring).
    duration_ms: int = 0
    # Hint surfaced to the user when failure_kind is set.
    user_hint: str = ""
    # Auxiliary fields populated on certain failure paths.
    detected_error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FAILURE_HINTS: dict[str, str] = {
    "brand_mismatch": (
        "The app rejected the WebSocket connection because the selected "
        "platform (Feishu vs Lark) does not match the app's actual brand. "
        "Unbind and re-bind, choosing the correct platform."
    ),
    "event_sub_disabled": (
        "Could not open the event subscription stream. The most common "
        "cause is that 'Event Subscription' is not enabled in the developer "
        "console for this app. Open the app's 'Events & Callbacks' page, "
        "make sure event subscription is enabled and Subscription Mode is "
        "set to 'WebSocket' (not 'Webhook'), then publish a new version."
    ),
    "timeout": (
        "The event-subscription probe timed out. The bot was bound, but "
        "we couldn't confirm that messages will actually be delivered. "
        "Common causes: slow network to Lark/Feishu, or event subscription "
        "is enabled but the server is rate-limiting. The trigger will "
        "retry automatically; if the bot still doesn't respond to messages "
        "after a minute, check 'Event Subscription' settings in the "
        "developer console."
    ),
    "connect_failed": (
        "The WebSocket subscriber could not connect. Check network "
        "connectivity from this host to Lark/Feishu (open.feishu.cn for "
        "Feishu, open.larksuite.com for Lark)."
    ),
    "other": (
        "Event subscription probe failed with an unrecognised error. "
        "Check the technical details below; the trigger may still work, "
        "but message delivery cannot be confirmed at bind time."
    ),
}


async def probe_event_subscription(agent_id: str) -> EventProbeResult:
    """Spawn lark-cli's event subscriber for PROBE_TIMEOUT_SEC and
    interpret what happened. See module docstring for rationale.
    """
    import time

    start = time.monotonic()
    env = get_home_env(agent_id)
    cmd = ["lark-cli", "event", "+subscribe", "--format", "compact"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return EventProbeResult(
            probe_ran=False,
            raw_error="lark-cli not found",
        )
    except OSError as exc:
        return EventProbeResult(probe_ran=False, raw_error=f"spawn failed: {exc}")

    stdout_buf = bytearray()
    stderr_buf = bytearray()

    async def _drain(stream: asyncio.StreamReader | None, buf: bytearray) -> None:
        if not stream:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            buf.extend(chunk)

    drain_tasks = [
        asyncio.create_task(_drain(proc.stdout, stdout_buf)),
        asyncio.create_task(_drain(proc.stderr, stderr_buf)),
    ]

    failure_kind = ""
    detected_code = ""
    healthy = False

    try:
        # We don't want to wait the full timeout if the process exits
        # early (success or error). Wait for whichever comes first.
        try:
            await asyncio.wait_for(proc.wait(), timeout=PROBE_TIMEOUT_SEC)
            # Process exited within timeout — either healthy completion
            # (unlikely for a long-poll subscriber) or an error.
            stderr_text = stderr_buf.decode(errors="replace")
            stdout_text = stdout_buf.decode(errors="replace")
            combined = (stderr_text + "\n" + stdout_text).lower()
            if ERROR_BRAND_MISMATCH in combined:
                failure_kind = "brand_mismatch"
                detected_code = ERROR_BRAND_MISMATCH
            elif any(
                hint in combined
                for hint in ("event subscription", "not enabled",
                             "subscribe_failed", "websocket disabled")
            ):
                failure_kind = "event_sub_disabled"
            elif any(
                hint in combined
                for hint in ("connect", "dial tcp", "dns", "network unreachable")
            ):
                failure_kind = "connect_failed"
            else:
                failure_kind = "other"
        except asyncio.TimeoutError:
            # Process still alive at timeout = subscriber is happily
            # connected and waiting for events. Healthy! Kill it cleanly.
            healthy = True
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
        for t in drain_tasks:
            t.cancel()

    duration_ms = int((time.monotonic() - start) * 1000)
    raw_err = stderr_buf.decode(errors="replace").strip()

    if healthy:
        logger.info(
            f"[lark.probe] agent={agent_id} event subscription OK "
            f"({duration_ms}ms)"
        )
        return EventProbeResult(
            probe_ran=True,
            healthy=True,
            duration_ms=duration_ms,
        )

    # Failure path
    logger.warning(
        f"[lark.probe] agent={agent_id} event subscription FAILED "
        f"kind={failure_kind} duration_ms={duration_ms} err={raw_err[:200]!r}"
    )
    return EventProbeResult(
        probe_ran=True,
        healthy=False,
        failure_kind=failure_kind,
        raw_error=raw_err,
        duration_ms=duration_ms,
        user_hint=_FAILURE_HINTS.get(failure_kind, _FAILURE_HINTS["other"]),
        detected_error_code=detected_code,
    )
