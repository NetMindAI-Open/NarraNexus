import os
"""
@file_name: context_runtime.py
@author: NetMind.AI
@date: 2025-11-06
@description: This file contains the runtime context for the agent context module.

"""


import hashlib
import json
from typing import List, Dict, Any, Tuple, Optional, Union
from loguru import logger

# Schema
from narranexus.platform.schema import (
    ContextData,
    ModuleInstructions,
    ContextRuntimeOutput,
    WorkingSource,
)

# Module
from narranexus.platform.module_system import XYZBaseModule, HookManager, agent_id_headers

# Narrative
from narranexus.platform.narrative import Narrative, Event, EventService, NarrativeService, config

# Utils
from narranexus.platform.utils import DatabaseClient, get_db_client_sync
from narranexus.platform.utils.timezone import (
    DEFAULT_TIMEZONE,
    format_timestamp_for_agent,
    resolve_timezone,
)

# Settings (leaf module, safe to import at module level)
from narranexus.platform.settings import settings

# Prompts
from narranexus.platform.context_runtime.prompts import (
    AUXILIARY_NARRATIVES_HEADER,
    MODULE_INSTRUCTIONS_HEADER,
    RECENT_ACTIONS_HEADER,
    REPLY_LANGUAGE_SECTION,
    BOOTSTRAP_INJECTION_PROMPT,
    USER_TEMPORAL_CONTEXT,
    SECURITY_IRON_RULES,
    TURN_CONTEXT_HEADER,
    USER_MESSAGE_SEPARATOR,
)


# Reply-language directive (fix: UI language never reached the model).
# Code -> English display name for the directive line; unknown codes fall
# back to the bare code. Kept tiny on purpose — the code itself is what
# the model needs.
_REPLY_LANGUAGE_NAMES = {
    "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean",
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "ru": "Russian", "ar": "Arabic", "it": "Italian", "nl": "Dutch",
}


def build_reply_language_section(language: str | None) -> str:
    """One byte-stable system-prompt section, empty when unset.

    Stable per user (changes only when the user flips the toggle), so it
    is safe in the cacheable prompt region — R4 discipline. Since
    2026-08-20 the directive's priority order is: an explicit language
    request in the message > the current message's own language > this
    configured preference as the undeterminable-language fallback (see
    prompts.REPLY_LANGUAGE_SECTION for the policy history).
    """
    code = (language or "").strip()
    if not code:
        return ""
    name = _REPLY_LANGUAGE_NAMES.get(code.lower().split("-")[0], code)
    return REPLY_LANGUAGE_SECTION.format(name=name, code=code)




#: ``part_sizes`` sentinel for a section that is required in this deployment
#: mode and rendered nothing — printed as ``<id>=MISSING`` in
#: ``[SYSPROMPT-BREAKDOWN]``. Negative so it can never collide with a real
#: character count.
MISSING_SECTION_SIZE = -1


