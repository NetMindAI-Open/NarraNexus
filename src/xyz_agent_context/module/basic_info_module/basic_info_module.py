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
from xyz_agent_context.utils.timezone import (
    utc_now,
    to_user_timezone,
    is_valid_timezone,
)

# Prompts
from xyz_agent_context.module.basic_info_module.prompts import (
    BASIC_INFO_MODULE_INSTRUCTIONS,
    DEPLOYMENT_CONTEXT_CLOUD,
    DEPLOYMENT_CONTEXT_LOCAL,
)
from xyz_agent_context.utils.deployment_mode import get_deployment_mode


_WEEKDAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


def _format_current_time_for_agent(user_tz: str) -> str:
    """Render the current time in a form an agent can reason about.

    Previous implementation used ``datetime.now().isoformat()`` which has
    three problems:
      1. Naive datetime (no timezone suffix) — the agent couldn't tell if
         it was UTC, server-local, or user-local. Observed symptom: agent
         would see a timestamp that disagreed with a search result and
         explain it away as "server relative time" instead of catching
         the mismatch.
      2. Used server's local clock, which diverges from the user's
         perceived "now" when backend runs in UTC and user is in Asia.
      3. No weekday / human-readable anchor for day-of-week reasoning
         ("this week's meeting" etc.).

    We now emit: ``2026-04-21 17:45:08 +08:00 (Tuesday, Asia/Shanghai)``
    — explicit offset, resolved in the user's timezone, weekday labelled.
    Falls back to UTC when user_tz is unknown or invalid.
    """
    now_utc = utc_now()
    # Validate tz up front so we label with "UTC" instead of echoing the
    # invalid string (keeps the time correct either way).
    effective_tz = user_tz if user_tz and is_valid_timezone(user_tz) else "UTC"
    local = to_user_timezone(now_utc, effective_tz)
    if local is None:
        local = now_utc

    weekday = _WEEKDAY_NAMES[local.weekday()]
    # ISO-ish but with a space instead of T for readability, and an explicit
    # UTC offset. Example: "2026-04-21 17:45:08 +08:00".
    base = local.strftime("%Y-%m-%d %H:%M:%S %z")
    # %z gives "+0800"; insert the colon for "+08:00" (what LLMs commonly see).
    if len(base) >= 5 and base[-5] in ("+", "-"):
        base = base[:-2] + ":" + base[-2:]
    return f"{base} ({weekday}, {effective_tz})"

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

        # 1. Get current time — resolved in the user's timezone, with
        # explicit UTC offset and weekday so the agent can reliably sanity-
        # check time references in search results / scheduling tools.
        user_tz = await self._get_user_timezone()
        ctx_data.current_time = _format_current_time_for_agent(user_tz)

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
            logger.exception(f"            Failed to load agent info: {e}")
            ctx_data.is_creator = False
            ctx_data.user_role = "User/Customer"
            ctx_data.agent_name = "Unknown Agent"
            ctx_data.agent_description = "No description"
            ctx_data.creator_id = "Unknown"

        logger.debug("          BasicInfoModule.data_gathering() completed")
        return ctx_data

    async def _get_user_timezone(self) -> str:
        """Look up the current user's preferred timezone (IANA string).

        Falls back to UTC if lookup fails or user has no preference set.
        Kept lenient because `current_time` injection is best-effort —
        a missing tz should degrade to "unknown tz" rather than fail the
        whole turn.
        """
        if not self.user_id or not self.db:
            return "UTC"
        try:
            from xyz_agent_context.repository.user_repository import UserRepository
            tz = await UserRepository(self.db).get_user_timezone(self.user_id)
            return tz or "UTC"
        except Exception as e:
            logger.debug(f"_get_user_timezone fallback to UTC: {e}")
            return "UTC"

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
