"""
@file_name: providers.py
@author: NexusAgent
@date: 2026-04-08
@description: REST API routes for LLM provider and slot configuration

Per-user provider isolation: each user has their own providers and slots
stored in user_providers and user_slots tables. Works identically on
both SQLite (local) and MySQL (cloud).
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.agent_framework.model_catalog import (
    get_all_known_models,
    get_suggested_models,
    get_known_embedding_models,
    OFFICIAL_BASE_URLS,
)
from xyz_agent_context.schema.provider_schema import (
    LLMConfig,
    SlotName,
    SLOT_REQUIRED_PROTOCOLS,
)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class SlotDefault(BaseModel):
    protocol: str
    model: str


class AddProviderRequest(BaseModel):
    card_type: str
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    auth_type: str = "api_key"
    models: list[str] = []
    default_slots: dict[str, SlotDefault] | None = None


class SetSlotRequest(BaseModel):
    provider_id: str
    model: str


class UpdateModelsRequest(BaseModel):
    models: list[str]


# =============================================================================
# Helpers
# =============================================================================

def _get_user_id(request: Request, user_id: Optional[str] = None) -> str:
    """Extract user_id from JWT (cloud) or query param (local)."""
    if hasattr(request.state, 'user_id') and request.state.user_id:
        return request.state.user_id
    if user_id:
        return user_id
    return "local_user"


async def _get_service():
    """Get UserProviderService with DB client."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.agent_framework.user_provider_service import UserProviderService
    db = await get_db_client()
    return UserProviderService(db)


def _config_to_response(config: LLMConfig) -> dict:
    """Convert LLMConfig to API response dict with masked api_key."""
    providers = {}
    for pid, prov in config.providers.items():
        d = prov.model_dump(mode="json")
        if d["api_key"] and len(d["api_key"]) > 4:
            d["api_key_masked"] = "***" + d["api_key"][-4:]
        else:
            d["api_key_masked"] = "***"
        del d["api_key"]
        providers[pid] = d

    slots = {}
    for slot_name in SlotName:
        slot_str = slot_name.value
        required = [p.value for p in SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])]
        slot_cfg = config.slots.get(slot_str)
        slots[slot_str] = {
            "required_protocols": required,
            "config": slot_cfg.model_dump() if slot_cfg else None,
        }

    return {"version": config.version, "providers": providers, "slots": slots}


# =============================================================================
# Endpoints
# =============================================================================

@router.get("")
async def get_providers(request: Request, user_id: Optional[str] = Query(None)):
    uid = _get_user_id(request, user_id)
    service = await _get_service()
    config = await service.get_user_config(uid)
    return {"success": True, "data": _config_to_response(config)}


