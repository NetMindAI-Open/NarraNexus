"""
@file_name: test_provider_default_embeddings.py
@author: Bin Liang
@date: 2026-05-21
@description: Custom OpenAI-protocol providers must auto-expose the official
embedding models so the embedding slot always has candidates.

debug/20260521: a custom OpenAI provider (official api.openai.com OR an
OpenAI-compatible forward) added with only chat models in the form left the
embedding slot with no options. add_provider now unions the official OpenAI
embedding models in. Vendor presets (NetMind) keep their own embeddings.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.user_provider_service import UserProviderService


@pytest.mark.asyncio
async def test_custom_openai_provider_auto_includes_embedding_models(db_client):
    svc = UserProviderService(db_client)
    config, ids = await svc.add_provider(
        user_id="alice",
        card_type="openai",
        name="Forward",
        api_key="k",
        base_url="https://proxy.example/v1",
        models=["gpt-5.4", "gpt-5.4-mini"],  # user listed chat models only
    )
    models = config.providers[ids[0]].models
    assert "text-embedding-3-small" in models
    assert "text-embedding-3-large" in models
    # user-listed models are preserved
    assert "gpt-5.4" in models


@pytest.mark.asyncio
async def test_custom_openai_no_duplicate_embeddings(db_client):
    svc = UserProviderService(db_client)
    config, ids = await svc.add_provider(
        user_id="carol",
        card_type="openai",
        name="Official",
        api_key="k",
        base_url="https://api.openai.com/v1",
        models=["text-embedding-3-small", "gpt-5.4"],  # already has one embedding
    )
    models = config.providers[ids[0]].models
    assert models.count("text-embedding-3-small") == 1
    assert "text-embedding-3-large" in models


@pytest.mark.asyncio
async def test_custom_anthropic_provider_has_no_embedding_models(db_client):
    svc = UserProviderService(db_client)
    config, ids = await svc.add_provider(
        user_id="bob",
        card_type="anthropic",
        name="Claude proxy",
        api_key="k",
        base_url="https://api.anthropic.com",
        models=["claude-opus-4-7"],
    )
    models = config.providers[ids[0]].models
    assert "text-embedding-3-small" not in models
