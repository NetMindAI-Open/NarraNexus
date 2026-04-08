"""
@file_name: user_provider_service.py
@author: NexusAgent
@date: 2026-04-08
@description: Per-user LLM provider configuration service

Manages provider and slot configurations per user in the database.
Replaces the global llm_config.json for multi-tenant cloud deployments.

In local mode (SQLite), falls back to llm_config.json for backward compatibility.
In cloud mode (MySQL), all provider configs are stored in user_providers and user_slots tables.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
    SlotName,
    SLOT_REQUIRED_PROTOCOLS,
)


def _is_cloud_mode() -> bool:
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite"):
        return False
    # Check settings for non-sqlite DB
    try:
        from xyz_agent_context.settings import settings
        url = getattr(settings, 'database_url', None) or ''
        return not url.startswith("sqlite")
    except Exception:
        return True  # Default to cloud if settings unavailable


def _generate_provider_id() -> str:
    return f"prov_{uuid4().hex[:8]}"


def _generate_group_id() -> str:
    return f"grp_{uuid4().hex[:8]}"


class UserProviderService:
    """
    Per-user provider management via database.

    Methods mirror provider_registry's API but are scoped to a user_id.
    """

    def __init__(self, db_client):
        self.db = db_client

    # =========================================================================
    # Read
    # =========================================================================

    async def get_user_config(self, user_id: str) -> LLMConfig:
        """Load a user's provider config from DB, returning an LLMConfig object."""
        # Get providers
        rows = await self.db.get("user_providers", filters={"user_id": user_id})
        providers = {}
        for row in rows:
            prov = ProviderConfig(
                provider_id=row["provider_id"],
                name=row["name"],
                source=row["source"],
                protocol=row["protocol"],
                auth_type=row.get("auth_type", "api_key"),
                api_key=row.get("api_key", ""),
                base_url=row.get("base_url", ""),
                models=json.loads(row["models"]) if row.get("models") else [],
                linked_group=row.get("linked_group", ""),
                is_active=bool(row.get("is_active", 1)),
            )
            providers[prov.provider_id] = prov

        # Get slots
        slot_rows = await self.db.get("user_slots", filters={"user_id": user_id})
        slots = {}
        for row in slot_rows:
            slots[row["slot_name"]] = SlotConfig(
                provider_id=row["provider_id"],
                model=row["model"],
            )

        return LLMConfig(providers=providers, slots=slots)

    # =========================================================================
    # Add Provider
    # =========================================================================

    async def add_provider(
        self,
        user_id: str,
        card_type: str,
        name: str = "",
        api_key: str = "",
        base_url: str = "",
        auth_type: str = "api_key",
        models: Optional[List[str]] = None,
    ) -> tuple[LLMConfig, list[str]]:
        """Add a provider for a user. Returns (updated_config, new_provider_ids)."""

        new_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        if card_type in ("netmind", "yunwu", "openrouter"):
            # Check uniqueness
            existing = await self.db.get("user_providers", filters={"user_id": user_id, "source": card_type})
            if existing:
                raise ValueError(f"A {card_type} provider already exists for this user")

            group_id = _generate_group_id()
            configs = _build_dual_providers(card_type, api_key, group_id, models)
            for cfg in configs:
                await self._insert_provider(user_id, cfg, now)
                new_ids.append(cfg["provider_id"])

        elif card_type == "claude_oauth":
            existing = await self.db.get("user_providers", filters={"user_id": user_id, "source": "claude_oauth"})
            if existing:
                raise ValueError("Claude OAuth provider already exists for this user")

            pid = _generate_provider_id()
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": "Claude Code (OAuth)",
                "source": "claude_oauth",
                "protocol": "anthropic",
                "auth_type": "oauth",
                "api_key": "",
                "base_url": "",
                "models": json.dumps(["claude-opus-4-6", "claude-sonnet-4-6"]),
            }, now)
            new_ids.append(pid)

        elif card_type in ("anthropic", "openai"):
            pid = _generate_provider_id()
            display_name = name or f"Custom {card_type.title()}"
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": display_name,
                "source": "user",
                "protocol": card_type,
                "auth_type": auth_type,
                "api_key": api_key,
                "base_url": base_url,
                "models": json.dumps(models or []),
            }, now)
            new_ids.append(pid)
        else:
            raise ValueError(f"Unknown card_type: {card_type}")

        config = await self.get_user_config(user_id)
        return config, new_ids

    async def _insert_provider(self, user_id: str, data: dict, now: str):
        await self.db.insert("user_providers", {
            "user_id": user_id,
            "provider_id": data["provider_id"],
            "name": data["name"],
            "source": data["source"],
            "protocol": data["protocol"],
            "auth_type": data.get("auth_type", "api_key"),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", ""),
            "models": data.get("models", "[]"),
            "linked_group": data.get("linked_group", ""),
            "is_active": 1,
            "created_at": now,
            "updated_at": now,
        })

    # =========================================================================
    # Remove Provider
    # =========================================================================

    async def remove_provider(self, user_id: str, provider_id: str) -> LLMConfig:
        """Remove a provider (and its linked group). Clears affected slots."""
        row = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not row:
            raise ValueError(f"Provider {provider_id} not found")

        # If linked group, delete all in group
        linked_group = row.get("linked_group", "")
        if linked_group:
            group_rows = await self.db.get("user_providers", {"user_id": user_id, "linked_group": linked_group})
            for r in group_rows:
                await self.db.delete("user_providers", {"user_id": user_id, "provider_id": r["provider_id"]})
                # Clear any slots using this provider
                await self.db.delete("user_slots", {"user_id": user_id, "provider_id": r["provider_id"]})
        else:
            await self.db.delete("user_providers", {"user_id": user_id, "provider_id": provider_id})
            await self.db.delete("user_slots", {"user_id": user_id, "provider_id": provider_id})

        return await self.get_user_config(user_id)

    # =========================================================================
    # Slots
    # =========================================================================

    async def set_slot(self, user_id: str, slot_name: str, provider_id: str, model: str) -> LLMConfig:
        """Assign a provider + model to a slot for a user."""
        # Validate slot name
        if slot_name not in [s.value for s in SlotName]:
            raise ValueError(f"Invalid slot: {slot_name}")

        # Validate provider exists for this user
        prov = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not prov:
            raise ValueError(f"Provider {provider_id} not found for user {user_id}")

        # Validate protocol
        required = SLOT_REQUIRED_PROTOCOLS.get(slot_name, [])
        if required and prov["protocol"] not in [p.value for p in required]:
            raise ValueError(f"Slot '{slot_name}' requires protocol {[p.value for p in required]}, got '{prov['protocol']}'")

        # Upsert slot
        existing = await self.db.get_one("user_slots", {"user_id": user_id, "slot_name": slot_name})
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            await self.db.update("user_slots",
                {"user_id": user_id, "slot_name": slot_name},
                {"provider_id": provider_id, "model": model, "updated_at": now}
            )
        else:
            await self.db.insert("user_slots", {
                "user_id": user_id,
                "slot_name": slot_name,
                "provider_id": provider_id,
                "model": model,
                "updated_at": now,
            })

        return await self.get_user_config(user_id)

    async def validate_slots(self, user_id: str) -> list[str]:
        """Validate all slots are configured."""
        config = await self.get_user_config(user_id)
        errors = []
        for slot in SlotName:
            if slot.value not in config.slots:
                errors.append(f"Slot '{slot.value}' not configured")
        return errors

    # =========================================================================
    # Update Models
    # =========================================================================

    async def update_models(self, user_id: str, provider_id: str, models: list[str]) -> LLMConfig:
        """Update available models for a provider."""
        now = datetime.now(timezone.utc).isoformat()
        affected = await self.db.update("user_providers",
            {"user_id": user_id, "provider_id": provider_id},
            {"models": json.dumps(models), "updated_at": now}
        )
        if affected == 0:
            raise ValueError(f"Provider {provider_id} not found")
        return await self.get_user_config(user_id)

    # =========================================================================
    # Test
    # =========================================================================

    async def test_provider(self, user_id: str, provider_id: str) -> tuple[bool, str]:
        """Test connectivity to a provider."""
        row = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not row:
            return False, "Provider not found"

        if row.get("auth_type") == "oauth":
            return True, "OAuth provider (managed by Claude Code CLI)"

        from xyz_agent_context.agent_framework.provider_registry import provider_registry
        prov = ProviderConfig(
            provider_id=row["provider_id"],
            name=row["name"],
            source=row["source"],
            protocol=row["protocol"],
            auth_type=row.get("auth_type", "api_key"),
            api_key=row.get("api_key", ""),
            base_url=row.get("base_url", ""),
            models=json.loads(row["models"]) if row.get("models") else [],
        )
        return await provider_registry.test_provider(prov)


