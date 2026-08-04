"""
@file_name: test_oauth_framework_binding_sqlite.py
@author: rujing.yan
@date: 2026-07-31
@description: End-to-end (real SQLite + registry schema) for the
subscription-card ↔ agent-framework rule.

The fake-DB unit tests pin the RULE; this one pins that it survives contact
with the real schema — that ``user_slots`` actually carries the columns the
cleanup writes, and that a cleared binding is what a subsequent config read
returns. The user-visible bug it guards: a Claude Code Login card bound to a
nexus_power agent slot saved fine and only failed mid-run.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.providers.user_service import (
    UserProviderService,
)


@pytest.fixture
async def service(tmp_path):
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(str(tmp_path / "providers.db"))
    await backend.initialize()
    await auto_migrate(backend)
    db = AsyncDatabaseClient(_backend=backend)
    try:
        yield UserProviderService(db)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_claude_login_cannot_be_bound_under_nexus_power(service):
    _, ids = await service.add_provider(user_id="u1", card_type="claude_oauth")
    # Adding the card auto-binds it AND switches the framework to claude_code.
    assert await service.get_user_agent_framework("u1") == "claude_code"
    config = await service.get_user_config("u1")
    assert config.slots["agent"].provider_id == ids[0]

    # Switching to nexus_power unbinds it: nexus_power drives the provider API
    # directly and refuses subscription credentials.
    cleared = await service.set_user_agent_framework("u1", "nexus_power")
    assert cleared is True
    config = await service.get_user_config("u1")
    assert config.slots["agent"].provider_id == ""

    # And re-binding it by hand is refused rather than deferred to the run.
    with pytest.raises(ValueError, match="CLI subscription"):
        await service.set_slot("u1", "agent", ids[0], "opus", actor_is_staff=None)

    # The helper slot kept the same card — one subscription still covers it.
    assert config.slots["helper_llm"].provider_id == ids[0]


@pytest.mark.asyncio
async def test_api_key_binding_survives_a_framework_switch(service):
    _, ids = await service.add_provider(
        user_id="u2",
        card_type="anthropic",
        api_key="sk-test",
        models=["claude-opus-4-8"],
    )
    await service.set_user_agent_framework("u2", "claude_code")
    await service.set_slot(
        "u2", "agent", ids[0], "claude-opus-4-8", actor_is_staff=None
    )

    cleared = await service.set_user_agent_framework("u2", "nexus_power")
    assert cleared is False
    config = await service.get_user_config("u2")
    assert config.slots["agent"].provider_id == ids[0]
    assert config.slots["agent"].model == "claude-opus-4-8"
