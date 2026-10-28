"""
@file_name: core.py
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

from backend.routes.agents.files import router as files_router
from backend.routes.agents.attachments import router as attachments_router
from backend.routes.agents.mcps import router as mcps_router
from backend.routes.agents.cost import router as cost_router
from backend.routes.agents.bus_failures import router as bus_failures_router
from backend.routes.agents.capabilities import router as capabilities_router
from backend.routes.agents.llm_config import router as llm_config_router
from backend.routes.agents.circuit_breaker import router as circuit_breaker_router
from backend.routes.agents.general_memory import router as general_memory_router
from backend.routes.agents.channel_credentials import router as channel_credentials_router


router = APIRouter()

# awareness / social_network / chat_history / narrative / jobs / profile are
# backend.routes contributions of their builtin plugins (batch 3c.4) — see
# backend/plugins_host.mount_plugin_routes; they are not included here.
router.include_router(files_router)
router.include_router(attachments_router)
router.include_router(mcps_router)
router.include_router(cost_router)
router.include_router(bus_failures_router)
router.include_router(llm_config_router)
router.include_router(capabilities_router)
router.include_router(general_memory_router)
router.include_router(circuit_breaker_router)
router.include_router(channel_credentials_router)
