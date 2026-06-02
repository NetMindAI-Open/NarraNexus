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
    CHAT_HISTORY_TIMELINE_PREAMBLE,
    RECENT_ACTIONS_HEADER,
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

        # Step 4: Build the complete System Prompt (Narrative + Modules)
        logger.info("    │ Step 1-4: Building Complete System Prompt")
        system_prompt = await self.build_complete_system_prompt(
            narrative_list=narrative_list,
            selected_events=selected_events,
            module_instructions_list=module_instructions_list,
            ctx_data=ctx_data,
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
    ) -> str:
        """
        Build the complete System Prompt.

        Prompt structure:
        1. Narrative Info - main Narrative metadata
        2. Module Instructions - Instructions from each Module
        3. Bootstrap Injection (first 3 turns only)
        (Short-term memory appended later in build_input_for_framework)

        Args:
            narrative_list: List of Narratives (the 1st is the main Narrative)
            selected_events: List of selected Events (currently unused)
            module_instructions_list: List of Module instructions
            ctx_data: Context data

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

    async def _build_auxiliary_narratives_prompt(
        self,
        auxiliary_summaries: List[Dict[str, Any]],
    ) -> str:
        """
        Build the summary Prompt for auxiliary Narratives.

        Args:
            auxiliary_summaries: List of auxiliary Narrative summaries

        Returns:
            Formatted auxiliary Narratives Prompt
        """
        prompt = AUXILIARY_NARRATIVES_HEADER
        for i, summary in enumerate(auxiliary_summaries):
            prompt += f"""
### Related Narrative {i + 1}
- Name: {summary.get('name', 'Unknown')}
- Summary: {summary.get('topic_hint', 'No summary available')}
- Event Count: {summary.get('event_count', 0)}
"""
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

        # 2026-05-20 (Fix #2): chat_history is ONE unified, time-sorted timeline
        # (current narrative + cross-narrative), each msg tagged with
        # narrative_id/alias by ChatModule.hook_data_gathering. Render every line
        # as a role message prefixed `[time · topic · nar_id]` + the channel
        # source prefix, and teach the agent how to read it via the preamble.
        # No more long/short split; no cross-narrative-into-system-prompt section.
        timeline = self._truncate_long_term_messages(chat_history)

        enhanced_system_prompt = system_prompt + "\n\n" + CHAT_HISTORY_TIMELINE_PREAMBLE

        # P2: append the recent background-activity section (centered small-text
        # in the UI) — a compact list with event_ids, separate from the timeline.
        recent_actions = (getattr(ctx_data, "extra_data", None) or {}).get("recent_actions") or []
        if recent_actions:
            enhanced_system_prompt += "\n\n" + self._build_recent_actions_section(recent_actions)
            logger.info(f"[RecentActions] rendered {len(recent_actions)} actions into system prompt")

        final_messages = [
            {"role": "system", "content": enhanced_system_prompt}
        ]
        logger.debug(f"        Added system prompt + timeline preamble: {len(enhanced_system_prompt)} chars")

        # Each line: [time · topic · nar_id] + channel source prefix + content.
        # The narrative tag lets the agent tell threads apart / re-route; the
        # source prefix (MessageSourceRegistry) marks UI vs Lark vs bus, etc.
        from xyz_agent_context.channel.message_source_handler import (
            MessageSourceRegistry,
        )
        cross_count = 0
        for msg in timeline:
            meta = msg.get("meta_data") or {}
            ws = meta.get("working_source", "chat")
            handler = MessageSourceRegistry.get(ws)
            src_prefix = handler.format_row_prefix(msg)
            tag = self._format_timeline_tag(meta)
            if meta.get("memory_type") == "short_term":
                cross_count += 1
            raw_content = msg.get("content", "") or ""
            prefix = f"{tag} {src_prefix}".strip()
            final_messages.append({
                "role": msg.get("role", "user"),
                # `_source` (internal) drives source-aware truncation in the LLM
                # adapter when system_prompt + history exceeds the SDK ceiling —
                # background rows drop first, then oldest chat. SDKs ignore it.
                "content": f"{prefix} {raw_content}" if prefix else raw_content,
                "_source": ws,
            })
        logger.info(
            f"[CHAT-CTX] unified timeline rendered: {len(timeline)} msgs "
            f"({cross_count} cross-narrative, {len(timeline) - cross_count} current) "
            f"source={history_source}"
        )

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

    @staticmethod
    def _format_timeline_tag(meta: Dict[str, Any]) -> str:
        """Render the per-message timeline tag
        `[<time> · <topic> · nar=<narrative_id> · evt=<event_id>]`.

        - time: the message's stored timestamp (compact YYYY-MM-DD HH:MM).
        - topic: the resolved narrative alias (name); falls back to the id.
        - nar=<narrative_id>: full id — the agent needs it for switch/view tools.
        - evt=<event_id>: the event that produced this message — the agent can
          pass it to view_event() to fetch that turn's full agent-loop +
          reasoning detail (only the sent message is in the timeline).
        """
        meta = meta or {}
        ts = (meta.get("timestamp") or "")
        t = ts[:16].replace("T", " ") if ts else "??"
        nid = meta.get("narrative_id") or "unknown"
        topic = meta.get("narrative_alias") or nid
        eid = meta.get("event_id") or "?"
        return f"[{t} · {topic} · nar={nid} · evt={eid}]"

    @staticmethod
    def _build_recent_actions_section(actions: List[Dict[str, Any]]) -> str:
        """Render the recent-background-activity list (Fix #2 P2): one compact
        line per action `- [time] <source>: <job title / summary>  (evt=<id>)`."""
        lines = [RECENT_ACTIONS_HEADER]
        for a in actions:
            t = (a.get("timestamp") or "")[:16].replace("T", " ")
            src = a.get("working_source") or "?"
            title = a.get("title") or a.get("summary") or f"({src} activity)"
            eid = a.get("event_id") or "?"
            lines.append(f"- [{t}] {src}: {title}  (evt={eid})")
        return "\n".join(lines)

    # Token budget for the short-term memory section.
    # ~4 chars per token is a rough estimate; keeps the section under ~10k tokens.
    SHORT_TERM_TOKEN_LIMIT = 40000  # characters (≈ 10000 tokens)

    def _build_short_term_memory_prompt(
        self,
        short_term_messages: List[Dict[str, Any]]
    ) -> str:
        """
        DEPRECATED (2026-05-20, Fix #2) — no longer called.

        Cross-narrative short-term memory used to be rendered as a separate
        system-prompt section via this method + SHORT_TERM_MEMORY_HEADER. It now
        flows through the SINGLE unified timeline (see build_input_for_framework
        + _format_timeline_tag + CHAT_HISTORY_TIMELINE_PREAMBLE). Kept only so any
        stray caller doesn't crash; safe to delete once nothing references it.

        Args:
            short_term_messages: List of short-term memory messages

        Returns:
            Formatted short-term memory Prompt
        """
        from datetime import datetime
        from xyz_agent_context.context_runtime.prompts import SHORT_TERM_MEMORY_HEADER

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

            # Build source label from the first message's MessageSource
            # handler. All msgs in this group share an instance_id, so they
            # all came from the same WorkingSource — pick the first.
            from xyz_agent_context.channel.message_source_handler import (
                MessageSourceRegistry,
            )
            head_ws = (
                (msgs[0].get("meta_data") or {}).get("working_source", "chat")
                if msgs else "chat"
            )
            head_handler = MessageSourceRegistry.get(head_ws)
            source_label = head_handler.format_row_prefix(msgs[0]) if msgs else ""

            section = f"\n**[{time_ago}]** {source_label}\n"

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



