"""
@file_name: test_agent_discovery_sync.py
@author: NarraNexus
@date: 2026-08-04
@description: An agent must be discoverable by its peers from the moment it is
created and configured — not from its first turn (P1 段02, targets 1 & 2).

Before this, ``bus_agent_registry`` had exactly one writer: an inline block in
``MessageBusModule.gather`` that ran per turn and hardcoded
``capabilities=[]``. Two consequences, both confirmed in prod:

  * an agent that was created and configured but had not taken a turn yet was
    absent from the registry entirely;
  * ``capabilities`` was empty for all 488 rows, and ``search_agents`` matches
    ``capabilities LIKE ? OR description LIKE ?`` — so "who can do X" answered
    nothing, for every X.

So the recompute lives in ONE service that every mutation point calls
(creation, description/name edits, awareness config, skill install), with the
per-turn snapshot kept as an idempotent backstop.

Capabilities are machine-derived on purpose: installed skills + active module
classes are facts the platform already owns, so discovery cannot depend on an
agent remembering to describe itself (iron rule #15), and nothing about a
scenario is hardcoded (iron rule #4).
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository.agent_registry_repository import (
    AgentRegistryRepository,
)
from narranexus.platform.message_bus.agent_discovery_sync import sync_agent_discovery
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

OWNER = "user_tc"


@pytest.fixture
async def db():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    try:
        yield client
    finally:
        await client.close()


async def _agent(db, agent_id: str, *, name: str, description: str = "",
                 is_public: int = 0) -> None:
    await db.insert("agents", {
        "agent_id": agent_id, "agent_name": name, "created_by": OWNER,
        "agent_description": description, "is_public": is_public,
    })


async def _skill(db, agent_id: str, skill_id: str, status: str = "installed") -> None:
    await db.insert("skill_installations", {
        "agent_id": agent_id, "user_id": OWNER, "skill_id": skill_id,
        "source_type": "marketplace", "status": status, "last_event": "install",
    })


async def _instance(db, agent_id: str, module_class: str) -> None:
    await db.insert("module_instances", {
        "instance_id": f"{module_class.lower()}_{agent_id[-4:]}",
        "agent_id": agent_id, "user_id": OWNER, "module_class": module_class,
        "status": "active",
    })


# ---------------------------------------------------------------------------
# What lands in the registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_registers_an_agent_that_never_took_a_turn(db):
    """Target 2: registration happens when the agent is created, not when it
    first runs. A configured-but-idle agent used to be invisible."""
    await _agent(db, "agent_a", name="咕咕嘎嘎", description="Reviews lesson plans.")

    await sync_agent_discovery(db, "agent_a")

    profile = await AgentRegistryRepository(db).get_profile("agent_a")
    assert profile is not None
    assert profile.owner_user_id == OWNER
    assert profile.description == "咕咕嘎嘎: Reviews lesson plans."


@pytest.mark.asyncio
async def test_capabilities_come_from_installed_skills_and_modules(db):
    await _agent(db, "agent_a", name="A", description="does things")
    await _skill(db, "agent_a", "officecli")
    await _skill(db, "agent_a", "home-assistant-setup")
    await _instance(db, "agent_a", "ChatModule")
    await _instance(db, "agent_a", "LarkModule")

    await sync_agent_discovery(db, "agent_a")

    caps = (await AgentRegistryRepository(db).get_profile("agent_a")).capabilities
    # Skills are the discovery-relevant part; module classes describe reach.
    assert "officecli" in caps
    assert "home-assistant-setup" in caps
    assert "chat" in caps and "lark" in caps
    assert caps == sorted(caps), "stable order — the row is rewritten every turn"


@pytest.mark.asyncio
async def test_uninstalled_skills_do_not_advertise_capability(db):
    """Installation rows are never deleted, only re-statused."""
    await _agent(db, "agent_a", name="A")
    await _skill(db, "agent_a", "officecli", status="removed")

    await sync_agent_discovery(db, "agent_a")

    caps = (await AgentRegistryRepository(db).get_profile("agent_a")).capabilities
    assert "officecli" not in caps


@pytest.mark.asyncio
async def test_another_agents_skills_never_leak_into_my_capabilities(db):
    await _agent(db, "agent_a", name="A")
    await _agent(db, "agent_b", name="B")
    await _skill(db, "agent_b", "officecli")

    await sync_agent_discovery(db, "agent_a")

    caps = (await AgentRegistryRepository(db).get_profile("agent_a")).capabilities
    assert "officecli" not in caps


# ---------------------------------------------------------------------------
# The placeholder must never reach a peer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_placeholder_description_is_not_republished(db):
    """The 488-row case: the registry must not repeat "a new agent ready for
    configuration" — that string is what made askers refuse to send."""
    from narranexus.platform.schema import LEGACY_AGENT_DESCRIPTION_PLACEHOLDER

    await _agent(db, "agent_a", name="凑企鹅",
                 description=LEGACY_AGENT_DESCRIPTION_PLACEHOLDER)
    await _skill(db, "agent_a", "officecli")

    await sync_agent_discovery(db, "agent_a")

    profile = await AgentRegistryRepository(db).get_profile("agent_a")
    assert "ready for configuration" not in profile.description.lower()
    # The name still identifies it, and capabilities still make it findable.
    assert "凑企鹅" in profile.description
    assert "officecli" in profile.capabilities


