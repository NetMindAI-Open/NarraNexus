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
from narranexus.platform.context_runtime.prompts import (
    AUXILIARY_NARRATIVES_HEADER,       # Auxiliary Narrative section header
    MODULE_INSTRUCTIONS_HEADER,        # Module instructions section header
    SHORT_TERM_MEMORY_HEADER,          # Short-term memory section header + description text
    BOOTSTRAP_INJECTION_PROMPT,        # Bootstrap injection wrapper (first-run setup)
)

# =============================================================================
# 2. Narrative Prompt Builder — Narrative Main Prompt Construction
# File: narrative/_narrative_impl/prompts.py
# =============================================================================
from narranexus.platform.narrative._narrative_impl.prompts import (
    NARRATIVE_TYPE_CHAT_PROMPT,        # CHAT type description
    NARRATIVE_TYPE_TASK_PROMPT,        # TASK type description
    NARRATIVE_TYPE_GENERAL_PROMPT,     # GENERAL type description
    ACTOR_TYPE_USER_DESCRIPTION,       # USER actor description
    ACTOR_TYPE_AGENT_DESCRIPTION,      # AGENT actor description
    ACTOR_TYPE_PARTICIPANT_DESCRIPTION, # PARTICIPANT actor description
    ACTOR_TYPE_SYSTEM_DESCRIPTION,     # SYSTEM actor description
    NARRATIVE_MAIN_PROMPT_TEMPLATE,    # Narrative main system prompt template
    CONTINUITY_DETECTION_INSTRUCTIONS,  # Narrative attribution/matching prompt
    NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS,  # Unified matching prompt (with PARTICIPANT)
    NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,  # Unified matching prompt (without PARTICIPANT)
    NARRATIVE_UPDATE_INSTRUCTIONS,     # Narrative metadata incremental update prompt
)

# =============================================================================
# 3. Event Prompt Builder — Event History Prompt Construction
# File: narrative/_event_impl/prompts.py
# =============================================================================
from narranexus.platform.narrative._event_impl.prompts import (
    EVENT_HISTORY_HEAD_PROMPT,         # Event section header description
    EVENT_HISTORY_TAIL_PROMPT,         # Event section footer requirements
    EVENT_DETAIL_PROMPT_TEMPLATE,      # Single Event detail template
)

# =============================================================================
# 4. Module Instance Decision — Module Instance Decision Prompt
# File: module/_module_impl/prompts.py
# =============================================================================
from narranexus.platform.module_system._module_impl.prompts import (
    INSTANCE_DECISION_PROMPT_TEMPLATE,  # Module instance decision main prompt (the largest prompt)
)

# =============================================================================
# Builtin module prompts (job / chat / awareness / basic_info / social_network)
# are NOT indexed here since batch 3c.5: each builtin plugin owns its
# ``<module>/prompts.py`` and the platform imports no builtin. Navigate to the
# module package directly.
# =============================================================================
# =============================================================================
# 10. Agent Framework prompts (Claude Agent SDK) — NOT indexed here since
# batch 6b: builtin.frameworks.claude_code owns
# ``narranexus_plugins/frameworks_claude_code/prompts.py`` and the platform
# imports no plugin. Navigate to the plugin package directly.
# =============================================================================

# =============================================================================
# 11. Bootstrap — First-Run Setup Template
# File: bootstrap/template.py
# =============================================================================
from narranexus.platform.bootstrap.template import (
    BOOTSTRAP_MD_TEMPLATE,             # Bootstrap.md content written at agent creation
)
