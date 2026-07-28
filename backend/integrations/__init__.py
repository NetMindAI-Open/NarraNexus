"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Platform-side external-service clients (API app only).

Clients here are consumed exclusively by the backend API app (routes /
lifespan) — never by agent-side processes (agent runtime, module/MCP
servers, workers). Anything an agent-side process must import stays in
src/xyz_agent_context (e.g. integrations/feedback_client.py there).

- `netmind/` — NetMind auth / billing / key provisioning / power-account
  detection / legacy-user identity migration.
- `arena/` — Agent Arena auto-provisioning.

Dependency direction: backend may import xyz_agent_context; the agent
package must NEVER import backend.
"""
