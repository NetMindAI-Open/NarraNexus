"""
@file_name: pip_target.py
@author: NarraNexus
@date: 2026-08-28
@description: PluginInstaller strategy that installs a pip wheel into the
              user-writable plugin pyenv via ``sys.executable -m pip install
              --target <pyenv_dir>``.

Uses ``sys.executable -m pip`` rather than a bare ``pip`` / ``uv`` binary on
PATH so the wheel's ABI always matches the interpreter that will later
``sys.path.append`` and import it (``agent_framework.plugin_paths.
activate_pyenv``) — a wheel built for a different interpreter would import
but crash on any C-extension boundary.

pip may be ABSENT from that interpreter: a ``uv``-managed venv (the ``bash
run.sh`` dev path) ships without pip, so ``python -m pip`` fails outright. We
are running AS ``sys.executable``, so ``importlib.util.find_spec("pip")``
answers directly whether the target interpreter has pip; when it does not we
bootstrap it from the stdlib ``ensurepip`` first. The desktop DMG's bundled
python already carries pip, so there the bootstrap is skipped. This keeps ONE
install path across both run modes (铁律 #7) with no dependency on a ``uv`` or
``pip`` binary being on PATH.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import re
import shutil
import sys
from pathlib import Path
from typing import AsyncIterator

from ..spec import InstallComponent
from .base import InstalledState, PluginInstaller, stream_subprocess

# Splits "claude-agent-sdk==0.1.43" into the distribution name and the pinned
# version. Installers only ever receive exact-pin requirements (registry.py
# is the single place that writes them), so "==" is the only operator to
# support.
_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)$")


def _parse_requirement(requirement: str) -> tuple[str, str]:
    match = _REQUIREMENT_RE.match(requirement.strip())
    if not match:
        raise ValueError(f"unsupported pip requirement (expected name==version): {requirement!r}")
    return match.group(1), match.group(2)


def _dist_dir_name(distribution_name: str) -> str:
    """Normalize a distribution name the way wheel/dist-info directories do
    (PEP 503-ish): non-alphanumeric runs become a single underscore."""
    return re.sub(r"[^A-Za-z0-9]+", "_", distribution_name).lower()


class PipTargetInstaller(PluginInstaller):
    async def install(self, component: InstallComponent, target: Path) -> AsyncIterator[str]:
        # Bootstrap pip when the target interpreter lacks it (uv venvs do).
        # We ARE sys.executable, so this find_spec reflects the very
        # interpreter the pip subprocess below runs in.
        if importlib.util.find_spec("pip") is None:
            yield "pip not found in this interpreter — bootstrapping via ensurepip"
            async for line in stream_subprocess(
                [sys.executable, "-m", "ensurepip", "--default-pip"]
            ):
                yield line

        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(target),
            component.requirement,
        ]
        async for line in stream_subprocess(cmd):
            yield line

    def detect(self, component: InstallComponent, target: Path) -> InstalledState:
        distribution_name, target_version = _parse_requirement(component.requirement)
        dir_name = _dist_dir_name(distribution_name)
        package_path = target / dir_name
        if not package_path.is_dir():
            return InstalledState(
                installed=False,
                version=None,
                target_version=target_version,
                update_available=False,
            )
        version = self._read_version(target, dir_name)
        update_available = version is not None and version != target_version
        return InstalledState(
            installed=True,
            version=version,
            target_version=target_version,
            update_available=update_available,
        )

    def _read_version(self, target: Path, dir_name: str) -> str | None:
        matches = sorted(target.glob(f"{dir_name}-*.dist-info"))
        if not matches:
            return None
        try:
            return importlib.metadata.PathDistribution(matches[-1]).version
        except Exception:  # noqa: BLE001 — a malformed dist-info reads as "unknown version"
            return None

    async def uninstall(self, component: InstallComponent, target: Path) -> None:
        # One pip --target subdir per plugin, so removing the whole subdir takes
        # the ENTIRE dependency closure (openai_codex_cli_bin ~90 MB, shared
        # deps, dist-info) with it — no stranded packages, no version-mixing
        # with a sibling plugin. Idempotent (rmtree of an absent dir is a no-op).
        # OFF the event loop: rmtree of ~190 MB / tens of thousands of files is
        # seconds of blocking IO; on the single desktop loop that would freeze
        # every in-flight WS/agent stream (铁律 #16).
        if target.is_dir():
            await asyncio.to_thread(lambda: shutil.rmtree(target, ignore_errors=True))
