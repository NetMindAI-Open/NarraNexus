"""
@file_name: browser_service.py
@author:
@date: 2026-09-22
@description: The single surface the UI and the agent use to reach the in-app browser.

Three callers need the same answer to "can we browse right now?",
and each degrades differently —

  * the agent's MCP tools get a ``NEEDS_HUMAN``-shaped refusal they can relay
    to the user over IM, never an exception and never a raw error string;
  * ``UrlRenderer``'s ``stream`` branch gets a status it turns into the
    install card instead of a blank canvas;
  * ``hook_data_gathering`` gets the same status to put in ContextData, so the
    agent knows before it tries.

Having one object answer all three is the point: three call sites computing
"is it installed" independently is how they drift, and the drift shows up as
an agent confidently telling the user it cannot browse while the panel next
to it is working.

Concurrency and idempotency of installing are NOT reimplemented here — they
live in ``InstallCoordinator`` and are delegated to, so there is exactly one
place that can start a download.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from narranexus.platform.browser._browser_impl.install import (
    Downloader,
    InstallCoordinator,
    InstallOutcome,
)
from narranexus.platform.browser._browser_impl.locate import locate_executable, probe_version
from narranexus.platform.browser._browser_impl.selection import locate_selected, locate_system_executable, read_mode, read_source
from narranexus.platform.browser._browser_impl.runtime import (
    BrowserRuntimeStatus,
    detect_runtime,
)

BROWSER_PROBE_INTERVAL = 60.0
BROWSER_PROBE_TIMEOUT = 5.0

#: What the user has to do, per runtime reason. Two distinct actions because
#: the advice differs: nothing installed → install; something installed but
#: unusable → re-install (the only clue the user gets about a broken unzip).
_ACTION_BY_REASON = {
    "no-executable": "install_browser",
    "probe-failed": "reinstall_browser",
    "installing": "wait_for_install",
}

_MESSAGE_BY_ACTION = {
    "install_browser": (
        "The in-app browser is not installed yet. Ask the user to install it "
        "from Settings → Browser, then retry."
    ),
    "reinstall_browser": (
        "The in-app browser is installed but will not start, so it is unusable. "
        "Ask the user to re-install it from Settings → Browser."
    ),
    "wait_for_install": (
        "The in-app browser is downloading right now. Tell the user it is in "
        "progress and retry once it finishes."
    ),
}


class BrowserService:
    """Runtime status, the agent-facing gate, and install delegation."""

    def __init__(
        self,
        *,
        locate: Optional[Callable[[], Optional[Path]]] = None,
        probe: Optional[Callable[[Path], Optional[str]]] = None,
        downloader: Optional[Downloader] = None,
    ) -> None:
        """
        Args:
            locate: Finds the executable. Defaults to the on-disk locator.
            probe: Proves it runs. Defaults to ``executable --version``.
            downloader: Fetches + unpacks. Required before ``install()`` can do
                anything; injected so the transport and the mirror policy stay
                out of this class.
        """
        self._selected_runtime = locate is None
        self._locate = locate or locate_selected
        self._probe = probe or probe_version
        self._installer = InstallCoordinator(
            downloader=downloader or _default_downloader(),
            is_ready=lambda: detect_runtime(
                locate=locate or locate_executable, probe=self._probe, installing=False,
            ).state == "ready",
        )
        self._sessions: dict[str, Any] = {}
        self._policies: dict[str, Any] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self._audit_tasks: set[asyncio.Task] = set()
        self._login_sessions: dict[str, tuple[Any, str]] = {}
        self._login_locks: dict[str, asyncio.Lock] = {}
        self._login_errors: dict[str, str] = {}
        self._login_requests: dict[str, str] = {}

    def _track_audit_task(self, task: asyncio.Task) -> asyncio.Task:
        self._audit_tasks.add(task)

        def finished(done: asyncio.Task) -> None:
            self._audit_tasks.discard(done)
            if not done.cancelled() and (exc := done.exception()) is not None:
                logger.error("Browser observation task failed: {}", type(exc).__name__)

        task.add_done_callback(finished)
        return task

    def _prepare_session_audit(self, agent_id: str, turn_id: str, thread_id: str, custom: Any):
        """Build one ordered durable sink and attach its lifetime to the session."""
        from narranexus.platform.services.service_audit import ServiceAuditor

        auditor = ServiceAuditor("browser")
        identity = {"agent_id": agent_id, "session_id": uuid.uuid4().hex}
        scope = {"turn_id": turn_id, "thread_id": thread_id}
        last_write: asyncio.Task | None = None
        write_failures = 0
        begin_close = None

        def emit(row: dict) -> None:
            nonlocal last_write
            if row["event"] == "closed" and begin_close is not None:
                begin_close()
                return
            previous = last_write
            detail = {**row, **identity}
            event = detail.pop("event")

            async def write() -> None:
                nonlocal write_failures
                if previous is not None:
                    await previous
                detail["audit_write_failures"] = write_failures
                try:
                    written = await auditor.event(event, detail)
                except Exception as exc:
                    logger.error("Browser audit raised for {}: {}", agent_id, type(exc).__name__)
                    written = False
                if not written:
                    write_failures += 1
                    logger.error("Browser audit write failed for {} ({})", agent_id, event)
                if custom is not None:
                    try:
                        result = custom({"event": event, **detail})
                        if inspect.isawaitable(result):
                            await result
                    except Exception as exc:
                        logger.error("Browser audit callback failed: {}", type(exc).__name__)

            last_write = self._track_audit_task(asyncio.create_task(write()))

        async def drain() -> None:
            if last_write is not None:
                try:
                    await asyncio.shield(last_write)
                except asyncio.CancelledError:
                    await last_write
                    raise

        async def failed(exc: BaseException) -> None:
            emit({"event": "error", "operation": "launch", "error_type": type(exc).__name__, **scope})
            emit({"event": "stopped", "reason": "launch_failed"})
            await drain()

        def attach(session: Any) -> None:
            nonlocal begin_close
            original_close = session.close
            closing: asyncio.Task | None = None
            probe: asyncio.Task | None = None
            last_control = None

            def control_changed(state: dict) -> None:
                nonlocal last_control
                current = (state["holder"], state["owner_connection_id"], state["agent_waiting"])
                if current != last_control:
                    last_control = current
                    emit({"event": "control", "holder": state["holder"],
                          "agent_waiting": state["agent_waiting"]})

            unsubscribe = session.control.subscribe(control_changed)

            async def finish() -> None:
                unsubscribe()
                if probe is not None:
                    probe.cancel()
                    await asyncio.gather(probe, return_exceptions=True)
                try:
                    await original_close()
                except BaseException as exc:
                    emit({"event": "error", "operation": "close", "error_type": type(exc).__name__})
                    raise
                finally:
                    emit({"event": "stopped", "frames": session.frames_delivered})
                    await drain()

            def start_close() -> asyncio.Task:
                nonlocal closing
                if closing is None:
                    closing = self._track_audit_task(asyncio.create_task(finish()))
                return closing

            async def close() -> None:
                task = start_close()
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise

            async def watch() -> None:
                count = 0
                while session.is_open:
                    count += 1
                    started = time.monotonic()
                    try:
                        # This read-only diagnostic does not take the action
                        # lock or cancel any user/agent work when it times out.
                        async with asyncio.timeout(BROWSER_PROBE_TIMEOUT):
                            result = await session._cdp.call("Runtime.evaluate", {
                                "expression": "1", "returnByValue": True,
                            }, timeout=BROWSER_PROBE_TIMEOUT)
                        if (result or {}).get("result", {}).get("value") != 1 or result.get("exceptionDetails"):
                            raise RuntimeError("invalid probe response")
                        emit({"event": "heartbeat", "responsive": True, "probe_count": count,
                              "latency_ms": round((time.monotonic() - started) * 1000, 1),
                              "frames": session.frames_delivered})
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        emit({"event": "error", "operation": "probe", "responsive": False,
                              "probe_count": count, "error_type": type(exc).__name__})
                    await asyncio.sleep(BROWSER_PROBE_INTERVAL)
                start_close()

            begin_close = start_close
            session.close = close
            emit({"event": "started", **scope})
            probe = self._track_audit_task(asyncio.create_task(watch()))

        return emit, attach, failed

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> BrowserRuntimeStatus:
        """Current runtime status. Recomputed every call, never cached.

        The user can delete a ~150 MB runtime between two agent turns; a
        cached ``ready`` would send the next one to a blank panel with no way
        to self-diagnose.
        """
        return detect_runtime(
            locate=self._locate,
            probe=self._probe,
            installing=self._installer.installing,
        )

    # ── the gate ─────────────────────────────────────────────────────────

    def require_ready(self) -> Optional[dict[str, Any]]:
        """None when browsing is possible; otherwise a refusal to relay.

        Returns a ``NEEDS_HUMAN`` envelope rather than raising, because the
        caller is an agent turn: a raised exception becomes an opaque failure
        the user cannot act on, whereas this shape carries the one thing that
        actually unblocks them — what to go and do.

        The refusal keeps the runtime *reason*: "not installed" and "installed
        but broken" need different advice, and collapsing them throws away
        the only clue the user has.
        """
        status = self.status()
        if status.state == "ready":
            return None

        action = _ACTION_BY_REASON.get(status.reason, "install_browser")
        if self._selected_runtime and read_source() == "system":
            return {
                "ok": False, "outcome": "NEEDS_HUMAN",
                "message": "Selected Google Chrome is unavailable. Open Settings > Browser to select a usable browser.",
                "human_action": {"action": "select_browser", "where": "settings.browser"},
            }
        return {
            "outcome": "NEEDS_HUMAN",
            "message": _MESSAGE_BY_ACTION[action],
            "human_action": {
                "action": action,
                "reason": status.reason,
                "where": "settings/browser",
            },
        }

    # ── install ──────────────────────────────────────────────────────────

    async def install(self) -> InstallOutcome:
        """Install the runtime if needed. Concurrency-safe; see the coordinator."""
        if self._selected_runtime and read_source() == "system":
            return InstallOutcome(ok=False, executable=None,
                                  error="Select the managed browser in Settings > Browser before installing it")
        return await self._installer.install()

    def cancel_install(self) -> None:
        """Ask an in-flight install to stop, keeping the partial download."""
        self._installer.cancel()

    @property
    def installing(self) -> bool:
        """Whether a runtime install is in flight."""
        return self._installer.installing

    @property
    def install_progress(self) -> Any:
        """The in-flight install's progress, or None when nothing is installing."""
        return self._installer.progress

    # ── login notices (cross-process) ────────────────────────────────────

    async def pending_notices(self, agent_id: str) -> list[dict]:
        """Open login / verification requests, raised in the MCP host and read here."""
        logins = await self._login_repository()
        return await logins.pending(agent_id)

    async def _login_repository(self) -> Any:
        from narranexus.platform.repository.browser_login_repository import BrowserLoginRepository
        from narranexus.platform.utils.db.db_factory import get_db_client

        return BrowserLoginRepository(await get_db_client())

    async def request_login(self, agent_id: str, *, reason: str, turn_id: str, thread_id: str) -> dict:
        """Publish a notice, wait for explicit handback, then inspect the current page.

        No deadline: the user may need time for login or verification. Completion
        means control was returned, never that authentication itself succeeded.
        """
        if not turn_id or not thread_id:
            raise ValueError("Login requests require the trusted caller scope")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A login request must explain why human action is needed")
        session = self.session_for(agent_id)
        if session is None:
            return {"outcome": "ERROR", "message": "No browser is open"}
        session.bind_scope(turn_id=turn_id, thread_id=thread_id)
        lock = self._login_locks.setdefault(agent_id, asyncio.Lock())
        row = None
        try:
            async with lock:
                if self.session_for(agent_id) is not session:
                    return {"outcome": "ERROR", "message": "Browser session changed; retry login"}
                # Record existing control before any storage await. Its stream
                # callback uses this lock, so handback during request creation
                # cannot overtake the notification's initial owner binding.
                owner = session.control.to_dict().get("owner_connection_id")
                binding = self._login_sessions.get(agent_id)
                new_session = binding is None or binding[0] is not session
                if new_session:
                    binding = (session, uuid.uuid4().hex)
                    self._login_sessions[agent_id] = binding
                repository = await self._login_repository()
                if new_session:
                    await repository.clear_for_agent(agent_id)
                row = await repository.request(agent_id=agent_id, session_key=binding[1],
                                               reason=reason, turn_id=turn_id, thread_id=thread_id)
                self._login_requests[row["request_id"]] = binding[1]
                if owner:
                    await repository.control_changed(agent_id, binding[1], owner, "take")
            while self.session_for(agent_id) is session:
                if row["request_id"] in self._login_errors:
                    return {"outcome": "ERROR", "message": self._login_errors[row["request_id"]]}
                current = await repository.get_by_id(row["request_id"])
                if current is None:
                    return {"outcome": "ERROR", "message": "Login request is no longer active"}
                if current["state"] == "completed":
                    verified = await session.read_page()
                    return {**verified, "login_request_id": row["request_id"],
                            "handoff": "completed", "login_state": "unverified"}
                await asyncio.sleep(0.2)
            return {"outcome": "ERROR", "message": "Browser session closed or changed during login"}
        finally:
            if row is not None:
                self._login_errors.pop(row["request_id"], None)
                self._login_requests.pop(row["request_id"], None)
                await repository.retire(row["request_id"])

    async def login_control_changed(self, agent_id: str, session: Any, connection_id: str, event: str) -> None:
        """Called by the authenticated stream after an actual control transition."""
        async with self._login_locks.setdefault(agent_id, asyncio.Lock()):
            binding = self._login_sessions.get(agent_id)
            if binding is None or binding[0] is not session:
                return
            if binding[1] not in self._login_requests.values():
                return
            try:
                repository = await self._login_repository()
                await repository.control_changed(agent_id, binding[1], connection_id, event)
            except Exception:
                # Wake each tool with an error even if the control transition
                # succeeded; a failed receipt must never strand a waiter.
                for request_id, session_key in self._login_requests.items():
                    if session_key == binding[1]:
                        self._login_errors[request_id] = "Could not record login handoff; retry browser_request_login"
                raise

    # ── permissions ──────────────────────────────────────────────────────

    async def policy_view(self, agent_id: str) -> dict:
        from narranexus.platform.browser._browser_impl.policy_store import PolicyStore
        from narranexus.platform.utils.db.db_factory import get_db_client

        return await PolicyStore(await get_db_client()).get_public(agent_id)

    async def set_policy_rule(self, agent_id: str, *, origin: str, capability: str, verdict: str) -> dict:
        from narranexus.platform.browser._browser_impl.policy_store import PolicyStore, managed_origin
        from narranexus.platform.services.service_audit import ServiceAuditor
        from narranexus.platform.utils.db.db_factory import get_db_client

        view = await PolicyStore(await get_db_client()).set_rule(
            agent_id, origin=origin, capability=capability, verdict=verdict,
        )
        self._policies.pop(agent_id, None)
        if not await ServiceAuditor("browser").event("policy_updated", {
            "agent_id": agent_id, "origin": managed_origin(origin), "capability": capability, "verdict": verdict,
        }):
            logger.error("Browser policy audit write failed for {}", agent_id)
        return view

    async def policy_for(self, agent_id: str, *, fresh: bool = False) -> Any:
        """The agent's permissions.

        ``fresh=True`` re-reads from the document. The session runs in the MCP
        host while Settings writes the policy in the backend, so a cached
        object on either side cannot see the other's change: the owner revokes
        script access and the agent keeps it.

        The cache is kept only as a same-process convenience; every decision
        that matters asks for a fresh read.
        """

        from narranexus.platform.browser._browser_impl.policy import BrowserPolicy

        cached = self._policies.get(agent_id)
        if cached is not None and not fresh:
            return cached

        from narranexus.platform.repository.browser_policy_repository import BrowserPolicyRepository
        from narranexus.platform.utils.db.db_factory import get_db_client

        db = await get_db_client()
        row = await db.get_one(BrowserPolicyRepository.table_name, {"agent_id": agent_id})
        stored = json.loads(row["policy_json"]) if row else None

        policy = BrowserPolicy.from_dict(stored) if stored else BrowserPolicy()
        self._policies[agent_id] = policy
        return policy

    # ── live sessions ────────────────────────────────────────────────────

    def register_session(self, agent_id: str, session: Any) -> None:
        """Make an open browser reachable by this agent's tools and its panel.

        One session per agent, deliberately. Two live browsers for the same
        agent would give the user two panels with no way to tell which one the
        agent is talking about — and the second would silently inherit the
        first's login state anyway.
        """
        self._sessions[agent_id] = session

    def forget_session(self, agent_id: str) -> None:
        """Drop the registration without closing it (the caller owns its lifetime)."""
        self._sessions.pop(agent_id, None)

    def session_for(self, agent_id: str) -> Optional[Any]:
        """The agent's open browser, or None.

        A dead session is hidden from callers but retained for cleanup before
        its replacement launches and acquires the same profile.
        """
        session = self._sessions.get(agent_id)
        if session is None:
            return None
        if not getattr(session, "is_open", True):
            return None
        return session

    async def open_session(self, agent_id: str, **kwargs: Any) -> tuple[Optional[Any], Optional[dict]]:
        async with self._session_locks.setdefault(agent_id, asyncio.Lock()):
            if self._closing:
                return None, {"ok": False, "outcome": "ERROR", "message": "Browser service is shutting down"}
            return await self._open_session(agent_id, **kwargs)

    async def _open_session(
        self,
        agent_id: str,
        *,
        policy: Optional[Any] = None,
        turn_id: str = "",
        thread_id: str = "",
        audit: Optional[Callable[[dict], None]] = None,
        headless: bool | None = None,
    ) -> tuple[Optional[Any], Optional[dict]]:
        """Get (or start) this agent's browser. Returns ``(session, refusal)``.

        Exactly one of the two is set. The refusal is the ``NEEDS_HUMAN`` /
        ``ERROR`` envelope the caller relays — a launch failure has to reach
        the user as something they can act on, not as a traceback in a log
        they will never read.

        Starting is idempotent per agent: an existing open session is reused
        rather than adding a second browser the user has no way to tell apart.
        """
        existing = self.session_for(agent_id)
        if existing is not None:
            existing.bind_scope(turn_id=turn_id, thread_id=thread_id)
            return existing, None

        if headless is None:
            try:
                headless = read_mode() == "headless"
            except (OSError, ValueError):
                return None, {"ok": False, "outcome": "ERROR",
                              "message": "Could not read browser mode preference. Check browser settings."}

        dead = self._sessions.pop(agent_id, None)
        if dead is not None:
            await dead.close()

        refusal = self.require_ready()
        if refusal is not None:
            return None, refusal

        from narranexus.platform.browser._browser_impl.locate import install_root
        from narranexus.platform.browser._browser_impl.runtime_launch import (
            LaunchError,
            launch_session,
        )

        status = self.status()
        if status.executable is None:  # pragma: no cover - require_ready covers it
            return None, self.require_ready()

        # An explicit policy stays authoritative. Otherwise privileged script
        # operations load current settings; ordinary browsing needs no policy.
        explicit = policy is not None
        from narranexus.platform.browser._browser_impl.policy import BrowserPolicy

        live_policy = policy if explicit else BrowserPolicy()

        audit_sink, attach_audit, audit_failed = self._prepare_session_audit(agent_id, turn_id, thread_id, audit)
        try:
            session = await launch_session(
                executable=status.executable,
                root=install_root(),
                agent_id=agent_id,
                # Different Chrome releases must not downgrade each other's
                # profile. Neither choice uses the user's personal profile.
                profile="system" if status.executable == locate_system_executable() else "default",
                policy=live_policy,
                turn_id=turn_id,
                thread_id=thread_id,
                audit=audit_sink,
                # Both modes stream inside the app. A native window is an
                # explicit local preference, using the same login profile.
                headless=headless,
                policy_provider=(
                    None if explicit else (lambda: self.policy_for(agent_id, fresh=True))
                ),
            )
        except LaunchError as exc:
            await audit_failed(exc)
            return None, {
                "ok": False,
                "outcome": "ERROR",
                "message": f"The browser is installed but would not start: {exc}",
            }
        except BaseException as exc:
            await audit_failed(exc)
            raise

        attach_audit(session)
        self.register_session(agent_id, session)
        return session, None

    async def close_session(self, agent_id: str) -> None:
        """Close and forget the agent's browser, if it has one."""
        async with self._session_locks.setdefault(agent_id, asyncio.Lock()):
            session = self._sessions.pop(agent_id, None)
            if session is not None:
                await session.close()
            if agent_id in self._login_sessions:
                repository = await self._login_repository()
                await repository.clear_for_agent(agent_id)
                self._login_sessions.pop(agent_id, None)

    async def close(self) -> None:
        """Drain sessions during host shutdown without orphaning a launch."""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close_sessions())
            self._close_task.add_done_callback(lambda task: task.cancelled() or task.exception())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await self._close_task
            raise

    async def _close_sessions(self) -> None:
        agents = self._session_locks.keys() | self._sessions.keys()
        results = await asyncio.gather(*(self.close_session(agent_id) for agent_id in agents), return_exceptions=True)
        while self._audit_tasks:
            await asyncio.gather(*list(self._audit_tasks), return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise BaseExceptionGroup("Browser session cleanup failed", errors)


def _default_downloader() -> Downloader:
    """The real downloader, rooted at the user's install dir.

    Built lazily rather than imported at module scope so that constructing a
    ``BrowserService`` never reaches for the network layer — the status query
    runs on every agent turn and must stay free.
    """
    from narranexus.platform.browser._browser_impl.downloader import ChromiumDownloader
    from narranexus.platform.browser._browser_impl.locate import install_root

    return ChromiumDownloader(root=install_root())


#: One service per process. The MCP tools open sessions on it and the frame
#: route reads them from it; two instances would mean the panel looking for a
#: session the tools put somewhere else.
_shared: Optional[BrowserService] = None


def get_shared_service() -> BrowserService:
    global _shared
    if _shared is None:
        _shared = BrowserService()
    return _shared
