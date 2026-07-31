"""
@file_name: runner.py
@author: Bin Liang
@date: 2026-07-29
@description: The standalone-process host — the agent turn runs as its
own process (Owner decision).

One protocol, two transports: in the cloud the per-user executor
container hosts this loop behind its existing HTTP NDJSON endpoint; in
local/desktop mode the driver spawns ``python -m …nexus_power.runner``
and speaks the SAME serialization over pipes (iron rule #7: both modes
behave identically).

Wire shape: stdin carries ONE JSON line —
``{"thread_id", "messages", "options"}`` — stdout streams NDJSON lines:
``{"event": {…}}`` (shape per ``options.output_mode``) and a final
``{"exit": {...}}``. Lines have NO length ceiling: readers must buffer
manually (the 128 KiB aiohttp line-limit incident of 2026-07-08 —
image-bearing events reach hundreds of KB).

Process-isolation payoffs: a loop crash never takes the host down;
cancellation = SIGTERM (trapped into a cooperative signal so pairing
invariants hold) plus process-group reaping; resources account per
process. Every event also lands in the local NDJSON truth file under
``<cwd>/.nexus_power/`` (constraint C1: the log is recorded, always).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from typing import Any

# litellm fetches its price map from GitHub AT IMPORT TIME unless told
# otherwise (5s timeout, then a bundled fallback). That call sits on the
# cold-start path of every runner process — an outbound request from a
# hardened executor container, on the one path we spent the warm pool to
# make fast. The bundled map ships with the pinned litellm version and is
# what ``price_usage`` reads anyway. ``setdefault``, so an operator who
# genuinely wants fresh prices can still export the opposite.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


class _SignalCancellation:
    """Cooperative cancellation raised by SIGTERM/SIGINT or stdin EOF
    (a broken downstream is a cancellation, never an orphaned run)."""

    def __init__(self) -> None:
        self._flag = False

    def set(self) -> None:
        self._flag = True

    def requested(self) -> bool:
        return self._flag


async def serve_turn(raw_request: str, write_line: Any) -> int:
    """Run one turn from a serialized TurnRequest; stream lines out.

    Returns the process exit code (0 = the turn closed itself — even an
    ERROR end-reason is a *successful* serve; non-zero = the request
    never became a running turn).
    """
    from xyz_agent_context.agent_framework.nexus_power.assembly import (
        TurnRequest,
        run_turn_events,
    )
    from xyz_agent_context.agent_framework.nexus_power.contracts.options import TurnOptions
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.event_adapter import (
        LegacyEventAdapter,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.event_log import (
        FileEventLogWriter,
        event_to_row,
    )

    try:
        payload = json.loads(raw_request)
        options = TurnOptions(**payload["options"])
        request = TurnRequest(
            thread_id=str(payload["thread_id"]),
            messages=list(payload["messages"]),
            options=options,
        )
    except Exception as exc:  # noqa: BLE001 - a bad request never ran
        await write_line({"exit": {"ok": False, "error": f"bad request: {exc}"}})
        return 2

    cancellation = _SignalCancellation()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, cancellation.set)
        except (NotImplementedError, ValueError):  # non-unix / nested loop
            pass

    log = FileEventLogWriter(
        request.thread_id,
        os.path.join(options.cwd, ".nexus_power", f"{request.thread_id}.ndjson"),
    )
    adapter = LegacyEventAdapter()
    legacy = options.output_mode == "legacy_dict"
    try:
        async for event in run_turn_events(request, cancellation, log=log):
            if legacy:
                for translated in adapter.translate(event):
                    await write_line({"event": translated})
            else:
                await write_line({"event": event_to_row(request.thread_id, event)})
        await write_line({"exit": {"ok": True}})
        return 0
    except Exception as exc:  # noqa: BLE001 - surfaced on the wire
        import traceback

        await write_line(
            {
                "exit": {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc()[-3000:],
                }
            }
        )
        return 1


def _prewarm() -> None:
    """Eagerly pay every import (assembly graph + litellm) BEFORE the
    request arrives — the warm-pool contract: a pooled runner idles
    fully loaded, so time-to-first-token excludes all import cost.
    Gated by NEXUS_POWER_PREWARM=1 (pool spawns set it); a bare one-shot
    spawn keeps lazy imports and the smaller footprint."""
    from xyz_agent_context.agent_framework.llm.litellm_client import LitellmClient
    from xyz_agent_context.agent_framework.nexus_power import assembly  # noqa: F401
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl import (  # noqa: F401
        event_adapter,
        loop,
    )

    LitellmClient._litellm()  # the 1.8s / 215MB import, paid while idle


def main() -> None:
    """``python -m xyz_agent_context.agent_framework.nexus_power.runner``:
    read one request line from stdin, stream NDJSON to stdout."""

    async def _run() -> int:
        if os.getenv("NEXUS_POWER_PREWARM") == "1":
            _prewarm()
        raw = sys.stdin.readline()
        if not raw.strip():
            return 2
        writer_lock = asyncio.Lock()

        async def write_line(obj: dict[str, Any]) -> None:
            async with writer_lock:
                sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
                sys.stdout.flush()

        return await serve_turn(raw, write_line)

    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
