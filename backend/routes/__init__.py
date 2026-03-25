"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-11-28
@description: API routes package
"""

from backend.routes.websocket import router as websocket_router
from backend.routes.agents import router as agents_router
from backend.routes.jobs import router as jobs_router
from backend.routes.agent_inbox import router as agent_inbox_router
from backend.routes.skills import router as skills_router
from backend.routes.providers import router as providers_router

__all__ = [
    "websocket_router",
    "agents_router",
    "jobs_router",
    "agent_inbox_router",
    "skills_router",
    "providers_router",
]
