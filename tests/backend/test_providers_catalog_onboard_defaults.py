"""
@file_name: test_providers_catalog_onboard_defaults.py
@description: GET /api/providers/catalog ships ``onboard_defaults`` -- the
    server-owned per-source onboarding default models -- so the UI's
    "recommended" picks never carry their own copy of the table.
"""
from __future__ import annotations

import pytest

from backend.routes.providers import get_catalog

FLASH = "deepseek-ai/DeepSeek-V4.1-Flash"


@pytest.mark.asyncio
async def test_catalog_ships_onboard_defaults_for_sources(monkeypatch):
    monkeypatch.delenv("FREE_TIER_AGENT_MODEL", raising=False)
    monkeypatch.delenv("FREE_TIER_HELPER_MODEL", raising=False)

    body = await get_catalog()

    defaults = body["onboard_defaults"]
    assert defaults["netmind"] == {"agent": FLASH, "helper": FLASH}
    assert defaults["netmind_free"] == {"agent": FLASH, "helper": FLASH}
    # protocol-keyed rows are provider types, not sources -- never shipped
    assert "anthropic" not in defaults and "openai" not in defaults


@pytest.mark.asyncio
async def test_catalog_onboard_defaults_follow_the_free_tier_env(monkeypatch):
    monkeypatch.setenv("FREE_TIER_AGENT_MODEL", "ops/pick")

    body = await get_catalog()

    assert body["onboard_defaults"]["netmind_free"]["agent"] == "ops/pick"
    assert body["onboard_defaults"]["netmind"]["agent"] == FLASH
