"""
@file_name: awareness.py
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
from xyz_agent_context.module import XYZBaseModule, mcp_host

# Schema
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    ModuleInstructions,
)

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client

# Repository
from xyz_agent_context.repository import InstanceRepository, InstanceAwarenessRepository

# Prompts
from xyz_agent_context.module.awareness_module.prompts import AWARENESS_MODULE_INSTRUCTIONS


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
        self.port = 7801

    def get_config(self) -> ModuleConfig:
        """
        """
        return ModuleConfig(
            name="AwarenessModule",
            priority=3,
            enabled=True,
            description="Provides awareness and perception capabilities"
        )
        
    # ============================================================================= Hooks

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
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
    
    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        """
        return MCPServerConfig(
            server_name="awareness_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
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
        mcp.settings.port = self.port

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
            # Use MCP-dedicated database connection
            db = await AwarenessModule.get_mcp_db_client()

            # Find instance_id through agent_id + module_class
            from xyz_agent_context.repository import InstanceRepository, InstanceAwarenessRepository
            instance_repo = InstanceRepository(db)
            instances = await instance_repo.get_by_agent(
                agent_id=agent_id,
                module_class="AwarenessModule"
            )

            if not instances:
                return f"Error: No AwarenessModule instance found for agent_id={agent_id}"

            instance_id = instances[0].instance_id

            # Use InstanceAwarenessRepository to update awareness
            awareness_repo = InstanceAwarenessRepository(db)
            await awareness_repo.upsert(instance_id, new_awareness)
            return "Awareness updated successfully"

        @mcp.tool()
        async def update_agent_name(agent_id: str, new_name: str) -> str:
            """
            Update the agent's display name.
            Call this when your creator tells you what your name should be during bootstrap setup.

            Args:
                agent_id: Agent's unique identifier
                new_name: The new display name chosen by the creator

            Returns:
                Success or error message
            """
            db = await AwarenessModule.get_mcp_db_client()

            from xyz_agent_context.repository import AgentRepository
            repo = AgentRepository(db)

            agent = await repo.get_agent(agent_id)
            if not agent:
                return f"Error: Agent {agent_id} not found"

            affected = await repo.update_agent(agent_id, {"agent_name": new_name})
            if affected > 0:
                return f"Agent name updated to '{new_name}' successfully"
            else:
                return "Error: No changes made — agent name may already be set to this value"

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
    