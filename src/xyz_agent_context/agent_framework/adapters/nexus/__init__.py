"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: Nexus agent-loop adapter: 自研 nexus_loop 框架的 driver 薄接口
(nexus_agent)。与 adapters/claude 同构——adapter 只做 AgentLoopDriver
契约翻译, 全部实现在 agent_framework/nexus_loop/。

No re-exports: consumers import modules explicitly (the package-level
agent_framework/__init__ keeps the public symbol surface).
"""
