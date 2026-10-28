"""
@file_name: bindings_runtime.py
@author: Bin Liang
@date: 2026-09-07
@description: One place every host (backend, mcp, workers) resolves the slot bindings at boot — default < distribution < ``<plugin home>/narranexus.toml`` < ``NX_BIND__*`` env — stores them on the registries (``Registries.set_bindings``) so ``kernel.plugins.bound`` can answer "who fills this slot", and snapshots them to ``<plugin home>/run/bindings.resolved.json`` for the factory page and ``narranexus slots``. A conflict is loud (spec section 6.4); a slot nobody binds is only logged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from loguru import logger

from narranexus.contracts import UnboundSlot
from narranexus.kernel.plugins.bindings import ResolvedBindings, parse_env, parse_toml, resolve, write_resolved
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

CONFIG_FILENAME = "narranexus.toml"
SNAPSHOT_RELPATH = Path("run") / "bindings.resolved.json"


def config_path(home: Optional[Path] = None) -> Path:
    from narranexus.kernel.plugins.paths import plugin_home

    return (home or plugin_home()) / CONFIG_FILENAME


def resolve_runtime_bindings(
    distribution: Any = None,
    *,
    registries: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    snapshot: bool = True,
) -> Optional[ResolvedBindings]:
    """Resolve, install on the registries and (by default) snapshot. Returns ``None`` when a slot is unbound."""
    from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
    from narranexus.kernel.plugins.paths import plugin_home

    regs = registries or KERNEL_REGISTRIES
    home = home or plugin_home()
    sources = []
    if distribution is not None:
        sources.append(distribution.bindings)
    toml_path = home / CONFIG_FILENAME
    if toml_path.is_file():
        sources.append(parse_toml(toml_path.read_text(encoding="utf-8"), origin=str(toml_path)))
    sources.append(parse_env(environ))
    # Non-strict: a one-arity slot with neither binding nor default (a plugin
    # declaring a defaultless slot) is reported, and every OTHER binding the
    # operator wrote is still installed. Discarding the whole resolution on one
    # unbound slot ran the host on a different configuration than the one in
    # narranexus.toml / NX_BIND__* with one warning line as the only signal.
    # BindingConflict stays loud (it propagates).
    resolved = resolve(slot_tree_with_builtins(), sources, strict=False)
    for path in resolved.unbound:
        logger.warning(f"[plugins] slot {path} has no binding and no default provider — consumers of it will fail loudly")
    regs.set_bindings(resolved)
    if snapshot:
        try:
            write_resolved(resolved, home / SNAPSHOT_RELPATH)
        except OSError as exc:
            logger.warning(f"[plugins] bindings snapshot not written: {exc}")
    return resolved


__all__ = ["CONFIG_FILENAME", "SNAPSHOT_RELPATH", "config_path", "resolve_runtime_bindings"]
