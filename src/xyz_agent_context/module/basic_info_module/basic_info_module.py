"""
@file_name: basic_info_module.py
@author: NetMind.AI
@date: 2025-11-18
@description: Basic Info Module - Provides basic information capabilities

According to the design document:
- Basic Info Module provides basic information capabilities, such as user info, Agent info, environment info, etc.
- Contains: Instructions (how to use basic_info), Tools (retrieve basic info), Data (basic info)
- Note: Basic Info Module itself does not include "multi-turn conversation" capability; multi-turn conversation requires Social-Network or Memory modules
"""

from datetime import datetime
from typing import Optional, List
from loguru import logger


# Module (same package)
from xyz_agent_context.module import XYZBaseModule

# Schema
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    ModuleInstructions,
)

# Utils
from xyz_agent_context.utils import DatabaseClient

# Prompts
from xyz_agent_context.module.basic_info_module.prompts import (
    BASIC_INFO_MODULE_INSTRUCTIONS,
    DEPLOYMENT_CONTEXT_CLOUD,
    DEPLOYMENT_CONTEXT_LOCAL,
)
from xyz_agent_context.utils.deployment_mode import get_deployment_mode

class BasicInfoModule(XYZBaseModule):
    """
    Basic Info Module
    """
    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        
        self.instructions = BASIC_INFO_MODULE_INSTRUCTIONS 

    def get_config(self) -> ModuleConfig:
        """
        Return Basic Info Module configuration
        """
        return ModuleConfig(
            name="BasicInfoModule",
            priority=2,
            enabled=True,
            description="Provides basic information capabilities"
        )
        
    # ============================================================================= Hooks

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Collect basic information

        Retrieve Agent information from the database, including:
        - agent_name: Agent name
        - agent_description: Agent description
        - creator_id: Creator ID (boss/owner)
        - is_creator: Whether the current conversation user is the Creator
        """
        logger.debug(f"          → BasicInfoModule.data_gathering() started for agent_id={self.agent_id}")

        # 1. Get current time
        current_time = datetime.now().isoformat()
        ctx_data.current_time = current_time

        # 1.5. Deployment environment — tell the agent whether it's
        # running on a shared cloud server or the user's own machine.
        # The two modes have fundamentally different filesystem / global-
        # install / credential semantics; the rest of the rule system
        # (SkillModule prompts, _tool_policy_guard) keys off this.
        mode = get_deployment_mode()
        ctx_data.deployment_mode = mode
        ctx_data.deployment_context = (
            DEPLOYMENT_CONTEXT_CLOUD if mode == "cloud"
            else DEPLOYMENT_CONTEXT_LOCAL
        )

        # 2. Get Agent information from database
        try:
            from xyz_agent_context.repository import AgentRepository
            agent_repo = AgentRepository(self.db)
            agent = await agent_repo.get_agent(self.agent_id)

            if agent:
                ctx_data.agent_name = agent.agent_name or "Unknown Agent"
                ctx_data.agent_description = agent.agent_description or "No description"
                ctx_data.creator_id = agent.created_by

                # 3. Determine whether the current user is the Creator, and set user role description
                ctx_data.is_creator = (self.user_id == agent.created_by)
                ctx_data.user_role = "Creator (Boss)" if ctx_data.is_creator else "User/Customer"

                logger.debug(f"            Agent info loaded: name={agent.agent_name}, creator={agent.created_by}")
                logger.debug(f"            Current user={self.user_id}, is_creator={ctx_data.is_creator}, user_role={ctx_data.user_role}")
            else:
                logger.warning(f"            Agent not found: {self.agent_id}")
                ctx_data.is_creator = False
                ctx_data.user_role = "User/Customer"
                ctx_data.agent_name = "Unknown Agent"
                ctx_data.agent_description = "No description"
                ctx_data.creator_id = "Unknown"

        except Exception as e:
            logger.error(f"            Failed to load agent info: {e}")
            ctx_data.is_creator = False
            ctx_data.user_role = "User/Customer"
            ctx_data.agent_name = "Unknown Agent"
            ctx_data.agent_description = "No description"
            ctx_data.creator_id = "Unknown"

        logger.debug("          BasicInfoModule.data_gathering() completed")
        return ctx_data

    # ============================================================================= MCP Server

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration
        """
        return MCPServerConfig(
            server_name="basic_info_module",
            server_url="",
            type="None"
        )
