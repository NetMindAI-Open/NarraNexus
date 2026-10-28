"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Agent-loop execution layer: the pluggable driver abstraction
(driver), the Executor-delegating implementation (remote_driver), the
broker client + transport errors, unified output transfer, and the
real-time circuit breaker.

No re-exports: consumers import modules explicitly (the package-level
agent_framework/__init__ keeps the public symbol surface).
"""
