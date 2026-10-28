"""
@file_name: service.py
@author: NarraNexus
@date: 2026-08-28
@description: Public facade for the plugin install subsystem — the only
              module Phase 3 routes should import from this package.

Owns the one piece of state the installers themselves are too dumb to know
about: which plugin is mid-operation right now (per-plugin ``asyncio.Lock`` +
a busy set). Install and uninstall share that one lock, so a route handler
can't accidentally run two of them against the same plugin at once and race
two package managers (or a package manager and an ``rm``) on the same target
directory.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator

from narranexus.platform.agent_framework.plugin_paths import node_prefix, plugin_pyenv

from ._installers.base import InstalledState, PluginInstaller
from ._installers.npm_prefix import NpmPrefixInstaller
from ._installers.pip_target import PipTargetInstaller
from .errors import PluginBusyError, classify_error
from .registry import build_plugin_specs
from .spec import InstallComponent, PluginSpec

@dataclass
class PluginStatus:
    """Everything the Settings -> Plugins UI needs to render one plugin row."""

    id: str
    display_name: str
    installed: bool
    version: str | None
    target_version: str
    update_available: bool
    logged_in: bool
    size_hint: str
    busy: bool


class PluginService:
    """Orchestrates install/detect/uninstall across a plugin's components."""

    def __init__(self, specs: dict[str, PluginSpec] | None = None) -> None:
        # Derived on FIRST USE, not at construction: the route module builds
        # its process-level singleton at import, before the plugin platform
        # has booted, and the framework registry (whose slot builtin.turn
        # declares at boot) does not exist yet then. Every request comes
        # after boot, so the first request sees the complete registry.
        self._given_specs = specs
        self._specs_cache: dict[str, PluginSpec] | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._busy: set[str] = set()
        self._installers: dict[str, PluginInstaller] = {
            "pip": PipTargetInstaller(),
            "npm": NpmPrefixInstaller(),
        }

    @property
    def _specs(self) -> dict[str, PluginSpec]:
        if self._specs_cache is None:
            self._specs_cache = self._given_specs if self._given_specs is not None else build_plugin_specs()
        return self._specs_cache

    def _spec(self, plugin_id: str) -> PluginSpec:
        try:
            return self._specs[plugin_id]
        except KeyError:
            raise KeyError(f"unknown plugin id: {plugin_id!r}") from None

    def _lock_for(self, plugin_id: str) -> asyncio.Lock:
        lock = self._locks.get(plugin_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[plugin_id] = lock
        return lock

    def _logged_in(self, plugin_id: str) -> bool:
        # Where the plugin's login/auth state lives is the framework's own
        # ``FrameworkMeta.login_marker``; a plain file-existence probe is all
        # the frontend needs (a "log in" CTA vs. not), so we do not shell out
        # to the CLI's auth-status command.
        marker = self._specs[plugin_id].login_marker if plugin_id in self._specs else None
        if marker is None:
            return False
        subdir, filename = marker
        return (Path.home() / subdir / filename).exists()

    def _target_for(self, spec: PluginSpec, component: InstallComponent) -> Path:
        """Where a component installs: a per-plugin pyenv subdir for pip (clean
        rmtree uninstall), the shared node prefix for npm (only Claude uses it)."""
        if component.kind == "pip":
            return plugin_pyenv(spec.id)
        return node_prefix()

    def _detect(self, spec: PluginSpec, component: InstallComponent) -> InstalledState:
        return self._installers[component.kind].detect(component, self._target_for(spec, component))

    def _status(self, spec: PluginSpec) -> PluginStatus:
        states = [self._detect(spec, component) for component in spec.components]
        installed = all(state.installed for state in states)

        version_component_kind = "npm" if spec.user_version_source == "npm_cli" else "pip"
        version_state = next(
            (state for component, state in zip(spec.components, states) if component.kind == version_component_kind),
            None,
        )
        target_version = version_state.target_version if version_state else ""
        version = version_state.version if (version_state and installed) else None
        update_available = installed and any(state.update_available for state in states)

        return PluginStatus(
            id=spec.id,
            display_name=spec.display_name,
            installed=installed,
            version=version,
            target_version=target_version,
            update_available=update_available,
            logged_in=self._logged_in(spec.id),
            size_hint=spec.size_hint,
            busy=spec.id in self._busy,
        )

    def list_plugins(self) -> list[PluginStatus]:
        return [self._status(spec) for spec in self._specs.values()]

    async def install(self, plugin_id: str) -> AsyncIterator[dict]:
        """Install every component of ``plugin_id`` in order, yielding
        progress events and a final ``{"done": True, ...}`` event.

        Rejects re-entry outright (no queueing) when the plugin is already
        mid-install: two concurrent package-manager processes writing into
        the same target directory would corrupt it.
        """
        spec = self._spec(plugin_id)
        lock = self._lock_for(plugin_id)
        if lock.locked():
            yield {
                "done": True,
                "ok": False,
                "error": f"{spec.display_name} install already in progress",
                "status": None,
            }
            return

        async with lock:
            self._busy.add(plugin_id)
            ok = True
            error: str | None = None
            try:
                for component in spec.components:
                    installer = self._installers[component.kind]
                    target = self._target_for(spec, component)
                    async for line in installer.install(component, target):
                        yield {"phase": component.kind, "line": line, "done": False}
            except Exception as exc:  # noqa: BLE001 — every failure must reach the UI, not the logs only
                ok = False
                error = classify_error(exc).message
            finally:
                self._busy.discard(plugin_id)
            # Compute the final status AFTER clearing busy, so the status dict
            # reports busy=False (it is no longer running by the time the caller
            # receives this event). A client that disconnected mid-install
            # raised GeneratorExit above and never reaches here — its lock and
            # busy flag were already released in the finally.
            # OFF the event loop: _status forks `claude --version` (npm detect),
            # and this runs inside the StreamingResponse body on the single
            # desktop loop — a sync fork here freezes every in-flight WS/agent
            # stream (铁律 #16). Same reason routes.list_plugins uses a thread.
            status = await asyncio.to_thread(self._status, spec) if ok else None
            yield {
                "done": True,
                "ok": ok,
                "error": error,
                "status": asdict(status) if status is not None else None,
            }

    async def uninstall(self, plugin_id: str) -> None:
        """Remove every component of ``plugin_id``.

        Takes the SAME per-plugin lock as ``install`` so the two are mutually
        exclusive — uninstalling mid-install (or double-uninstalling) would
        race a package manager and the ``rm`` on the same target directory.
        Refused outright (no queueing) when the plugin is already busy.
        """
        spec = self._spec(plugin_id)
        lock = self._lock_for(plugin_id)
        if lock.locked():
            raise PluginBusyError(
                plugin_id,
                f"{spec.display_name} is busy (install or uninstall in progress); "
                f"cannot uninstall right now",
            )

        async with lock:
            self._busy.add(plugin_id)
            try:
                for component in spec.components:
                    target = self._target_for(spec, component)
                    await self._installers[component.kind].uninstall(component, target)
            finally:
                self._busy.discard(plugin_id)
