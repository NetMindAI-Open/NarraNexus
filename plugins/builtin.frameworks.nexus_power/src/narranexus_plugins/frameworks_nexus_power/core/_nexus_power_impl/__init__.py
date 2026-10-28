"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: Private implementation layer (iron rule #23: private
implementations live under _*_impl/ and are never re-exported).

Five cohesive groups — groups import contracts only and NEVER each
other (shared types go up to contracts/):
  harness/   the thinking-mode layer: expression contract, stop,
             steering, hooks
  prompts/   every prompt the framework speaks (except tool
             descriptions, which travel with their specs)
  modeling/  model client, provider profiles, cache policy, argument
             streaming, projection, compaction
  tooling/   dispatcher, policy engine, builtin tools, MCP channel,
             capability expansion, future channels
  session/   turn ledger, event-log writers, error classification
plus loop.py (the phase machine) and event_adapter.py (the only legacy
contract translation point).
"""
