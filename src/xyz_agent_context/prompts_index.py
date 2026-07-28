"""
@file_name: prompts_index.py
@author: NetMind.AI
@date: 2025-12-22
@description: Global Prompt Index

Centralized import of all Prompt constants in the project for unified lookup, IDE navigation, and consistency management.
Each import block corresponds to a module's prompts.py file.
"""

# =============================================================================
# 1. ContextRuntime — Context Building Engine Prompts
# File: context_runtime/prompts.py
# =============================================================================
from xyz_agent_context.context_runtime.prompts import (
    AUXILIARY_NARRATIVES_HEADER,       # Auxiliary Narrative section header
    MODULE_INSTRUCTIONS_HEADER,        # Module instructions section header
    SHORT_TERM_MEMORY_HEADER,          # Short-term memory section header + description text
    BOOTSTRAP_INJECTION_PROMPT,        # Bootstrap injection wrapper (first-run setup)
)

# =============================================================================
# 2. Narrative Prompt Builder — Narrative Main Prompt Construction
# File: narrative/_narrative_impl/prompts.py
# =============================================================================
from xyz_agent_context.narrative._narrative_impl.prompts import (
    NARRATIVE_TYPE_CHAT_PROMPT,        # CHAT type description
    NARRATIVE_TYPE_TASK_PROMPT,        # TASK type description
    NARRATIVE_TYPE_GENERAL_PROMPT,     # GENERAL type description
    ACTOR_TYPE_USER_DESCRIPTION,       # USER actor description
    ACTOR_TYPE_AGENT_DESCRIPTION,      # AGENT actor description
    ACTOR_TYPE_PARTICIPANT_DESCRIPTION, # PARTICIPANT actor description
    ACTOR_TYPE_SYSTEM_DESCRIPTION,     # SYSTEM actor description
    NARRATIVE_MAIN_PROMPT_TEMPLATE,    # Narrative main system prompt template
    CONTINUITY_DETECTION_INSTRUCTIONS,  # Narrative attribution/matching prompt
    NARRATIVE_SINGLE_MATCH_INSTRUCTIONS,  # Single-candidate Narrative matching prompt
    NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS,  # Unified matching prompt (with PARTICIPANT)
    NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,  # Unified matching prompt (without PARTICIPANT)
    NARRATIVE_UPDATE_INSTRUCTIONS,     # Narrative metadata incremental update prompt
)

# =============================================================================
# 3. Event Prompt Builder — Event History Prompt Construction
# File: narrative/_event_impl/prompts.py
# =============================================================================
from xyz_agent_context.narrative._event_impl.prompts import (
    EVENT_HISTORY_HEAD_PROMPT,         # Event section header description
    EVENT_HISTORY_TAIL_PROMPT,         # Event section footer requirements
    EVENT_DETAIL_PROMPT_TEMPLATE,      # Single Event detail template
)

# =============================================================================
# 4. Module Instance Decision — Module Instance Decision Prompt
# File: module/_module_impl/prompts.py
# =============================================================================
from xyz_agent_context.module._module_impl.prompts import (
    INSTANCE_DECISION_PROMPT_TEMPLATE,  # Module instance decision main prompt (the largest prompt)
)

# =============================================================================
# 5. JobModule (JobTrigger) — Job Execution Prompt
# File: module/job_module/prompts.py
# =============================================================================
from xyz_agent_context.module.job_module.prompts import (
    JOB_TASK_INFO_TEMPLATE,            # Task information section
    JOB_ENTITIES_SECTION_TEMPLATE,     # Related entities section
    JOB_PROGRESS_SECTION_TEMPLATE,     # Current progress section
    JOB_DEPENDENCIES_SECTION_TEMPLATE, # Prerequisites/dependencies section
    JOB_EXECUTION_PROMPT_TEMPLATE,     # Job execution main prompt
)

# =============================================================================
# 6. ChatModule — Chat Module Prompt
# File: module/chat_module/prompts.py
# =============================================================================
from xyz_agent_context.module.chat_module.prompts import (
    CHAT_MODULE_INSTRUCTIONS,          # ChatModule system instructions (thinking vs speaking)
)

# =============================================================================
# 7. AwarenessModule — Awareness Perception Module Prompt
# File: module/awareness_module/prompts.py
# =============================================================================
from xyz_agent_context.module.awareness_module.prompts import (
    AWARENESS_MODULE_INSTRUCTIONS,     # Awareness system instructions template
)

# =============================================================================
# 8. BasicInfoModule — Basic Information Module Prompt
# File: module/basic_info_module/prompts.py
# =============================================================================
from xyz_agent_context.module.basic_info_module.prompts import (
    BASIC_INFO_MODULE_INSTRUCTIONS,    # BasicInfo system instructions template
)

# =============================================================================
# 9. SocialNetworkModule — Social Network Module Prompt
# File: module/social_network_module/prompts.py
# =============================================================================
from xyz_agent_context.module.social_network_module.prompts import (
    SOCIAL_NETWORK_MODULE_INSTRUCTIONS,  # SocialNetwork system instructions template
    ENTITY_SUMMARY_INSTRUCTIONS,         # Entity information summary LLM instructions
    DESCRIPTION_COMPRESSION_INSTRUCTIONS, # Profile compression LLM instructions
    PERSONA_INFERENCE_INSTRUCTIONS,      # Persona inference LLM instructions
)

# =============================================================================
# 10. Agent Framework (Claude Agent SDK) — Agent Framework Prompt
# File: agent_framework/adapters/claude/prompts.py
# =============================================================================
from xyz_agent_context.agent_framework.adapters.claude.prompts import (
    CHAT_HISTORY_HEADER,               # Chat history section header
    CHAT_HISTORY_TRUNCATED_HEADER,     # Truncated chat history section header
    CHAT_HISTORY_END_INSTRUCTION,      # Chat history section footer instruction
    SYSTEM_PROMPT_TRUNCATION_WARNING,  # System prompt truncation warning
)

# =============================================================================
# 11. Bootstrap — First-Run Setup Template
# File: bootstrap/template.py
# =============================================================================
from xyz_agent_context.bootstrap.template import (
    BOOTSTRAP_MD_TEMPLATE,             # Bootstrap.md content written at agent creation
)
