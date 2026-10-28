"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: ``narranexus.sdk`` — what a plugin author imports: the contracts, the registration primitives, and a test host.

Plugins import from here (or from ``narranexus.contracts`` directly); never
from ``narranexus.kernel`` internals. The names are re-exports, so the SDK
is a stable door, not a second copy. ``PluginTestHost`` (``testing``) boots
a minimal host around one plugin directory for its tests.

The framework contracts (``AgentLoopDriver`` / ``FrameworkMeta`` /
``FrameworkInstall`` / ``InstallComponent`` / ``CAPABILITY_VOCABULARY``) are
exported here because "swap the agent-loop framework" is the headline
extension point: an author who had to reach past the SDK into
``narranexus.contracts.framework`` for it was being told by the import line
that the seam was not really supported. ``templates/framework`` scaffolds
against exactly these names.
"""
from narranexus.contracts import API_VERSIONS, Disposable, DisposableStack, PluginError
from narranexus.contracts.agent.capability import ContextProvider
from narranexus.contracts.agent.pipeline import PipelineProfile, StageStrategy
from narranexus.contracts.agent.stages import Stage
from narranexus.contracts.bundle import BundleSpec
from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.contracts.framework import (
    CAPABILITY_VOCABULARY,
    AgentLoopDriver,
    FrameworkInstall,
    FrameworkMeta,
    InstallComponent,
)
from narranexus.contracts.mcp_server import McpServerSpec
from narranexus.contracts.route import RouterSpec, plugin_route_prefix
from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.contracts.skill import SkillSpec
from narranexus.contracts.table import ColumnSpec, IndexSpec, MigrationSpec, TableSpec, table_prefix_for
from narranexus.contracts.tool import ToolProvider, ToolSpec
from narranexus.contracts.trigger import TriggerSpec
from narranexus.contracts.ui import Theme
from narranexus.contracts.worker import WorkerHandle, WorkerSpec
from narranexus.kernel.plugins.context import PluginContext
from narranexus.kernel.plugins.hooks import HookImplSpec, hookimpl
from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.services import ServiceRef

__all__ = [
    "API_VERSIONS",
    "CAPABILITY_VOCABULARY",
    "AgentLoopDriver",
    "BundleSpec",
    "ChannelContextBuilderBase",
    "ChannelDescriptor",
    "ChannelModuleBase",
    "ChannelUi",
    "ChatType",
    "ColumnSpec",
    "ContextProvider",
    "Contribution",
    "CredentialField",
    "CredentialSchema",
    "Disposable",
    "DisposableStack",
    "FrameworkInstall",
    "FrameworkMeta",
    "GenericCredentialStore",
    "HookImplSpec",
    "IndexSpec",
    "InstallComponent",
    "McpServerSpec",
    "MessageContentType",
    "MigrationSpec",
    "ModuleConfig",
    "ParsedMessage",
    "PipelineProfile",
    "PluginContext",
    "PluginError",
    "RouterSpec",
    "ServiceRef",
    "SettingField",
    "SettingsSchema",
    "SkillSpec",
    "Stage",
    "StageStrategy",
    "TableSpec",
    "Theme",
    "ToolProvider",
    "ToolSpec",
    "TriggerSpec",
    "WebhookChannelTriggerBase",
    "WorkerHandle",
    "WorkerSpec",
    "WorkingSource",
    "hookimpl",
    "plugin_route_prefix",
    "table_prefix_for",
]


# ── Channel authoring surface (ALPHA) ─────────────────────────────────────
# ``narranexus plugin new … --kinds channel`` used to scaffold seven imports
# from ``narranexus.platform.*`` — modules ``docs/API_POLICY.md`` §1 says are
# NOT a public API and that the griffe gate (``scripts/dev/api_check.sh``,
# which diffs ``contracts`` + ``sdk`` only) never sees change in. Shipping a
# template built on them means the first third-party channel plugin breaks on
# a patch release with no deprecation entry. They are re-exported here so the
# gate covers them; ``STABILITY["channel"]`` stays ALPHA and API_POLICY §2
# says why (batch 4 landed the channel framework one batch ago and the
# credential seam below is still moving).
#
# Re-exported LAZILY (PEP 562): these live in ``narranexus.platform``, which
# pulls the database stack and the module system. The SDK is also what a
# ``narranexus-contracts``-only consumer imports (the CLI's scaffold check,
# a plugin's unit tests), so making every ``import narranexus.sdk`` pay for
# the platform would be a regression in exactly the direction batch 6 spent
# its budget reversing.
#
# FOLLOW-UP (plugin-platform, expires 2026-12-31): ``GenericCredentialStore``
# is a platform CLASS a channel module constructs by hand. It should be a
# ``ServiceRef``/``PluginContext`` seam ("give me this channel's credential
# for this agent") so a plugin never holds the store. Doing that means
# changing ``platform/channel/channel_module_base.py``, which batch 4 owns;
# re-exported as-is here so the template has one import root today.
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — the names below are resolved lazily at runtime
    # Present statically so the griffe gate (scripts/dev/api_check.sh) and type
    # checkers see these as real members of narranexus.sdk. Without this block a
    # PEP 562 __getattr__ is opaque to static analysis, and "re-exported so the
    # gate covers them" would be a claim the gate does not actually honour.
    from narranexus.platform.channel.channel_context_builder_base import ChannelContextBuilderBase
    from narranexus.platform.channel.channel_module_base import ChannelModuleBase
    from narranexus.platform.channel.credential_store import GenericCredentialStore
    from narranexus.platform.channel.webhook_transport import WebhookChannelTriggerBase
    from narranexus.platform.schema.hook_schema import WorkingSource
    from narranexus.platform.schema.module_schema import ModuleConfig
    from narranexus.platform.schema.parsed_message import ChatType, MessageContentType, ParsedMessage

_LAZY_PLATFORM: dict[str, str] = {
    "ChannelContextBuilderBase": "narranexus.platform.channel.channel_context_builder_base",
    "ChannelModuleBase": "narranexus.platform.channel.channel_module_base",
    "WebhookChannelTriggerBase": "narranexus.platform.channel.webhook_transport",
    "GenericCredentialStore": "narranexus.platform.channel.credential_store",
    "WorkingSource": "narranexus.platform.schema.hook_schema",
    "ModuleConfig": "narranexus.platform.schema.module_schema",
    "ParsedMessage": "narranexus.platform.schema.parsed_message",
    "ChatType": "narranexus.platform.schema.parsed_message",
    "MessageContentType": "narranexus.platform.schema.parsed_message",
}


def __getattr__(name: str):
    """Resolve the lazily re-exported channel names (PEP 562)."""
    module_path = _LAZY_PLATFORM.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # resolve once
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
