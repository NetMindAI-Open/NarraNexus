"""
@file_name: test_awareness_tools.py
@author: Bin Liang
@date: 2026-09-07
@description: The agent's self-awareness tools: platform_overview names the host, plugins, slot domains and non-default bindings; platform_slots filters by domain; contract_docs explains a kind with its contract classes; agent_self reports capabilities, model slots and prompt sections; capability_set is owner-only, refuses base capabilities and persists a switch.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl import awareness


def test_platform_overview_and_slots_and_contract_docs():
    over = awareness.platform_overview()
    assert over["host_version"] and any(p["id"] == "builtin.prompts" for p in over["builtin_plugins"])
    assert [d["domain"] for d in over["slot_domains"]][:2] == ["kernel", "prompt"]
    assert "how_to_go_deeper" in over
    prompt = awareness.platform_slots("prompt")
    assert len(prompt) == 1 and {s["path"] for s in prompt[0]["slots"]} == {"prompt", "prompt.sections", "prompt.assembler"}
    docs = awareness.contract_docs("prompt")
    assert docs["stability"] == "stable" and "prompt.assembler" in docs["slots"]
    assert any("assemble" in m for c in docs["contracts"].values() for m in c.get("members", []))
    assert "kinds" in awareness.contract_docs("nope")


@pytest.fixture
async def db_client():
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_agent_self_and_capability_set(db_client):
    from narranexus.platform.repository.agent_repository import AgentRepository
    from narranexus.platform.schema.entity_schema import Agent

    await AgentRepository(db_client).insert(Agent(agent_id="a_me", agent_name="Me", created_by="u1"))
    me = await awareness.agent_self(db_client, "a_me", "u1")
    assert me["agent"]["is_owner"] and me["capabilities"]["capabilities"] and [s["id"] for s in me["prompt_sections"]][0] == "security"
    assert "error" in await awareness.capability_set(db_client, "a_me", "u2", "JobModule", False)
    assert "base capability" in (await awareness.capability_set(db_client, "a_me", "u1", "ChatModule", False))["error"]
    out = await awareness.capability_set(db_client, "a_me", "u1", "JobModule", False)
    assert out["enabled"] is False
    me = await awareness.agent_self(db_client, "a_me", "u1")
    job = next(c for c in me["capabilities"]["capabilities"] if c["module_class"] == "JobModule")
    assert job["enabled"] is False
