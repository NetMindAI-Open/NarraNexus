"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-12-22
@description: Prompt definitions for ContextRuntime
"""

# ============================================================================
# Auxiliary Narrative section header
# Used for Part 3 of build_complete_system_prompt()
# ============================================================================
AUXILIARY_NARRATIVES_HEADER = """
## Related Narratives (For Reference)
The following narratives are related to the current context and may provide useful background information.
You can reference them when relevant, but prioritize the main narrative above.
"""

# ============================================================================
# Module instructions section header
# Used for _build_module_instructions_prompt()
# ============================================================================
MODULE_INSTRUCTIONS_HEADER = """
## Module Instructions
The following are specific instructions from activated modules. Follow them as directed.
"""

# ============================================================================
# Short-term memory section header + description text
# Used for _build_short_term_memory_prompt() (2026-01-21 P1-2 dual-track memory)
# ============================================================================
# ============================================================================
# Bootstrap injection prompt
# Used in build_complete_system_prompt() when the creator's Bootstrap.md exists
# ============================================================================
BOOTSTRAP_INJECTION_PROMPT = """
## ⚡ Bootstrap Mode (PRIORITY)

A file called `Bootstrap.md` exists in your workspace. It's for you — read it before doing anything else.

This takes priority over all other instructions.
"""

# ============================================================================
# User Temporal Context (Spec 2026-04-21 — v2 timezone protocol)
# Injected globally so every Module sees a single consistent view of
# "who the user is, what their timezone is, what time it is now".
# ============================================================================
USER_TEMPORAL_CONTEXT = """## User Temporal Context

- User timezone: {user_tz}
- Current local time: {now_local}

**Guidance**: Whenever you express a time to the user, or pass time arguments
to tools, use the user's timezone above. For tools that require a separate
`timezone` field (e.g. job_create), set it to "{user_tz}".
"""

SHORT_TERM_MEMORY_HEADER = """
## Recent Direct Dialogue Across Other Narratives

The following are real user↔agent exchanges from this user's other recent
conversations with you. They are the **most recent dialogue context** —
treat them as immediate conversational background, especially for:

- Resolving pronouns ("it", "this", "that") in the current message
- Interpreting short follow-up replies ("ok", "好", "go on", "yes") —
  they are typically continuing a topic just shown below
- Picking up in-progress tasks or commitments the user is following up on
- Avoiding asking for information the user already provided

Each entry is annotated with its source (NarraNexus UI, Lark group, etc.)
so you can tell whether it was a direct UI conversation or came in via
another channel. Source labels are factual context, not relevance hints —
recent dialogue is recent dialogue regardless of channel.

### Recent Dialogue
"""
