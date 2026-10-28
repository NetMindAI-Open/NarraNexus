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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Type-only: the real class is never imported at module top — runner's
    # imports stay lazy so the cold path pays them only on demand (the
    # warm-pool / _prewarm contract). Annotating the seam still lets the
    # transport callers be checked against the Protocol.
    from narranexus_plugins.frameworks_nexus_power.core.contracts.protocols import (
        SteeringInlet,
    )

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


async def serve_turn(
    raw_request: str, write_line: Any, *, steering: "SteeringInlet | None" = None
) -> int:
    """Run one turn from a serialized TurnRequest; stream lines out.

    Returns the process exit code (0 = the turn closed itself — even an
    ERROR end-reason is a *successful* serve; non-zero = the request
    never became a running turn).

    ``steering`` is an optional live-injection inlet forwarded to the
    loop; ``None`` keeps today's behaviour (no mid-turn injection). The
    process host is what feeds it — the inlet object never crosses the
    serialization boundary.
    """
    from narranexus_plugins.frameworks_nexus_power.core.assembly import (
        TurnRequest,
        run_turn_events,
    )
    from narranexus_plugins.frameworks_nexus_power.core.contracts.events import (
        TYPE_STEER_CONSUMED,
    )
    from narranexus_plugins.frameworks_nexus_power.core.contracts.options import TurnOptions
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.event_adapter import (
        LegacyEventAdapter,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.event_log import (
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
        async for event in run_turn_events(request, cancellation, log=log, steering=steering):
            # Steer-consumption is a transient control signal, not turn output —
            # it rides its OWN line (never the {"event": …} stream) so it bypasses
            # the legacy adapter's type whitelist (which would drop it) and the
            # driver can intercept it without it reaching AgentRuntime.
            if event.type == TYPE_STEER_CONSUMED:
                await write_line({"steer_consumed": list(event.payload.get("ids", []))})
                continue
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
    from narranexus.platform.agent_framework.llm.litellm_client import LitellmClient
    from narranexus_plugins.frameworks_nexus_power.core import assembly  # noqa: F401
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl import (  # noqa: F401
        event_adapter,
        loop,
    )

    LitellmClient._litellm()  # the 1.8s / 215MB import, paid while idle


def forward_steer_lines(lines: Any, deliver: Any) -> None:
    """Parse steer lines from a blocking ``lines`` iterable (the runner passes
    ``sys.stdin``) and hand each valid injection to ``deliver``. Extracted from
    the daemon-thread body so the parse-and-dispatch logic is unit-tested; the
    thread wrapper that supplies ``sys.stdin`` and a cross-thread ``deliver`` is
    the only untested glue. Stops at EOF (stdin closed)."""
    for line in lines:
        msg = parse_steer_line(line)
        if msg is not None:
            deliver(msg)


def parse_steer_line(line: str) -> "dict[str, Any] | None":
    """A post-request stdin line → the provider message to inject, or None.

    The steer transport frames each injection as ``{"steer": {provider msg}}``.
    Anything else (a blank keep-alive, a malformed line, a non-steer object) is
    ignored — a bad line must never take the turn down."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    msg = obj.get("steer") if isinstance(obj, dict) else None
    return msg if isinstance(msg, dict) else None


def main() -> None:
    """``python -m narranexus_plugins.frameworks_nexus_power.core.runner``:
    read one request line from stdin, stream NDJSON to stdout. Subsequent
    stdin lines are live-steering injections (``{"steer": …}``)."""
    import threading

    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.steering import (
        QueueSteeringInlet,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.event_log import (
        ndjson_line,
    )

    async def _run() -> int:
        # This process runs the turn: the framework's own seats
        # (turn.pipeline.act.framework.nexus_power.*), providers, clients and
        # modules all come from the plugin boot. Without it the registries hold
        # only the kernel seed tree and the first seat lookup in assembly fails
        # with "unknown slot …nexus_power.expression". Same contribution set as
        # the executor service (workers), but the read-only form: this process
        # lives one turn and never serves, so it must not own the workers boot
        # marker (three turns used to flip the app into SAFE MODE).
        from narranexus.platform.module_system.plugins_boot import boot_turn_plugins

        try:
            boot_turn_plugins()
        except Exception as exc:  # noqa: BLE001 - surfaced on the wire like every other failure
            # A corrupt registry.json or an unresolvable binding must reach the
            # driver as a turn error, not as a raw traceback on stderr.
            sys.stdout.write(json.dumps({"exit": {"ok": False, "error": f"plugin boot failed: {exc}"}}) + "\n")
            sys.stdout.flush()
            return 3
        if os.getenv("NEXUS_POWER_PREWARM") == "1":
            _prewarm()
        # The request line read is UNCHANGED (blocking, load-bearing): every
        # turn depends on it, so live steering never touches it.
        raw = sys.stdin.readline()
        if not raw.strip():
            return 2
        writer_lock = asyncio.Lock()

        async def write_line(obj: dict[str, Any]) -> None:
            async with writer_lock:
                sys.stdout.write(ndjson_line(obj) + "\n")
                sys.stdout.flush()

        # Live steering: a daemon thread does the blocking reads of any FURTHER
        # stdin lines and hands each parsed injection to this loop's queue.
        # asyncio.Queue is not thread-safe, so the cross-thread put goes through
        # call_soon_threadsafe (the writer contract in steering.py). For a
        # non-steerable run the driver writes one line then closes stdin, so the
        # thread hits EOF at once and exits — zero behaviour change.
        loop = asyncio.get_running_loop()
        steer_queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        inlet = QueueSteeringInlet(steer_queue)

        def _deliver(msg: dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(steer_queue.put_nowait, msg)
            except RuntimeError:
                # The loop is closing (turn already ended) — a late steer line
                # has nowhere to go and is dropped silently, like a bad line.
                return

        def _reader_body() -> None:
            # Thread-side fire-and-forget: catch so a stray exception collapses
            # to ONE line instead of a multi-line traceback (incident lesson #2,
            # thread variant). Honest scope: the runner never calls
            # setup_logging(), so loguru keeps its default stderr sink — this
            # line DOES reach stderr, and the driver may surface it in a
            # non-zero turn's stderr tail. That is a readability win over a raw
            # traceback, not stderr suppression; WARNING (not DEBUG) because it
            # is going to be seen anyway and DEBUG would falsely imply it is
            # hidden. (loguru imported here, not at module top, to keep the cold
            # path's imports lazy — warm-pool / _prewarm contract.)
            try:
                forward_steer_lines(sys.stdin, _deliver)
            except Exception as e:  # noqa: BLE001
                from loguru import logger

                logger.warning(f"[runner] steer reader thread exiting on {e!r}")

        reader = threading.Thread(target=_reader_body, daemon=True)
        reader.start()

        return await serve_turn(raw, write_line, steering=inlet)

    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
