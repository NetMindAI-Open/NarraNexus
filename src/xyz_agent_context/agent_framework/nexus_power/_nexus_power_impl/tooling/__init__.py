"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: Tooling group — every agent capability behind one
ecosystem (Owner decision: nothing missing, everything generic and
standard). All capabilities are ToolChannel members sharing one
dispatcher, one policy engine, one event log:

  builtin/              thinking primitives (files / shell / web seat /
                        context self-service; expression is NOT basic —
                        voices are platform-granted via MCP)
  mcp_channel.py        MCP client (platform capabilities + any server)
  expansion.py          Expandable / CapabilityExpander (framework-
                        neutral dynamic loading; platforms translate
                        their concepts into Expandables)
  dispatcher.py         the single dispatcher (generation cache, marker
                        short-circuit, policy checkpoint, tool search)
  policy.py             fail-closed PolicyEngine + v1 layers
  skills_channel.py     P3 seat (agentskills.io, progressive disclosure)
  plugins.py            P3 seat (in-process extension registration)
  scheduling_channel.py P3/P4 seat (update_plan / sleep)
  subagent_channel.py   P4 seat (capability intersection, lineage,
                        posthumous results)

Adding a capability = writing a channel and registering it; the loop
and dispatcher never change for it.
"""
