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
    get_default_models,
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
    return ""


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
        logger.exception(f"[add_provider] Error: {e}", exc_info=True)
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


@router.post("/sync-defaults")
async def sync_default_models(request: Request, user_id: Optional[str] = Query(None)):
    """Backfill the latest default model list from `model_catalog` into every
    one of this user's providers whose (source, protocol) pair has defaults.

    Idempotent — providers already in sync return zero added entries.
    Existing user-curated entries are preserved; only missing defaults are
    appended at the end.
    """
    uid = _get_user_id(request, user_id)
    service = await _get_service()
    config = await service.get_user_config(uid)

    updates: list[dict] = []
    for prov_id, prov in config.providers.items():
        # Only sync preset providers (netmind, yunwu, openrouter, claude_oauth, ...).
        # `source="user"` means a custom provider where the user picked the model
        # list themselves — auto-injecting "official" suggestion lists there
        # would dump OpenAI/Anthropic-only models into proxies that may not
        # support them.
        if prov.source.value == "user":
            continue
        defaults = list(get_default_models(prov.source.value, prov.protocol.value))
        if not defaults:
            continue  # no canonical default list registered for this combo
        existing = list(prov.models or [])
        missing = [m for m in defaults if m not in existing]
        if not missing:
            continue
        new_models = existing + missing
        await service.update_models(uid, prov_id, new_models)
        updates.append({
            "provider_id": prov_id,
            "name": prov.name,
            "source": prov.source.value,
            "protocol": prov.protocol.value,
            "added": missing,
        })

    return {
        "success": True,
        "updates": updates,
        "providers_updated": len(updates),
        "total_models_added": sum(len(u["added"]) for u in updates),
    }


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
    """Check if Claude Code CLI is logged in. Cloud: only staff can use it.

    Response fields:
      - cli_installed: bool — `claude` binary on PATH
      - logged_in:     bool — auth status reports an active token
      - email:         str | None — account email if discoverable
      - expires_at:    str | None — ISO-8601 token expiry if surfaced
    """
    import json as _json
    from pathlib import Path

    result = {"cli_installed": False, "logged_in": False, "email": None, "expires_at": None}

    is_staff = getattr(request.state, 'role', '') == 'staff'
    is_cloud = not os.environ.get("DATABASE_URL", "").startswith("sqlite")
    if is_cloud and not is_staff:
        return {"success": True, "data": {**result, "allowed": False}}

    import shutil
    import subprocess
    if shutil.which("claude"):
        result["cli_installed"] = True

    # Preferred: use `claude auth status` (Claude Code v2.x+).
    # Output schema isn't formally documented and shifts between minor
    # versions, so we probe a few common shapes for email / expiry instead
    # of pinning to one. Anything we can't parse stays None — the UI just
    # won't show those subfields.
    try:
        auth_out = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        if auth_out.returncode == 0 and auth_out.stdout.strip():
            auth_data = _json.loads(auth_out.stdout)
            if isinstance(auth_data, dict):
                if auth_data.get("loggedIn"):
                    result["logged_in"] = True
                # Email — try flat then nested under account/user.
                email = auth_data.get("email")
                if not email:
                    for nested_key in ("account", "user", "profile"):
                        nested = auth_data.get(nested_key)
                        if isinstance(nested, dict) and nested.get("email"):
                            email = nested["email"]
                            break
                if isinstance(email, str) and email:
                    result["email"] = email
                # Expiry — flat fields first, then under token/oauth.
                for key in ("expiresAt", "expires_at", "tokenExpiresAt"):
                    val = auth_data.get(key)
                    if val:
                        result["expires_at"] = str(val)
                        break
                if not result["expires_at"]:
                    for nested_key in ("token", "oauth", "credentials"):
                        nested = auth_data.get(nested_key)
                        if isinstance(nested, dict):
                            for key in ("expiresAt", "expires_at"):
                                if nested.get(key):
                                    result["expires_at"] = str(nested[key])
                                    break
                            if result["expires_at"]:
                                break
    except Exception:
        pass

    # Fallback: check legacy credentials file (Claude Code v1.x).
    # Mostly used to backfill logged_in when `claude auth status` is missing
    # or the user is on an older CLI. Email/expires_at usually aren't in
    # this file, so they may stay None even when we mark logged_in=True.
    if not result["logged_in"]:
        creds_file = Path.home() / ".claude" / ".credentials.json"
        if creds_file.is_file():
            try:
                data = _json.loads(creds_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in ("accessToken", "oauthToken", "claudeAiOauth", "oauth"):
                        if data.get(key):
                            result["logged_in"] = True
                            break
                    # Best-effort metadata extraction from the credentials
                    # file. Different CLI versions stash these in different
                    # places; we walk the common ones.
                    if not result["email"]:
                        for nested_key in ("claudeAiOauth", "oauth", "account", "user"):
                            nested = data.get(nested_key)
                            if isinstance(nested, dict) and nested.get("email"):
                                result["email"] = nested["email"]
                                break
                    if not result["expires_at"]:
                        for nested_key in ("claudeAiOauth", "oauth"):
                            nested = data.get(nested_key)
                            if isinstance(nested, dict):
                                for key in ("expiresAt", "expires_at"):
                                    if nested.get(key):
                                        result["expires_at"] = str(nested[key])
                                        break
                                if result["expires_at"]:
                                    break
            except Exception:
                pass

    return {"success": True, "data": result}


# =============================================================================
# Embedding Migration
# =============================================================================

@router.get("/embeddings/status")
async def get_embedding_status(user_id: str = Query(..., description="User ID to scope the status")):
    """
    Per-user embedding migration status.

    Returns counts of entities (narrative / event / job / entity) that
    belong to `user_id` and whether each has an embedding for that user's
    active model. Concurrent status checks by different users do not
    interfere with each other.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    db = await get_db_client()
    service = EmbeddingMigrationService(db, user_id=user_id)
    status = await service.get_status()
    return {"success": True, "data": status}


@router.post("/embeddings/rebuild")
async def rebuild_embeddings(
    background_tasks: BackgroundTasks,
    user_id: str = Query(..., description="User ID whose entities to rebuild"),
):
    """
    Kick off a background rebuild of this user's missing embeddings.

    Each user has an independent `MigrationProgress`; starting a rebuild
    for user A does not block user B. If the same user already has a
    rebuild running, the request is a no-op.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import (
        EmbeddingMigrationService,
        get_migration_progress,
    )
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    progress = get_migration_progress(user_id)
    if progress.is_running:
        return {
            "success": False,
            "error": "Migration already in progress",
            "data": progress.to_dict(),
        }
    db = await get_db_client()
    service = EmbeddingMigrationService(db, user_id=user_id)
    background_tasks.add_task(service.rebuild_all)
    return {"success": True, "message": "Embedding rebuild started"}
