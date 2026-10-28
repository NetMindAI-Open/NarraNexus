"""
@file_name: browser_module.py
@author:
@date: 2026-09-22
@description: Optional browser capability on the shared MCP host.

The module exposes browser operations and human handoff. Browser lifecycle,
permission checks and control arbitration belong to the platform service.
Site-specific workflows belong to each agent's Awareness and skills.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from narranexus.platform.browser.browser_service import BrowserService
from narranexus.platform.module_system import XYZBaseModule, mcp_server_url
from narranexus.platform.schema import ContextData, MCPServerConfig, ModuleConfig
from narranexus.platform.schema.module_schema import ModuleAgentInstance
from narranexus.platform.utils import DatabaseClient
from narranexus_plugins.browser_module._browser_module_impl.tools import policy_context, register_tools
from narranexus_plugins.browser_module.prompts import BROWSER_MODULE_INSTRUCTIONS


def get_service() -> BrowserService:
    """Use the same process-wide session registry as the MCP stream route."""
    from narranexus.platform.browser.browser_service import get_shared_service

    return get_shared_service()


class BrowserModule(XYZBaseModule):
    """Drive a real browser the user can watch and take over."""

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.instructions = BROWSER_MODULE_INSTRUCTIONS

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="BrowserModule",
            agent_instance=ModuleAgentInstance(
                description="Drive a real browser the user can watch and take over",
                keywords=["browser", "web", "website", "login", "form", "click", "screenshot"],
                topic_hint="Operate a website in a real browser",
            ),
            priority=13,
            enabled=True,
            description="Operate websites in a real browser, with the user able to take over.",
            module_type="capability",
        )

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="browser_module",
            server_url=mcp_server_url("browser_module"),
            type="sse",
        )

    async def gather(self, ctx_data: ContextData) -> ContextData:
        """Collect runtime readiness without blocking the shared event loop."""
        try:
            status = await asyncio.to_thread(get_service().status)
            ctx_data.extra_data["browser_runtime"] = status.to_dict()
        except Exception:
            logger.exception("Could not collect browser runtime status")
            ctx_data.extra_data["browser_runtime"] = {
                "state": "unknown", "reason": "status-unavailable",
            }
        try:
            ctx_data.extra_data["browser_policy"] = await policy_context(get_service(), self.agent_id)
        except Exception:
            logger.exception("Could not collect browser policy for {}", self.agent_id)
            ctx_data.extra_data["browser_policy"] = {"state": "unknown"}
        return ctx_data

    async def contribute_turn_context(self, ctx_data: ContextData) -> str:
        """Render the gathered signal in the runtime's actual volatile prompt."""
        status = ctx_data.extra_data.get("browser_runtime")
        if not status:
            return ""
        visible = {key: status.get(key) for key in ("state", "reason", "version")}
        block = "Browser runtime: " + json.dumps(visible, ensure_ascii=True)
        block += "\nBrowser profile: persistent per agent; login state is unverified until the page is inspected."
        policy = ctx_data.extra_data.get("browser_policy")
        if policy:
            block += "\nConfigured browser policy: " + json.dumps(policy, ensure_ascii=True)
        if status.get("state") in {"absent", "installing"}:
            block += "\nBrowser installation and progress: settings/browser."
        elif status.get("state") == "unknown":
            block += "\nCall browser_status to check availability."
        return block

    def create_mcp_server(self) -> Optional[Any]:
        mcp = FastMCP("browser_module")
        register_tools(mcp, get_service)
        return mcp
