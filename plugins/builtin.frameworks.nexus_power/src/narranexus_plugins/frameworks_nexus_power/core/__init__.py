"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: NexusPowerLoop — the home-grown, standalone agent-loop
framework.

Identity (the divergence from every other harness): assistant text is
inner monologue; acting on the world — including speaking to the user —
happens only through tool calls; a turn ends when the model stops
acting, not when it stops talking.

Public surface: ``assembly`` (TurnRequest / run_turn_events) plus the
``contracts`` types; the consumer is ``adapters/nexus`` (the
AgentLoopDriver seat) and the standalone ``runner`` process host. The
framework is platform-neutral: it knows Expandables and TurnOptions,
never "modules" or "users" — platforms translate themselves into data.

No re-exports (private implementation stays private).
"""
