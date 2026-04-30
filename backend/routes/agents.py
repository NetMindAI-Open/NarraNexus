"""
@file_name: agents.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent route aggregator

Aggregates domain-specific sub-routers under the /api/agents prefix:
- Awareness (self-awareness)
- Social Network (entity management)
- Chat History (narratives & events)
- Files (workspace file management)
- MCPs (MCP URL management)
- RAG (RAG file management)
"""

from fastapi import APIRouter

from backend.routes.agents_awareness import router as awareness_router
from backend.routes.agents_social_network import router as social_network_router
from backend.routes.agents_chat_history import router as chat_history_router
from backend.routes.agents_files import router as files_router
from backend.routes.agents_attachments import router as attachments_router
from backend.routes.agents_mcps import router as mcps_router
from backend.routes.agents_rag import router as rag_router
from backend.routes.agents_cost import router as cost_router


router = APIRouter()

router.include_router(awareness_router)
router.include_router(social_network_router)
router.include_router(chat_history_router)
router.include_router(files_router)
router.include_router(attachments_router)
router.include_router(mcps_router)
router.include_router(rag_router)
router.include_router(cost_router)
