"""
@file_name: registry.py
@author: NarraNexus
@date: 2026-08-28
@description: The installable framework plugins as a lookup table keyed by plugin id (== framework name).

Since batch 1 of the plugin platform this table is DERIVED on demand
(``build_plugin_specs()``, no import-time snapshot): every agent-loop framework
contribution whose ``FrameworkMeta.install`` is set (today Claude Code and
Codex CLI) becomes one ``PluginSpec``. The pins therefore live in one
place — each framework's plugin package (``narranexus_plugins.frameworks_*``,
batch 6b.2b) next to its driver factory —
and the invariant "installer pin == locked version" is guarded by
``tests/backend/integrations/plugins/test_registry.py::test_pip_pins_match_uv_lock``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from narranexus.contracts.framework import FrameworkMeta
from narranexus.platform.agent_framework.loop.driver import framework_registry

from .spec import PluginSpec

if TYPE_CHECKING:
    from narranexus.kernel.plugins.registries import Registries


def _spec_from_meta(meta: FrameworkMeta) -> PluginSpec:
    if meta.install is None:
        raise ValueError(f"framework {meta.name!r} declares no install recipe")
    return PluginSpec(
        id=meta.name,
        display_name=meta.display_name,
        framework_name=meta.name,
        components=tuple(meta.install.components),
        probe_package=meta.install.probe_package,
        user_version_source=meta.install.user_version_source,
        size_hint=meta.install.size_hint,
        login_marker=meta.login_marker,
    )


def build_plugin_specs(registries: "Registries | None" = None) -> dict[str, PluginSpec]:
    """Installable plugins = registered frameworks that declare an install recipe.

    Derived from the framework registry at call time (``registries`` defaults
    to the process registries, as ``framework_registry`` does), so a framework
    registered after an earlier build shows up in the next one.
    """
    specs: dict[str, PluginSpec] = {}
    for entry in framework_registry(registries).entries():
        meta = entry.meta.get("framework")
        if isinstance(meta, FrameworkMeta) and meta.install is not None:
            specs[entry.name] = _spec_from_meta(meta)
    return specs


__all__ = ["build_plugin_specs"]
