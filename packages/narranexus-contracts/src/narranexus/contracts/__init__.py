"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: The public API surface of the plugin platform.

Plugins import from here and nowhere else. Two tables govern evolution
(docs/API_POLICY.md):

``API_VERSIONS``   integer contract version per kind; only ever increases, and a
                   breaking change to a kind's Protocol MUST bump it.
``STABILITY``      stability level per kind; everything is alpha until batch 6 of
                   the plugin-platform roadmap marks the surface stable.
"""
from __future__ import annotations

from narranexus.contracts._base import (
    plugin_id_slug,
    BindingConflict,
    CancellationSignal,
    Disposable,
    DisposableStack,
    IncompatibleProvider,
    ManifestError,
    PluginError,
    RegistryConflict,
    RegistryFrozen,
    Stability,
    UnboundSlot,
    UnknownEntry,
)

# Two kinds of key share this table: registry kinds (the ``Registry.api_version``
# of a slot that has a registry — ``framework`` / ``provider`` / ``llm_client`` /
# ``memory`` / ``tool`` / ...) and contract modules without a registry
# (``agent`` / ``agent_events`` / ``services`` / ``ui`` / ``events``), whose
# version only tags the shapes they define. ``STABILITY`` covers both.
API_VERSIONS: dict[str, int] = {
    "framework": 0,
    "agent_events": 0,
    "agent": 0,
    "services": 0,
    "ui": 1,  # frontend HostAPI shape (frontend/src/platform/host.ts HOST_API_VERSION); bumped with the batch-6 registries/slot-points surface
    "provider": 0,
    "llm_client": 0,
    "memory": 0,
    "events": 0,
    "hook": 0,
    "route": 0,
    "table": 0,
    "worker": 0,
    "trigger": 0,
    "data_access": 0,
    "channel": 0,
    "settings": 0,
    "tool": 0,
    "mcp_server": 0,
    "bundle": 0,
    "skill": 0,
    "theme": 0,
    "stage_strategy": 0,
    "pipeline_profile": 0,
    "context_provider": 0,
    "module": 0,
    "auth": 0,
    "prompt": 0,
    # Batch 6c. Not a manifest/slot kind: these two name the surfaces a plugin
    # AUTHOR imports (contracts.web + the channel base classes re-exported by
    # narranexus.sdk). They are versioned and graded here because that is the
    # only table the griffe gate and the release notes read — a surface a
    # third party builds on and nobody grades is exactly the Hyrum's-Law trap
    # docs/API_POLICY.md §1 refuses.
    "web": 0,
    "channel_authoring": 0,
}

# The OLDEST contract version a plugin may still declare per kind. Equal to
# API_VERSIONS until a kind is bumped; then the previous version stays here
# for the deprecation window docs/API_POLICY.md §5 promises, and is removed
# when the window closes. A manifest's api[kind] must satisfy
# MIN_SUPPORTED_VERSIONS[kind] <= api[kind] <= API_VERSIONS[kind].
MIN_SUPPORTED_VERSIONS: dict[str, int] = {**API_VERSIONS, "ui": 0}  # manifests written against ui 0 stay loadable through the deprecation window

# Stability is declared PER KIND, deliberately: marking a kind stable is a
# decision (docs/API_POLICY.md), not the by-product of a comprehension over
# whatever happens to be in API_VERSIONS. Since plugin platform batch 6 every
# shipped kind is STABLE; a new kind starts ALPHA until it is promoted here.
STABILITY: dict[str, Stability] = {
    "framework": Stability.STABLE,
    "agent_events": Stability.STABLE,
    "agent": Stability.STABLE,
    "services": Stability.STABLE,
    "ui": Stability.STABLE,
    "provider": Stability.STABLE,
    "llm_client": Stability.STABLE,
    "memory": Stability.STABLE,
    "events": Stability.STABLE,
    "hook": Stability.STABLE,
    "route": Stability.STABLE,
    "table": Stability.STABLE,
    "worker": Stability.STABLE,
    "trigger": Stability.STABLE,
    "data_access": Stability.STABLE,
    "channel": Stability.STABLE,
    "settings": Stability.STABLE,
    "tool": Stability.STABLE,
    "mcp_server": Stability.STABLE,
    "bundle": Stability.STABLE,
    "skill": Stability.STABLE,
    "theme": Stability.STABLE,
    "stage_strategy": Stability.STABLE,
    "pipeline_profile": Stability.STABLE,
    "context_provider": Stability.STABLE,
    "module": Stability.STABLE,
    "auth": Stability.STABLE,
    "prompt": Stability.STABLE,
    # ALPHA, honestly (docs/API_POLICY.md §2): ``web`` is one batch old and its
    # ownership/visibility split is still under review; ``channel_authoring``
    # re-exports base classes batch 4 landed and whose credential seam is a
    # tracked follow-up. Marking either STABLE to look tidy would promise a
    # deprecation window we cannot honour yet.
    "web": Stability.ALPHA,
    "channel_authoring": Stability.ALPHA,
}


class Namespace:
    """Contract of a grouping slot (``model``, ``agent``, ``backend``, ...): owned by the kernel, never bound."""

__all__ = [
    "API_VERSIONS",
    "MIN_SUPPORTED_VERSIONS",
    "STABILITY",
    "Namespace",
    "Stability",
    "PluginError",
    "RegistryConflict",
    "UnknownEntry",
    "RegistryFrozen",
    "BindingConflict",
    "UnboundSlot",
    "IncompatibleProvider",
    "ManifestError",
    "Disposable",
    "DisposableStack",
    "CancellationSignal",
]
