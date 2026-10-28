"""
@file_name: nexus_agent.py
@author: Bin Liang
@date: 2026-07-29
@description: NexusAgent — the AgentLoopDriver for the home-grown
nexus_power framework (structurally the twin of adapters/claude: the
adapter translates contracts and owns zero business logic).

Three jobs only:
  1. legacy call shape (messages, mcp_servers, streaming, extra_env,
     cancellation, **kwargs) → ``TurnRequest``, reading whichever of the
     per-turn provider configs the resolver populated: NexusPower drives
     the provider API itself, so BOTH anthropic- and openai-protocol
     providers work (unlike the CLI-backed drivers, each locked to one);
  2. run the turn in its OWN PROCESS by default (the runner subprocess,
     fed from a warm pool — this is the path the executor container takes,
     since the broker never sets ``NEXUS_POWER_INPROCESS``; that flag =1
     is opt-in for tests / callers wanting no subprocess) and relay its
     NDJSON — with manual line buffering, never a line-length assumption;
  3. guarantee the legacy stream ends with exactly one
     ``response.done`` on every path (the billing chain's sole source).

Platform adaptation seams accepted via **kwargs (forward-compatible,
optional): ``expressive_tools``, ``marker_tools``, ``expandables``,
``initial_expansions``, ``agent_id``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import uuid
from typing import Any, AsyncGenerator

from loguru import logger

from narranexus.platform.agent_framework.api_config import (
    _is_own_gateway_url,
    claude_config,
    codex_config,
)
from narranexus.platform.agent_framework.providers.model_catalog import (
    is_official_provider,
)
from narranexus.platform.agent_framework.loop.cancellation_view import CancellationView
from narranexus.platform.schema.turn_profile import TurnProfile
from narranexus.platform.schema.provider_schema import SUBSCRIPTION_AUTH_TYPES
from narranexus.contracts.agent_events import (
    DATA_TYPE_DONE,
    TYPE_RAW_RESPONSE_EVENT,
    raw_error_event,
)
from narranexus.platform.utils.logging import timed

_STREAM_LIMIT_BYTES = 32 * 1024 * 1024  # image-bearing lines reach 100s of KB
_CANCEL_POLL_S = 0.2
_STDERR_TAIL_BYTES = 4096
# agent_id stand-in when the caller passes none; never a real identity.
_PLACEHOLDER_AGENT_ID = "agent"


def start_stderr_drain(process: asyncio.subprocess.Process) -> "asyncio.Task[bytes]":
    """Read the runner's stderr continuously from the moment it is spawned.

    stderr is a pipe with a ~64 KB kernel buffer. The runner logs to it
    (module registrations at import, loguru, litellm's own logging), and a
    chatty environment — local dev with litellm debug on — fills that buffer
    mid-turn; the child then blocks on its next stderr write while the parent
    sits in ``stdout.readline()`` waiting for the child's next event: a silent
    deadlock (found 2026-09-06: the guide agent's first turn never finished).
    Draining in the background keeps the pipe empty; only the tail is kept
    and reported when the turn fails."""
    stream = process.stderr
    tail = bytearray()

    async def _drain() -> bytes:
        if stream is None:
            return b""
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return bytes(tail)
                tail.extend(chunk)
                if len(tail) > _STDERR_TAIL_BYTES:
                    del tail[: len(tail) - _STDERR_TAIL_BYTES]
        except Exception as exc:  # noqa: BLE001 — the pipe closed under us; keep what was read, say so
            logger.warning(f"nexus runner stderr drain stopped for pid {process.pid}: {type(exc).__name__}: {exc}")
            tail.extend(f"\n[stderr drain stopped: {type(exc).__name__}: {exc}]".encode())
            return bytes(tail)

    task = asyncio.create_task(_drain(), name=f"nexus-runner-stderr-{process.pid}")
    # Fire-and-forget discipline: a lost exception is a buried mine (and a
    # pooled runner whose drain died silently would deadlock on the next turn).
    task.add_done_callback(
        lambda t: (not t.cancelled() and t.exception())
        and logger.warning(f"nexus runner stderr drain failed for pid {process.pid}: {t.exception()}")
    )
    setattr(process, "_nx_stderr_tail", task)
    return task


async def stderr_tail(process: asyncio.subprocess.Process) -> str:
    """The last bytes the runner wrote to stderr (after it exited)."""
    task = getattr(process, "_nx_stderr_tail", None)
    if task is None:
        if process.stderr is None:
            return ""
        return (await process.stderr.read())[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")
    try:
        return (await task).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — a failed drain must not mask the turn's own error
        return f"<stderr drain failed: {exc}>"


class _WarmRunnerPool:
    """Pre-spawned, fully-imported runner processes idling on stdin.

    The cold path costs ~1.4s of package imports plus ~1.8s of litellm
    import per turn; a warm runner has paid both while idle, so a
    simple question answers at provider speed. One process serves one
    turn (isolation is preserved); every acquisition schedules a
    background refill. ``NEXUS_POWER_POOL_SIZE`` sizes the pool
    (default 1; 0 disables pooling — each idle warm process holds
    ~350 MB RSS, so the pool is a deliberate speed-for-memory trade).
    """

    _shared: "_WarmRunnerPool | None" = None

    def __init__(self) -> None:
        self._idle: list[asyncio.subprocess.Process] = []
        self._lock = asyncio.Lock()
        self._size = int(os.getenv("NEXUS_POWER_POOL_SIZE", "1"))

    @classmethod
    def shared(cls) -> "_WarmRunnerPool":
        if cls._shared is None:
            cls._shared = cls()
            import atexit

            atexit.register(cls._shared._shutdown_sync)
        return cls._shared

    @property
    def enabled(self) -> bool:
        return self._size > 0

    async def spawn(self, *, prewarm: bool) -> asyncio.subprocess.Process:
        env = dict(os.environ)
        if prewarm:
            env["NEXUS_POWER_PREWARM"] = "1"
        # Set HERE as well as in the runner module: ``-m …runner`` imports the
        # parent packages first, so litellm can already be loaded (and its
        # GitHub price-map fetch already paid) before the runner's own
        # setdefault runs. The child's environment is the only point that is
        # unambiguously earlier than every import it will do.
        env.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "narranexus_plugins.frameworks_nexus_power.core.runner",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT_BYTES,
            start_new_session=True,
            env=env,
        )
        # From the first byte: an idle pooled runner already logs at import.
        start_stderr_drain(process)
        return process

    async def acquire(self) -> asyncio.subprocess.Process:
        process: asyncio.subprocess.Process | None = None
        async with self._lock:
            while self._idle:
                candidate = self._idle.pop()
                if candidate.returncode is None:
                    process = candidate
                    break
        if process is None:
            process = await self.spawn(prewarm=True)
        self.schedule_refill()
        return process

    def schedule_refill(self) -> None:
        task = asyncio.create_task(self._refill())
        # Fire-and-forget discipline: a lost exception is a buried mine.
        task.add_done_callback(
            lambda t: t.exception()
            and logger.warning(f"runner pool refill failed: {t.exception()}")
        )

    async def _refill(self) -> None:
        async with self._lock:
            while len(self._idle) < self._size:
                self._idle.append(await self.spawn(prewarm=True))

    def _shutdown_sync(self) -> None:
        for process in self._idle:
            if process.returncode is None:
                _terminate_group(process.pid)
        self._idle.clear()


class NexusAgent:
    """AgentLoopDriver implementation for framework name ``nexus_power``."""

    def __init__(self, working_path: str = "./"):
        self.working_path = working_path
        # Start warming immediately so even the process's FIRST turn overlaps
        # imports with request preparation. Local/desktop (no AGENT_EXECUTOR_URL)
        # constructs a NexusAgent per turn in step_3, so this __init__ call is
        # that path's only prewarm point — do not remove it (binding rule #7).
        self._schedule_pool_prewarm()

    def _schedule_pool_prewarm(self) -> None:
        """Single source of truth for "should we prewarm the runner pool, and
        can we right now". Shared by ``__init__`` (per-turn / local path) and
        ``warmup()`` (executor startup). No-op in in-process mode or when
        pooling is disabled; and — because ``schedule_refill``'s ``create_task``
        needs a running loop — silently returns when there is none, so the
        sync/import-time ``__init__`` path and the async startup path share ONE
        gate and NEITHER raises."""
        if os.getenv("NEXUS_POWER_INPROCESS") == "1":
            return
        pool = _WarmRunnerPool.shared()
        if not pool.enabled:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop yet → create_task would raise; caller unharmed
        pool.schedule_refill()

    def warmup(self) -> None:
        """Eagerly fill the warm-runner pool NOW (called from the executor's
        startup lifespan). Without this the pool only starts filling when the
        process's FIRST turn constructs a NexusAgent — too late, so that turn
        pays the cold ~1.4s+1.8s import inline (measured ~12s vs ~2s warm on
        dev). Priming at startup lets the first real turn draw a pre-imported
        runner too. No-op in in-process mode or when pooling is disabled;
        best-effort — the caller is never blocked or raised into (see
        ``_schedule_pool_prewarm``)."""
        self._schedule_pool_prewarm()

    def capabilities(self) -> set[str]:
        """Shipped beyond the base contract: the two-track event log (local
        NDJSON truth file per turn) and live ``steering`` — in-process the loop
        drains the SteerChannel directly, subprocess the pump feeds it down
        stdin. The orchestrator gates whether to make a run steerable on
        ``"steering" in driver.capabilities()``. The remote (HTTP) path now ALSO
        carries steering for nexus_power: ``RemoteAgentLoopDriver`` POSTs each
        injection to the executor's ``/steer`` endpoint and forwards the loop's
        ``steer_consumed`` frames back (it declares ``steering`` framework-aware,
        i.e. only when it wraps a steer-capable driver like this one). So a cloud
        nexus_power run is steerable end to end, not degraded to a fresh turn.

        ``native_replay``: this driver consumes structured provider messages, so
        a past turn's event_log folds back into positioned monologue/tool
        segments instead of a flattened prose row (see
        ``platform.agent_framework.loop.history_projection``). CLI-backed
        drivers flatten at their doorstep and cannot declare it.

        Static twin: ``contribution.META.capabilities`` — the hosts that must
        answer these questions before a driver exists read that; keep both in
        step (``test_nexus_power_meta_matches_driver_capabilities``)."""
        return {"event_log", "steering", "native_replay"}

    @timed("llm.nexus.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],  # {name: {"url": str, "headers": {str: str}?}}
        *,
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        cancel = CancellationView(cancellation)
        done_seen = False
        try:
            request_payload = self._build_request_payload(
                messages, mcp_servers, extra_env, kwargs
            )
            # Live steering (opt-in): the orchestrator's SteerChannel for this
            # run, or None. In-process the loop drains its queue directly; the
            # subprocess path pumps it down stdin (added with the runner reader).
            steer_channel = kwargs.get("steering")
            if os.getenv("NEXUS_POWER_INPROCESS") == "1":
                events = self._run_inprocess(request_payload, cancel, steer_channel)
            else:
                events = self._run_subprocess(request_payload, cancel, steer_channel)
            async for event in events:
                if _is_done(event):
                    done_seen = True
                yield event
        except Exception as exc:  # noqa: BLE001 - classified for the wire
            from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.error_classifier import (
                DefaultErrorClassifier,
            )

            error = DefaultErrorClassifier().classify(exc)
            logger.exception(f"[nexus_power] turn failed: {error!r}")
            from typing import cast as _cast

            yield _cast(
                dict[str, Any], raw_error_event(error.message, error.legacy_error_type())
            )
        finally:
            if not done_seen:
                # Every path pays the billing chain exactly once.
                yield {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {"type": DATA_TYPE_DONE, "usage": {},
                             "stop_reason": "error",
                             "model": claude_config.model or ""},
                }

    # ------------------------------------------------------------------

    def _build_request_payload(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],
        extra_env: dict[str, str] | None,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        protocol, model, api_key, base_url, auth_type = _resolve_provider()
        if auth_type in SUBSCRIPTION_AUTH_TYPES:
            raise ValueError(
                "nexus_power drives the provider API directly and cannot use "
                "subscription OAuth credentials; keep this agent on the "
                "claude_code framework or configure an API-key provider"
            )
        if not model:
            raise ValueError(
                "nexus_power has no model configured for this turn — bind an "
                "anthropic- or openai-protocol provider to the agent slot"
            )
        # The delivery surface is DECLARED by the platform (modules'
        # expressive_tools → TurnInput → here). No guessing from
        # server names: a rename must never silently mute the agent, and
        # channel reply tools (lark_cli & co.) are part of the surface too.
        expressive = tuple(kwargs.get("expressive_tools") or ())
        llm_extra: dict[str, Any] = {}
        if protocol == "anthropic" and auth_type == "bearer_token" and api_key:
            # Anthropic-protocol gateways expecting Authorization: Bearer
            # (litellm's anthropic route sends x-api-key; add the header).
            llm_extra["extra_headers"] = {"Authorization": f"Bearer {api_key}"}
        # Platform-origin binding: forward the broker identity token so our
        # gateway can prove the call originates on-platform. ONLY to our own
        # gateway (never a BYOK third party). The token lives on the config the
        # resolver picked (claude for anthropic, codex for openai).
        _identity_token = (
            claude_config.identity_token if protocol == "anthropic"
            else codex_config.identity_token
        )
        if _identity_token and _is_own_gateway_url(base_url):
            _headers = dict(llm_extra.get("extra_headers") or {})
            _headers["X-NarraNexus-Identity-Token"] = _identity_token
            llm_extra["extra_headers"] = _headers
        _merge_llm_extra(
            llm_extra,
            _request_identity_params(protocol, base_url, kwargs.get("agent_id")),
        )
        # Per-turn fast-mode profile. Arrives as the in-process model or as
        # its model_dump() dict off the executor wire — normalize once here.
        # Absent profile MUST leave the payload semantically identical to
        # the pre-TurnProfile build (defaults below match the old hardwired
        # values; see tests/agent_framework/test_nexus_turn_profile.py).
        profile = kwargs.get("turn_profile")
        if isinstance(profile, dict):
            profile = TurnProfile(**profile)
        prompt_mode = profile.prompt_mode if profile is not None else "full"
        if profile is not None and profile.reasoning_effort:
            # litellm passthrough: rides ModelParams.extra straight into
            # acompletion kwargs — the gateway's reasoning knob.
            llm_extra["reasoning_effort"] = profile.reasoning_effort
        options: dict[str, Any] = {
            "cwd": self.working_path,
            "agent_id": str(kwargs.get("agent_id") or _PLACEHOLDER_AGENT_ID),
            "env": dict(extra_env or {}),
            # Collaborative areas (e.g. the team shared folder) sit outside
            # this agent's workspace by design; the caller decides which
            # roots this turn may additionally read. Absent → unchanged
            # workspace-only confinement.
            "extra_accessible_roots": tuple(kwargs.get("extra_accessible_roots") or ()),
            "model": model,
            "provider": protocol,
            "api_key": api_key,
            "base_url": base_url,
            "llm_extra": llm_extra,
            "thinking": bool(getattr(claude_config, "thinking", "") == "enabled"),
            "mcp_servers": mcp_servers,
            "disallowed_tools": tuple(kwargs.get("disallowed_tools") or ()),
            "deferred_tools": tuple(kwargs.get("deferred_tools") or ()),
            "expressive_tools": expressive,
            # The step layer's rendered origin line — passed through, never
            # re-phrased here. Both frameworks emit the SAME sentence because
            # neither one composes it.
            "origin_declaration": str(kwargs.get("origin_declaration") or ""),  # noqa: E501
            "marker_tools": tuple(kwargs.get("marker_tools") or ()),
            "expandables": tuple(kwargs.get("expandables") or ()),
            "initial_expansions": sorted(kwargs.get("initial_expansions") or ()),
            "output_mode": "legacy_dict",
            "prompt_mode": prompt_mode,
            # Steerability crosses the boundary EXPLICITLY: the runner mounts an
            # inlet on every turn, so only this flag tells it whether a producer
            # can actually feed one — it gates the wait_for_input tool. The single
            # truth is "did this run get a SteerChannel", the same criterion
            # `_open_steer_transport` uses to keep stdin open vs close it.
            "steerable": kwargs.get("steering") is not None,
        }
        if profile is not None and profile.include_arg_deltas is not None:
            options["include_arg_deltas"] = profile.include_arg_deltas
        if profile is not None and profile.expression_nudge is not None:
            options["expression_nudge"] = profile.expression_nudge
        return {
            "thread_id": f"turn_{uuid.uuid4().hex[:12]}",
            "messages": messages,
            "options": options,
        }

    async def _run_inprocess(
        self, payload: dict[str, Any], cancel: CancellationView,
        steer_channel: Any = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Same code path as the runner, minus the process boundary
        (executor containers and tests — already isolated)."""
        from narranexus_plugins.frameworks_nexus_power.core.runner import serve_turn
        from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.steering import (
            QueueSteeringInlet,
        )

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        # In-process the loop's inlet drains the SteerChannel's own queue, so a
        # push from the orchestrator lands where the loop reads — no pump, no
        # copy. None channel → no steering inlet (today's behaviour).
        inlet = QueueSteeringInlet(steer_channel.queue) if steer_channel is not None else None

        async def write_line(obj: dict[str, Any]) -> None:
            await queue.put(obj)

        async def _serve() -> None:
            try:
                await serve_turn(
                    json.dumps(payload, default=_json_default), write_line,
                    steering=inlet,
                )
            finally:
                await queue.put(None)

        # Bridge the platform token into the runner's cancellation view.
        class _Bridge:
            def set(self) -> None:  # runner's signal-handler hook
                return None

            def requested(self) -> bool:
                return cancel.requested()

        import narranexus_plugins.frameworks_nexus_power.core.runner as runner_module

        original = runner_module._SignalCancellation
        runner_module._SignalCancellation = lambda: _Bridge()  # type: ignore[assignment]
        try:
            task = asyncio.create_task(_serve())
            while True:
                line = await queue.get()
                if line is None:
                    break
                # Steer-consumption: the loop reported which steer_inbox rows it
                # drained. Tell the SteerChannel so the producer advances its
                # cursor on consumption; never forwarded onward.
                if "steer_consumed" in line:
                    if steer_channel is not None:
                        await steer_channel.deliver_consumed(list(line["steer_consumed"]))
                    continue
                event = self._line_to_event(line)
                if event is not None:
                    yield event
            await task
        finally:
            runner_module._SignalCancellation = original  # type: ignore[assignment]

    async def _run_subprocess(
        self, payload: dict[str, Any], cancel: CancellationView,
        steer_channel: Any = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        pool = _WarmRunnerPool.shared()
        if pool.enabled:
            process = await pool.acquire()
        else:
            process = await pool.spawn(prewarm=False)
        assert process.stdin and process.stdout
        steer_pump = await self._open_steer_transport(process, payload, steer_channel, cancel)

        signalled = False
        try:
            while True:
                read_task = asyncio.ensure_future(process.stdout.readline())
                while not read_task.done():
                    await asyncio.wait({read_task}, timeout=_CANCEL_POLL_S)
                    if cancel.requested() and not signalled:
                        signalled = True
                        _terminate_group(process.pid)
                raw = read_task.result()
                if not raw:
                    break
                try:
                    line = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning(f"[nexus_power] non-JSON runner line: {raw[:200]!r}")
                    continue
                # Steer-consumption rides its own line (see runner.serve_turn):
                # tell the SteerChannel which rows the run drained so the producer
                # advances its cursor on consumption; never forwarded onward.
                if "steer_consumed" in line:
                    if steer_channel is not None:
                        await steer_channel.deliver_consumed(list(line["steer_consumed"]))
                    continue
                event = self._line_to_event(line)
                if event is not None:
                    yield event
            await process.wait()
            if process.returncode not in (0, None):
                tail = await stderr_tail(process)
                if tail.strip():
                    logger.warning(f"[nexus_power] runner stderr tail: {tail}")
        finally:
            if steer_pump is not None:
                steer_pump.cancel()
                # Await the cancellation so the task settles here rather than
                # surfacing a "Task was destroyed but it is pending" warning.
                with contextlib.suppress(asyncio.CancelledError):
                    await steer_pump
                if not process.stdin.is_closing():
                    process.stdin.close()
            if process.returncode is None:
                _terminate_group(process.pid)
                await process.wait()

    async def _open_steer_transport(
        self, process: "asyncio.subprocess.Process", payload: dict[str, Any],
        steer_channel: Any, cancel: CancellationView,
    ) -> "asyncio.Task[None] | None":
        """Write the request line, then set up the steer transport and return
        its pump task (or None).

        A steerable run keeps stdin OPEN and a pump writes each pushed injection
        as a ``{"steer": …}`` line the runner reads. A non-steerable run closes
        stdin exactly as before — the runner's reader then hits EOF at once —
        so the default path is byte-for-byte unchanged. Extracted so this
        close-vs-keep-open decision is unit-tested without spawning a runner."""
        assert process.stdin is not None
        process.stdin.write(
            (json.dumps(payload, default=_json_default) + "\n").encode("utf-8")
        )
        await process.stdin.drain()
        if steer_channel is None:
            process.stdin.close()
            return None

        def _on_pump_done(t: "asyncio.Task[None]") -> None:
            if t.cancelled():
                return  # cancelled at turn end — expected, not an error
            exc = t.exception()
            if exc is not None:
                logger.warning(f"[nexus_power] steer pump died: {exc!r}")

        pump = asyncio.create_task(
            self._pump_steer_to_stdin(process, steer_channel, cancel)
        )
        pump.add_done_callback(_on_pump_done)
        return pump

    async def _pump_steer_to_stdin(
        self, process: "asyncio.subprocess.Process", steer_channel: Any,
        cancel: CancellationView,
    ) -> None:
        """Drain the run's SteerChannel and write each injection as a
        ``{"steer": …}`` line to the runner's stdin, until the turn ends
        (cancelled by the caller) or the pipe closes."""
        assert process.stdin is not None
        while not cancel.requested():
            try:
                msg = await asyncio.wait_for(steer_channel.queue.get(), timeout=_CANCEL_POLL_S)
            except asyncio.TimeoutError:
                continue
            try:
                process.stdin.write(
                    (json.dumps({"steer": msg}, default=_json_default) + "\n").encode("utf-8")
                )
                await process.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                return  # runner gone; the read loop's EOF handles the turn

    @staticmethod
    def _line_to_event(line: dict[str, Any]) -> dict[str, Any] | None:
        if "event" in line:
            return line["event"]
        exit_info = line.get("exit")
        if isinstance(exit_info, dict) and not exit_info.get("ok", True):
            trace = exit_info.get("traceback")
            if trace:
                logger.warning(f"[nexus_power] runner traceback:\n{trace}")
            raise RuntimeError(str(exit_info.get("error") or "nexus_power runner failed"))
        return None


def _resolve_provider() -> tuple[str, str, str, str, str]:
    """Pick this turn's provider config: (protocol, model, key, base_url, auth).

    NexusPower is protocol-agnostic by construction — it drives the
    provider API itself rather than shelling out to a CLI, so it works
    with either of the platform's two provider families. The resolver
    populates the config matching the bound provider, so we simply take
    whichever one carries a model: anthropic first (the platform's
    default family), then openai.
    """
    if claude_config.model:
        return (
            "anthropic",
            claude_config.model,
            claude_config.api_key or "",
            claude_config.base_url or "",
            claude_config.auth_type or "api_key",
        )
    if codex_config.model:
        # codex_config is the platform's carrier for "the agent slot on an
        # openai-protocol provider" — shared with codex_cli, never with
        # the helper slot (that is openai_config).
        return (
            "openai",
            codex_config.model,
            codex_config.api_key or "",
            codex_config.base_url or "",
            codex_config.auth_type or "api_key",
        )
    return ("anthropic", "", "", "", "api_key")


def _merge_llm_extra(llm_extra: dict[str, Any], params: dict[str, Any]) -> None:
    """Fold ``params`` into ``llm_extra``; dict-valued passthroughs
    (``extra_body``) merge key-wise like ``extra_headers`` so another
    producer's sub-keys survive."""
    for key, value in params.items():
        current = llm_extra.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            value = {**current, **value}
        llm_extra[key] = value


def _request_identity_params(
    protocol: str, base_url: str, agent_id: Any
) -> dict[str, Any]:
    """Per-agent request identifiers, in each protocol's own vocabulary.

    - anthropic: ``metadata.user_id`` (the Messages API's only metadata
      field). Passed as litellm's ``user``, which its anthropic route
      translates into ``metadata.user_id``.
    - openai: ``prompt_cache_key`` (cache-routing hint: same prefix + same
      key lands on the same cache host). Sent via ``extra_body`` because
      litellm 1.94's ``acompletion`` has no such parameter and silently
      drops it as a plain kwarg (measured 2026-09-11). ``extra_body`` also
      bypasses ``drop_params``, so it goes only to hosts known to accept
      it: our own gateway (NetMind upstream answers 200) and an explicit
      official OpenAI base_url (``is_official_provider``). Arbitrary
      OpenAI-compatible BYOK hosts may reject unknown fields.
      An EMPTY base_url is fail-closed: model_client._litellm_model then
      hands the model id to litellm's prefix routing (``groq/...``,
      ``mistral/...``), so the destination is not knowable here.

    No real agent id (missing or the placeholder) → nothing.
    """
    agent = str(agent_id or "")
    if not agent or agent == _PLACEHOLDER_AGENT_ID:
        return {}
    if protocol == "anthropic":
        return {"user": agent}
    if base_url and (
        _is_own_gateway_url(base_url) or is_official_provider("openai", base_url)
    ):
        return {"extra_body": {"prompt_cache_key": agent}}
    return {}


def _terminate_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _is_done(event: dict[str, Any]) -> bool:
    return (
        event.get("type") == TYPE_RAW_RESPONSE_EVENT
        and (event.get("data") or {}).get("type") == DATA_TYPE_DONE
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)