@router.post("")
async def add_provider(req: AddProviderRequest, request: Request, user_id: Optional[str] = Query(None)):
    uid = _get_user_id(request, user_id)
    try:
        service = await _get_service()
        config, new_ids = await service.add_provider(
            user_id=uid,
            card_type=req.card_type,
            name=req.name,
            api_key=req.api_key,
            base_url=req.base_url,
            auth_type=req.auth_type,
            models=req.models if req.models else None,
        )

        if req.default_slots:
            for slot_name, slot_def in req.default_slots.items():
                match_pid = None
                for pid in new_ids:
                    prov = config.providers.get(pid)
                    if prov and prov.protocol.value == slot_def.protocol:
                        match_pid = pid
                        break
                if match_pid:
                    config = await service.set_slot(uid, slot_name, match_pid, slot_def.model)

        # Hot-reload for current process (local mode)
        try:
            from xyz_agent_context.agent_framework.api_config import get_user_llm_configs, set_user_config
            claude, openai_cfg, emb = await get_user_llm_configs(uid)
            set_user_config(claude, openai_cfg, emb)
        except Exception:
            pass

        return {"success": True, "provider_ids": new_ids, "data": _config_to_response(config)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[add_provider] Error: {e}", exc_info=True)
        return {"success": False, "detail": str(e)}


@router.delete("/{provider_id}")
async def remove_provider(provider_id: str, request: Request, user_id: Optional[str] = Query(None)):
    uid = _get_user_id(request, user_id)
    service = await _get_service()
    try:
        config = await service.remove_provider(uid, provider_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": _config_to_response(config)}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, request: Request, user_id: Optional[str] = Query(None)):
    uid = _get_user_id(request, user_id)
    service = await _get_service()
    success, message = await service.test_provider(uid, provider_id)
    return {"success": success, "message": message}


@router.put("/{provider_id}/models")
async def update_provider_models(provider_id: str, req: UpdateModelsRequest, request: Request, user_id: Optional[str] = Query(None)):
    uid = _get_user_id(request, user_id)
    service = await _get_service()
    try:
        config = await service.update_models(uid, provider_id, req.models)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": _config_to_response(config)}


@router.put("/slots/{slot_name}")
async def set_slot(slot_name: str, req: SetSlotRequest, request: Request, user_id: Optional[str] = Query(None)):
    uid = _get_user_id(request, user_id)
    try:
        service = await _get_service()
        config = await service.set_slot(uid, slot_name, req.provider_id, req.model)

        errors = []
        for s in SlotName:
            if s.value not in config.slots:
                errors.append(f"Slot '{s.value}' not configured")

        # Hot-reload for current process
        try:
            from xyz_agent_context.agent_framework.api_config import get_user_llm_configs, set_user_config
            claude, openai_cfg, emb = await get_user_llm_configs(uid)
            set_user_config(claude, openai_cfg, emb)
        except Exception:
            pass

        return {"success": True, "data": _config_to_response(config), "validation_errors": errors}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/slots/validate")
async def validate_slots(request: Request, user_id: Optional[str] = Query(None)):
    uid = _get_user_id(request, user_id)
    service = await _get_service()
    errors = await service.validate_slots(uid)
    return {"success": True, "errors": errors, "all_configured": len(errors) == 0}


@router.get("/catalog")
async def get_catalog():
    return {
        "success": True,
        "known_models": get_all_known_models(),
        "suggestions": {
            "anthropic": get_suggested_models("anthropic"),
            "openai": get_suggested_models("openai"),
        },
        "embedding_models": get_known_embedding_models(),
        "official_base_urls": {protocol: list(urls) for protocol, urls in OFFICIAL_BASE_URLS.items()},
        "slot_protocols": {slot.value: [p.value for p in protos] for slot, protos in SLOT_REQUIRED_PROTOCOLS.items()},
    }


# =============================================================================
# Claude Code Auth Status
# =============================================================================

@router.get("/claude-status")
async def get_claude_status(request: Request):
    """Check if Claude Code CLI is logged in. Cloud: only staff can use it."""
    import json as _json
    from pathlib import Path

    result = {"cli_installed": False, "logged_in": False, "expires_at": None}

    is_staff = getattr(request.state, 'role', '') == 'staff'
    is_cloud = not os.environ.get("DATABASE_URL", "").startswith("sqlite")
    if is_cloud and not is_staff:
        return {"success": True, "data": {**result, "allowed": False}}

    import shutil
    if shutil.which("claude"):
        result["cli_installed"] = True

    creds_file = Path.home() / ".claude" / ".credentials.json"
    if creds_file.is_file():
        try:
            data = _json.loads(creds_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("accessToken", "oauthToken", "claudeAiOauth"):
                    if data.get(key):
                        result["logged_in"] = True
                        result["expires_at"] = data.get("expiresAt")
                        break
                if not result["logged_in"] and data.get("oauth"):
                    result["logged_in"] = True
                    result["expires_at"] = data["oauth"].get("expiresAt")
        except Exception:
            pass

    return {"success": True, "data": result}


# =============================================================================
# Embedding Migration
# =============================================================================

@router.get("/embeddings/status")
async def get_embedding_status():
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService
    db = await get_db_client()
    service = EmbeddingMigrationService(db)
    status = await service.get_status()
    return {"success": True, "data": status}


@router.post("/embeddings/rebuild")
async def rebuild_embeddings(background_tasks: BackgroundTasks):
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService, get_migration_progress
    progress = get_migration_progress()
    if progress.is_running:
        return {"success": False, "error": "Migration already in progress", "data": progress.to_dict()}
    db = await get_db_client()
    service = EmbeddingMigrationService(db)
    background_tasks.add_task(service.rebuild_all)
    return {"success": True, "message": "Embedding rebuild started"}
