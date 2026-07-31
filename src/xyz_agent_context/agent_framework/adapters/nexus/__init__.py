"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: Nexus agent-loop adapter — the AgentLoopDriver seat for
the home-grown ``nexus_power`` framework (nexus_agent). Structurally the
twin of adapters/claude: contract translation only; the implementation
lives in ``agent_framework/nexus_power/``.

No re-exports: consumers import modules explicitly (the package-level
agent_framework/__init__ keeps the public symbol surface).
"""
