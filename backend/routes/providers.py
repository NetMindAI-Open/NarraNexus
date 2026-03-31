"""
@file_name: providers.py
@author: Bin Liang
@date: 2026-03-23
@description: REST API routes for LLM provider and slot configuration

Provides endpoints for:
- GET    /api/providers              - Get current provider & slot config
- POST   /api/providers              - Add a provider (atomic, 4 card types)
- DELETE /api/providers/{id}         - Remove a provider (and its linked group)
- POST   /api/providers/{id}/test    - Test provider connectivity
- PUT    /api/providers/{id}/models  - Update provider's model list
- PUT    /api/providers/slots/{slot} - Update slot assignment (provider + model)
- GET    /api/providers/slots/validate - Validate all slots are configured
- GET    /api/providers/catalog      - Get known model metadata + suggestions
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.agent_framework.provider_registry import provider_registry
from xyz_agent_context.agent_framework.model_catalog import (
    get_all_known_models,
    get_suggested_models,
    get_model_display_name,
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
    """Default model assignment for a slot (used by Quick Setup)"""
    protocol: str   # "anthropic" | "openai"
    model: str      # e.g. "claude-sonnet-4-6"


class AddProviderRequest(BaseModel):
    """Unified request for adding a provider (all 4 card types)"""
    card_type: str          # "netmind" | "yunwu" | "openrouter" | "claude_oauth" | "anthropic" | "openai"
    name: str = ""          # Display name (for anthropic/openai cards)
    api_key: str = ""       # API key (not needed for claude_oauth)
    base_url: str = ""      # Base URL (defaults to official if empty)
    auth_type: str = "api_key"  # "api_key" | "bearer_token"
    models: list[str] = []  # User-specified model IDs
    # Quick Setup: auto-assign slots after creating providers.
    # Keys: "agent", "embedding", "helper_llm". Values: {protocol, model}.
    # When provided, the backend assigns all specified slots atomically.
    default_slots: dict[str, SlotDefault] | None = None


class SetSlotRequest(BaseModel):
    provider_id: str
    model: str


class UpdateModelsRequest(BaseModel):
    models: list[str]


# =============================================================================
# Helper
# =============================================================================

def _get_or_empty_config() -> LLMConfig:
    """Load existing config or return an empty one."""
    config = provider_registry.load()
    return config if config is not None else LLMConfig()


def _config_to_response(config: LLMConfig) -> dict:
    """Convert LLMConfig to API response dict with masked api_key."""
    # Build providers list with masked api_key
    providers = {}
    for pid, prov in config.providers.items():
        d = prov.model_dump(mode="json")
        # Mask API key for security (show last 4 chars)
        if d["api_key"] and len(d["api_key"]) > 4:
            d["api_key_masked"] = "***" + d["api_key"][-4:]
        else:
            d["api_key_masked"] = "***"
        del d["api_key"]
        providers[pid] = d

    # Build slots with required protocol info
    slots = {}
    for slot_name in SlotName:
        slot_str = slot_name.value
        required = [p.value for p in SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])]
        slot_cfg = config.slots.get(slot_str)
        slots[slot_str] = {
            "required_protocols": required,
            "config": slot_cfg.model_dump() if slot_cfg else None,
        }

    return {
        "version": config.version,
        "providers": providers,
        "slots": slots,
    }


# =============================================================================
# Endpoints
# =============================================================================

@router.get("")
async def get_providers():
    """Get current provider and slot configuration."""
    config = _get_or_empty_config()
    return {"success": True, "data": _config_to_response(config)}


@router.post("")
async def add_provider(req: AddProviderRequest):
    """
    Atomic add of a provider (or two for NetMind).

    Card types:
    - "netmind": one API key → 2 providers (anthropic + openai). Unique.
    - "claude_oauth": OAuth login → 1 anthropic provider. Unique.
    - "anthropic": user-configured anthropic-protocol provider. Can have multiple.
    - "openai": user-configured openai-protocol provider. Can have multiple.
    """
    try:
        logger.info(f"[add_provider] card_type={req.card_type}, name={req.name}")
        config, new_ids = provider_registry.add_provider(
            card_type=req.card_type,
            name=req.name,
            api_key=req.api_key,
            base_url=req.base_url,
            auth_type=req.auth_type,
            models=req.models if req.models else None,
        )
        logger.info(f"[add_provider] Success: created {new_ids}")

        # Auto-assign slots if default_slots provided (Quick Setup flow)
        if req.default_slots:
            new_providers = [config.providers[pid] for pid in new_ids if pid in config.providers]
            for slot_name, slot_def in req.default_slots.items():
                match = next(
                    (p for p in new_providers if p.protocol.value == slot_def.protocol),
                    None,
                )
                if match:
                    config = provider_registry.set_slot(config, slot_name, match.provider_id, slot_def.model)
                    logger.info(f"[add_provider] Auto-assigned slot {slot_name}: provider={match.provider_id}, model={slot_def.model}")
                else:
                    logger.warning(f"[add_provider] No new provider with protocol={slot_def.protocol} for slot {slot_name}")
            provider_registry.save(config)

            # Hot-reload + EverMemOS sync
            from xyz_agent_context.agent_framework.api_config import reload_llm_config
            reload_llm_config()
            try:
                from xyz_agent_context.agent_framework.evermemos_sync import sync_evermemos_from_config
                sync_evermemos_from_config(config)
            except Exception:
                logger.exception("[add_provider] EverMemOS sync failed")

        return {
            "success": True,
            "provider_ids": new_ids,
            "data": _config_to_response(config),
        }
    except ValueError as e:
        logger.warning(f"[add_provider] ValueError: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[add_provider] Unexpected error: {e}", exc_info=True)
        return {"success": False, "detail": str(e)}


@router.delete("/{provider_id}")
async def remove_provider(provider_id: str):
    """Remove a provider and its linked group. Clears affected slots."""
    config = _get_or_empty_config()
    if provider_id not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    config = provider_registry.remove_provider(config, provider_id)
    provider_registry.save(config)

    from xyz_agent_context.agent_framework.api_config import reload_llm_config
    reload_llm_config()

    return {"success": True, "data": _config_to_response(config)}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str):
    """Test connectivity to a provider with a minimal API call."""
    config = _get_or_empty_config()
    if provider_id not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    prov = config.providers[provider_id]
    success, message = await provider_registry.test_provider(prov)
    return {"success": success, "message": message}


@router.put("/{provider_id}/models")
async def update_provider_models(provider_id: str, req: UpdateModelsRequest):
    """Update the available models list for a provider."""
    config = _get_or_empty_config()
    try:
        config = provider_registry.update_provider_models(config, provider_id, req.models)
        provider_registry.save(config)
        return {"success": True, "data": _config_to_response(config)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/slots/{slot_name}")
async def set_slot(slot_name: str, req: SetSlotRequest):
    """Assign a provider + model to a slot."""
    logger.info(f"[set_slot] {slot_name} <- provider={req.provider_id}, model={req.model}")
    try:
        config = _get_or_empty_config()
        config = provider_registry.set_slot(config, slot_name, req.provider_id, req.model)
        provider_registry.save(config)

        # Hot-reload api_config so changes take effect immediately
        from xyz_agent_context.agent_framework.api_config import reload_llm_config
        reload_llm_config()

        # Sync EverMemOS .env when embedding or helper_llm slot changes
        if slot_name in ("embedding", "helper_llm"):
            try:
                from xyz_agent_context.agent_framework.evermemos_sync import sync_evermemos_from_config
                sync_evermemos_from_config(config)
            except Exception:
                logger.exception(
                    f"[set_slot] EverMemOS sync failed after saving slot '{slot_name}'"
                )

        errors = provider_registry.validate(config)
        return {
            "success": True,
            "data": _config_to_response(config),
            "validation_errors": errors,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/slots/validate")
async def validate_slots():
    """Validate that all slots are properly configured."""
    config = _get_or_empty_config()
    errors = provider_registry.validate(config)
    return {
        "success": len(errors) == 0,
        "errors": errors,
        "all_configured": len(errors) == 0,
    }


@router.get("/catalog")
async def get_catalog():
    """
    Get known model metadata and suggested models for each protocol.

    Returns:
    - known_models: metadata dict (dimensions, max_output_tokens) for all known models
    - suggestions: suggested model lists per protocol (for UI pre-population)
    - slot_protocols: which protocols each slot requires
    """
    return {
        "success": True,
        "known_models": get_all_known_models(),
        "suggestions": {
            "anthropic": get_suggested_models("anthropic"),
            "openai": get_suggested_models("openai"),
        },
        "embedding_models": get_known_embedding_models(),
        "official_base_urls": {
            protocol: list(urls)
            for protocol, urls in OFFICIAL_BASE_URLS.items()
        },
        "slot_protocols": {
            slot.value: [p.value for p in protos]
            for slot, protos in SLOT_REQUIRED_PROTOCOLS.items()
        },
    }


# =============================================================================
# Claude Code Auth Status
# =============================================================================

@router.get("/claude-status")
async def get_claude_status():
    """
    Check if Claude Code CLI is logged in by inspecting local credentials.

    Returns login state so the web frontend can show guidance.
    """
    import json as _json
    from pathlib import Path

    claude_dir = Path.home() / ".claude"
    creds_file = claude_dir / ".credentials.json"

    result = {
        "cli_installed": False,
        "logged_in": False,
        "expires_at": None,
    }

    import shutil
    if shutil.which("claude"):
        result["cli_installed"] = True

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
# Embedding Migration Endpoints
# =============================================================================

@router.get("/embeddings/status")
async def get_embedding_status():
    """Get embedding migration status for the current model."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService

    db = await get_db_client()
    service = EmbeddingMigrationService(db)
    status = await service.get_status()
    return {"success": True, "data": status}


@router.post("/embeddings/rebuild")
async def rebuild_embeddings(background_tasks: BackgroundTasks):
    """Trigger embedding rebuild for the current model. Runs in background."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import (
        EmbeddingMigrationService,
        get_migration_progress,
    )

    progress = get_migration_progress()
    if progress.is_running:
        return {
            "success": False,
            "error": "Migration already in progress",
            "data": progress.to_dict(),
        }

    db = await get_db_client()
    service = EmbeddingMigrationService(db)
    background_tasks.add_task(service.rebuild_all)

    return {
        "success": True,
        "message": "Embedding rebuild started",
    }
