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
  2. run the turn in its OWN PROCESS by default (the runner; in-process
     mode via ``NEXUS_POWER_INPROCESS=1`` for the executor container and
     tests) and relay its NDJSON — with manual line buffering, never a
     line-length assumption;
  3. guarantee the legacy stream ends with exactly one
     ``response.done`` on every path (the billing chain's sole source).

Platform adaptation seams accepted via **kwargs (forward-compatible,
optional): ``expressive_tools``, ``marker_tools``, ``expandables``,
``initial_expansions``, ``agent_id``.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from typing import Any, AsyncGenerator

from loguru import logger

from xyz_agent_context.agent_framework.api_config import claude_config, codex_config
from xyz_agent_context.agent_framework.loop.cancellation_view import CancellationView
from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    TYPE_RAW_RESPONSE_EVENT,
    raw_error_event,
)
from xyz_agent_context.utils.logging import timed

_STREAM_LIMIT_BYTES = 32 * 1024 * 1024  # image-bearing lines reach 100s of KB
_CANCEL_POLL_S = 0.2


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
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "xyz_agent_context.agent_framework.nexus_power.runner",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT_BYTES,
            start_new_session=True,
            env=env,
        )

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
        # Start warming immediately so even the process's FIRST turn
        # overlaps imports with request preparation (no-op when pooling
        # is disabled or no event loop is running yet).
        if os.getenv("NEXUS_POWER_INPROCESS") != "1":
            pool = _WarmRunnerPool.shared()
            if pool.enabled:
                try:
                    asyncio.get_running_loop()
                    pool.schedule_refill()
                except RuntimeError:
                    pass

    def capabilities(self) -> set[str]:
        """Shipped beyond the base contract: the two-track event log
        (local NDJSON truth file per turn)."""
        return {"event_log"}

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
            if os.getenv("NEXUS_POWER_INPROCESS") == "1":
                events = self._run_inprocess(request_payload, cancel)
            else:
                events = self._run_subprocess(request_payload, cancel)
            async for event in events:
                if _is_done(event):
                    done_seen = True
                yield event
        except Exception as exc:  # noqa: BLE001 - classified for the wire
            from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
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
        if auth_type in ("oauth", "oauth_token"):
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
        # get_expressive_tools → TurnInput → here). No guessing from
        # server names: a rename must never silently mute the agent, and
        # channel reply tools (lark_cli & co.) are part of the surface too.
        expressive = tuple(kwargs.get("expressive_tools") or ())
        llm_extra: dict[str, Any] = {}
        if protocol == "anthropic" and auth_type == "bearer_token" and api_key:
            # Anthropic-protocol gateways expecting Authorization: Bearer
            # (litellm's anthropic route sends x-api-key; add the header).
            llm_extra["extra_headers"] = {"Authorization": f"Bearer {api_key}"}
        options: dict[str, Any] = {
            "cwd": self.working_path,
            "agent_id": str(kwargs.get("agent_id") or "agent"),
            "env": dict(extra_env or {}),
            "model": model,
            "provider": protocol,
            "api_key": api_key,
            "base_url": base_url,
            "llm_extra": llm_extra,
            "thinking": bool(getattr(claude_config, "thinking", "") == "enabled"),
            "mcp_servers": mcp_servers,
            "disallowed_tools": tuple(kwargs.get("disallowed_tools") or ()),
            "expressive_tools": expressive,
            "marker_tools": tuple(kwargs.get("marker_tools") or ()),
            "expandables": tuple(kwargs.get("expandables") or ()),
            "initial_expansions": sorted(kwargs.get("initial_expansions") or ()),
            "output_mode": "legacy_dict",
        }
        return {
            "thread_id": f"turn_{uuid.uuid4().hex[:12]}",
            "messages": messages,
            "options": options,
        }

    async def _run_inprocess(
        self, payload: dict[str, Any], cancel: CancellationView
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Same code path as the runner, minus the process boundary
        (executor containers and tests — already isolated)."""
        from xyz_agent_context.agent_framework.nexus_power.runner import serve_turn

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def write_line(obj: dict[str, Any]) -> None:
            await queue.put(obj)

        async def _serve() -> None:
            try:
                await serve_turn(json.dumps(payload, default=_json_default), write_line)
            finally:
                await queue.put(None)

        # Bridge the platform token into the runner's cancellation view.
        class _Bridge:
            def set(self) -> None:  # runner's signal-handler hook
                return None

            def requested(self) -> bool:
                return cancel.requested()

        import xyz_agent_context.agent_framework.nexus_power.runner as runner_module

        original = runner_module._SignalCancellation
        runner_module._SignalCancellation = lambda: _Bridge()  # type: ignore[assignment]
        try:
            task = asyncio.create_task(_serve())
            while True:
                line = await queue.get()
                if line is None:
                    break
                event = self._line_to_event(line)
                if event is not None:
                    yield event
            await task
        finally:
            runner_module._SignalCancellation = original  # type: ignore[assignment]

    async def _run_subprocess(
        self, payload: dict[str, Any], cancel: CancellationView
    ) -> AsyncGenerator[dict[str, Any], None]:
        pool = _WarmRunnerPool.shared()
        if pool.enabled:
            process = await pool.acquire()
        else:
            process = await pool.spawn(prewarm=False)
        assert process.stdin and process.stdout
        process.stdin.write(
            (json.dumps(payload, default=_json_default) + "\n").encode("utf-8")
        )
        await process.stdin.drain()
        process.stdin.close()

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
                event = self._line_to_event(line)
                if event is not None:
                    yield event
            await process.wait()
            if process.returncode not in (0, None) and process.stderr:
                stderr_tail = (await process.stderr.read())[-2000:].decode(
                    "utf-8", errors="replace"
                )
                if stderr_tail.strip():
                    logger.warning(f"[nexus_power] runner stderr tail: {stderr_tail}")
        finally:
            if process.returncode is None:
                _terminate_group(process.pid)
                await process.wait()

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
