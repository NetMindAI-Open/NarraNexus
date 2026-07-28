"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-11-15
@description: Prompt definitions for Agent Framework (Claude Agent SDK)
"""

# ============================================================================
# Chat History Header
# Separator added when building the system prompt in agent_loop() for history records
# ============================================================================
CHAT_HISTORY_HEADER = "\n\n=== Chat History ===\n"

# ============================================================================
# Truncated Chat History Header
# Separator used in agent_loop() when the history is too long and gets truncated
# ============================================================================
CHAT_HISTORY_TRUNCATED_HEADER = "\n\n=== Chat History (truncated) ===\n"

# ============================================================================
# Chat History End Instruction
# Instruction text appended after the chat history in agent_loop()
# ============================================================================
CHAT_HISTORY_END_INSTRUCTION = "\n=== Chat History End ===\n These are the chat history between you and the user. This time please make the response by user input in this turn."

# ============================================================================
# System Prompt Truncation Warning
# Truncation notice appended when the system prompt exceeds the length limit in agent_loop()
# ============================================================================
SYSTEM_PROMPT_TRUNCATION_WARNING = "\n\n[...truncated due to length limit...]"
