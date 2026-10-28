"""
@file_name: nexus_plugins_module.py
@author: Bin Liang
@date: 2026-09-03
@description: The module shell: instructions inject the agent's plugin summary; the MCP server exposes the plugin_* tools.

Shape follows ``SkillModule``: a capability module that always loads, a
compact instruction block, and a stateless MCP server whose tools take
``agent_id``/``user_id``. Local only — on cloud the instructions are empty
and every tool refuses (D1: cloud ships the standard build). The module is
itself a plugin (``builtin.nexus_plugins_module``) marked ``protected``: no
tool here can touch it, disable it, or edit the kernel.
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from narranexus.kernel.deployment import is_cloud_mode
from narranexus.platform.module_system.base import XYZBaseModule, mcp_server_url
from narranexus.platform.schema.context_schema import ContextData
from narranexus.platform.schema.module_schema import MCPServerConfig, ModuleConfig

class NexusPluginsModule(XYZBaseModule):
    """Agent-facing self-extension: scaffold → validate → test → register → canary → observe."""

    def __init__(self, agent_id: str, user_id: Optional[str], database_client: Any = None, instance_id: Optional[str] = None, instance_ids: Optional[list[str]] = None):
        super().__init__(agent_id=agent_id, user_id=user_id, database_client=database_client, instance_id=instance_id, instance_ids=instance_ids)

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="NexusPluginsModule",
            always_load=True,
            priority=95,
            enabled=True,
            description="Lets the agent write, test, register and observe plugins for its own instance",
            module_type="capability",
        )

    async def contribute_instructions(self, ctx_data: ContextData) -> str:
        if is_cloud_mode():
            return ""
        from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.state import summary_for_agent

        summary = summary_for_agent(self.agent_id)
        return (
            "## Plugins (self-extension)\n"
            "You can extend THIS instance with plugins: plugin_docs → plugin_scaffold → plugin_edit → "
            "plugin_validate → plugin_test → plugin_register → plugin_activate(scope=agent). Activation needs the "
            "user's approval (a card is shown); observe with plugin_observe before asking for global scope. "
            "Never put credentials in plugin files (use settings). "
            "Self-awareness: platform_overview (what you run on), platform_slots(domain) and contract_docs(kind) "
            "(what can be replaced and how), agent_self (your capabilities, model slots, prompt sections), "
            "capability_set(module_class, enabled) to switch one of your own capabilities. "
            f"State: {summary}\n"
        )

    async def contribute_turn_context(self, ctx_data: ContextData) -> str:
        return ""

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        if is_cloud_mode():
            return None
        return MCPServerConfig(server_name="nexus_plugins_module", server_url=mcp_server_url("nexus_plugins_module"), type="sse")

    def create_mcp_server(self) -> Optional[Any]:
        from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.tools import create_nexus_plugins_mcp_server

        return create_nexus_plugins_mcp_server()


__all__ = ["NexusPluginsModule"]
