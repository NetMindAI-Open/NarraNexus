"""
@file_name: install.py
@author:
@date: 2026-09-22
@description: Coordinate the one-time, user-triggered browser runtime install.

The Chromium runtime is not shipped in the dmg (Owner decision 2026-09-22);
the user installs it from the UI. This module owns the part of that flow that
is easy to get subtly wrong:

**One download, no matter how many askers.** Two browser tabs, or a tab plus
an agent turn that hit the "not installed" gate at the same moment, must not
each start a ~150 MB download. Callers past the first attach to the in-flight
install and get the same outcome.

**Idempotent.** Pressing install on a working runtime is a no-op, not another
download.

**Nothing here may propagate.** A dead CDN, a cancelled install and a buggy
progress listener all become reportable state — the caller is an agent turn or
a render request, and neither should die because a download did.

The actual bytes-on-the-wire work is injected as ``downloader`` so this
coordinator is testable without network, and so swapping the source (vendor
CDN, a mirror for users who cannot reach it, a preseeded local archive) does
not touch the concurrency logic.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import shlex
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal, Optional, Protocol

from loguru import logger

#: Coarse phase, for a progress label the user can understand.
InstallPhase = Literal["downloading", "extracting", "verifying"]


@dataclass(frozen=True)
class InstallProgress:
    """A progress sample. ``bytes_total`` is optional on purpose."""

    phase: InstallPhase
    bytes_done: int
    bytes_total: Optional[int]

    @property
    def percent(self) -> Optional[int]:
        """0-100, or None when the total is unknown.

        Some CDNs answer without ``Content-Length``. Inventing a percentage
        from an unknown total lies to the user; None lets the UI render an
        indeterminate bar instead.
        """
        if not self.bytes_total or self.bytes_total <= 0:
            return None
        pct = int(self.bytes_done * 100 // self.bytes_total)
        return max(0, min(100, pct))


@dataclass(frozen=True)
class InstallOutcome:
    """Terminal result of one install attempt."""

    ok: bool
    executable: Optional[Path]
    error: Optional[str]


class Downloader(Protocol):
    """Fetches + unpacks the runtime. Injected; see module docstring."""

    def __call__(
        self,
        *,
        on_progress: Callable[[InstallProgress], None],
        cancelled: Callable[[], bool],
    ) -> Awaitable[InstallOutcome]: ...


class InstallCoordinator:
    """Serialises install attempts and exposes progress to the UI."""

    def __init__(
        self,
        *,
        downloader: Downloader,
        is_ready: Callable[[], bool],
        on_progress: Optional[Callable[[InstallProgress], None]] = None,
    ) -> None:
        self.downloader = downloader
        self._is_ready = is_ready
        self._on_progress = on_progress
        self._task: Optional[asyncio.Task[InstallOutcome]] = None
        self._cancelled = False
        self._progress: Optional[InstallProgress] = None

    # ── observable state ─────────────────────────────────────────────────

    @property
    def installing(self) -> bool:
        """True only while a download is actually in flight.

        Read by ``detect_runtime`` to distinguish `installing` from `absent`;
        it must go false on every exit path, including failure, or the UI
        parks on a progress bar forever.
        """
        return ((self._task is not None and not self._task.done())
                or bool(getattr(self.downloader, "installing", False)))

    @property
    def progress(self) -> Optional[InstallProgress]:
        """Latest sample, or None when nothing is running."""
        return self._progress or getattr(self.downloader, "progress", None)

    def cancel(self) -> None:
        """Ask the in-flight download to stop.

        Cooperative: the flag is polled by the downloader between chunks
        rather than killing it mid-write, so a partial file is always left in
        a state the next attempt can resume from or discard cleanly.
        """
        self._cancelled = self._task is not None and not self._task.done()
        cancel = getattr(self.downloader, "cancel", None)
        if cancel is not None:
            cancel()

    # ── the one entry point ──────────────────────────────────────────────

    async def install(self) -> InstallOutcome:
        """Install if needed. Safe to call concurrently and repeatedly."""
        if self._task is not None and not self._task.done():
            # Attach to the running attempt instead of starting a second one.
            assert self._task is not None
            return await asyncio.shield(self._task)

        try:
            if self._is_ready():
                self._cancelled = False
                return InstallOutcome(ok=True, executable=None, error=None)
        except Exception as exc:
            return InstallOutcome(ok=False, executable=None, error=str(exc))
        self._task = asyncio.create_task(self._run())
        return await asyncio.shield(self._task)

    async def _run(self) -> InstallOutcome:
        def report(sample: InstallProgress) -> None:
            self._progress = sample
            if self._on_progress is None:
                return
            try:
                self._on_progress(sample)
            except Exception:
                # A UI subscriber that blew up must not cost the user their
                # download. Logged, not raised, and not silenced either.
                logger.exception("browser install: progress listener raised")

        try:
            return await self.downloader(on_progress=report, cancelled=lambda: self._cancelled)
        except Exception as exc:
            logger.exception("browser install failed")
            return InstallOutcome(ok=False, executable=None, error=str(exc))
        finally:
            # The attempt owns state, not an HTTP waiter that can disconnect.
            self._cancelled = False
            self._progress = None


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _InstallState:
    """OS-owned lock plus atomic status shared by backend and MCP processes.

    The lock file is never unlinked: replacing its inode would let two
    processes own different locks. Kernel release on exit avoids stale PID
    files wedging install after a crash.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock_path = root / ".install.lock"
        self.state_path = root / ".install.json"
        self.cancel_path = root / ".install-cancel.json"
        self.handle = None

    def acquire(self) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            import errno
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is not None:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None

    @property
    def active(self) -> bool:
        if self.handle is not None:
            return True
        if not self.lock_path.exists():
            return False
        observer = _InstallState(self.root)
        try:
            if not observer.acquire():
                return True
            observer.release()
            return False
        except OSError:
            return False

    def read(self) -> dict:
        try:
            value = json.loads(self.state_path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def cancel(self) -> None:
        if self.active:
            token = self.read().get("token")
            if token:
                _atomic_json(self.cancel_path, {"token": token})

    def cancelled(self, token: str) -> bool:
        try:
            return json.loads(self.cancel_path.read_text()).get("token") == token
        except (OSError, ValueError, AttributeError):
            return False


def manual_install_help(
    *, root: Optional[Path] = None, manifest_url: Optional[str] = None,
    download_host: Optional[str] = None,
) -> dict:
    """Build copyable recovery commands for this interpreter and install root.

    Mirror arguments override the process environment for this command only.
    No network, writes, guessed public mirrors, or system Python dependency.
    """
    from narranexus.platform.browser._browser_impl.downloader import (
        CFT_MANIFEST_URL, DOWNLOAD_HOST_ENV, MANIFEST_URL_ENV,
    )
    from narranexus.platform.browser._browser_impl.locate import install_root

    root = (root if root is not None else install_root()).expanduser()
    if not root.is_absolute():
        root = Path.home() / root
    manifest = (manifest_url or os.environ.get(MANIFEST_URL_ENV) or CFT_MANIFEST_URL).strip()
    host = (download_host if download_host is not None else os.environ.get(DOWNLOAD_HOST_ENV, "")).strip()
    # Pin an empty host too: a copied command must not pick up a different
    # mirror from the user's terminal environment.
    options = ["--root", str(root), "--manifest-url", manifest, "--download-host", host]
    prefix = [sys.executable, "-m", "narranexus.platform.browser"]
    windows = sys.platform == "win32"

    def command(arguments: list[str]) -> str:
        if windows:
            return "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)
        return shlex.join(arguments)

    argv = [*prefix, "install", *options]
    return {
        "root": str(root), "shell": "powershell" if windows else "posix",
        "argv": argv, "command": command(argv),
        "status_command": command([*prefix, "status", *options]),
        "cancel_command": command([*prefix, "cancel", *options]),
        "manifest_url": manifest, "download_host": host or None,
        "mirror_flags": {"manifest_url": "--manifest-url", "download_host": "--download-host"},
    }


def main(argv: Optional[list[str]] = None) -> int:
    """CLI used by both checkout Python and the relocatable desktop Python."""
    from narranexus.platform.browser._browser_impl.download import validate_https_url
    from narranexus.platform.browser._browser_impl.downloader import ChromiumDownloader
    from narranexus.platform.browser._browser_impl.locate import locate_executable, probe_version
    from narranexus.platform.browser._browser_impl.runtime import detect_runtime

    parser = argparse.ArgumentParser(description="Install or inspect NarraNexus's optional browser runtime.")
    parser.add_argument("action", choices=("install", "status", "cancel"))
    parser.add_argument("--root", type=Path, help="Runtime root; relative paths use the user's home.")
    parser.add_argument("--manifest-url", help="Complete HTTPS Chrome for Testing manifest URL. "
                        "Fully trusted: it chooses the version that is installed.")
    parser.add_argument("--download-host", help="HTTPS archive mirror base; vendor paths are retained. "
                        "Fully trusted: the downloaded browser is executed, and its only check is "
                        "the --version probe (no published per-file digests).")
    args = parser.parse_args(argv)
    help_info = manual_install_help(root=args.root, manifest_url=args.manifest_url,
                                    download_host=args.download_host)
    root = Path(help_info["root"])
    downloader = ChromiumDownloader(root=root, host=help_info["download_host"] or "",
                                    manifest_url=help_info["manifest_url"])

    def emit(record: dict) -> None:
        print(json.dumps(record, ensure_ascii=True), flush=True)

    def status() -> dict:
        return detect_runtime(locate=lambda: locate_executable(root=root), probe=probe_version,
                              installing=downloader.installing).to_dict()

    try:
        if args.action == "status":
            progress = downloader.progress
            emit({"type": "status", "status": status(), "progress": asdict(progress) if progress else None,
                  "manual_install": help_info})
            return 0
        if args.action == "cancel":
            installing = downloader.installing
            downloader.cancel()
            emit({"type": "cancel", "ok": True, "installing": installing})
            return 0

        validate_https_url(help_info["manifest_url"])
        if help_info["download_host"]:
            validate_https_url(help_info["download_host"])

        async def install() -> InstallOutcome:
            return await downloader(on_progress=lambda sample: emit({"type": "progress", **asdict(sample)}),
                                    cancelled=lambda: False)

        outcome = asyncio.run(install())
        emit({"type": "result", "ok": outcome.ok, "error": outcome.error,
              "executable": str(outcome.executable) if outcome.executable else None,
              "status": status(), "manual_install": help_info})
        return 0 if outcome.ok else 1
    except KeyboardInterrupt:
        emit({"type": "result", "ok": False, "error": "cancelled", "manual_install": help_info})
        return 130
    except Exception as exc:
        emit({"type": "result", "ok": False, "error": str(exc), "manual_install": help_info})
        return 1
