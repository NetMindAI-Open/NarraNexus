"""
@file_name: importer.py
@author: Bin Liang
@date: 2026-09-03
@description: Isolated import of plugin code: ``nxplugins.<id>`` synthetic packages and a per-plugin dependency finder.

Two plugins may both ship a top-level ``utils`` module, and a plugin's pip
dependencies must never shadow the host's. So plugin code is never put on
``sys.path``. Instead each plugin becomes a synthetic package
``nxplugins.<id with dots as underscores>`` whose ``__path__`` is its
``backend`` directory (relative imports inside the plugin work unchanged), and
its dependencies live in a private directory served by ``PluginDepsFinder`` —
a ``sys.meta_path`` finder that answers ONLY when the importing module is that
plugin's package and ONLY for names the host cannot import itself (host
first, plugin deps second — the Home Assistant "one site-packages, everyone
tramples everyone" lesson).

Imports run in a bounded thread pool with a timeout so a plugin whose import
blocks (network at import time, a stuck subprocess) is isolated instead of
freezing the boot.

What this is NOT: a separate interpreter. A private dependency a plugin has
imported is cached in ``sys.modules`` like any module. The guarantee is
"plugin code and plugin deps never shadow host packages and never leak onto
``sys.path``", which is the failure mode that bit Home Assistant.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import threading
import types
from pathlib import Path
from typing import Any, Sequence

from narranexus.contracts import PluginError

NAMESPACE = "nxplugins"


def package_name(plugin_id: str) -> str:
    return f"{NAMESPACE}.{plugin_id.replace('.', '_').replace('-', '_')}"


class PluginFinder(importlib.abc.MetaPathFinder):
    """One meta-path finder for both jobs: synthetic packages and private dependencies.

    - ``nxplugins`` itself is a namespace package with no location.
    - ``nxplugins.<id>`` resolves to ``<backend_dir>/__init__.py`` with
      ``submodule_search_locations=[backend_dir]``, so the plugin's own code
      runs only when it is imported and its submodules resolve through the
      package ``__path__`` (never through ``sys.path``).
    - Any other top-level name is served from a plugin's private deps dir,
      but only when the importing module belongs to that plugin AND the host
      cannot import the name itself (host wins).
    """

    def __init__(self) -> None:
        self._packages: dict[str, Path] = {}  # package name -> backend dir
        self._deps: dict[str, list[str]] = {}  # package name -> deps dirs
        self._lock = threading.Lock()

    # ------------------------------------------------------------ registry

    def add_package(self, plugin_id: str, backend_dir: Path) -> str:
        name = package_name(plugin_id)
        with self._lock:
            current = self._packages.get(name)
            if current is not None and current != backend_dir:
                raise PluginError(f"{plugin_id}: {name} is already installed from {current}")
            self._packages[name] = backend_dir
        return name

    def remove_package(self, plugin_id: str) -> None:
        with self._lock:
            self._packages.pop(package_name(plugin_id), None)

    def register_deps(self, plugin_id: str, deps_dir: Path) -> None:
        with self._lock:
            dirs = self._deps.setdefault(package_name(plugin_id), [])
            if str(deps_dir) not in dirs:
                dirs.append(str(deps_dir))

    def unregister_deps(self, plugin_id: str) -> None:
        with self._lock:
            self._deps.pop(package_name(plugin_id), None)

    # alias kept for readability at call sites
    register = register_deps
    unregister = unregister_deps

    # ------------------------------------------------------------- finder

    @staticmethod
    def _requesting_plugin() -> str | None:
        """Walk the import stack for the nearest ``nxplugins.<id>`` module."""
        frame = sys._getframe(1)
        depth = 0
        while frame is not None and depth < 60:
            name = frame.f_globals.get("__name__", "")
            if isinstance(name, str) and name.startswith(NAMESPACE + "."):
                return ".".join(name.split(".")[:2])
            frame = frame.f_back
            depth += 1
        return None

    def find_spec(self, fullname: str, path: Sequence[str] | None = None, target: types.ModuleType | None = None):
        if fullname == NAMESPACE:
            spec = importlib.machinery.ModuleSpec(NAMESPACE, None, is_package=True)
            spec.submodule_search_locations = []
            return spec
        backend_dir = self._packages.get(fullname)
        if backend_dir is not None:
            init = backend_dir / "__init__.py"
            return importlib.util.spec_from_file_location(fullname, init, submodule_search_locations=[str(backend_dir)])
        if fullname.startswith(NAMESPACE + ".") or path is not None or not self._deps:
            return None  # plugin submodules resolve via the package __path__; nested names are not ours
        requester = self._requesting_plugin()
        dirs = self._deps.get(requester) if requester else None
        if not dirs:
            return None
        # Host first: if the ordinary finders can import it, the plugin gets the host copy.
        for finder in sys.meta_path:
            if finder is self:
                continue
            try:
                spec = finder.find_spec(fullname, None)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001 - a foreign finder must not break ours
                spec = None
            if spec is not None:
                return None
        return importlib.machinery.PathFinder.find_spec(fullname, dirs)


_FINDER = PluginFinder()


def plugin_finder() -> PluginFinder:
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)
    return _FINDER


# Backwards-readable names used by the loader/tests.
PluginDepsFinder = PluginFinder
deps_finder = plugin_finder


def install_synthetic_package(plugin_id: str, backend_dir: Path) -> str:
    """Make ``nxplugins.<id>`` importable from ``backend_dir`` (lazily; nothing runs yet). Idempotent per (id, dir)."""
    if not backend_dir.is_dir() or not (backend_dir / "__init__.py").is_file():
        raise PluginError(f"{plugin_id}: backend directory {backend_dir} does not exist or has no __init__.py")
    return plugin_finder().add_package(plugin_id, backend_dir)


def uninstall_synthetic_package(plugin_id: str) -> int:
    """Forget the package and drop it plus every submodule from ``sys.modules``; returns how many modules were removed."""
    plugin_finder().remove_package(plugin_id)
    name = package_name(plugin_id)
    victims = [m for m in sys.modules if m == name or m.startswith(name + ".")]
    for m in victims:
        del sys.modules[m]
    return len(victims)


class PluginImportTimeout(PluginError):
    """A plugin's import did not finish inside its deadline: SLOW, not broken (boot records it as such, never as a crash)."""


