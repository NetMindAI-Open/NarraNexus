"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-11-28
@description: FastAPI backend for Agent Context

This package provides:
- WebSocket endpoint for real-time agent runtime streaming
- REST APIs for jobs, inbox, agents, and awareness

The ASGI entrypoint is ``backend.main:app`` (run.sh / Makefile / Tauri /
compose all name it explicitly). This ``__init__`` deliberately imports
nothing: builtin feature plugins expose symbols under ``backend.*``
(``narranexus_plugins.teams.routes:ROUTES``) and importing one of them must not
construct the whole application.
"""
