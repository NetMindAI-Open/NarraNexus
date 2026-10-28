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

from typing import Any, Optional, List
from loguru import logger


# Module (same package)
from narranexus.platform.module_system import XYZBaseModule
from narranexus.platform.module_system.base import mcp_server_url

# Schema
from narranexus.platform.schema.module_schema import ModuleAgentInstance, ModuleDisplay, ModuleDecisionMeta
from narranexus.platform.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    ModuleInstructions,
    is_agent_description_unset,
)

# Utils
from narranexus.platform.utils import DatabaseClient
from narranexus.platform.utils.timezone import format_now_for_agent

# Prompts
from narranexus_plugins.basic_info_module.prompts import (
    BASIC_INFO_MODULE_INSTRUCTIONS,
    BASIC_INFO_MODULE_INSTRUCTIONS_STABLE,
    BASIC_INFO_REAL_WORLD_TURN_TEMPLATE,
    DEPLOYMENT_CONTEXT_CLOUD,
    DEPLOYMENT_CONTEXT_LOCAL,
)
from narranexus.platform.utils.deployment_mode import get_deployment_mode

# Settings (leaf module, safe to import at module level)
from narranexus.platform.settings import settings


# What the agent reads about itself when no description has been written yet.
# Deliberately an INSTRUCTION, not a label: the field is injected into the
# system prompt, and "No description" invited the agent to conclude it was
# unconfigured (the placeholder that used to sit here did so explicitly, and a
# peer asking "are you set up?" got "not configured yet" (还没配置完成) back — P1 section 02).
UNSET_AGENT_DESCRIPTION_NOTICE = (
    "(not recorded yet — ask your creator what you are for, then save it with "
    "update_agent_profile so other agents can find you)"
)

