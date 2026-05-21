"""
@file_name: test_embedding_client_no_env.py
@author: Bin Liang
@date: 2026-05-21
@description: EmbeddingClient must never read embedding credentials from the
environment / global holder. Credentials come only from explicit args or the
per-task ContextVar; otherwise it fails fast.

debug/20260521: removed the `.env` → embedding api_key path entirely
(_load_from_settings no longer seeds it; EmbeddingClient no longer falls back
to the global embedding_config holder).
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework import api_config
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    EmbeddingConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.llm_api.embedding import (
    EmbeddingClient,
    EmbeddingProviderNotConfigured,
)


@pytest.fixture(autouse=True)
def _clear_embedding_ctx():
    api_config._embedding_ctx.set(None)
    yield
    api_config._embedding_ctx.set(None)


def test_explicit_api_key_is_used():
    c = EmbeddingClient(model="text-embedding-3-small", api_key="explicit-key", base_url="https://x/v1")
    assert c.model == "text-embedding-3-small"


def test_contextvar_provider_is_used():
    set_user_config(
        ClaudeConfig(),
        OpenAIConfig(),
        EmbeddingConfig(api_key="ctx-key", base_url="https://ctx/v1", model="ctx-model"),
    )
    c = EmbeddingClient()
    assert c.model == "ctx-model"


def test_fail_fast_without_any_config():
    # No explicit key, no ContextVar — must raise rather than read env/holder.
    with pytest.raises(EmbeddingProviderNotConfigured):
        EmbeddingClient(api_key=None)


def test_fail_fast_even_if_env_key_present(monkeypatch):
    # Even with OPENAI_API_KEY in the environment, EmbeddingClient must NOT
    # use it — embedding credentials never come from env.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-should-not-be-used")
    with pytest.raises(EmbeddingProviderNotConfigured):
        EmbeddingClient()
