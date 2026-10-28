"""
@file_name: awareness_module.py
@author: NetMind.AI
@date: 2025-06-06
@description: This file is used to define the awareness of the agent.

Refactoring notes (2025-12-24):
- Use instance_awareness table to replace awareness table
- Data isolation through instance_id
- instance_id obtained from self.instance_id (passed in by ModuleLoader)
"""


from typing import Optional, List, Any
from mcp.server.fastmcp import FastMCP
from loguru import logger


# Module (same package)
from narranexus.platform.module_system import XYZBaseModule, mcp_server_url

# Schema
from narranexus.platform.schema.module_schema import ModuleAgentInstance, ModuleDisplay, ModuleDecisionMeta
from narranexus.platform.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    ModuleInstructions,
)

# Utils
from narranexus.platform.utils import DatabaseClient, get_db_client

# Repository
from narranexus.platform.repository import InstanceRepository, InstanceAwarenessRepository

# Prompts
from narranexus_plugins.awareness_module.prompts import AWARENESS_MODULE_INSTRUCTIONS


class AwarenessModule(XYZBaseModule):
    """
    Awareness Module
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
        
        self.instructions = AWARENESS_MODULE_INSTRUCTIONS 

    @staticmethod
    def get_config() -> ModuleConfig:
        """
        """
        return ModuleConfig(
            name="AwarenessModule",
            discovery_hidden=True,
            agent_instance=ModuleAgentInstance(description="Agent self-awareness and cognitive state management", keywords=["awareness", "self", "cognition"], topic_hint="Agent's self-cognition, goals and state"),
            role="awareness",
            base=True,
            default=True,
            instance_prefix="aware",
            display=ModuleDisplay(icon="🤖", name="Awareness", desc="Agent self-knowledge"),
            decision=ModuleDecisionMeta(
                capabilities=["Perceive current time, date and other environmental info", "Understand user's situation and intent", "Provide context-relevant suggestions"],
                use_cases=["Time-aware conversations", "Context-relevant recommendations", "Environment-related queries"],
                instance_type="persistent",
                typical_instance_id="aware_{uuid8}",
            ),
            priority=3,
            enabled=True,
            description="Provides awareness and perception capabilities"
        )
        
    # ============================================================================= Hooks

    async def gather(self, ctx_data: ContextData) -> ContextData:
        """
        Get awareness data from the instance_awareness table.

        Refactoring notes:
        - Use self.instance_id to query the instance_awareness table
        - If self.instance_id is None, find instance through agent_id + module_class
        - If no record exists, create a default record
        - Use InstanceAwarenessRepository for data access
        """
        logger.debug(f"          → AwarenessModule.data_gathering() started for agent_id={self.agent_id}")
        default_awareness = "(You are a helpful assistant. You do not have any special abilities. Please try to ask the user to update your awareness.)"

        # Get instance_id
        instance_id = await self._get_instance_id()
        if not instance_id:
            logger.warning("            No instance_id found, using default awareness")
            ctx_data.awareness = default_awareness
            return ctx_data

        # Query using InstanceAwarenessRepository
        logger.debug(f"            Querying instance_awareness for instance_id={instance_id}")
        awareness_repo = InstanceAwarenessRepository(self.db)
        awareness_entity = await awareness_repo.get_by_instance(instance_id)

        if not awareness_entity:
            # If no record exists, create a default record
            logger.debug("            No awareness record found, creating default record")
            await awareness_repo.upsert(instance_id, default_awareness)
            awareness = default_awareness
            logger.debug(f"            Default awareness created: {awareness[:50]}...")
        else:
            # Extract the value of the "awareness" field
            awareness = awareness_entity.awareness
            logger.debug(f"            Awareness loaded from DB: {awareness[:50]}...")

        # Assign awareness string to ctx_data
        ctx_data.awareness = awareness
        logger.debug("          AwarenessModule.data_gathering() completed")

        return ctx_data

    async def _get_instance_id(self) -> Optional[str]:
        """
        Get the current Module's instance_id

        Prioritizes self.instance_id; if None, looks up through agent_id + module_class.
        AwarenessModule is an Agent-level module (is_public=1), each Agent has only one instance.
        """
        if self.instance_id:
            return self.instance_id

        # Look up through agent_id + module_class
        try:
            instance_repo = InstanceRepository(self.db)
            instances = await instance_repo.get_by_agent(
                agent_id=self.agent_id,
                module_class="AwarenessModule"
            )
            if instances:
                self.instance_id = instances[0].instance_id
                return self.instance_id
        except Exception as e:
            logger.warning(f"Failed to get instance_id: {e}")

        return None


    # ============================================================================= MCP Server
    
    async def mcp_server(self) -> Optional[MCPServerConfig]:
        """
        """
        return MCPServerConfig(
            server_name="awareness_module",
            server_url=mcp_server_url("awareness_module"),
            type="sse"
        )
        
    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server, providing the update_awareness tool

        Refactoring notes:
        - Use instance_awareness table to replace awareness table
        - Data isolation through instance_id
        """

        mcp = FastMCP("awareness_module")

        @mcp.tool()
        async def update_awareness(agent_id: str, new_awareness: str) -> str:
            """
            Update the agent's awareness profile with user preferences.

            ## When to Update

            **Immediately** when user:
            - Gives explicit preference: "Please always...", "I prefer...", "Don't..."
            - Provides feedback: "That was too long", "I liked that format"
            - Defines agent role: "You are my...", "Your job is to..."
            - Expresses style preference: "Be more concise", "Use technical terms"

            **After pattern observation (2-3 occurrences)** for:
            - Topic switching patterns (focused work vs. multi-tasking)
            - Task handling preferences (atomic steps vs. holistic)
            - Response format engagement (lists vs. paragraphs)

            **Do NOT update** for:
            - One-time task instructions
            - Temporary/situational requests

            ## Required Format

            Provide COMPLETE Markdown profile:

            ```markdown
            # Agent Awareness Profile

            ## 1. Narrative Management Preferences (Topic Organization)
            ### Topic Continuity Style
            - [observations]
            ### Topic Transition Preferences
            - [observations]
            ### Long-term Project Organization
            - [observations]

            ---

            ## 2. Task Decomposition Preferences (Work Style)
            ### Task Granularity
            - [observations]
            ### Tool Usage Patterns
            - [observations]
            ### Proactivity Level
            - [observations]
            ### Background Task Preferences
            - [observations]

            ---

            ## 3. Communication Style Preferences (Interaction)
            ### Tone and Voice
            - [observations]
            ### Response Format
            - [observations]
            ### Explanation Depth
            - [observations]
            ### Language Preferences
            - [observations]

            ---

            ## 4. Role and Identity
            ### Role Definition
            - [definition]
            ### Capability Boundaries
            - [boundaries]
            ### Behavioral Principles
            - [principles]
            ```

            ## Merge Strategy
            1. Preserve existing valid preferences
            2. Add new observations under appropriate sections
            3. Update/remove outdated preferences if user changes mind
            4. Always include all four sections

            Args:
                agent_id: Agent's unique identifier
                new_awareness: Complete awareness profile in Markdown format

            Returns:
                Success or error message
            """
            # Route through the AgentDataStore seam: DirectStore (local, unchanged
            # DB access) or HttpStore (cloud, backend API — no db creds in mcp),
            # chosen by NARRANEXUS_BACKEND_URL. Blueprint P0 (behaviour-preserving).
            from narranexus.platform.module_system.data_access import get_agent_data_store
            return await get_agent_data_store().update_awareness(agent_id, new_awareness)

        @mcp.tool()
        async def update_agent_profile(
            agent_id: str,
            new_name: Optional[str] = None,
            new_description: Optional[str] = None,
        ) -> str:
            """
            Record who you are: your display name and/or your one-line description.

            Call this during bootstrap once your creator has told you what you
            are for — and again whenever that changes.

            **Your description is read by OTHER AGENTS, not by humans.** It is
            how a peer decides whether to route a question to you ("who can
            review a lesson plan?"). Write one plain line saying what you do and
            what to ask you for. Do not write marketing copy, and do not leave
            it empty: an agent with no description cannot be found by the peers
            who need it, and its owner's requests to "go ask X" fail.

            Renaming also files a dated correction into your Awareness profile,
            because your memories of being called something else do not update
            themselves — and the old name may now belong to a different agent.

            Args:
                agent_id: Agent's unique identifier
                new_name: New display name (omit to leave the name unchanged)
                new_description: New one-line description for peers (omit to
                    leave the description unchanged)

            Returns:
                Success or error message. Read it: it also tells you when the
                name you chose is already in use by another of your owner's
                agents.
            """
            # Route through the AgentDataStore seam: DirectStore (local, the
            # unchanged rename transaction) or HttpStore (cloud, backend API —
            # no db creds in mcp), chosen by NARRANEXUS_BACKEND_URL. The whole
            # rename transaction (name/description, identity-note correction,
            # same-owner clash note, discovery refresh) lives in the shared
            # update_agent_profile_from_args (_awareness_writes) so this path
            # and the backend twin route stay byte-identical.
            from narranexus.platform.module_system.data_access import get_agent_data_store
            return await get_agent_data_store().update_agent_profile(
                agent_id, new_name, new_description
            )

        return mcp

    # ============================================================================= Database

    async def init_database_tables(self):
        """
        Initialize the instance_awareness table

        Table structure is managed by create_instance_awareness_table.py
        """
        db = await get_db_client()
        await db.create_table("""
            CREATE TABLE IF NOT EXISTS instance_awareness (
                id INT AUTO_INCREMENT PRIMARY KEY,
                instance_id VARCHAR(64) NOT NULL UNIQUE,
                awareness TEXT NOT NULL,
                created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
            )
        """)
        
    
if __name__ == "__main__":
    
    import asyncio
    asyncio.run(AwarenessModule("test_agent_id").init_database_tables())
    