# The "now" renderer moved to utils/timezone.format_now_for_agent (2026-08-18).
# It is no longer this Module's private concern: the date MCP tools and the
# diagnostic temporal guard must produce and compare the SAME bytes, and
# importing a Module from either of those would break 铁律 #3.

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
        # MCP port for the narrative-awareness tools (Fix #2 P3). Registered in
        # module_runner CORE_MODULE_PORTS. 7808 = next free after CommonTools(7807).

    @staticmethod
    def get_config() -> ModuleConfig:
        """
        Return Basic Info Module configuration
        """
        return ModuleConfig(
            name="BasicInfoModule",
            discovery_hidden=True,
            agent_instance=ModuleAgentInstance(description="Basic information and environment context", keywords=["basic", "info", "time", "context"], topic_hint="Basic information, time, environment context"),
            base=True,
            default=True,
            instance_prefix="info",
            display=ModuleDisplay(icon="ℹ️", name="BasicInfo", desc="Time, location utilities"),
            decision=ModuleDecisionMeta(
                capabilities=["Manage user basic information", "Store and retrieve preference settings", "Maintain user profiles"],
                use_cases=["User information queries", "Preference settings management", "Personalized services"],
                instance_type="persistent",
                typical_instance_id="info_{uuid8}",
            ),
            priority=2,
            enabled=True,
            description="Provides basic information capabilities"
        )

    # ============================================================================= Instructions

    async def contribute_instructions(self, ctx_data: ContextData) -> str:
        """Render the module instruction, selecting the template by the R4
        relocation flag.

        Flag ON  → stable template ({current_time} span replaced by a static
                   pointer) so the output is byte-stable across turns; the
                   volatile span travels via contribute_turn_context() instead.
        Flag OFF → untouched legacy template, functionally equivalent to pre-R4.

        Same include_volatile split as the narrative prompt builder
        (PromptBuilder.build_main_prompt), with the flag read here because
        the contribute_instructions signature is fixed by the base-module contract.
        """
        self.instructions = (
            BASIC_INFO_MODULE_INSTRUCTIONS_STABLE
            if settings.prompt_turn_context_relocation_enabled
            else BASIC_INFO_MODULE_INSTRUCTIONS
        )
        return await super().contribute_instructions(ctx_data)

    async def contribute_turn_context(self, ctx_data: ContextData) -> str:
        """Per-turn volatile span: the "Real World Information" section.

        Wording is byte-identical to the legacy in-template section (R4:
        relocated, never dropped). Empty when gather did not
        populate current_time — the temporal ground truth is best-effort,
        same as before.
        """
        if not ctx_data.current_time:
            return ""
        return BASIC_INFO_REAL_WORLD_TURN_TEMPLATE.format(
            current_time=ctx_data.current_time
        )

    # ============================================================================= Hooks

    async def gather(self, ctx_data: ContextData) -> ContextData:
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
        ctx_data.current_time = format_now_for_agent(user_tz)

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

        # 1.6. Runtime LLM identity — the agent's REAL framework + model,
        # resolved from the same slot overlay the runtime dispatches on
        # (single source of truth; `_resolve_agent_framework_name` delegates
        # to it too). Replaces a formerly hardcoded "Claude Agent SDK /
        # sonnet-4" that made every agent misreport itself. The resolver
        # itself never raises; the try/except here only guards the local
        # import + any truly unexpected error, and still sets safe non-None
        # strings so the prompt's `.format()` never renders "None".
        try:
            from narranexus.platform.agent_framework.providers.model_identity import (
                resolve_agent_model_identity,
            )
            identity = await resolve_agent_model_identity(self.agent_id, self.db)
            ctx_data.agent_info_model_type = identity.framework_display
            ctx_data.model_name = identity.model or "(provider default)"
        except Exception as e:  # noqa: BLE001 — identity is best-effort
            logger.warning(f"            Failed to resolve model identity: {e}")
            ctx_data.agent_info_model_type = "the configured agent runtime"
            ctx_data.model_name = "(unknown)"

        # 2. Get Agent information from database
        try:
            from narranexus.platform.repository import AgentRepository
            agent_repo = AgentRepository(self.db)
            agent = await agent_repo.get_agent(self.agent_id)

            if agent:
                from narranexus.platform.repository import UserRepository
                user_repo = UserRepository(self.db)

                ctx_data.agent_name = agent.agent_name or "Unknown Agent"
                # An unset description must read as "nobody has written this
                # yet, go write it" — never as prose. The creation placeholder
                # used to land here verbatim, so an agent read
                # "I am a new agent ready for configuration" about ITSELF and
                # said so when a peer asked whether it was configured
                # (P1 section 02). See AwarenessModule §5 for the tool it needs.
                ctx_data.agent_description = (
                    UNSET_AGENT_DESCRIPTION_NOTICE
                    if is_agent_description_unset(agent.agent_description)
                    else agent.agent_description
                )
                ctx_data.creator_id = agent.created_by  # opaque key, not for display
                # Creator's HUMAN name (NetMind nickname / local display name).
                # user_id stays an opaque key; this is what the LLM reads.
                ctx_data.creator_name = await user_repo.get_display_name(agent.created_by)

                # 3. Who is the CURRENT SENDER, and are they the Creator?
                # agent_runtime overrides self.user_id to the owner (created_by),
                # so self.user_id can't tell visitor from owner. The real sender
                # rides in extra_data.sender_user_id (set by the chat trigger);
                # absent that (IM / job / owner self-chat) we fall back to the
                # owner identity.
                extra = ctx_data.extra_data or {}
                sender_id = extra.get("sender_user_id") or self.user_id
                ctx_data.is_creator = (sender_id == agent.created_by)
                ctx_data.user_role = "Creator (Boss)" if ctx_data.is_creator else "User/Customer"
                ctx_data.current_speaker_name = (
                    ctx_data.creator_name
                    if ctx_data.is_creator
                    else await user_repo.get_display_name(sender_id)
                )

                logger.debug(f"            Agent info loaded: name={agent.agent_name}, creator={ctx_data.creator_name}")
                logger.debug(f"            Current sender={sender_id}, is_creator={ctx_data.is_creator}, speaker={ctx_data.current_speaker_name}")
            else:
                logger.warning(f"            Agent not found: {self.agent_id}")
                ctx_data.is_creator = False
                ctx_data.user_role = "User/Customer"
                ctx_data.agent_name = "Unknown Agent"
                ctx_data.agent_description = UNSET_AGENT_DESCRIPTION_NOTICE
                ctx_data.creator_id = "Unknown"
                ctx_data.creator_name = "Unknown"
                ctx_data.current_speaker_name = "Unknown"

        except Exception as e:
            logger.exception(f"            Failed to load agent info: {e}")
            ctx_data.is_creator = False
            ctx_data.user_role = "User/Customer"
            ctx_data.agent_name = "Unknown Agent"
            ctx_data.agent_description = UNSET_AGENT_DESCRIPTION_NOTICE
            ctx_data.creator_id = "Unknown"
            ctx_data.creator_name = "Unknown"
            ctx_data.current_speaker_name = "Unknown"

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
            from narranexus.platform.repository.user_repository import UserRepository
            tz = await UserRepository(self.db).get_user_timezone(self.user_id)
            return tz or "UTC"
        except Exception as e:
            logger.debug(f"_get_user_timezone fallback to UTC: {e}")
            return "UTC"

    # ============================================================================= MCP Server

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration.

        2026-05-20 (Fix #2 P3): basic_info now hosts the narrative-awareness
        tools (view_narrative / view_event / switch_narrative / create_narrative)
        on an SSE MCP server, so it advertises a real URL instead of "None".
        """
        return MCPServerConfig(
            server_name="basic_info_module",
            server_url=mcp_server_url("basic_info_module"),
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        from narranexus_plugins.basic_info_module._basic_info_mcp_tools import (
            create_basic_info_mcp_server,
        )
        return create_basic_info_mcp_server()
