"""
@file_name: runtime_launch.py
@author:
@date: 2026-09-22
@description: Start a browser process and hand back a session the agent can drive.

The glue between "a binary on disk" and "a BrowserSession". Small, but it owns
the one thing child processes reliably get wrong:

**Nothing may be orphaned.** Every exit path — a failed CDP connect, a normal
close, a browser that ignores SIGTERM — ends with the process gone. An orphaned
Chromium holds its debugging port and its profile lock, so the *next* launch
fails with a message pointing nowhere near the actual leak. (This exact shape
bit during development of this feature: killing a Tauri parent left its Python
sidecars holding :8000, and the next start reported a port conflict.)

The three IO seams — spawn, endpoint discovery, socket connect — are injected
so the lifecycle above can be tested without a 190 MB browser, and so a future
backend that gets its CDP endpoint another way (a container, a remote grid)
reuses this assembly rather than copying it.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from narranexus.platform.browser._browser_impl.cdp import CdpSession
from narranexus.platform.browser._browser_impl.launcher import (
    build_launch_args,
    pick_profile_dir,
    wait_for_endpoint,
)
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
from narranexus.platform.browser._browser_impl.session import BrowserSession

#: How long a browser gets to exit politely before it is killed.
TERMINATE_GRACE_SEC = 5.0
STDERR_TAIL_BYTES = 16 * 1024
STDERR_DRAIN_GRACE_SEC = 0.5


class LaunchError(RuntimeError):
    """The browser could not be started, or could not be driven once started."""


class _StderrTail:
    """Continuously drain the child pipe while retaining only bounded diagnostics."""

    def __init__(self, process: Any) -> None:
        self._stream = getattr(process, "stderr", None)
        self._tail = bytearray()
        self._task = asyncio.create_task(self._read())
        self._task.add_done_callback(lambda task: task.cancelled() or task.exception())

    async def _read(self) -> None:
        if self._stream is None:
            return
        try:
            while chunk := await self._stream.read(4096):
                self._tail.extend(chunk)
                del self._tail[:-STDERR_TAIL_BYTES]
        except Exception:
            logger.exception("could not read browser stderr")

    async def finish(self) -> None:
        """Drain after process exit; inherited descendant pipes cannot stall close."""
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=STDERR_DRAIN_GRACE_SEC)
        except asyncio.TimeoutError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def diagnostic(self) -> str:
        value = self._tail.decode("utf-8", errors="replace").strip()
        return f"\nChromium stderr (last {STDERR_TAIL_BYTES} bytes):\n{value}" if value else ""


async def _during_startup(process: Any, operation: Awaitable[None]) -> None:
    """Stop discovery/setup promptly if Chromium has already exited."""
    async def wait_for_exit() -> None:
        # Process.wait can depend on pipe EOF. A descendant inheriting stderr
        # must not hide the owned process's exit from startup diagnostics.
        while process.returncode is None:
            await asyncio.sleep(0.05)

    starting = asyncio.create_task(operation)
    exited = asyncio.create_task(wait_for_exit())
    try:
        await asyncio.wait((starting, exited), return_when=asyncio.FIRST_COMPLETED)
        if process.returncode is not None:
            raise LaunchError(f"browser exited during startup (exit code {process.returncode})")
        await starting
    finally:
        for task in (starting, exited):
            task.cancel()
        await asyncio.gather(starting, exited, return_exceptions=True)


def free_port() -> int:
    """An unused loopback port.

    Bound and released rather than picked from a range: two agents opening a
    browser at the same moment must not be handed the same number, and a
    fixed port makes that a certainty rather than a race.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _default_spawn(argv: list[str]) -> Any:
    return await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )


async def _default_discover(*, port: int) -> str:
    """Discover the browser transport, whose lifetime outlives individual tabs."""
    import httpx

    async def probe() -> str:
        # The owned loopback endpoint must not traverse a user's HTTP proxy.
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            res = await client.get(f"http://127.0.0.1:{port}/json/version")
            res.raise_for_status()
            target = res.json()
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise ConnectionError("browser did not expose its debugging websocket")
        return str(ws_url)

    return await wait_for_endpoint(probe=probe, timeout=30.0, interval=0.25)


async def _default_connect(ws_url: str) -> Any:
    import websockets

    return await websockets.connect(ws_url, max_size=None, proxy=None)


