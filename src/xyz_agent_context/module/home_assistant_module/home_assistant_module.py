"""
@file_name: home_assistant_module.py
@author: NetMind.AI
@date: 2026-07-14
@description: HomeAssistantModule — query and control the user's smart-home
devices through their Home Assistant instance.

Capability module (auto-loaded). Exposes 4 MCP tools that proxy Home Assistant's
REST API (list/get entities, list services, call a service). Talks ONLY to HA's
Apache-2.0 API via a per-instance binding (base_url + Long-Lived Token) — no
Xiaomi/Miloco code, brand-agnostic. Scene/routine logic lives in Awareness
(binding rule #4); this module stays generic. MCP port 7810.
"""

from typing import Any, List, Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.module import XYZBaseModule, mcp_host
from xyz_agent_context.module.home_assistant_module._home_assistant_impl.binding import resolve_client
from xyz_agent_context.module.home_assistant_module._home_assistant_impl.ha_client import HAError
from xyz_agent_context.module.home_assistant_module.prompts import HOME_ASSISTANT_MODULE_INSTRUCTIONS
from xyz_agent_context.schema import MCPServerConfig, ModuleConfig
from xyz_agent_context.utils import DatabaseClient


def _summarize_entity(state: dict) -> dict:
    """Compact one HA state dict for listing (drop the heavy attributes blob)."""
    attrs = state.get("attributes") or {}
    return {
        "entity_id": state.get("entity_id"),
        "name": attrs.get("friendly_name") or state.get("entity_id"),
        "state": state.get("state"),
    }


class HomeAssistantModule(XYZBaseModule):
    """Smart-home query/control via the user's Home Assistant."""

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.instructions = HOME_ASSISTANT_MODULE_INSTRUCTIONS
        self.port = 7810

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="HomeAssistantModule",
            priority=12,
            enabled=True,
            description="Query and control smart-home devices via the user's Home Assistant.",
            module_type="capability",
        )

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="home_assistant_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        mcp = FastMCP("home_assistant_module")
        mcp.settings.port = self.port

        @mcp.tool()
        async def ha_list_entities(agent_id: str, domain: Optional[str] = None) -> Any:
            """List the user's Home Assistant entities (devices) and their current state.

            Args:
                agent_id: The calling agent's id.
                domain: Optional filter, e.g. "light" / "switch" / "climate" /
                    "cover" / "sensor". Omit to list everything.

            Returns:
                {"count": N, "entities": [{entity_id, name, state}, ...]} or an
                actionable message if Home Assistant isn't connected.
            """
            db = await HomeAssistantModule.get_mcp_db_client()
            client, err = await resolve_client(db, agent_id)
            if client is None:
                return err
            try:
                states = await client.list_states()
            except HAError as e:
                return str(e)
            if domain:
                states = [s for s in states if str(s.get("entity_id", "")).startswith(f"{domain}.")]
            return {"count": len(states), "entities": [_summarize_entity(s) for s in states]}

        @mcp.tool()
        async def ha_get_entity(agent_id: str, entity_id: str) -> Any:
            """Get one entity's full state + attributes (e.g. brightness, temperature).

            Args:
                agent_id: The calling agent's id.
                entity_id: e.g. "light.living_room".
            """
            db = await HomeAssistantModule.get_mcp_db_client()
            client, err = await resolve_client(db, agent_id)
            if client is None:
                return err
            try:
                return await client.get_state(entity_id)
            except HAError as e:
                return str(e)

        @mcp.tool()
        async def ha_list_services(agent_id: str, domain: Optional[str] = None) -> Any:
            """Discover which services (actions) a domain supports, e.g. light.turn_on.

            Args:
                agent_id: The calling agent's id.
                domain: Optional filter, e.g. "light". Omit to list all domains.
            """
            db = await HomeAssistantModule.get_mcp_db_client()
            client, err = await resolve_client(db, agent_id)
            if client is None:
                return err
            try:
                services = await client.list_services()
            except HAError as e:
                return str(e)
            if domain:
                services = [s for s in services if s.get("domain") == domain]
            return services

        @mcp.tool()
        async def ha_call_service(
            agent_id: str,
            domain: str,
            service: str,
            entity_id: Optional[str] = None,
            data: Optional[dict] = None,
        ) -> Any:
            """Control a device by calling a Home Assistant service.

            Examples: domain="light", service="turn_on", entity_id="light.living_room";
            domain="climate", service="set_temperature", entity_id=..., data={"temperature": 22}.

            CONFIRM WITH THE USER FIRST for high-impact actions (locks, alarms,
            garage doors). Low-impact actions (lights, fans) don't need confirmation.

            Args:
                agent_id: The calling agent's id.
                domain: Service domain, e.g. "light".
                service: Service name, e.g. "turn_on".
                entity_id: Target entity (optional if the service is area/global).
                data: Extra service data, e.g. {"temperature": 22, "brightness": 200}.
            """
            db = await HomeAssistantModule.get_mcp_db_client()
            client, err = await resolve_client(db, agent_id)
            if client is None:
                return err
            try:
                result = await client.call_service(domain, service, entity_id=entity_id, data=data)
                return {"ok": True, "result": result}
            except HAError as e:
                logger.warning(f"ha_call_service failed ({domain}.{service}): {e}")
                return {"ok": False, "error": str(e)}

        return mcp
