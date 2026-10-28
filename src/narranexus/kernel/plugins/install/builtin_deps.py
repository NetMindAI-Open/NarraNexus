"""
@file_name: builtin_deps.py
@author: Bin Liang
@date: 2026-09-04
@description: On-demand dependencies for BUILTIN plugins (spec section 843): check the declared imports at boot, install the declared pip requirements when they are missing, else mark the builtin ``deps_missing`` and boot without it.

A builtin with ``install.deps == "on_demand"`` declares ``backend.pip`` (what
to install) and ``backend.imports`` (what proves it is there). At boot the
imports are probed; missing ones trigger a wheels-only install into
``~/.narranexus/plugin-deps/<id>`` which is appended to ``sys.path`` (builtin
code lives in ``narranexus_plugins`` / ``narranexus.platform``, outside the ``nxplugins`` namespace the
plugin finder serves, so the finder's per-plugin dependency routing does not
apply). A failed or disabled install (cloud images bake their dependencies)
marks the builtin ``deps_missing``: its contributions are removed and the
host boots without it — the factory shows the state and offers a retry.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from narranexus.kernel.plugins.install.deps import DepsError, Runner, install_deps
from narranexus.kernel.plugins.paths import deps_dir


@dataclass(frozen=True)
class DepsStatus:
    plugin_id: str
    on_demand: bool
    missing: tuple[str, ...] = ()
    installed: tuple[str, ...] = ()
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def is_on_demand(manifest: Any) -> bool:
    return bool(manifest.install.deps == "on_demand" and manifest.backend is not None and manifest.backend.pip)


def builtin_deps_target(plugin_id: str) -> Path:
    return deps_dir(plugin_id, mode="link")


def _ensure_on_path(target: Path) -> None:
    if target.is_dir() and str(target) not in sys.path:
        sys.path.append(str(target))
        importlib.invalidate_caches()


def missing_imports(manifest: Any) -> tuple[str, ...]:
    names = tuple(manifest.backend.imports) if manifest.backend is not None else ()
    _ensure_on_path(builtin_deps_target(manifest.id))
    out = []
    for name in names:
        try:
            present = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            present = False
        if not present:
            out.append(name)
    return tuple(out)


def ensure_builtin_deps(manifest: Any, *, cloud: bool, runner: Runner | None = None, install: bool = True) -> DepsStatus:
    """Probe (and on the local build, when ``install`` is set, install) an on-demand builtin's dependencies."""
    if not is_on_demand(manifest):
        return DepsStatus(manifest.id, on_demand=False)
    missing = missing_imports(manifest)
    if not missing:
        return DepsStatus(manifest.id, on_demand=True)
    if cloud:
        return DepsStatus(manifest.id, on_demand=True, missing=missing, error=f"missing {', '.join(missing)}; cloud images bake dependencies, on-demand install is disabled")
    if not install:
        return DepsStatus(manifest.id, on_demand=True, missing=missing, error=f"missing {', '.join(missing)} (not installed at import; the boot or the factory installs)")
    target = builtin_deps_target(manifest.id)
    try:
        result = install_deps(manifest.backend.pip, target, runner=runner)
    except DepsError as exc:
        logger.warning(f"[plugins] {manifest.id}: on-demand dependency install failed: {exc}")
        return DepsStatus(manifest.id, on_demand=True, missing=missing, error=str(exc))
    _ensure_on_path(target)
    still = missing_imports(manifest)
    if still:
        return DepsStatus(manifest.id, on_demand=True, missing=still, error=f"installed {manifest.backend.pip} but {', '.join(still)} still cannot be imported")
    logger.info(f"[plugins] {manifest.id}: installed on-demand dependencies {manifest.backend.pip} into {target}")
    return DepsStatus(manifest.id, on_demand=True, installed=result.requirements if result else ())


__all__ = ["DepsStatus", "builtin_deps_target", "ensure_builtin_deps", "is_on_demand", "missing_imports"]