def _conversation_scope(ctx_data: ContextData, *, agent_id: str, user_id: str | None, event_id: str) -> str:
    """A bearer-safe authorization scope built only from runtime routing facts.

    Narratives can be shared across users and sources, so their id alone is
    insufficient. Missing conversation routing degrades to this turn only;
    it must never become a shared empty grant scope.
    """
    if not event_id:
        return ""
    extra = ctx_data.extra_data or {}
    tag: Any = extra.get("channel_tag") or {}
    serialize_tag = getattr(tag, "to_dict", None)
    if callable(serialize_tag):
        tag = serialize_tag()
    if not isinstance(tag, dict):
        tag = {}
    source = str(getattr(ctx_data.working_source, "value", None) or ctx_data.working_source or "")
    room = str(extra.get("bus_channel_id") or tag.get("room_id") or "")
    narrative = ctx_data.narrative_id or ""
    parts = [agent_id, user_id or "", source, str(tag.get("channel") or ""),
             room, str(tag.get("thread_id") or ""), narrative]
    if not narrative and not room:
        parts.append(event_id)
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode("utf-8")).hexdigest()
    return f"thread_{digest}"


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
    6. Build the final messages and mcp_servers
    """

    # Maximum characters per single message (prevents a single overly long message from consuming too much Context)
    SINGLE_MESSAGE_MAX_CHARS = 4000

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        # APPENDED, never inserted: existing callers pass database_client
        # positionally, so a new parameter ahead of it would silently rebind
        # their third argument. Same rule the bearer-identity record follows,
        # and for the same reason.
        event_id: Optional[str] = None,
    ):
        """
        Initialize ContextRuntime

        Args:
            agent_id: Agent ID
            user_id: User ID (if applicable)
            database_client: Database client (used for reading data)
            event_id: This turn's events-row id, forwarded to MCP tools so
                artifact attribution can name the turn that made a change.
        """
        logger.debug(f"    → ContextRuntime.__init__() called with agent_id={agent_id}, user_id={user_id}")
        self.agent_id = agent_id
        self.user_id = user_id
        # This turn's events-row id, for the MCP identity headers. Created in
        # Step 0, so Step 3 (which builds this) already has it — which is what
        # lets artifact attribution record WHICH turn made a change instead of
        # inferring one from timestamps.
        self.event_id = event_id
        self.db = database_client or get_db_client_sync()
        self.hook_manager = HookManager()
        logger.debug("    ContextRuntime initialized")

    async def run(
        self,
        narrative_list: List[Narrative],
        active_instances: List,  # Changed to active_instances (module already bound)
        input_content: str,  # Added: current user input
        working_source: Union[WorkingSource, str] = WorkingSource.CHAT,
        created_job_ids: Optional[List[str]] = None,
        trigger_extra_data: Optional[Dict[str, Any]] = None,
        context_providers: tuple = (),
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
        # Extract the list of module objects (for gather)
        module_list = [inst.module for inst in active_instances if inst.module is not None]
        ctx_data = await self.hook_manager.gather(module_list, ctx_data)

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
        if context_providers:
            # contextProviders (L2 capabilities, Assemble only): stable sections
            # first, volatile ones after, in registry order; an empty string
            # says nothing, a raising provider costs a warning, not the turn.
            extra = await self._context_provider_sections(context_providers, ctx_data)
            if extra:
                system_prompt = system_prompt + "\n\n" + extra
                logger.info(f"    │ ✅ {len(context_providers)} context provider(s) appended {len(extra)} characters")

        # Step 5: Build input for Agent Framework
        logger.info("    │ Step 2: Building input for Agent Framework")
        messages, mcp_servers, disallowed_tools, expressive_tools, deferred_tools = await self.build_input_for_framework(
            messages, system_prompt, active_instances, ctx_data,
            narrative_list=narrative_list,
        )
        logger.info(f"    │ ✅ Framework input built: {len(messages)} messages, {len(mcp_servers)} MCP servers")

        logger.info("    └─ ContextRuntime.run() completed")
        return ContextRuntimeOutput(
            messages=messages,
            mcp_servers=mcp_servers,
            disallowed_tools=disallowed_tools,
            expressive_tools=expressive_tools,
            deferred_tools=deferred_tools,
            ctx_data=ctx_data,
        )


    @staticmethod
    async def _context_provider_sections(providers: tuple, ctx_data: ContextData) -> str:
        stable: list[str] = []
        volatile: list[str] = []
        for provider in providers:
            name = getattr(provider, "name", type(provider).__name__)
            for method, bucket in (("contribute_instructions", stable), ("contribute_turn_context", volatile)):
                fn = getattr(provider, method, None)
                if fn is None:
                    continue
                try:
                    text = await fn(ctx_data)
                except Exception as exc:  # noqa: BLE001 — one provider must not cost the turn
                    logger.warning(f"          context provider {name!r}.{method} failed: {exc}")
                    continue
                if text and text.strip():
                    bucket.append(f"## {name}\n{text.strip()}")
        return "\n\n".join(stable + volatile)

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
        # Step 1: Call the module's contribute_instructions method
        instructions = await module_object.contribute_instructions(ctx_data)
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
    ) -> Tuple[List[Dict[str, Any]], List[Event], ContextData]:
        """
        Extract data from Narratives (enhanced version: supports multiple Narratives + intelligent Event selection).

        Processing logic:
        1. Main Narrative (1st): Use hybrid strategy to select Events (for detailed history in System Prompt)
        2. Auxiliary Narratives (2nd and beyond): Only load topic_hint as reference

        Note (after 2025-12-09 refactoring):
        - Chat history (chat_history) is now provided by ChatModule via EventMemoryModule
        - The messages returned by this method are mainly used for detailed Event history display in System Prompt
        - ChatModule.gather() will populate ctx_data.chat_history

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
                max_recent=config.MAX_RECENT_EVENTS,
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
        # Since 2026-09-07 the prompt is a slot: providers in ``prompt.sections``
        # render the parts (builtin.prompts ships security / temporal /
        # narrative / modules / bootstrap), the ``prompt.assembler`` binding
        # joins them. A distribution or narranexus.toml reorders, drops or
        # replaces without touching this file.
        from narranexus.contracts.prompt import (
            PromptContext,
            RenderedSection,
            RequiredSectionMissing,
            section_required,
        )
        from narranexus.platform.prompt_slots import assembler_for, sections_for
        from narranexus.platform.utils.deployment_mode import get_deployment_mode

        pctx = PromptContext(
            agent_id=self.agent_id,
            user_id=ctx_data.user_id,
            ctx_data=ctx_data,
            narrative_list=list(narrative_list),
            selected_events=list(selected_events),
            module_instructions=list(module_instructions_list),
            db=self.db,
            runtime=self,
            deployment_mode=get_deployment_mode(),
        )
        # The Bootstrap.md lifecycle (threshold, auto-delete, the
        # ctx_data.bootstrap_active flag) is the platform's, settled here
        # before any section renders: a prompt section is optional and
        # reorderable, so a lifecycle side effect inside one would vanish the
        # moment a distribution dropped it.
        await self._settle_bootstrap(ctx_data, pctx.user_id)
        rendered: List[RenderedSection] = []
        for provider in sections_for():
            # Per-section isolation is the right policy for a DEGRADABLE
            # contribution ("one section must not take the turn down") and the
            # wrong one for a load-bearing one: the cloud security preamble used
            # to be droppable by a binding, a builtin_overrides disable or one
            # raising provider, with a single warning and no visible difference.
            # A section that declares itself required in THIS deployment mode
            # fails the prompt instead, and shows up in [SYSPROMPT-BREAKDOWN] as
            # ``<id>=MISSING`` so the existing greps see it.
            required = section_required(provider, pctx.deployment_mode)
            try:
                text = await provider.render(pctx)
            except Exception as exc:  # noqa: BLE001 — one OPTIONAL section must not take the turn down
                if required:
                    self._report_missing_section(provider.id, rendered)
                    logger.error(f"        required prompt section {provider.id!r} raised: {exc}")
                    raise RequiredSectionMissing(provider.id, pctx.deployment_mode, exc) from exc
                logger.warning(f"        prompt section {getattr(provider, 'id', provider)!r} failed: {exc}")
                text = None
            if not text and required:
                self._report_missing_section(provider.id, rendered)
                logger.error(f"        required prompt section {provider.id!r} rendered empty")
                raise RequiredSectionMissing(provider.id, pctx.deployment_mode)
            if text:
                rendered.append(RenderedSection(id=provider.id, owner=type(provider).__module__, order=getattr(provider, "order", 100), text=text, budget_chars=int(getattr(provider, "budget_chars", 0) or 0)))
                logger.debug(f"        Added prompt section {provider.id}: {len(text)} chars")
        full_prompt = await assembler_for().assemble(rendered, pctx)
        part_sizes: Dict[str, int] = dict(pctx.part_sizes) or {s.id: s.chars for s in rendered}
        narrative_meta: Dict[str, int] = {k: v for k, v in pctx.meta.items() if k.startswith("nar_")}
        logger.debug(f"      build_complete_system_prompt() completed: {len(full_prompt)} total chars")
        self._last_part_sizes = part_sizes
        self._last_module_instructions = module_instructions_list
        self._last_narrative_meta = narrative_meta
        self._maybe_dump_system_prompt(self.agent_id, full_prompt, part_sizes, module_instructions_list)
        return full_prompt.strip()

    def _report_missing_section(self, section_id: str, rendered: List[Any]) -> None:
        """Emit the one [SYSPROMPT-BREAKDOWN] line for a refused prompt.

        The turn is about to fail, so the normal emission point (after
        assembly) is never reached — and "the prompt build raised" alone does
        not say WHICH contribution went missing. This line carries the sections
        that did render plus ``<id>=MISSING``, which is what the existing greps
        already look at.
        """
        try:
            part_sizes = {s.id: s.chars for s in rendered}
            part_sizes[section_id] = MISSING_SECTION_SIZE
            self._log_system_prompt_breakdown(self.agent_id, 0, part_sizes, [], {})
        except Exception as exc:  # noqa: BLE001 — diagnostics must never mask the real failure
            logger.warning(f"[SYSPROMPT-BREAKDOWN] emission failed: {exc}")

    @staticmethod
    def _maybe_dump_system_prompt(
        agent_id: str,
        full_prompt: str,
        part_sizes: Dict[str, int],
        module_instructions_list: List[ModuleInstructions],
    ) -> None:
        """TEMPORARY (debug/system-prompt-part-breakdown branch only): when
        ``NARRA_SYSPROMPT_DUMP_DIR`` is set, write the full system prompt of
        every round to a file so its exact content is inspectable while
        chasing the system-prompt-growth incident (2026-07). Off unless the
        env var is set — never runs in normal operation. Each dump is prefixed
        with a per-Part + per-module size header so a directory of dumps can be
        diffed round-to-round to see what grew. Failures never break a turn.
        """
        import os
        dump_dir = os.environ.get("NARRA_SYSPROMPT_DUMP_DIR")
        if not dump_dir:
            return
        try:
            import time
            os.makedirs(dump_dir, exist_ok=True)
            stamp = f"{time.time_ns()}"
            fname = os.path.join(dump_dir, f"{agent_id}_{len(full_prompt)}_{stamp}.txt")
            header_parts = " ".join(f"{k}={v}" for k, v in sorted(part_sizes.items()))
            # Emitted order (R4d) — same order the blocks were concatenated
            # in, so a same-length REORDER is visible in a dump diff.
            header_mods = " ".join(
                f"{mi.name}={len(mi.instruction or '')}"
                for mi in ContextRuntime._sorted_module_instructions(module_instructions_list)
            )
            header = (
                f"# agent={agent_id} total={len(full_prompt)}\n"
                f"# parts: {header_parts}\n"
                f"# modules: {header_mods}\n"
                f"{'=' * 80}\n"
            )
            with open(fname, "w", encoding="utf-8") as f:
                f.write(header + full_prompt)
        except Exception as e:  # noqa: BLE001 — dump is diagnostic; never break a turn
            logger.warning(f"[SYSPROMPT-DUMP] failed: {e}")

    # Prefix buckets for the [SYSPROMPT-BREAKDOWN] localization hashes (R4d).
    # Chosen to bracket the regions where divergence has actually been found:
    # ~1K (narrative metadata), ~4-5K (module block boundaries), and the tail
    # of the module section.
    _PREFIX_BUCKETS: Tuple[Tuple[str, int], ...] = (
        ("pfx2k", 2000),
        ("pfx8k", 8000),
        ("pfx32k", 32000),
    )

    @staticmethod
    def _prefix_bucket_hashes(text: str) -> str:
        """Render `pfx2k=<6hex> pfx8k=<6hex> pfx32k=<6hex>` for `text`.

        Each value is sha256 over the FIRST N characters of the measured
        string, truncated to 6 hex chars. Purpose (R4d, 2026-07-28): the
        per-part byte counts on the same log line cannot see a same-length
        substitution or reorder — two rounds can report identical
        `total=`/`parts:`/`modules:` and still differ in bytes. Bucket
        hashes turn that invisible class of divergence into a localized
        diff: pfx2k differing means the break is in the first 2K
        (narrative metadata region), pfx2k equal + pfx8k differing narrows
        it to 2K-8K (module block boundaries), and so on — no packet
        capture or prompt dump needed. Empty string when there is nothing
        to hash, so the field simply disappears for callers that pass no
        text.
        """
        if not text:
            return ""
        import hashlib

        return " ".join(
            f"{label}={hashlib.sha256(text[:size].encode('utf-8')).hexdigest()[:6]}"
            for label, size in ContextRuntime._PREFIX_BUCKETS
        )

    @staticmethod
    def _log_system_prompt_breakdown(
        agent_id: str,
        total_chars: int,
        part_sizes: Dict[str, int],
        module_instructions_list: List[ModuleInstructions],
        narrative_meta: Dict[str, int],
        ctx_sha256: str = "",
        prompt_text: str = "",
    ) -> None:
        """Emit one INFO line decomposing the system prompt into its Parts,
        every module-instruction contributor in EMITTED order, the Narrative's
        growth-prone sub-fields (current_summary length, dynamic_summary entry
        count), and prefix-bucket hashes for divergence localization.

        Diagnostic for the system-prompt-growth incident (2026-07): the prompt
        drifts toward the 115K ceiling (MAX_SYSTEM_PROMPT_LENGTH) and, once the
        reply instruction is diluted / history is evicted, the agent stops
        calling its reply tool at all. Logging every round's
        composition makes the growth source greppable in production without a
        debug build. Pure/static so it is unit-testable in isolation.

        `prompt_text` is the string whose composition is being reported; it is
        used ONLY to compute the prefix-bucket hashes (see
        _prefix_bucket_hashes) and is optional so existing callers/tests that
        only care about sizes keep working.
        """
        # The section set is whatever the prompt.sections registry rendered
        # (a plugin section shows up here by its id), in emitted order, plus the
        # runtime-appended parts (turn_context, reply_language). The six builtin
        # ids keep their names so existing grep one-liners still match. A size of
        # ``MISSING_SECTION_SIZE`` prints as ``<id>=MISSING``: a section that is
        # REQUIRED in this deployment mode and rendered nothing (the prompt is
        # refused, and this line is what says which one) — a numeric 0 would be
        # indistinguishable from a section that simply had nothing to say.
        parts_str = " ".join(
            f"{name}=" + ("MISSING" if size == MISSING_SECTION_SIZE else str(size))
            for name, size in part_sizes.items()
        )
        # ALL module instruction sizes — not just the top few — so the per-turn
        # grower (a module whose contribute_instructions embeds accumulating ctx_data)
        # is identifiable by diffing this list across rounds.
        #
        # Order = EMITTED order (R4d, 2026-07-28), i.e. exactly the order
        # _build_module_instructions_prompt concatenated the blocks in. It used
        # to be sorted by size descending, which made a same-length block
        # REORDER — the Awareness<->SocialNetwork class of prefix breaker —
        # completely invisible here: the printed list is identical either way.
        # In emitted order, a reorder shows up as the tokens changing places.
        module_sizes = [
            (mi.name, len(mi.instruction or ""))
            for mi in ContextRuntime._sorted_module_instructions(module_instructions_list)
        ]
        modules_str = " ".join(f"{name}={size}" for name, size in module_sizes)
        nar_str = " ".join(f"{k}={v}" for k, v in narrative_meta.items()) or "n/a"
        # ctx_sha256 = first 12 hex chars of sha256 over the system prompt as
        # assembled by ContextRuntime (incl. timeline preamble). Two turns
        # with a byte-stable ContextRuntime output log the SAME value. NOTE
        # (R4c instrument calibration): this is NOT the full adapter-facing
        # system[2] — the claude adapter may still append the cold-round
        # "=== Chat History ===" tail after this point. The authoritative
        # `sys_sha256=` line is emitted by the claude adapter
        # post-assemble_argv_prompt ([SYSPROMPT-SHA]); this narrower hash
        # was renamed ctx_sha256 so a `grep sys_sha256` finds only the real
        # sent-bytes hash. Empty when the caller didn't hash.
        pfx_str = ContextRuntime._prefix_bucket_hashes(prompt_text)
        logger.info(
            f"[SYSPROMPT-BREAKDOWN] agent={agent_id} total={total_chars} | "
            f"parts: {parts_str} | narrative: {nar_str} | modules: {modules_str} | "
            f"ctx_sha256={ctx_sha256}" + (f" {pfx_str}" if pfx_str else "")
        )

    async def _resolve_user_timezone(self, user_id: Optional[str]) -> str:
        """Read `users.timezone`, degrading to UTC rather than failing.

        Single entry point for "what timezone should this turn render in",
        used by both the temporal block and the history timeline so the two
        can never end up in different frames — which is the exact defect
        `_format_timeline_tag` documents.
        """
        if not user_id:
            return DEFAULT_TIMEZONE
        try:
            from narranexus.platform.repository import UserRepository
            return resolve_timezone(
                await UserRepository(self.db).get_user_timezone(user_id)
            )
        except Exception as e:  # noqa: BLE001 — a tz lookup must not kill a turn
            logger.warning(f"        Timezone lookup failed for {user_id}: {e}; using UTC")
            return DEFAULT_TIMEZONE

    async def _settle_bootstrap(self, ctx_data: Any, user_id: Optional[str]) -> None:
        """Owner's first turns: mark ``ctx_data.bootstrap_active`` while Bootstrap.md is inside its threshold; delete it once past."""
        try:
            from narranexus.platform.bootstrap.lifecycle import is_bootstrap_active
            from narranexus.platform.repository import AgentRepository

            agent_record = await AgentRepository(self.db).get_agent(self.agent_id)
            if not (agent_record and agent_record.created_by and agent_record.created_by == user_id):
                return
            status = await is_bootstrap_active(self.db, self.agent_id, agent_record.created_by, agent_record.agent_metadata)
            if status.present and not status.active:
                try:
                    os.remove(status.bootstrap_path)
                    logger.info(
                        f"        Auto-deleted Bootstrap.md after {status.event_count} events "
                        f"(threshold={status.threshold}, agent={self.agent_id})"
                    )
                except OSError as rm_err:
                    logger.warning(f"        Failed to auto-delete Bootstrap.md: {rm_err}")
                return
            if status.active:
                ctx_data.bootstrap_active = True
        except Exception as exc:  # noqa: BLE001 — bootstrap must never break a turn
            logger.warning(f"        Failed to settle Bootstrap: {exc}")

    async def build_user_temporal_block(
        self, user_id: Optional[str], user_tz: Optional[str] = None
    ) -> str:
        """
        Build the User Temporal Context block (v2 timezone protocol).

        `user_tz` lets a caller that has already resolved the turn's timezone
        pass it in instead of paying for a second identical query — and, more
        importantly, guarantees that every block in that turn names the SAME
        zone even if the user's row changes mid-turn.

        Reads users.timezone (falls back to UTC for users who have never
        synced their browser timezone) and states the user's IANA timezone
        plus the protocol for expressing times back to them.

        It deliberately does NOT restate the current time (2026-08-18). It
        used to, as `now_local_dt.replace(tzinfo=None).isoformat()` — naive,
        no offset, no weekday — which is the exact shape
        `utils/timezone.format_now_for_agent` exists to replace, and it sat
        in the same turn-context block as the correctly-framed "Real World
        Information" value. Two "now"s, one of them in the format a prior
        incident was traced to, is worse than one: there is no reading of the
        prompt where the second one is the authority, so it can only add
        doubt. The ground truth now has exactly one renderer and one site.
        """
        if not user_id:
            return ""
        return USER_TEMPORAL_CONTEXT.format(
            user_tz=user_tz or await self._resolve_user_timezone(user_id)
        )

    async def _build_turn_context_block(
        self,
        active_instances: List,
        ctx_data: ContextData,
        narrative_list: Optional[List[Narrative]] = None,
    ) -> str:
        """Assemble the per-turn [Turn context] block (R4 relocation).

        Order (fixed — cache stability depends on determinism):
        1. User Temporal Context (heading name unchanged — job MCP tool
           docstrings reference it)
        2. Current narrative state (updated_at + current_summary)
        3. Module contribute_turn_context blocks — deduplicated by module_class in
           active_instances order, then sorted by the total
           (priority, module_class) order (same ordering semantics as
           _build_module_instructions_prompt / _sorted_module_instructions)
        4. Recent background activity

        Every source is individually fail-open: a failing part is skipped
        with a warning, never fatal to the turn (same semantics its
        system-prompt predecessor had).
        """
        parts: List[str] = [TURN_CONTEXT_HEADER]

        # Resolved once for this block: the temporal section names this zone
        # and the recent-activity section renders its timestamps in it.
        #
        # Scope of that guarantee, stated exactly (2026-08-18): it covers the
        # blocks assembled HERE, and — with relocation enabled, the default —
        # that is every dated block in the turn. With the relocation gate OFF,
        # `build_complete_system_prompt` emits its own User Temporal Context
        # from a separate lookup, so the prompt carries two resolutions rather
        # than one. Not unified because the two live in different methods
        # called in sequence, and the only way to span them is a per-turn
        # cache — which would hold a stale zone if the user changed theirs,
        # a worse failure than one redundant primary-key read. The window for
        # them to disagree is a user editing their timezone between two
        # awaits of the same turn.
        block_tz = await self._resolve_user_timezone(ctx_data.user_id)

        # 1. Temporal block (relocated Part 0 — same wording, same heading)
        try:
            temporal_block = await self.build_user_temporal_block(
                ctx_data.user_id, block_tz
            )
            if temporal_block:
                parts.append(temporal_block)
        except Exception as e:  # noqa: BLE001 — fail-open per part
            logger.warning(f"        Turn context: failed to build User Temporal Context: {e}")

        # 2. Narrative volatile state (relocated from Part 1)
        if narrative_list:
            try:
                narrative_turn_prompt = await NarrativeService(
                    self.agent_id
                ).combine_narrative_turn_prompt(narrative_list[0])
                if narrative_turn_prompt:
                    parts.append(narrative_turn_prompt)
            except Exception as e:  # noqa: BLE001 — fail-open per part
                logger.warning(f"        Turn context: failed to build narrative state: {e}")

        # 3. Module per-turn blocks (deduped by module_class, then sorted by
        #    the TOTAL (priority, module_class) order — same semantics as
        #    _sorted_module_instructions. This block lands in the message,
        #    not the cacheable prefix, so a reorder here is not a cache
        #    breaker; it uses the total order anyway so that "module block
        #    order" means exactly one thing everywhere (R4d).
        module_blocks: List[Tuple[int, str, str]] = []
        seen_module_classes = set()
        for inst in active_instances:
            if inst.module_class in seen_module_classes or inst.module is None:
                continue
            seen_module_classes.add(inst.module_class)
            try:
                block = await inst.module.contribute_turn_context(ctx_data)
            except Exception as e:  # noqa: BLE001 — one module must not kill the turn
                logger.warning(
                    f"        Turn context: contribute_turn_context failed for "
                    f"{inst.module_class}: {e}"
                )
                continue
            if block:
                module_blocks.append(
                    (inst.module.config.priority, inst.module_class, block)
                )
        module_blocks.sort(key=lambda kv: (kv[0], kv[1]))
        parts.extend(block for _, _, block in module_blocks)

        # 4. Recent background activity (relocated from the system prompt tail)
        try:
            recent_actions = (getattr(ctx_data, "extra_data", None) or {}).get("recent_actions") or []
            if recent_actions:
                parts.append(
                    self._build_recent_actions_section(recent_actions, block_tz)
                )
                logger.info(
                    f"[RecentActions] rendered {len(recent_actions)} actions into turn context"
                )
        except Exception as e:  # noqa: BLE001 — fail-open per part
            logger.warning(f"        Turn context: failed to build recent actions: {e}")

        # Header-only means every part was empty or failed. Returning it would
        # prepend "[Turn context]" with nothing under it to the user's message —
        # tokens spent on a heading, and an instruction to read a section that
        # does not exist. Same reasoning that moved the timeline reading-guide
        # out of the system prompt: never announce content that isn't there.
        if len(parts) == 1:
            return ""

        return "\n\n".join(parts)

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

    @staticmethod
    def _sorted_module_instructions(
        module_instructions_list: List[ModuleInstructions]
    ) -> List[ModuleInstructions]:
        """Total order for module instruction blocks: (priority, name).

        THE single ordering authority for module blocks — the emitted prompt
        (_build_module_instructions_prompt) and both diagnostics
        (_log_system_prompt_breakdown, _maybe_dump_system_prompt) all go
        through it, so what the log prints is what the prompt concatenated.

        Why the secondary key (R4d, 2026-07-28): `sorted` is stable, so a
        priority-only key left TIES inheriting upstream order. Upstream is
        InstanceRepository.get_public_instances(), which had no `order_by` —
        i.e. whatever row order the engine felt like. SQLite happens to
        return rowid order today, but Postgres/MySQL make no such promise
        (a HEAP re-read, a plan flip, or an index-only scan reorders rows
        freely). Live ties exist: BasicInfo(2)/GeneralMemory(2),
        Awareness(3)/SocialNetwork(3), Lark(6)/Discord(6)/Slack(6),
        Telegram(7)/WeChat(7). An Awareness<->SocialNetwork swap moves
        ~4018 and ~4880 bytes with ZERO net length change — a same-length
        reorder that punctures the cacheable prefix while every byte-count
        diagnostic reports "no change". `name` is the module class name and
        the list is deduplicated by module_class upstream, so (priority,
        name) is a genuinely total order.
        """
        return sorted(module_instructions_list, key=lambda x: (x.priority, x.name))

    async def build_module_instructions_prompt(
        self,
        module_instructions_list: List[ModuleInstructions]
    ) -> str:
        """Build the Prompt for Module instructions."""
        sorted_instructions = self._sorted_module_instructions(module_instructions_list)

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
        return await self.build_module_instructions_prompt(module_instructions_list)

    async def build_input_for_framework(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        active_instances: List,  # Changed to active_instances
        ctx_data: ContextData,
        narrative_list: Optional[List[Narrative]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
        """
        Build input for the Agent Framework.

        Args:
            messages: Historical messages extracted from Narrative/Event (for System Prompt reference, now deprecated)
            system_prompt: The built system prompt
            active_instances: List of Module Instances (module already bound)
            ctx_data: Context data (containing chat_history populated by ChatModule)
            narrative_list: Narratives for this turn (1st = main). Needed for
                the per-turn narrative state in the [Turn context] block (R4
                relocation); None = no narrative turn block.

        Returns:
            (messages, mcp_servers, disallowed_tools)
            - messages: Complete messages list including system prompt and historical messages
            - mcp_servers: Dictionary of {server_name: {"url": str, "headers": {str: str}?}}
            - disallowed_tools: Fully-qualified tool names modules asked to
              suppress this turn (setup-residency; sorted, deduplicated)

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
        # narrative_id/alias by ChatModule.gather. Render every line
        # as a role message prefixed `[time · topic · nar_id]` + the channel
        # source prefix. No more long/short split; no cross-narrative-into-
        # system-prompt section. The "how to read this timeline" preamble is
        # NOT added here — the materializer emits it together with the
        # history block, so a turn whose rows get evicted never carries a
        # reading guide for rows that aren't there (prod 2026-07-29).
        timeline = self._truncate_long_term_messages(chat_history)

        # Native turn replay (2026-07-29): when this agent's framework
        # projects its own context (NexusPower), expand current-narrative
        # assistant rows into the turn's REAL message sequence — monologue
        # text + tool calls + paired results rebuilt from events.event_log —
        # instead of the two-line flattened summary. Cross-narrative rows
        # and rows without a usable log stay flattened; the narrative's
        # summary (system prompt Part 2) keeps covering everything that
        # fell off the timeline window. Fail-open at every level: replay
        # is context enrichment, never worth breaking a turn.
        native_replays: Dict[str, List[Dict[str, Any]]] = {}
        try:
            native_replays = await self._load_native_turn_replays(timeline)
        except Exception as e:  # noqa: BLE001 — enrichment, never fatal
            logger.warning(
                f"[NativeReplay] load failed, keeping flattened history: {e}"
            )

        enhanced_system_prompt = system_prompt

        relocation_enabled = settings.prompt_turn_context_relocation_enabled

        # One timezone for everything this method renders — the recent-activity
        # timestamps below and every history-timeline row further down. Hoisted
        # here rather than resolved next to the timeline loop so the two cannot
        # drift apart; they describe overlapping events in the same prompt.
        turn_tz = await self._resolve_user_timezone(ctx_data.user_id)

        # P2: append the recent background-activity section (centered small-text
        # in the UI) — a compact list with event_ids, separate from the timeline.
        # R4: volatile (accumulates per turn) → relocated to the turn context
        # when relocation is enabled, so the system prompt tail stays stable.
        recent_actions = (getattr(ctx_data, "extra_data", None) or {}).get("recent_actions") or []
        if recent_actions and not relocation_enabled:
            enhanced_system_prompt += "\n\n" + self._build_recent_actions_section(
                recent_actions, turn_tz
            )
            logger.info(f"[RecentActions] rendered {len(recent_actions)} actions into system prompt")

        # Reply language: the user's persisted preference reaches the model
        # here (it used to live only in frontend i18n — the "UI set to
        # Chinese, replies stay English" bug). Byte-stable per user, so the
        # cacheable region is safe; fail-open — a settings read must never
        # break a turn.
        reply_language_chars = 0
        if self.user_id:
            try:
                from narranexus.platform.repository.user_settings_repository import (
                    UserSettingsRepository,
                )

                reply_lang = await UserSettingsRepository(self.db).get_reply_language(
                    self.user_id
                )
                section = build_reply_language_section(reply_lang)
                if section:
                    enhanced_system_prompt += "\n\n" + section
                    reply_language_chars = len(section)
            except Exception as e:  # noqa: BLE001 — preference is enrichment
                logger.warning(f"[ReplyLanguage] lookup failed, skipping: {e}")

        final_messages = [
            {"role": "system", "content": enhanced_system_prompt}
        ]
        logger.debug(f"        Added system prompt + timeline preamble: {len(enhanced_system_prompt)} chars")

        # Each line: [time · topic · nar_id] + channel source prefix + content.
        # The narrative tag lets the agent tell threads apart / re-route; the
        # source prefix (MessageSourceRegistry) marks UI vs Lark vs bus, etc.
        from narranexus.platform.channel.message_source_handler import (
            MessageSourceRegistry,
        )
        cross_count = 0
        replayed_count = 0
        for msg in timeline:
            meta = msg.get("meta_data") or {}
            ws = meta.get("working_source", "chat")
            handler = MessageSourceRegistry.get(ws)
            src_prefix = handler.format_row_prefix(msg)
            tag = self._format_timeline_tag(meta, turn_tz)
            if meta.get("memory_type") == "short_term":
                cross_count += 1
            if msg.get("role") == "assistant":
                # Native replay substitutes the ASSISTANT side of a turn
                # only: the user row above it keeps carrying the timeline
                # tag (time/topic/narrative anchoring), the replay carries
                # the structure. pop() so a duplicated row cannot inject
                # the same tool sequence twice.
                replay = native_replays.pop(str(meta.get("event_id") or ""), None)
                if replay:
                    final_messages.extend(replay)
                    replayed_count += 1
                    continue
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
            f"({cross_count} cross-narrative, {len(timeline) - cross_count} current, "
            f"{replayed_count} native-replayed) source={history_source}"
        )

        # Add current user input — augment with Read-tool markers for any
        # attachments carried on this turn WITHOUT mutating
        # ``ctx_data.input_content`` (which is the string persisted by
        # ChatModule.persist_turn as the user message's ``content`` and
        # rendered verbatim in the frontend chat panel). The marker is
        # visible ONLY to the LLM this turn; the next turn's history read
        # will re-synthesise the SAME marker from ``msg["attachments"]``,
        # so agent behaviour is uniform across current-turn vs historical.
        current_user_content = ctx_data.input_content or ""

        # R4 turn-context relocation: prepend the per-turn volatile block to
        # the LLM-facing current message (attachment-marker precedent: the
        # persisted ``ctx_data.input_content`` is NEVER touched, so chat
        # persistence / frontend rendering / cold-start history re-synthesis
        # stay turn-context-free). Runs unconditionally each turn — cold and
        # resume rounds get the identical structure. Fail-open: any assembly
        # failure keeps the message as-is (same drop-with-warning semantics
        # the volatile sections already had on the system-prompt path).
        turn_context_chars = 0
        if relocation_enabled:
            try:
                turn_context_block = await self._build_turn_context_block(
                    active_instances, ctx_data, narrative_list
                )
                turn_context_chars = len(turn_context_block)
                # An empty block means no part produced content. Wrapping anyway
                # would prefix the user's words with two blank lines and a lone
                # [User message] separator that separates nothing — so send the
                # message through untouched instead.
                if turn_context_block:
                    current_user_content = (
                        f"{turn_context_block}\n\n{USER_MESSAGE_SEPARATOR}\n\n{current_user_content}"
                    )
                    logger.debug(
                        f"        Prepended [Turn context] block: {turn_context_chars} chars "
                        f"(persisted input_content stays original)"
                    )
                else:
                    logger.debug(
                        "        Turn context empty — user message sent unwrapped"
                    )
            except Exception as e:  # noqa: BLE001 — turn context must never break a turn
                logger.warning(f"        Failed to assemble [Turn context] block: {e}")

        raw_atts = (ctx_data.extra_data or {}).get("attachments")
        if isinstance(raw_atts, list) and raw_atts:
            from narranexus.platform.schema.attachment_schema import Attachment

            markers = Attachment.markers_from_dicts(
                raw_atts,
                agent_id=ctx_data.agent_id,
                user_id=ctx_data.user_id or "",
            )
            if markers:
                current_user_content = (
                    f"{current_user_content}\n{markers}"
                    if current_user_content
                    else markers
                )
                logger.debug(
                    f"        Injected {len(raw_atts)} current-turn "
                    f"attachment marker(s) into LLM-facing user message "
                    f"(persisted content stays original)"
                )
        final_messages.append({
            "role": "user",
            "content": current_user_content,
        })
        logger.debug(f"        Added current user input: {len(current_user_content)} chars")

        # [SYSPROMPT-BREAKDOWN] — emitted here (not in
        # build_complete_system_prompt) because this is where ContextRuntime's
        # final system prompt string exists (incl. preamble). ctx_sha256
        # hashes THIS string; the claude adapter may still append the
        # cold-round history tail, so the authoritative sent-bytes hash
        # (`sys_sha256=`) is emitted by the adapter itself ([SYSPROMPT-SHA],
        # R4c instrument calibration). Part sizes were stashed by
        # build_complete_system_prompt; direct callers of this method
        # (tests) simply get empty parts. The string is also passed as
        # prompt_text so the line carries prefix-bucket hashes (R4d) —
        # they localize a same-length divergence that the byte counts and
        # the whole-string ctx_sha256 cannot.
        try:
            import hashlib
            ctx_sha256 = hashlib.sha256(enhanced_system_prompt.encode("utf-8")).hexdigest()[:12]
            part_sizes = dict(getattr(self, "_last_part_sizes", {}) or {})
            part_sizes["turn_context"] = turn_context_chars
            if reply_language_chars:
                part_sizes["reply_language"] = reply_language_chars
            self._log_system_prompt_breakdown(
                self.agent_id,
                len(enhanced_system_prompt),
                part_sizes,
                getattr(self, "_last_module_instructions", []) or [],
                getattr(self, "_last_narrative_meta", {}) or {},
                ctx_sha256=ctx_sha256,
                prompt_text=enhanced_system_prompt,
            )
        except Exception as e:  # noqa: BLE001 — diagnostics must never break a turn
            logger.warning(f"[SYSPROMPT-BREAKDOWN] emission failed: {e}")

        # Step 2: Collect all Module MCP URLs (deduplicated by module_class)
        logger.debug("        Step 2: Collecting MCP URLs from instances (deduped by module_class)")
        mcp_servers = {}
        disallowed_tools: list[str] = []
        # Facts about this turn that module MCP tools cannot otherwise know,
        # injected once and shared by every server below: WHAT KIND of turn is
        # calling, and — when MessageBusTrigger classified this turn as
        # continuing the agent's own errand — WHICH peer/channel that errand
        # is with. The tools decide what to do with them; notably a bus send
        # stamps itself as an errand question only when it is aimed AT that
        # scope, because the same turn also answers unrelated peers whose
        # unread arrived with it (2026-08-03 review; see
        # message_bus_module/_message_bus_mcp_tools._send_turn_source).
        turn_source = str(
            getattr(ctx_data.working_source, "value", None)
            or ctx_data.working_source or ""
        )
        turn_extra = ctx_data.extra_data or {}
        errand_peer = str(turn_extra.get("bus_errand_peer") or "")
        errand_channel = str(turn_extra.get("bus_errand_channel") or "")
        # This turn's events-row id. Created in Step 0, so it is already known
        # by the time Step 3 builds this spec — which is what lets attribution
        # record WHICH turn changed an artifact instead of guessing.
        # getattr, not self.event_id: several tests build this object with
        # ``ContextRuntime.__new__`` and set only the attributes they exercise,
        # so a bare read would make an optional field break unrelated suites.
        # Computed BEFORE root_run_id: a root turn's own id IS its root_run_id
        # (see the fallback below).
        event_id = str(getattr(self, "event_id", None) or "")
        thread_id = _conversation_scope(
            ctx_data, agent_id=self.agent_id, user_id=self.user_id, event_id=event_id,
        )
        # Which trigger TREE this turn belongs to. A message the agent sends
        # this turn becomes the trigger for someone else's run, and the tree
        # would otherwise be lost at that hop — so the bus send tools stamp it
        # onto the message.
        #
        # Falls back to this turn's OWN event_id when turn_extra carries none:
        # that is exactly the case where nothing upstream handed us a tree to
        # join, i.e. THIS turn is the root of a new one — the same rule
        # RunRecorder already applies when it writes `events.root_run_id`
        # (`inherited_root_run_id or run_id`). Skipping the fallback here left
        # the header "" for a root turn while the events row correctly held
        # the turn's own id, so anything this turn stamped via the header
        # (e.g. a work-board item via `record_handoffs`) recorded root_run_id
        # "" and could never be found again by `pause_by_root(root)` once the
        # owner stopped the run — the item stayed in_progress forever even
        # though the run itself did stop (GitHub #124).
        root_run_id = str(turn_extra.get("root_run_id") or "") or event_id
        # The turn's team, when it has one. Server-side on purpose: tools take
        # agent_id as a model-filled parameter, so "am I in a team" can never
        # be answered from tool arguments without letting a private turn claim
        # a team it is not in.
        team_id = str(turn_extra.get("bus_team_id") or "")
        # Delivery declaration (reply contract, both frameworks): each
        # module states which of its tools DELIVER content to a human.
        # Collected per module, then sorted by (origin_rank, priority,
        # module_class): the module that OWNS this turn's working_source
        # (claims_source) ranks first, plain (priority,
        # module_class) — the R4d order — breaks ties within a rank. NOT
        # the active_instances order: that is created_at-driven (see
        # get_public_instances), so a later-created channel instance
        # would steal the first slot. The first entry becomes the turn's
        # DEFAULT reply tool and is frozen into the framework's stable
        # prompt prefix — it follows whoever contacted the agent (a bus
        # turn defaults to the bus send, a wechat turn to wechat_send),
        # falling back to priority order (chat=1 first) when no module
        # owns the source. Deterministic across turns either way.
        #
        # Team rooms USED to be the one surface with no delivery surface at
        # all: the agent's plain text auto-posted and the prompt forbade
        # delivery tools, so an empty declaration was the only way to keep both
        # frameworks' reminders from contradicting the team prompt.
        #
        # That exception is gone (2026-08-17). A team reply is a `message_team`
        # call like every other surface's reply, so the general rule — "whoever
        # contacted you receives only what you send through a reply tool" — is
        # true everywhere again and the reminder can simply be rendered. The
        # modules decide WHICH verb belongs on this turn; the collection no
        # longer needs to know that team rooms exist.
        expressive_declarations: list[tuple[int, int, str, list[str]]] = []
        seen_module_classes = set()
        collected_count = 0

        for inst in active_instances:
            if inst.module_class in seen_module_classes or inst.module is None:
                continue
            seen_module_classes.add(inst.module_class)
            logger.debug(f"          Collecting the tool surface from {inst.module_class} ({inst.instance_id})")
            # The module's Assemble-stage cell (batch 5c): its MCP server, the
            # tools it suppresses for THIS agent THIS turn (setup-residency) and
            # the reply tools it declares — composed fail-open by the base class.
            surface = await inst.module.contribute_tools(ctx_data)
            for server_name, spec in surface.mcp_servers.items():
                server_url = spec.get("server_url") or spec.get("url")
                if not server_url:
                    logger.debug(f"          ⏭ Skipped MCP: {server_name} -> (empty URL)")
                    continue
                # Tell the module MCP server WHICH agent is calling. Module
                # servers are one shared process for all agents, so before
                # this the caller's own id arrived only as a tool parameter
                # the MODEL filled in — and a model that guessed
                # `agent_id="agent_current"` got a hard dead end and told
                # the user it couldn't do the task (P1, evt_0dcee899).
                # Headers are the only channel that survives on BOTH
                # adapters' transports; see module/_mcp_identity.py.
                mcp_servers[server_name] = {
                    "url": server_url,
                    "headers": agent_id_headers(
                        self.agent_id,
                        turn_source=turn_source,
                        errand_peer=errand_peer,
                        errand_channel=errand_channel,
                        # The turn owner. None on ownerless turns (some
                        # triggers) — the builder then omits the header
                        # and tools fall back to the model's parameter.
                        user_id=self.user_id,
                        root_run_id=root_run_id,
                        team_id=team_id,
                        event_id=event_id,
                        thread_id=thread_id,
                    ),
                }
                collected_count += 1
                logger.debug(f"          ✓ Added MCP: {server_name} -> {server_url}")
            if surface.disallowed_tools:
                disallowed_tools.extend(surface.disallowed_tools)
                logger.debug(
                    f"          ⛔ {inst.module_class} suppresses "
                    f"{len(surface.disallowed_tools)} tools (setup-residency)"
                )
            if surface.expressive_tools:
                # Origin-first: the module that OWNS this turn's
                # working_source sorts ahead of everyone, so the
                # first collected tool — the framework's default
                # reply tool — follows "whoever contacted you"
                # instead of hard-wiring the owner-chat tool.
                # Fail-open to the non-origin rank: a module
                # without the hook (or a crashing one) just keeps
                # plain priority order.
                try:
                    origin_rank = (
                        0
                        if inst.module.claims_source(
                            getattr(ctx_data, "working_source", None)
                        )
                        else 1
                    )
                except Exception:  # noqa: BLE001 — fail-open
                    origin_rank = 1
                expressive_declarations.append(
                    (
                        origin_rank,
                        inst.module.config.priority,
                        inst.module_class,
                        list(surface.expressive_tools),
                    )
                )

        logger.debug(f"        Collected {collected_count} MCP URLs from {len(active_instances)} instances (deduped by module_class)")

        # R4c: deterministic MCP server ordering. The dict insertion order
        # above follows active_instances iteration; sort by server name so
        # every consumer downstream (SDK mcp config JSON -> CLI) receives an
        # order that is byte-stable across turns and across processes.
        # (Per-server tool order is FastMCP registration order — code order,
        # deterministic; the cross-server merge order inside the CLI is the
        # one link we cannot control, see the claude adapter.)
        # Plugin contributions (batch 2a.5): site-level MCP servers join the
        # surface after the modules' own (a module server of the same name
        # wins), and plugin tools that are not always_visible become
        # "deferred": present on their server, reachable through tool_search,
        # kept out of the model's up-front tool list (spec §12).
        plugin_servers, deferred_tools = self._plugin_tool_surface(set(mcp_servers))
        for name, cfg in plugin_servers.items():
            mcp_servers[name] = cfg
        mcp_servers = dict(sorted(mcp_servers.items()))

        expressive_declarations.sort(key=lambda kv: (kv[0], kv[1], kv[2]))
        expressive_tools: list[str] = []
        for _, _, _, declared in expressive_declarations:
            for tool_name in declared:
                if tool_name not in expressive_tools:
                    expressive_tools.append(tool_name)

        logger.debug(
            f"      build_input_for_framework() completed: {len(final_messages)} messages, "
            f"{len(mcp_servers)} MCP servers, {len(disallowed_tools)} suppressed tools, "
            f"{len(expressive_tools)} reply tools"
        )
        return final_messages, mcp_servers, sorted(set(disallowed_tools)), expressive_tools, deferred_tools

    @staticmethod
    def _plugin_tool_surface(module_server_names: set[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Plugin MCP servers (minus names the modules already own) and the deferred plugin tool names.

        A plugin tool naming a server that is neither a plugin nor a module
        server is dropped with a warning (it could never be called). Deferred
        names are fully qualified (``mcp__<server>__<tool>``) so the frameworks
        can match them against their own tool namespace.
        """
        from narranexus.platform.utils.plugin_contributions import plugin_mcp_servers, plugin_tools

        servers = {n: c for n, c in plugin_mcp_servers().items() if n not in module_server_names}
        known = module_server_names | set(servers)
        deferred: list[str] = []
        for tool in plugin_tools():
            if tool.server and tool.server not in known:
                logger.warning(f"[plugins] tool {tool.name!r} names unknown MCP server {tool.server!r}; ignored")
                continue
            if not tool.always_visible:
                deferred.append(f"mcp__{tool.server}__{tool.name}" if tool.server else tool.name)
        return servers, deferred

    async def _load_native_turn_replays(
        self, timeline: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Load native replays for current-narrative turns in the timeline.

        Returns ``{event_id: provider_messages}`` — the assistant/tool
        sequence of each past turn rebuilt from ``events.event_log`` by
        ``fold_event_log_to_messages``. An empty dict means "keep every
        row flattened": the agent's framework does not consume structured
        history (CLI-backed drivers flatten at their doorstep), there are
        no candidate rows, or a lookup failed.

        Scope decisions (Owner 2026-07-29):
        - candidates are the timeline's own assistant rows (the loaded
          window IS the replay window — no separate K knob); rows that
          fell off the window stay covered by the narrative summary;
        - cross-narrative rows (``memory_type == "short_term"``) never
          expand — their tool structure belongs to their own thread and
          would break this thread's role pairing;
        - event_log content replays in full (no capture-side cap yet).
        """
        candidate_ids: List[str] = []
        for msg in timeline:
            if msg.get("role") != "assistant":
                continue
            meta = msg.get("meta_data") or {}
            if meta.get("memory_type") == "short_term":
                continue
            event_id = meta.get("event_id")
            if event_id:
                candidate_ids.append(str(event_id))
        if not candidate_ids:
            return {}

        from narranexus.platform.agent_framework.loop.history_projection import (
            fold_event_log_to_messages,
            framework_supports_native_replay,
        )
        from narranexus.platform.agent_framework.providers.model_identity import (
            resolve_agent_model_identity,
        )

        identity = await resolve_agent_model_identity(self.agent_id, self.db)
        # Registry-derived (the framework's declared ``native_replay``
        # capability), not a builtin name table: a third-party framework whose
        # driver stamps positioned monologue segments gets native replay too.
        if not framework_supports_native_replay(identity.framework):
            return {}

        rows = await self.db.get_by_ids(
            "events", "event_id", sorted(set(candidate_ids))
        )
        replays: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows or []:
            if not row:
                continue
            raw = row.get("event_log")
            try:
                entries = json.loads(raw) if isinstance(raw, str) else (raw or [])
                folded = fold_event_log_to_messages(entries)
            except Exception as e:  # noqa: BLE001 — per-event fail-open
                logger.warning(
                    f"[NativeReplay] fold failed for {row.get('event_id')}: {e}"
                )
                continue
            if folded:
                replays[str(row.get("event_id"))] = folded
        if replays:
            logger.info(
                f"[NativeReplay] expanded {len(replays)} of "
                f"{len(set(candidate_ids))} current-narrative turns"
            )
        return replays

    @staticmethod
    def _format_timeline_tag(meta: Dict[str, Any], user_tz: str = DEFAULT_TIMEZONE) -> str:
        """Render the per-message timeline tag
        `[<time> · <topic> · nar=<narrative_id> · evt=<event_id>]`.

        - time: the message's stored timestamp, converted to the USER's
          timezone and stamped with an explicit UTC offset
          (`2026-07-30 23:00 +08:00`).
        - topic: the resolved narrative alias (name); falls back to the id.
        - nar=<narrative_id>: full id — the agent needs it for switch/view tools.
        - evt=<event_id>: the event that produced this message — the agent can
          pass it to view_event() to fetch that turn's full agent-loop +
          reasoning detail (only the sent message is in the timeline).

        The timezone conversion is NOT cosmetic (2026-08-18). `meta.timestamp`
        is written as `utc_now().isoformat()` by every producer, and this tag
        used to render it with `ts[:16]` — a raw string slice, so the frame
        was silently discarded. The same prompt then carried "Real World
        Information" in the user's local time WITH an offset, and the model
        had no way to know the two disagreed by the user's UTC offset. For a
        +08:00 user every message sent between local 00:00 and 08:00 was
        tagged with the PREVIOUS day, which is enough to make "下周五" /
        "next Friday" resolve to the wrong date and enough to make an
        already-past date read as still upcoming.

        The offset rides on every row rather than being declared once in the
        preamble: it costs ~7 chars a line and it makes each row comparable
        to the ground-truth "now" without the model holding a frame
        declaration in mind across thirty lines.
        """
        meta = meta or {}
        t = format_timestamp_for_agent(meta.get("timestamp"), user_tz)
        nid = meta.get("narrative_id") or "unknown"
        topic = meta.get("narrative_alias") or nid
        eid = meta.get("event_id") or "?"
        return f"[{t} · {topic} · nar={nid} · evt={eid}]"

    @staticmethod
    def _build_recent_actions_section(
        actions: List[Dict[str, Any]], user_tz: str = DEFAULT_TIMEZONE
    ) -> str:
        """Render the recent-background-activity list (Fix #2 P2): one compact
        line per action `- [time] <source>: <job title / summary>  (evt=<id>)`.

        Timestamps use the same renderer as the history timeline (2026-08-18).
        This block sits in the SAME prompt as those rows, twelve lines away in
        this file, and it used to share their raw `[:16]` UTC slice — so the
        two were at least consistently wrong. Framing only the timeline would
        have introduced a divergence that did not exist before: one block in
        the user's local time with an offset, the next in bare UTC, describing
        overlapping events. That is the exact mechanism this change set was
        opened to remove, and it would have been worse than leaving both
        alone, because the framed one looks more authoritative.

        `user_tz` is passed in rather than looked up: the callers already hold
        the turn's resolved timezone, and a second query here would make the
        two blocks capable of disagreeing if the user's row changed mid-turn.
        """
        lines = [RECENT_ACTIONS_HEADER]
        for a in actions:
            t = format_timestamp_for_agent(a.get("timestamp"), user_tz)
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
        from narranexus.platform.context_runtime.prompts import SHORT_TERM_MEMORY_HEADER

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
                    from narranexus.platform.utils import utc_now
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
            from narranexus.platform.channel.message_source_handler import (
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

