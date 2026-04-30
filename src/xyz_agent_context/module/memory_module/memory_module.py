"""
@file_name: memory_module.py
@author: NetMind.AI
@date: 2026-02-05
@description: MemoryModule - the sole entry point for memory management

Responsibilities:
1. Service Methods: called directly by external components
   - search_evermemos(): called by NarrativeService Step 1
   - write_to_evermemos(): write to EverMemOS
2. Hook Methods: automatically executed during Module lifecycle
   - hook_data_gathering: extract semantic memories from EverMemOS results cached in Step 1
   - hook_after_event_execution: write current conversation to EverMemOS
3. get_instructions: tell the LLM how to use the semantic memory section

Architecture notes (refer to docs/MEMORY_MODULE_REFACTOR.md):
- MemoryModule is the sole external interface for memory management
- NarrativeService retrieves memories via MemoryModule.search_evermemos()
- Writing is done via MemoryModule.write_to_evermemos() or hook_after_event_execution()
"""

from __future__ import annotations

import asyncio
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from loguru import logger

from xyz_agent_context.module import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
)
from xyz_agent_context.utils import DatabaseClient
from xyz_agent_context.narrative.config import config as narrative_config

from xyz_agent_context.utils.evermemos import EverMemOSClient, get_evermemos_client

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from xyz_agent_context.narrative.models import NarrativeSearchResult, Event, Narrative


# Global MemoryModule instance cache (for service method calls)
_memory_modules: Dict[str, "MemoryModule"] = {}


def get_memory_module(agent_id: str, user_id: str) -> "MemoryModule":
    """
    Get or create a MemoryModule instance

    Used by external components (e.g., NarrativeService) to call service methods.

    Args:
        agent_id: Agent ID
        user_id: User ID

    Returns:
        MemoryModule instance
    """
    key = f"{agent_id}_{user_id}"
    if key not in _memory_modules:
        _memory_modules[key] = MemoryModule(agent_id, user_id)
    return _memory_modules[key]


