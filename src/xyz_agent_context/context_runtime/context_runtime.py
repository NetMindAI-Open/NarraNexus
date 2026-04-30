"""
@file_name: context_runtime.py
@author: NetMind.AI
@date: 2025-11-06
@description: This file contains the runtime context for the agent context module.

"""


from typing import List, Dict, Any, Tuple, Optional, Union
from loguru import logger

# Schema
from xyz_agent_context.schema import (
    ContextData,
    ModuleInstructions,
    ContextRuntimeOutput,
    WorkingSource,
)

# Module
from xyz_agent_context.module import XYZBaseModule, HookManager

# Narrative
from xyz_agent_context.narrative import Narrative, Event, EventService, NarrativeService, config

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client_sync

# Prompts
from xyz_agent_context.context_runtime.prompts import (
    AUXILIARY_NARRATIVES_HEADER,
    MODULE_INSTRUCTIONS_HEADER,
    SHORT_TERM_MEMORY_HEADER,
    BOOTSTRAP_INJECTION_PROMPT,
    USER_TEMPORAL_CONTEXT,
)


class ContextRuntime:
    """
    ContextRuntime is responsible for building the Context required for the Agent Loop.

    According to the design document:
    - Context is built from Agent basic info + Narrative
    - Flow: ContextData -> ContextBuild -> ContextUsing

    Main steps:
    1. Extract Active Module Instances from Narrative
    2. Select additional Modules if needed
    3. Each Module performs data_gathering (expanding ContextData)
    4. Extract historical information from Narrative/Events
    5. Build system prompt (sort module instructions)
    6. Build the final messages and mcp_urls
    """

    # Maximum characters per single message (prevents a single overly long message from consuming too much Context)
    SINGLE_MESSAGE_MAX_CHARS = 4000

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None
    ):
        """
        Initialize ContextRuntime

        Args:
            agent_id: Agent ID
            user_id: User ID (if applicable)
            database_client: Database client (used for reading data)
        """
        logger.debug(f"    → ContextRuntime.__init__() called with agent_id={agent_id}, user_id={user_id}")
        self.agent_id = agent_id
        self.user_id = user_id
        self.db = database_client or get_db_client_sync()
        self.hook_manager = HookManager()
        logger.debug("    ContextRuntime initialized")

    async def run(
        self,
        narrative_list: List[Narrative],
        active_instances: List,  # Changed to active_instances (module already bound)
        input_content: str,  # Added: current user input
        working_source: Union[WorkingSource, str] = WorkingSource.CHAT,
        query_embedding: Optional[List[float]] = None,
        created_job_ids: Optional[List[str]] = None,
        trigger_extra_data: Optional[Dict[str, Any]] = None,
        relevant_episodes: Optional[List] = None,  # EverMemOS episodes (fetched in parallel upstream)
    ) -> ContextRuntimeOutput:
        logger.info("    ┌─ ContextRuntime.run() started")
        logger.info(f"    │ Narratives: {len(narrative_list)}, Instances: {len(active_instances)}")
        logger.debug(f"    │ Input content: {input_content}")

        # Step 0: Initialize ContextData
        logger.debug("    │ Step 0: Initializing ContextData")
        main_narrative_id = narrative_list[0].id if narrative_list else None
        ctx_data = ContextData(
            agent_id=self.agent_id,
            user_id=self.user_id,
            input_content=input_content,
            narrative_id=main_narrative_id,
            agent_info_model_type="Claude Agent SDK",
            model_name="sonnet-4",
            working_source=working_source
        )
        ctx_data.extra_data = ctx_data.extra_data or {}
        if trigger_extra_data:
            ctx_data.extra_data.update(trigger_extra_data)

        if narrative_list:
            ctx_data.extra_data["narrative_ids"] = [n.id for n in narrative_list]
            logger.debug(f"    │ ContextData initialized with narrative_id={main_narrative_id}, narrative_ids={len(narrative_list)}")

        if created_job_ids:
            ctx_data.extra_data["created_job_ids_this_turn"] = created_job_ids

        # Step 1: Extract data from Narrative (disabled — ChatModule provides history)
        logger.info("    │ Step 1-1: Extracting Narrative data (Event selection disabled)")
        messages = []
        selected_events = []
        logger.info("    │ ✅ Narrative data extracted (Event selection disabled, using ChatModule for history)")

        # Step 2: Gather data from Modules (executed for each instance)
        logger.info("    │ Step 1-2: Gathering information from Module Instances")
        # Extract the list of module objects (for hook_data_gathering)
        module_list = [inst.module for inst in active_instances if inst.module is not None]
        ctx_data = await self.hook_manager.hook_data_gathering(module_list, ctx_data)

        # Get chat_history from chat_module. Since Chat Module may not be loaded, there will be no interaction history if it is not loaded.
        messages = ctx_data.chat_history or []

        logger.info(f"    │ ✅ Information gathered from {len(module_list)} Module Instances")

        # Step 3: Build Module instructions (deduplicated by module_class)
        logger.info("    │ Step 1-3: Building Module instructions (deduped by module_class)")
        module_instructions_list = []
        seen_module_classes = set()

        for inst in active_instances:
            if inst.module_class not in seen_module_classes and inst.module is not None:
                module_instructions = await self.build_module_instructions(inst.module, ctx_data)
                module_instructions_list.append(module_instructions)
                seen_module_classes.add(inst.module_class)
                logger.debug(f"    │   Built instructions for {inst.module_class} ({inst.instance_id})")

        logger.info(f"    │ ✅ Built {len(module_instructions_list)} Module instructions (deduped from {len(active_instances)} instances)")

        # Step 4: Build the complete System Prompt (including Narrative + Relevant Memory + Modules)
        logger.info("    │ Step 1-4: Building Complete System Prompt")
        system_prompt = await self.build_complete_system_prompt(
            narrative_list=narrative_list,
            selected_events=selected_events,
            module_instructions_list=module_instructions_list,
            ctx_data=ctx_data,
            relevant_episodes=relevant_episodes,
        )
        logger.info(f"    │ ✅ System Prompt built: {len(system_prompt)} characters")

        # Step 5: Build input for Agent Framework
        logger.info("    │ Step 2: Building input for Agent Framework")
        messages, mcp_urls = await self.build_input_for_framework(
            messages, system_prompt, active_instances, ctx_data
        )
        logger.info(f"    │ ✅ Framework input built: {len(messages)} messages, {len(mcp_urls)} MCP servers")

        logger.info("    └─ ContextRuntime.run() completed")
        return ContextRuntimeOutput(messages=messages, mcp_urls=mcp_urls, ctx_data=ctx_data)


    async def build_module_instructions(
        self,
        module_object: XYZBaseModule,
        ctx_data: ContextData
    ) -> ModuleInstructions:
        """
        Build instructions for a single Module.

        Args:
            module_object: Module object
            ctx_data: Context data (Module may need to dynamically generate instructions based on data)

        Returns:
            ModuleInstructions
        """
        # Step 1: Call the module's get_instructions method
        instructions = await module_object.get_instructions(ctx_data)
        module_instructions = ModuleInstructions(
            name=module_object.config.name,
            instruction=instructions,
            priority=module_object.config.priority
        )

        # Step 2: Return ModuleInstructions
        return module_instructions

    async def extract_narrative_data(
        self,
        narrative_list: List[Narrative],
        ctx_data: ContextData,
        query_embedding: Optional[List[float]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Event], ContextData]:
        """
        Extract data from Narratives (enhanced version: supports multiple Narratives + intelligent Event selection).

        Processing logic:
        1. Main Narrative (1st): Use hybrid strategy to select Events (for detailed history in System Prompt)
        2. Auxiliary Narratives (2nd and beyond): Only load topic_hint as reference

        Note (after 2025-12-09 refactoring):
        - Chat history (chat_history) is now provided by ChatModule via EventMemoryModule
        - The messages returned by this method are mainly used for detailed Event history display in System Prompt
        - ChatModule.hook_data_gathering() will populate ctx_data.chat_history

        Returns:
            (messages, selected_events, updated_ctx_data)
            - messages: Simplified user/assistant message pairs (for System Prompt reference)
            - selected_events: Selected Event objects (for generating detailed prompt)
            - updated_ctx_data: Updated context data
        """
        logger.debug(f"      → extract_narrative_data() called with {len(narrative_list)} narratives")
        messages = []
        selected_events = []
        event_service = EventService(self.agent_id)

        if not narrative_list:
            logger.debug("        No narratives found")
            return messages, selected_events, ctx_data

        # ========================================================================
        # Step 1: Process main Narrative (1st) - detailed Event processing
        # ========================================================================
        main_narrative = narrative_list[0]
        logger.debug(f"        Processing main Narrative: {main_narrative.id}")
        
        # Use hybrid strategy to select Events
        if main_narrative.event_ids:
            selected_events = await event_service.select_events_for_context(
                narrative_event_ids=main_narrative.event_ids,
                query_embedding=query_embedding,
                max_recent=config.MAX_RECENT_EVENTS,
                max_relevant=config.MAX_RELEVANT_EVENTS,
                max_total=config.MAX_EVENTS_IN_CONTEXT
            )
            
            logger.debug(f"        Selected {len(selected_events)} Events")
            
            # Convert Events to simplified messages (user/assistant pairs)
            for event in selected_events:
                if event:
                    user_input = event.env_context.get("input", "")
                    if user_input:
                        messages.append({
                            "role": "user",
                            "content": user_input
                        })
                    if event.final_output:
                        messages.append({
                            "role": "assistant",
                            "content": event.final_output
                        })
        else:
            logger.debug("        Main Narrative has no Events")

        # ========================================================================
        # Step 2: Process auxiliary Narratives (2nd and beyond) - extract summaries only
        # ========================================================================
        auxiliary_narratives = narrative_list[1:] if len(narrative_list) > 1 else []
        
        if auxiliary_narratives:
            logger.debug(f"        Processing {len(auxiliary_narratives)} auxiliary Narratives")
            
            # Add auxiliary Narrative summaries to ctx_data
            auxiliary_summaries = []
            for aux_narrative in auxiliary_narratives:
                summary_info = {
                    "narrative_id": aux_narrative.id,
                    "name": aux_narrative.narrative_info.name if aux_narrative.narrative_info else "Unknown",
                    "topic_hint": aux_narrative.topic_hint or (aux_narrative.narrative_info.current_summary if aux_narrative.narrative_info else ""),
                    "event_count": len(aux_narrative.event_ids) if aux_narrative.event_ids else 0
                }
                auxiliary_summaries.append(summary_info)
                logger.debug(f"          Auxiliary Narrative: {aux_narrative.id} - {summary_info['name']}")
            
            # Store auxiliary summaries in ctx_data
            ctx_data.extra_data = ctx_data.extra_data or {}
            ctx_data.extra_data["auxiliary_narratives"] = auxiliary_summaries

        # ========================================================================
        # Step 3: Extract data from the main Narrative's env_variables
        # ========================================================================
        if main_narrative.env_variables:
            ctx_data.extra_data = ctx_data.extra_data or {}
            ctx_data.extra_data["narrative_env_variables"] = main_narrative.env_variables
            logger.debug(f"        Extracted {len(main_narrative.env_variables)} environment variables")

        logger.debug(f"      extract_narrative_data() completed: {len(messages)} messages, {len(selected_events)} events")
        return messages, selected_events, ctx_data

    async def build_complete_system_prompt(
        self,
        narrative_list: List[Narrative],
        selected_events: List[Event],
        module_instructions_list: List[ModuleInstructions],
        ctx_data: ContextData,
        relevant_episodes: Optional[List] = None,
    ) -> str:
        """
        Build the complete System Prompt.

        Prompt structure (after decoupling):
        1. Narrative Info - main Narrative metadata
        2. Relevant Memory - EverMemOS episodes (decoupled, query-relevant)
        3. Module Instructions - Instructions from each Module
        4. Bootstrap Injection (first 3 turns only)
        (Short-term memory appended later in build_input_for_framework)

        Args:
            narrative_list: List of Narratives (the 1st is the main Narrative)
            selected_events: List of selected Events (currently unused)
            module_instructions_list: List of Module instructions
            ctx_data: Context data
            relevant_episodes: EverMemOS episodes retrieved by query (decoupled)

        Returns:
            The complete system prompt string
        """
        logger.debug("      → build_complete_system_prompt() started")
        prompt_parts = []
        narrative_service = NarrativeService(self.agent_id)

        # ========================================================================
        # Part 0: User Temporal Context (v2 timezone protocol, 2026-04-21)
        # Injected first so every downstream section + all Module instructions
        # can reference it. Source of truth = users.timezone (IANA).
        # ========================================================================
        try:
            temporal_block = await self._build_user_temporal_block(ctx_data.user_id)
            if temporal_block:
                prompt_parts.append(temporal_block)
                logger.debug(f"        Added User Temporal Context: {len(temporal_block)} chars")
        except Exception as e:
            logger.warning(f"        Failed to build User Temporal Context: {e}")

        # ========================================================================
        # Part 1: Narrative Info (main Narrative)
        # ========================================================================
        if narrative_list:
            main_narrative = narrative_list[0]
            narrative_prompt = await narrative_service.combine_main_narrative_prompt(main_narrative)
            prompt_parts.append(narrative_prompt)
            logger.debug(f"        Added Narrative prompt: {len(narrative_prompt)} chars")

        # ========================================================================
        # Part 2: Relevant Memory (EverMemOS episodes — decoupled from narrative)
        # ========================================================================
        if relevant_episodes:
            memory_section = self._build_relevant_memory_prompt(relevant_episodes)
            prompt_parts.append(memory_section)
            logger.debug(f"        Added Relevant Memory: {len(memory_section)} chars ({len(relevant_episodes)} episodes)")

        # ========================================================================
        # Part 3: Module Instructions
        # ========================================================================
        if module_instructions_list:
            module_prompt = await self._build_module_instructions_prompt(module_instructions_list)
            prompt_parts.append(module_prompt)
            logger.debug(f"        Added Module Instructions: {len(module_prompt)} chars")

        # ========================================================================
        # Part 5: Bootstrap Injection (first-run setup, creator only)
        # Derives creator status directly from DB to avoid dependency on
        # BasicInfoModule being loaded.
        # ========================================================================
        try:
            import os
            from xyz_agent_context.settings import settings
            from xyz_agent_context.repository import AgentRepository

            agent_record = await AgentRepository(self.db).get_agent(self.agent_id)
            if agent_record and agent_record.created_by and agent_record.created_by == ctx_data.user_id:
                bootstrap_path = os.path.join(
                    settings.base_working_path,
                    f"{self.agent_id}_{agent_record.created_by}",
                    "Bootstrap.md"
                )
                if os.path.isfile(bootstrap_path):
                    # Auto-delete Bootstrap.md after 3 rounds to prevent
                    # perpetual bootstrap mode if the agent fails to delete it.
                    try:
                        event_count_rows = await self.db.execute(
                            "SELECT COUNT(*) AS cnt FROM events WHERE agent_id = %s",
                            (self.agent_id,),
                            fetch=True,
                        )
                        event_count = event_count_rows[0]["cnt"] if event_count_rows else 0
                    except Exception:
                        event_count = 0

                    if event_count >= 3:
                        try:
                            os.remove(bootstrap_path)
                            logger.info(
                                f"        Auto-deleted Bootstrap.md after {event_count} events "
                                f"(agent={self.agent_id})"
                            )
                        except OSError as rm_err:
                            logger.warning(f"        Failed to auto-delete Bootstrap.md: {rm_err}")
                    else:
                        prompt_parts.append(BOOTSTRAP_INJECTION_PROMPT)
                        ctx_data.bootstrap_active = True
                        logger.debug("        Added Bootstrap injection (file-read approach)")
        except Exception as e:
            logger.warning(f"        Failed to inject Bootstrap: {e}")

        # Combine all parts
        full_prompt = "\n\n".join(prompt_parts)
        logger.debug(f"      build_complete_system_prompt() completed: {len(full_prompt)} total chars")
        return full_prompt.strip()

    async def _build_user_temporal_block(self, user_id: Optional[str]) -> str:
        """
        Build the User Temporal Context block (v2 timezone protocol).

        Reads users.timezone (falls back to UTC for users who have never
        synced their browser timezone) and produces a prompt section telling
        the LLM the user's IANA timezone and current local time.
        """
        if not user_id:
            return ""
        from xyz_agent_context.repository import UserRepository
        from xyz_agent_context.utils.timezone import utc_now, to_user_timezone
        user_tz = await UserRepository(self.db).get_user_timezone(user_id)
        now_local_dt = to_user_timezone(utc_now(), user_tz)
        if now_local_dt is None:
            return ""
        now_local = now_local_dt.replace(tzinfo=None).isoformat(timespec="seconds")
        return USER_TEMPORAL_CONTEXT.format(user_tz=user_tz, now_local=now_local)

    def _build_relevant_memory_prompt(self, episodes: List) -> str:
        """
        Build the Relevant Memory section from EverMemOS episodes.

        Episodes are a flat list ranked by query relevance (decoupled from narrative).
        Each episode has: episode_text (full content), summary, score, timestamp.
        Uses full content — no truncation per episode, only top_k limit on count.
        """
        if not episodes:
            return ""

        section = "## Relevant Memory (from past conversations)\n\n"
        section += "The following are semantically relevant memories from your past conversations, "
        section += "retrieved based on the current query. Use them for context when responding.\n\n"

        for i, ep in enumerate(episodes, 1):
            # Prefer full episode_text, fall back to summary
            content = ep.episode_text if ep.episode_text else ep.summary
            if not content:
                continue

            section += f"### Memory {i}"
            if ep.timestamp:
                section += f" ({ep.timestamp})"
            section += "\n"
            section += f"{content}\n\n"

        return section

    # Legacy method — kept for backward compatibility, may be removed
    async def _build_auxiliary_narratives_prompt(
        self,
        auxiliary_summaries: List[Dict[str, Any]],
        evermemos_memories: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build the summary Prompt for auxiliary Narratives.

        Phase 3 enhancement: If evermemos_memories contains relevant content summaries for a Narrative,
        add a Related Content field to help the LLM understand the specific content related to the current query within that topic.

        Args:
            auxiliary_summaries: List of auxiliary Narrative summaries
            evermemos_memories: EverMemOS cache data (optional)

        Returns:
            Formatted auxiliary Narratives Prompt
        """
        prompt = AUXILIARY_NARRATIVES_HEADER
        for i, summary in enumerate(auxiliary_summaries):
            narrative_id = summary.get('narrative_id', 'Unknown')
            prompt += f"""
### Related Narrative {i + 1}
- Name: {summary.get('name', 'Unknown')}
- Summary: {summary.get('topic_hint', 'No summary available')}
- Event Count: {summary.get('event_count', 0)}
"""
            # Phase 3: Add Related Content (if available)
            if evermemos_memories and narrative_id in evermemos_memories:
                episode_summaries = evermemos_memories[narrative_id].get("episode_summaries", [])
                if episode_summaries:
                    prompt += "- Related Content:\n"
                    for episode_summary in episode_summaries[:3]:  # Show at most 3 entries
                        # Truncate overly long summaries
                        truncated = episode_summary[:150] + "..." if len(episode_summary) > 150 else episode_summary
                        prompt += f"  - {truncated}\n"

        return prompt

    async def _build_module_instructions_prompt(
        self,
        module_instructions_list: List[ModuleInstructions]
    ) -> str:
        """Build the Prompt for Module instructions."""
        # Sort by priority
        sorted_instructions = sorted(
            module_instructions_list,
            key=lambda x: x.priority
        )
        
        prompt = MODULE_INSTRUCTIONS_HEADER
        for instructions in sorted_instructions:
            prompt += f"\n### {instructions.name}\n{instructions.instruction}"
        
        return prompt

    async def build_system_prompt(
        self,
        module_instructions_list: List[ModuleInstructions]
    ) -> str:
        """
        Build System Prompt (simplified version, containing only Module instructions).

        Note: It is recommended to use build_complete_system_prompt() to get the complete prompt.

        Args:
            module_instructions_list: List of Module instructions

        Returns:
            System prompt string
        """
        logger.debug(f"      → build_system_prompt() called with {len(module_instructions_list)} instructions")
        return await self._build_module_instructions_prompt(module_instructions_list)

    async def build_input_for_framework(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        active_instances: List,  # Changed to active_instances
        ctx_data: ContextData
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """
        Build input for the Agent Framework.

        Args:
            messages: Historical messages extracted from Narrative/Event (for System Prompt reference, now deprecated)
            system_prompt: The built system prompt
            active_instances: List of Module Instances (module already bound)
            ctx_data: Context data (containing chat_history populated by ChatModule)

        Returns:
            (messages, mcp_urls)
            - messages: Complete messages list including system prompt and historical messages
            - mcp_urls: Dictionary of {module_name: mcp_url}

        Note (after 2025-12-09 refactoring):
        - Chat history preferentially uses ctx_data.chat_history (provided by ChatModule via EventMemoryModule)
        - If chat_history is empty, falls back to the messages parameter (extracted from Events)

        Dual-track memory (2026-01-21 P1-2):
        - Long-term memory (long_term): Complete conversation history of current Narrative -> as normal messages
        - Short-term memory (short_term): Cross-Narrative recent conversations -> added to system prompt
        """
        logger.debug("      → build_input_for_framework() called")
        logger.debug(f"        Input: {len(messages)} event messages, {len(active_instances)} instances")

        # Get chat_history
        chat_history = ctx_data.chat_history if ctx_data.chat_history else messages
        history_source = "ChatModule Memory" if ctx_data.chat_history else "Event System (fallback)"

        # ========== Separate long-term memory and short-term memory ==========
        long_term_messages = []
        short_term_messages = []

        for msg in chat_history:
            meta = msg.get("meta_data", {})
            memory_type = meta.get("memory_type", "long_term")

            if memory_type == "short_term":
                short_term_messages.append(msg)
            else:
                long_term_messages.append(msg)

        logger.debug(
            f"        Dual-track memory: long-term {len(long_term_messages)} messages, short-term {len(short_term_messages)} messages"
        )

        # ========== Single message truncation (prevent overly long content) ==========
        long_term_messages = self._truncate_long_term_messages(long_term_messages)

        # ========== Build enhanced system prompt (including short-term memory) ==========
        enhanced_system_prompt = system_prompt

        if short_term_messages:
            short_term_section = self._build_short_term_memory_prompt(short_term_messages)
            enhanced_system_prompt = system_prompt + "\n\n" + short_term_section
            logger.debug(f"        Added short-term memory to system prompt: {len(short_term_section)} chars")

        # Step 1: Build messages list
        logger.debug("        Step 1: Building messages list")
        final_messages = [
            {"role": "system", "content": enhanced_system_prompt}
        ]
        logger.debug(f"        Added system prompt: {len(enhanced_system_prompt)} chars")

        # Add long-term memory historical messages (only add role and content, strip extra fields)
        for msg in long_term_messages:
            final_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
        logger.debug(f"        Added {len(long_term_messages)} long-term messages from {history_source}")

        # Add current user input
        final_messages.append({
            "role": "user",
            "content": ctx_data.input_content
        })
        logger.debug(f"        Added current user input: {len(ctx_data.input_content)} chars")

        # Step 2: Collect all Module MCP URLs (deduplicated by module_class)
        logger.debug("        Step 2: Collecting MCP URLs from instances (deduped by module_class)")
        mcp_urls = {}
        seen_module_classes = set()
        collected_count = 0

        for inst in active_instances:
            if inst.module_class not in seen_module_classes and inst.module is not None:
                logger.debug(f"          Getting MCP config from {inst.module_class} ({inst.instance_id})")
                mcp_config = await inst.module.get_mcp_config()
                if mcp_config and mcp_config.server_url:
                    mcp_urls[mcp_config.server_name] = mcp_config.server_url
                    collected_count += 1
                    logger.debug(f"          ✓ Added MCP: {mcp_config.server_name} -> {mcp_config.server_url}")
                elif mcp_config:
                    logger.debug(f"          ⏭ Skipped MCP: {mcp_config.server_name} -> (empty URL)")
                seen_module_classes.add(inst.module_class)

        logger.debug(f"        Collected {collected_count} MCP URLs from {len(active_instances)} instances (deduped by module_class)")

        logger.debug(f"      build_input_for_framework() completed: {len(final_messages)} messages, {len(mcp_urls)} MCP URLs")
        return final_messages, mcp_urls

    # Token budget for the short-term memory section.
    # ~4 chars per token is a rough estimate; keeps the section under ~10k tokens.
    SHORT_TERM_TOKEN_LIMIT = 40000  # characters (≈ 10000 tokens)

    def _build_short_term_memory_prompt(
        self,
        short_term_messages: List[Dict[str, Any]]
    ) -> str:
        """
        Build the short-term memory Prompt section.

        Loads recent messages at full length (up to per-message limit) and stops
        when the total token budget is reached. Most recent messages are
        prioritised — groups are processed in reverse chronological order.

        Args:
            short_term_messages: List of short-term memory messages

        Returns:
            Formatted short-term memory Prompt
        """
        from datetime import datetime

        prompt = SHORT_TERM_MEMORY_HEADER

        # Group by instance_id, preserving insertion order (most-recent last)
        messages_by_instance: dict[str, list] = {}
        for msg in short_term_messages:
            meta = msg.get("meta_data", {})
            instance_id = meta.get("instance_id", "unknown")
            if instance_id not in messages_by_instance:
                messages_by_instance[instance_id] = []
            messages_by_instance[instance_id].append(msg)

        # Reverse so most-recent groups are processed first
        groups = list(reversed(messages_by_instance.items()))

        budget = self.SHORT_TERM_TOKEN_LIMIT - len(prompt)
        sections: list[str] = []

        for instance_id, msgs in groups:
            if budget <= 0:
                break

            # Get the earliest message timestamp for display
            first_timestamp = ""
            for msg in msgs:
                meta = msg.get("meta_data", {})
                ts = meta.get("timestamp", "")
                if ts:
                    first_timestamp = ts
                    break

            # Calculate relative time
            time_ago = ""
            if first_timestamp:
                try:
                    from xyz_agent_context.utils import utc_now
                    msg_time = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
                    now = utc_now()
                    delta = now - msg_time
                    minutes = int(delta.total_seconds() / 60)
                    if minutes < 1:
                        time_ago = "Just now"
                    elif minutes < 60:
                        time_ago = f"{minutes} minutes ago"
                    else:
                        hours = minutes // 60
                        time_ago = f"{hours} hours ago"
                except Exception:
                    time_ago = "Recently"

            # Build source label from channel_tag if available
            source_label = ""
            for msg in msgs:
                ct = msg.get("meta_data", {}).get("channel_tag")
                if ct and isinstance(ct, dict):
                    ch = ct.get("channel", "").capitalize()
                    name = ct.get("sender_name", "")
                    source_label = f" via {ch}" + (f" ({name})" if name else "")
                    break

            section = f"\n**[{time_ago}{source_label}]**\n"

            for msg in msgs:
                if budget <= 0:
                    break
                role = msg.get("role", "user")
                content = msg.get("content", "")
                role_label = "User" if role == "user" else "Assistant"
                line = f"- {role_label}: {content}\n"
                if len(section) + len(line) > budget:
                    break
                section += line

            budget -= len(section)
            sections.append(section)

        # Reverse back to chronological order for the final prompt
        sections.reverse()
        prompt += "".join(sections)

        return prompt

    def _truncate_long_term_messages(
        self,
        long_term_messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Truncate individual messages in long-term memory.

        Prevents a single overly long message (e.g., pasted large code/document blocks) from consuming too much Context.
        Overall budget control is backed by Claude Agent SDK's MAX_HISTORY_LENGTH.

        Args:
            long_term_messages: List of long-term memory messages

        Returns:
            List of messages after truncation
        """
        if not long_term_messages:
            return []

        truncated_messages = []
        truncated_count = 0

        for msg in long_term_messages:
            content = msg.get("content", "")
            if len(content) > self.SINGLE_MESSAGE_MAX_CHARS:
                # Truncate and add truncation marker
                truncated_content = content[:self.SINGLE_MESSAGE_MAX_CHARS] + "...[content truncated]"
                truncated_msg = msg.copy()
                truncated_msg["content"] = truncated_content
                truncated_messages.append(truncated_msg)
                truncated_count += 1
            else:
                truncated_messages.append(msg)

        if truncated_count > 0:
            logger.debug(f"        Single message truncation: {truncated_count} overly long message(s) truncated")

        return truncated_messages



