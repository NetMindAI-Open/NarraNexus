"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: External-platform clients the AGENT side itself needs.

Placement rule: src/xyz_agent_context holds only what agent-side
processes (agent runtime, module/MCP servers, workers) must import.
Platform/business clients consumed only by the API app live in
backend/integrations/ instead (netmind/, arena/ moved there 2026-07-24).

- `feedback_client.py` — feedback intake; stays here because the agent's
  `submit_feedback` MCP tool (basic_info_module) calls it in-process.

No re-exports: consumers import the client modules explicitly.
"""
