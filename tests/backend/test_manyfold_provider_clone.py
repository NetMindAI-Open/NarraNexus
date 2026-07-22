"""
@file_name: test_manyfold_provider_clone.py
@author: NarraNexus
@date: 2026-07-18
@description: Netmind-only filtering on the Manyfold cross-user provider clone.

``_clone_provider_setup`` copies ``user_providers`` + ``user_slots`` rows
directly (not through the gated slot writers), so it applies the cloud
netmind-only policy itself: on cloud, only NetMind-source providers (and
the slots pointing at them) are cloned — an mf_* user is an ordinary
non-staff cloud user and must not be born holding a bring-your-own
binding the gated routes would refuse. Local clones stay unfiltered.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from backend.routes.manyfold_agents import _clone_provider_setup
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.schema_registry import auto_migrate


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


async def _seed_src(db_client):
    """Template user with one netmind card + one bring-your-own card, and a
    slot bound to each."""
    for pid, source in (("p_nm", "netmind"), ("p_own", "user")):
        await db_client.insert(
            "user_providers",
            {
                "provider_id": pid,
                "user_id": "template",
                "name": pid,
                "source": source,
                "protocol": "anthropic",
                "auth_type": "api_key",
                "api_key": "sk-test",
                "base_url": "",
                "models": json.dumps(["m"]),
                "is_active": 1,
            },
        )
    await db_client.insert(
        "user_slots",
        {"user_id": "template", "slot_name": "agent", "provider_id": "p_own",
         "model": "m", "agent_framework": "claude_code"},
    )
    await db_client.insert(
        "user_slots",
        {"user_id": "template", "slot_name": "helper_llm", "provider_id": "p_nm",
         "model": "m"},
    )


@pytest.mark.asyncio
async def test_cloud_clone_drops_non_netmind_providers_and_their_slots(
    db_client, monkeypatch
):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    await _seed_src(db_client)

    await _clone_provider_setup(db_client, src_user_id="template", dst_user_id="mf_x")

    provs = await db_client.get("user_providers", {"user_id": "mf_x"})
    assert [p["source"] for p in provs] == ["netmind"]

    slots = await db_client.get("user_slots", {"user_id": "mf_x"})
    # The agent slot pointed at the dropped bring-your-own card → skipped;
    # only the helper slot (netmind-backed, remapped pid) survives.
    assert [s["slot_name"] for s in slots] == ["helper_llm"]
    assert slots[0]["provider_id"] == provs[0]["provider_id"]


@pytest.mark.asyncio
async def test_local_clone_copies_everything(db_client, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    await _seed_src(db_client)

    await _clone_provider_setup(db_client, src_user_id="template", dst_user_id="mf_x")

    provs = await db_client.get("user_providers", {"user_id": "mf_x"})
    assert sorted(p["source"] for p in provs) == ["netmind", "user"]
    slots = await db_client.get("user_slots", {"user_id": "mf_x"})
    assert sorted(s["slot_name"] for s in slots) == ["agent", "helper_llm"]
