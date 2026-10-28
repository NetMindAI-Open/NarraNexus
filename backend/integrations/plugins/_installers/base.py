"""
@file_name: base.py
@author: NarraNexus
@date: 2026-08-28
@description: The PluginInstaller strategy contract, its result type, and
              the shared subprocess-streaming helper both concrete
              installers (pip_target, npm_prefix) build on.

Why a shared subprocess helper
-------------------------------
Both installers shell out to a package manager and want the same behavior:
stream stdout+stderr line-by-line as install progress, and on a non-zero
exit raise one exception that carries the full captured output — so
``errors.classify_error`` has something to pattern-match against. Keeping
that plumbing here (rather than duplicated in both installer files) is the
only shared logic between two otherwise-independent strategies.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from .. import spec as _spec


@dataclass
class InstalledState:
    """What ``PluginInstaller.detect`` learned about one component."""

    installed: bool
    version: str | None
    target_version: str
    update_available: bool


class PluginInstallSubprocessError(RuntimeError):
    """A package-manager subprocess exited non-zero.

    Carries the full captured stdout+stderr text so ``errors.classify_error``
    can pattern-match the real failure reason (slow registry, EACCES,
    network block) instead of guessing from the exit code alone.
    """

    def __init__(self, cmd: list[str], returncode: int, output: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.output = output
        super().__init__(f"{' '.join(cmd)!r} exited {returncode}: {output[-500:]}")


class PluginInstaller(ABC):
    """Strategy for installing/detecting/removing one InstallComponent kind.

    ``target`` is the directory this component installs into — the caller
    (``PluginService``) picks it per component kind: a per-plugin pyenv subdir
    for pip, the shared node prefix for npm. Threading it through (rather than
    each installer reaching for a global path) is what lets pip give every
    plugin its own isolated subdir.
    """

    @abstractmethod
    async def install(
        self, component: "_spec.InstallComponent", target: Path
    ) -> AsyncIterator[str]:
        """Run the install, yielding human-readable progress lines as they
        arrive. Raises ``PluginInstallSubprocessError`` on non-zero exit."""
        raise NotImplementedError

    @abstractmethod
    def detect(self, component: "_spec.InstallComponent", target: Path) -> InstalledState:
        """Synchronously probe whether ``component`` is already installed."""
        raise NotImplementedError

    @abstractmethod
    async def uninstall(self, component: "_spec.InstallComponent", target: Path) -> None:
        """Remove whatever ``install`` put down for ``component``."""
        raise NotImplementedError


async def stream_subprocess(cmd: list[str]) -> AsyncIterator[str]:
    """Run ``cmd``, yielding decoded stdout+stderr lines as they arrive.

    Raises ``PluginInstallSubprocessError`` (with every line seen so far) if
    the process exits non-zero. Tests monkeypatch ``asyncio.create_subprocess_exec``
    (as ``...base.asyncio.create_subprocess_exec``) to avoid the real process
    table.

    Cleanup is guaranteed: if the caller abandons the generator mid-stream
    (a client disconnect closes the ``StreamingResponse`` body → ``GeneratorExit``,
    or a cancellation), the ``finally`` kills and reaps the package-manager
    process. Without that the process would be orphaned and keep writing the
    target dir AFTER ``PluginService`` released the per-plugin lock — the exact
    race the lock exists to prevent (工程教训 #2).
    """
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    captured: list[str] = []
    try:
        if process.stdout is None:  # explicit (an `assert` is stripped under -O)
            raise RuntimeError(f"{cmd[0]!r} produced no stdout pipe")
        async for raw_line in process.stdout:
            line = raw_line.decode(errors="replace").rstrip("\n")
            captured.append(line)
            yield line
        returncode = await process.wait()
        if returncode != 0:
            raise PluginInstallSubprocessError(
                cmd=cmd, returncode=returncode, output="\n".join(captured)
            )
    finally:
        # Reap the process on ANY exit path (normal, error, GeneratorExit,
        # CancelledError). `returncode is None` means it is still running.
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
