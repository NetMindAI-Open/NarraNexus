"""
@file_name: npm_prefix.py
@author: NarraNexus
@date: 2026-08-28
@description: PluginInstaller strategy that installs the Claude CLI into
              the user-writable plugin node tree via ``npm install --prefix
              <node_prefix>``. Assumes ``node``/``npm`` are on PATH (bundled
              with the desktop app / expected on the host for ``run.sh``).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import AsyncIterator

from loguru import logger

from ..spec import InstallComponent
from .base import InstalledState, PluginInstaller, stream_subprocess

# "@anthropic-ai/claude-code@2.1.220" -> "2.1.220". The version is always the
# trailing "@<digit...>" segment; the package name itself may contain "@"
# (scoped packages), so anchor on a digit right after the last "@".
_NPM_REQUIREMENT_VERSION_RE = re.compile(r"@(\d[\w.\-]*)$")

# `claude --version` prints e.g. "2.1.220 (Claude Code)".
_CLI_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")

# `claude --version` is a sub-second call; a few seconds means the binary is
# wedged. Bounded low so the detect() probe can't stall the caller (it runs off
# the event loop via run_in_threadpool, but a 20s hang there is still 20s of a
# thread pinned + a UI spinner). Over budget → report "version unknown", which
# is more honest than freezing.
_VERSION_PROBE_TIMEOUT_S = 5.0


def _pinned_version(requirement: str) -> str:
    match = _NPM_REQUIREMENT_VERSION_RE.search(requirement.strip())
    if not match:
        raise ValueError(f"unsupported npm requirement (expected pkg@version): {requirement!r}")
    return match.group(1)


def _package_name(requirement: str) -> str:
    """Strip the trailing ``@<version>`` to get the bare package name
    (``@anthropic-ai/claude-code@2.1.220`` -> ``@anthropic-ai/claude-code``)."""
    req = requirement.strip()
    match = _NPM_REQUIREMENT_VERSION_RE.search(req)
    if not match:
        raise ValueError(f"unsupported npm requirement (expected pkg@version): {requirement!r}")
    return req[: match.start()]


class NpmPrefixInstaller(PluginInstaller):
    async def install(self, component: InstallComponent, target: Path) -> AsyncIterator[str]:
        cmd = ["npm", "install", "--prefix", str(target), component.requirement]
        async for line in stream_subprocess(cmd):
            yield line

    def detect(self, component: InstallComponent, target: Path) -> InstalledState:
        target_version = _pinned_version(component.requirement)
        cli_path = target / "node_modules" / ".bin" / "claude"
        if not cli_path.exists():
            return InstalledState(
                installed=False,
                version=None,
                target_version=target_version,
                update_available=False,
            )
        # The binary IS present, so the plugin is installed even if we cannot
        # read its version (e.g. bundled node missing from PATH so the shim
        # can't run). Reporting "not installed" here would send the user to
        # reinstall — which wouldn't fix a PATH problem. Keep installed=True,
        # surface version=None, and leave a breadcrumb instead of swallowing it.
        version = self._probe_version(cli_path)
        if version is None:
            logger.warning(
                f"[plugins] claude CLI present at {cli_path} but `--version` was "
                f"unreadable; reporting installed with unknown version"
            )
        update_available = version is not None and version != target_version
        return InstalledState(
            installed=True,
            version=version,
            target_version=target_version,
            update_available=update_available,
        )

    def _probe_version(self, cli_path) -> str | None:
        try:
            result = subprocess.run(
                [str(cli_path), "--version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_PROBE_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — an unreadable version reads as "unknown"
            return None
        match = _CLI_VERSION_RE.search((result.stdout or "") + (result.stderr or ""))
        return match.group(1) if match else None

    async def uninstall(self, component: InstallComponent, target: Path) -> None:
        # Remove ONLY this package (and let npm prune its deps + the .bin
        # shim), rather than wiping the whole node prefix tree — so a future
        # second npm plugin sharing this prefix is not collateral. (pip gets a
        # per-plugin subdir instead; npm stays a shared prefix because only
        # Claude uses it.) npm is a no-op when the package is already absent, so
        # this is safe to call twice.
        cmd = ["npm", "uninstall", "--prefix", str(target), _package_name(component.requirement)]
        async for _line in stream_subprocess(cmd):
            pass