@pytest.mark.asyncio
async def test_a_real_description_is_published_with_the_name(db):
    await _agent(db, "agent_a", name="咕咕嘎嘎", description="Reviews lesson plans.")

    await sync_agent_discovery(db, "agent_a")

    desc = (await AgentRegistryRepository(db).get_profile("agent_a")).description
    assert "咕咕嘎嘎" in desc and "Reviews lesson plans." in desc


@pytest.mark.asyncio
async def test_public_agents_are_registered_public(db):
    await _agent(db, "agent_pub", name="Pub", is_public=1)
    await _agent(db, "agent_priv", name="Priv", is_public=0)

    await sync_agent_discovery(db, "agent_pub")
    await sync_agent_discovery(db, "agent_priv")

    repo = AgentRegistryRepository(db)
    assert (await repo.get_profile("agent_pub")).visibility == "public"
    assert (await repo.get_profile("agent_priv")).visibility == "private"


# ---------------------------------------------------------------------------
# Behaviour under repetition and failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_reflects_later_changes(db):
    """Called on every mutation AND every turn, so it must converge, not
    accumulate — and it must pick up a description written later."""
    await _agent(db, "agent_a", name="A")
    await sync_agent_discovery(db, "agent_a")
    await sync_agent_discovery(db, "agent_a")

    rows = await db.execute("SELECT agent_id FROM bus_agent_registry")
    assert len(rows) == 1

    await db.update("agents", {"agent_id": "agent_a"},
                    {"agent_description": "now configured"})
    await sync_agent_discovery(db, "agent_a")

    assert "now configured" in (await AgentRegistryRepository(db).get_profile("agent_a")).description


@pytest.mark.asyncio
async def test_unknown_agent_is_a_no_op_not_a_crash(db):
    """Called from routes and hooks on best-effort paths; a deleted agent must
    not take a request down with it."""
    assert await sync_agent_discovery(db, "agent_missing") is False
    assert await AgentRegistryRepository(db).get_profile("agent_missing") is None


# ---------------------------------------------------------------------------
# Every creation path goes through this seam (review 2026-08-05, issue 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instance_provisioning_registers_through_the_seam(db):
    """``InstanceFactory`` provisions the agent-level instances on all four
    creation paths (HTTP create, bundle/migration import, arena provisioning,
    ensure-exists). It used to write ``bus_agent_registry`` itself — a
    hand-rolled upsert with ``capabilities=json.dumps([])`` and its own copy of
    the description rule, i.e. the exact defect this work removes, in a third
    file.

    Only the HTTP route followed it with a sync, so an imported or
    arena-provisioned agent sat in the directory with empty capabilities until
    something else happened to re-sync it — "discovery waits for the first
    turn", which is the failure this work exists to end.
    """
    from narranexus.platform.module_system._module_impl.instance_factory import InstanceFactory

    await _agent(db, "agent_imported", name="Imported Agent",
                 description="Imported via Agent Migration")
    await _skill(db, "agent_imported", "officecli")

    await InstanceFactory(db).create_agent_level_instances("agent_imported")

    profile = await AgentRegistryRepository(db).get_profile("agent_imported")
    assert profile is not None, "provisioning must leave a discovery row"
    assert profile.owner_user_id == OWNER, "owner must never be blanked"
    # Derived, not hardcoded empty: the whole point of the seam.
    assert "officecli" in profile.capabilities
    assert profile.capabilities, "capabilities must not be an empty list"


def test_the_factory_does_not_write_the_registry_table_itself():
    """Pins the invariant the module docstring claims: ONE writer. A second
    hand-rolled upsert is how the description rule and the capability
    derivation drift apart again."""
    import inspect

    from narranexus.platform.module_system._module_impl import instance_factory

    source = inspect.getsource(instance_factory)
    # The quoted form is what a DB call uses; prose may still name the table
    # (the docstring explains why the write moved out).
    assert '"bus_agent_registry"' not in source, (
        "instance_factory must go through sync_agent_discovery, not write the table"
    )
    assert "sync_agent_discovery" in source