class MemoryModule(XYZBaseModule):
    """
    Memory Module - EverMemOS-based semantic memory management

    Responsibilities:
    1. hook_data_gathering: extract semantic memories from EverMemOS results cached in Step 1, inject into context
    2. hook_after_event_execution: write current conversation to EverMemOS
    3. get_instructions: tell the LLM how to use the semantic memory section

    Does not provide MCP Tools (Agent does not call EverMemOS directly; can be extended later)
    """

    # Configuration constants
    MEMORY_MODULE_PRIORITY = 0  # Highest priority, ensures execution before ChatModule
    MAX_SEMANTIC_MEMORY_CHARS = 1500  # Max characters for semantic memory section
    MAX_EPISODES_PER_NARRATIVE = 5    # Max episode summaries to keep per Narrative
    MAX_EPISODE_SUMMARY_LENGTH = 200  # Max characters for a single episode summary

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        # Note: MemoryModule does not need instructions
        # EverMemOS data is already displayed via Auxiliary Narratives Related Content, no need to duplicate
        self.instructions = ""

    def get_config(self) -> ModuleConfig:
        """
        Return Module configuration

        MemoryModule is an Agent-level module (semantics of is_public=True),
        each agent has only one instance, no LLM decision needed for activation, always active.
        """
        return ModuleConfig(
            name="MemoryModule",
            priority=self.MEMORY_MODULE_PRIORITY,
            enabled=True,
            description="EverMemOS-based semantic memory management, providing cross-conversation long-term memory retrieval and injection"
        )

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        Does not provide MCP Tools for now; can be extended later to let Agent proactively search memories
        """
        return None

    # =========================================================================
    # EverMemOS Client
    # =========================================================================

    def _get_evermemos_client(self) -> EverMemOSClient:
        """
        Get EverMemOS client instance

        Returns:
            EverMemOSClient instance
        """
        return get_evermemos_client(self.agent_id, self.user_id)

    # =========================================================================
    # Service Methods - called directly by external components
    # =========================================================================

    async def search_evermemos(
        self,
        query: str,
        top_k: int = 20
    ) -> list:
        """
        Search for relevant episodes from EverMemOS.

        After decoupling: returns flat episode list (not grouped by narrative).
        Called independently during context assembly.

        Args:
            query: Query text
            top_k: Number of episodes to return

        Returns:
            List of EpisodeResult, sorted by relevance
        """
        client = self._get_evermemos_client()
        results = await client.search_episodes(query, top_k)
        logger.debug(
            f"[MemoryModule] search_evermemos: query='{query[:50]}...', "
            f"returned {len(results)} episodes"
        )
        return results

    async def write_to_evermemos(
        self,
        event: Event,
        narrative: Narrative
    ) -> bool:
        """
        Write Event to EverMemOS

        Can be called by NarrativeUpdater (transitional period),
        or internally by hook_after_event_execution (final architecture).

        Args:
            event: Narrative Event
            narrative: Associated Narrative

        Returns:
            bool: Whether the write was successful
        """
        client = self._get_evermemos_client()
        success = await client.write_event(event, narrative)
        if success:
            logger.debug(
                f"[MemoryModule] write_to_evermemos succeeded: event={event.id}, "
                f"narrative={narrative.id}"
            )
        else:
            logger.warning(
                f"[MemoryModule] write_to_evermemos failed: event={event.id}, "
                f"narrative={narrative.id}"
            )
        return success

    # =========================================================================
    # Hooks - automatically executed during Module lifecycle
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Processing during the data gathering phase

        Note: Semantic memory Section has been removed (duplicates Auxiliary Narratives Related Content functionality).
        EverMemOS data is now directly read by context_runtime's _build_auxiliary_narratives_prompt()
        from ctx_data.extra_data["evermemos_memories"] and displayed as Related Content.

        This hook is retained as a no-op for future extension.

        # todo - In the future, the chatmodel memory injection may be moved here to maintain single responsibility: chatmodel focuses on conversation management, memory module focuses on memory management
        """
        logger.debug("          → MemoryModule.hook_data_gathering() - no-op (semantic memory section removed)")
        return ctx_data

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """
        Post-Event execution processing - write conversation to EverMemOS

        Responsibilities:
        - Check if EverMemOS is enabled
        - Asynchronously write Event to EverMemOS (non-blocking)

        Args:
            params: HookAfterExecutionParams, containing:
                - execution_ctx: Execution context (event_id, agent_id, user_id, working_source)
                - io_data: Input/output (input_content, final_output)
                - trace: Execution trace (event_log, agent_loop_response)
                - ctx_data: Complete context data
                - event: Current Event object
                - narrative: Primary Narrative object
        """
        # Check if EverMemOS is enabled
        if not narrative_config.EVERMEMOS_ENABLED:
            logger.debug(
                "          → MemoryModule.hook_after_event_execution() - "
                "EverMemOS not enabled, skipping write"
            )
            return None

        # Get event and narrative
        event = params.event
        narrative = params.narrative

        if not event or not narrative:
            logger.warning(
                f"          → MemoryModule.hook_after_event_execution() - "
                f"event or narrative is null, skipping write "
                f"(event={event is not None}, narrative={narrative is not None})"
            )
            return None

        # Directly await EverMemOS write (completed within parallel hook execution)
        logger.debug(
            f"          → MemoryModule.hook_after_event_execution() - "
            f"triggering EverMemOS write: event={event.id}, narrative={narrative.id}, "
            f"final_output={'present' if event.final_output else 'absent'} "
            f"({len(event.final_output) if event.final_output else 0} chars)"
        )
        await self._async_evermemos_write(event, narrative)

        return None

    async def _async_evermemos_write(
        self,
        event: "Event",
        narrative: "Narrative"
    ) -> None:
        """
        Write Event to EverMemOS.

        After decoupling: group_id = agent_id (not narrative_id).
        Narrative param kept for agent_id extraction and logging only.
        """
        try:
            user_id = event.user_id
            agent_id = narrative.agent_id

            if not user_id:
                logger.warning(f"[EverMemOS-Write] Event {event.id} has no user_id, skipping")
                return

            evermemos = get_evermemos_client(agent_id, user_id)
            success = await evermemos.write_event(event)

            if success:
                logger.info(f"[EverMemOS-Write] Succeeded: event={event.id}, agent={agent_id}")
            else:
                logger.warning(f"[EverMemOS-Write] Failed: event={event.id}")

        except Exception as e:
            logger.exception(f"[EverMemOS-Write] Exception: {type(e).__name__}: {e}")
