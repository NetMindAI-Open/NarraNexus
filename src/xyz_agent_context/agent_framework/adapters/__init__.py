"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Agent-framework adapters (binding rule #9's swap seam): one
subpackage per framework (claude/, codex/), the OpenAI-agents caller,
and the shared PreToolUse policy guard.

No re-exports: consumers import modules explicitly (the package-level
agent_framework/__init__ keeps the public symbol surface).
"""