async def launch_session(
    *,
    executable: Path,
    root: Path,
    agent_id: str,
    policy: BrowserPolicy,
    profile: str = "default",
    turn_id: str = "",
    thread_id: str = "",
    audit: Optional[Callable[[dict], None]] = None,
    policy_provider: Optional[Callable[[], Any]] = None,
    headless: bool = False,
    viewport: tuple[int, int] = (1280, 800),
    spawn: Optional[Callable[[list[str]], Awaitable[Any]]] = None,
    discover: Optional[Callable[..., Awaitable[str]]] = None,
    connect: Optional[Callable[[str], Awaitable[Any]]] = None,
) -> BrowserSession:
    """Start a browser and wrap it in a session.

    Raises:
        LaunchError: with the underlying reason. The caller is an agent tool,
            which turns this into an outcome the user can act on — a traceback
            would not be one.
    """
    spawn = spawn or _default_spawn
    discover = discover or _default_discover
    connect = connect or _default_connect

    port = free_port()
    profile_dir = pick_profile_dir(root=root, profile=profile, agent_id=agent_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    argv = build_launch_args(
        executable=executable, port=port, profile=profile_dir, headless=headless
    )

    spawning = asyncio.create_task(spawn(argv))
    try:
        process = await asyncio.shield(spawning)
    except asyncio.CancelledError:
        try:
            process = await spawning
        except Exception:
            logger.exception("browser spawn failed while launch was cancelled")
        else:
            stderr = _StderrTail(process)
            try:
                await _stop_process(process)
            finally:
                await stderr.finish()
        raise
    except Exception as exc:
        raise LaunchError(f"could not start the browser: {exc}") from exc

    stderr = _StderrTail(process)
    cdp = None
    session = None

    async def initialize() -> None:
        nonlocal cdp, session
        ws_url = await discover(port=port)
        socket_ = await connect(ws_url)
        cdp = CdpSession(socket=socket_)
        await cdp.start()
        # No download tool currently enforces origin-specific download grants.
        # Deny filesystem downloads for the whole browser rather than expose
        # a permission setting that scripts could silently bypass.
        await cdp.call("Browser.setDownloadBehavior", {"behavior": "deny"})
        target = await cdp.call("Target.createTarget", {"url": "about:blank"})
        attached = await cdp.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        page = cdp.channel(attached["sessionId"])
        if headless:
            await page.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": viewport[0],
                    "height": viewport[1],
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
        session = BrowserSession(
            cdp=page, browser=cdp, page_id=target["targetId"], policy=policy,
            audit=audit or (lambda _row: None), turn_id=turn_id, thread_id=thread_id,
            policy_provider=policy_provider,
        )
        await session.pages.start()

    try:
        await _during_startup(process, initialize())
    except BaseException as exc:
        try:
            if session is not None:
                await session.close()
            elif cdp is not None:
                await cdp.close()
        finally:
            try:
                await _stop_process(process)
            finally:
                await stderr.finish()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise LaunchError(f"browser started but could not be driven: {exc}{stderr.diagnostic()}") from exc

    assert session is not None and cdp is not None
    _attach_process(session, process, cdp, stderr)
    logger.info("browser session up for {} on port {}", agent_id, port)
    return session


def _attach_process(session: BrowserSession, process: Any, cdp: CdpSession, stderr: _StderrTail) -> None:
    """Make ``session.close()`` also stop the browser.

    Wrapping rather than threading the process through ``BrowserSession``:
    that class is about policy, control and frames, and should not grow a
    second job just because today's backend happens to be a local process.
    """
    original_close = session.close
    closing: asyncio.Task | None = None

    async def cleanup() -> None:
        try:
            session.control.close()
            await session.pages.stop_tracking()
            if cdp.is_open and process.returncode is None:
                try:
                    async with asyncio.timeout(TERMINATE_GRACE_SEC):
                        try:
                            await cdp.call("Browser.close")
                        except ConnectionError:
                            # Chromium may close CDP before replying, while
                            # still flushing the profile in its main process.
                            pass
                        await process.wait()
                except Exception as exc:
                    logger.warning("graceful browser shutdown failed; stopping owned process: {}", exc)
        finally:
            try:
                await original_close()
            finally:
                try:
                    await _stop_process(process)
                finally:
                    await stderr.finish()

    async def close_and_stop() -> None:
        nonlocal closing
        if closing is None:
            closing = asyncio.create_task(cleanup())
            closing.add_done_callback(lambda task: task.cancelled() or task.exception())
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            await closing
            raise
        finally:
            if monitor is not asyncio.current_task():
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)

    async def watch_transport() -> None:
        try:
            await cdp.wait_closed()
            await close_and_stop()
        except Exception:
            logger.exception("browser disconnected during process cleanup")

    monitor = asyncio.create_task(watch_transport())
    monitor.add_done_callback(lambda task: task.cancelled() or task.exception())
    session.close = close_and_stop  # type: ignore[method-assign]


async def _stop_process(process: Any) -> None:
    """Terminate, then kill if it will not go. Never raises."""
    try:
        if getattr(process, "returncode", None) is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SEC)
        except asyncio.TimeoutError:
            # A hung renderer must not keep the port and the profile lock.
            logger.warning("browser ignored terminate; killing it")
            process.kill()
            # Reap best-effort. A process that ignored SIGKILL is the OS's
            # problem, and blocking `close()` on it would hang the caller for
            # a second grace period to learn nothing.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=0.5)
    except ProcessLookupError:
        pass  # already gone
    except Exception:
        logger.exception("failed to stop the browser process")