# plugin id -> module name whose import is still hung in this process. A wedged
# import cannot be interrupted (Python has no thread cancellation), so the
# plugin is failed fast on every later attempt instead of parking another
# worker behind it — a fixed-size pool let two hung plugins make EVERY later
# import time out and get innocent plugins auto-disabled.
_WEDGED: dict[str, str] = {}


def import_plugin_module(plugin_id: str, submodule: str = "", *, timeout: float = 30.0) -> types.ModuleType:
    """Import ``nxplugins.<id>[.submodule]`` on its own daemon thread with a deadline.

    Only ever called from the boot / activation top level: a worker thread
    importing back into a module that is mid-import on the caller's thread
    would deadlock on the per-module import lock.
    """
    name = package_name(plugin_id) + (f".{submodule}" if submodule else "")
    if plugin_id in _WEDGED:
        raise PluginImportTimeout(f"{plugin_id}: an earlier import of {_WEDGED[plugin_id]} is still hung in this process; not retried")
    outcome: dict[str, Any] = {}

    def _run() -> None:
        try:
            outcome["module"] = importlib.import_module(name)
        except BaseException as exc:  # noqa: BLE001 — reported to the caller
            outcome["error"] = exc

    worker = threading.Thread(target=_run, name=f"nx-plugin-import:{plugin_id}", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        _WEDGED[plugin_id] = name
        raise PluginImportTimeout(f"{plugin_id}: importing {name} exceeded {timeout:.0f}s")
    if "error" in outcome:
        exc = outcome["error"]
        raise PluginError(f"{plugin_id}: importing {name} failed: {type(exc).__name__}: {exc}") from exc
    return outcome["module"]


__all__ = [
    "NAMESPACE",
    "PluginImportTimeout",
    "PluginFinder",
    "plugin_finder",
    "import_plugin_module",
    "install_synthetic_package",
    "package_name",
    "uninstall_synthetic_package",
]
