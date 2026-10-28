"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-11-28
@description: API routes package
"""

from backend.routes.websocket import router as websocket_router
from backend.routes.agents.core import router as agents_router
from backend.routes.providers import router as providers_router

# jobs / skills routers are backend.routes contributions of builtin.job /
# builtin.skills (batch 3c.5) and are not re-exported here.
__all__ = [
    "websocket_router",
    "agents_router",
    "providers_router",
]
