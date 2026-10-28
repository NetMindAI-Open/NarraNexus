"""
@file_name: activation.py
@author: Bin Liang
@date: 2026-09-03
@description: Activation events — a plugin's code is imported and ``activate(ctx)`` runs on the first event it asked for.

Boot registers declarative contributions only. ``Activator.fire(event)``
activates every not-yet-active plugin subscribed to that event (``onStartup``,
``onPage:<id>``, ``onPanel:<id>``, ``onCommand:<id>``, ``onChannel:<name>``,
``onEvent:<name>``, ``onModule:<name>``, ``onTool:<name>``). Activation is
once per process; a failure is recorded on the registry file (crash count →
auto-disable on the second) and never propagates to the caller — the page
or event that triggered it must not fail because a plugin did.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from narranexus.contracts import PluginError
from narranexus.kernel.plugins.context import PluginContext
from narranexus.kernel.plugins.importer import import_plugin_module
from narranexus.kernel.plugins.manifest import Manifest, derive_activation_events

ContextFactory = Callable[[Manifest], PluginContext]
CrashSink = Callable[[str, str], None]  # (plugin_id, error)
ActivatedSink = Callable[[str, str], Awaitable[None] | None]  # (plugin_id, version)

EVENT_PREFIXES = ("onStartup", "onPage:", "onPanel:", "onCommand:", "onRenderer:", "onTimelineEvent:", "onSlot:", "onChannel:", "onEvent:", "onModule:", "onTool:")


def validate_event_name(event: str) -> str:
    if event == "onStartup" or any(event.startswith(p) and len(event) > len(p) for p in EVENT_PREFIXES[1:]):
        return event
    raise PluginError(f"invalid activation event {event!r}; expected one of {EVENT_PREFIXES}")


@dataclass
class Activation:
    plugin_id: str
    version: str
    event: str
    duration_ms: float
    error: str | None = None


@dataclass
class Activator:
    """Per-process activation state for one host role."""

    context_factory: ContextFactory
    on_crash: CrashSink = lambda pid, err: None
    on_activated: ActivatedSink | None = None
    import_timeout_s: float = 30.0
    activate_timeout_s: float = 30.0
    context_timeout_s: float = 15.0
    _manifests: dict[str, Manifest] = field(default_factory=dict)
    _by_event: dict[str, list[str]] = field(default_factory=dict)
    _active: dict[str, PluginContext] = field(default_factory=dict)
    _failed: dict[str, str] = field(default_factory=dict)
    _history: list[Activation] = field(default_factory=list)

    # ------------------------------------------------------------ setup

    def register(self, manifest: Manifest) -> tuple[str, ...]:
        """Remember which events activate this plugin; returns them."""
        if manifest.backend is None or not manifest.backend.activate:
            return ()
        events = tuple(validate_event_name(e) for e in derive_activation_events(manifest))
        self._manifests[manifest.id] = manifest
        for event in events:
            ids = self._by_event.setdefault(event, [])
            if manifest.id not in ids:
                ids.append(manifest.id)
        return events

    def events_for(self, plugin_id: str) -> tuple[str, ...]:
        return tuple(e for e, ids in self._by_event.items() if plugin_id in ids)

    def is_active(self, plugin_id: str) -> bool:
        return plugin_id in self._active

    def context_of(self, plugin_id: str) -> PluginContext | None:
        return self._active.get(plugin_id)

    @property
    def history(self) -> tuple[Activation, ...]:
        return tuple(self._history)

    # ------------------------------------------------------------- firing

    async def fire(self, event: str) -> list[Activation]:
        """Activate every pending subscriber of ``event``; never raises."""
        validate_event_name(event)
        results: list[Activation] = []
        for plugin_id in list(self._by_event.get(event, ())):
            if plugin_id in self._active or plugin_id in self._failed:
                continue
            results.append(await self._activate(plugin_id, event))
        return results

    async def activate_now(self, plugin_id: str) -> Activation:
        """Explicit activation (factory page / Nexus_Plugins_Module)."""
        if plugin_id in self._active:
            return Activation(plugin_id, self._manifests[plugin_id].version, "explicit", 0.0)
        if plugin_id not in self._manifests:
            raise PluginError(f"{plugin_id}: not registered for activation")
        self._failed.pop(plugin_id, None)
        return await self._activate(plugin_id, "explicit")

    async def _activate(self, plugin_id: str, event: str) -> Activation:
        manifest = self._manifests[plugin_id]
        started = time.perf_counter()
        error: str | None = None
        try:
            package = manifest.backend.package if manifest.backend else "backend"
            module = await asyncio.get_running_loop().run_in_executor(
                None, lambda: import_plugin_module(plugin_id, "" if package == "backend" else package, timeout=self.import_timeout_s)
            )
            activate = getattr(module, "activate", None)
            if activate is None:
                raise PluginError(f"{plugin_id}: backend package has no activate(ctx)")
            # The context factory may do I/O (the settings store loads the
            # plugin's rows): run it off the loop with a bound so a stalled DB
            # cannot park the host's event loop — and with it /health — for
            # the duration of a connection hang.
            ctx = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, self.context_factory, manifest),
                timeout=self.context_timeout_s,
            )
            result = activate(ctx)
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=self.activate_timeout_s)
            self._active[plugin_id] = ctx
            if self.on_activated is not None:
                maybe = self.on_activated(plugin_id, manifest.version)
                if inspect.isawaitable(maybe):
                    await maybe
        except Exception as exc:  # noqa: BLE001 — isolate; the trigger must not fail
            error = f"{type(exc).__name__}: {exc}"
            self._failed[plugin_id] = error
            logger.warning(f"[plugins] {plugin_id}: activation on {event} failed: {error}")
            try:
                self.on_crash(plugin_id, error)
            except Exception as sink_exc:  # noqa: BLE001
                logger.error(f"[plugins] crash sink failed for {plugin_id}: {sink_exc}")
        record = Activation(plugin_id, manifest.version, event, (time.perf_counter() - started) * 1000.0, error)
        self._history.append(record)
        return record

    # -------------------------------------------------------- deactivate

    async def deactivate(self, plugin_id: str) -> bool:
        ctx = self._active.pop(plugin_id, None)
        if ctx is None:
            return False
        try:
            module = import_plugin_module(plugin_id, timeout=self.import_timeout_s)
            deactivate = getattr(module, "deactivate", None)
            if deactivate is not None:
                result = deactivate(ctx)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=self.activate_timeout_s)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] {plugin_id}: deactivate raised {exc}; disposing subscriptions anyway")
        ctx.dispose()
        return True


__all__ = ["Activation", "Activator", "EVENT_PREFIXES", "validate_event_name"]