# =============================================================================
# Dual-protocol provider builder (NetMind, Yunwu, OpenRouter)
# =============================================================================

_DUAL_PROVIDER_CONFIGS = {
    "netmind": {
        "anthropic": {"name": "NetMind (Anthropic)", "base_url": "https://api.netmind.ai/inference-api/anthropic", "auth_type": "bearer_token"},
        "openai": {"name": "NetMind (OpenAI)", "base_url": "https://api.netmind.ai/inference-api/openai/v1", "auth_type": "api_key"},
    },
    "yunwu": {
        "anthropic": {"name": "Yunwu (Anthropic)", "base_url": "https://api.yunwuai.cloud/v1/messages", "auth_type": "api_key"},
        "openai": {"name": "Yunwu (OpenAI)", "base_url": "https://api.yunwuai.cloud/v1", "auth_type": "api_key"},
    },
    "openrouter": {
        "anthropic": {"name": "OpenRouter (Anthropic)", "base_url": "https://openrouter.ai/api/v1/messages", "auth_type": "api_key"},
        "openai": {"name": "OpenRouter (OpenAI)", "base_url": "https://openrouter.ai/api/v1", "auth_type": "api_key"},
    },
}


def _build_dual_providers(card_type: str, api_key: str, group_id: str, models: Optional[list] = None) -> list[dict]:
    cfg = _DUAL_PROVIDER_CONFIGS[card_type]
    result = []
    for protocol, info in cfg.items():
        result.append({
            "provider_id": _generate_provider_id(),
            "name": info["name"],
            "source": card_type,
            "protocol": protocol,
            "auth_type": info["auth_type"],
            "api_key": api_key,
            "base_url": info["base_url"],
            "models": json.dumps(models or []),
            "linked_group": group_id,
        })
    return result
