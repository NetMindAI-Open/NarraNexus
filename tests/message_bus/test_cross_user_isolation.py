"""
@file_name: test_cross_user_isolation.py
@date: 2026-06-18
@description: The message bus must not cross user boundaries — an agent can
neither discover (search) nor direct-message another user's agents.
"""
import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus


async def _seed_agent(db, agent_id: str, owner: str):
    await db.insert("agents", {
        "agent_id": agent_id,
        "agent_name": agent_id,
        "created_by": owner,
    })


async def _make_bus_with_two_users(db_client) -> LocalMessageBus:
    # agent_a owned by userA, agent_b owned by userB
    await _seed_agent(db_client, "agent_a", "userA")
    await _seed_agent(db_client, "agent_b", "userB")
    bus = LocalMessageBus(db_client._backend)
    await bus.register_agent("agent_a", "userA", ["help"], "helper agent A", "private")
    await bus.register_agent("agent_b", "userB", ["help"], "helper agent B", "private")
    return bus


@pytest.mark.asyncio
async def test_search_is_scoped_to_requester_owner(db_client):
    bus = await _make_bus_with_two_users(db_client)
    # agent_a (userA) searches a term that matches BOTH agents.
    results = await bus.search_agents(query="help", requester_agent_id="agent_a")
    ids = {r.agent_id for r in results}
    assert "agent_a" in ids
    assert "agent_b" not in ids  # cross-user agent must NOT be discoverable


@pytest.mark.asyncio
async def test_search_without_requester_unchanged(db_client):
    # No requester → no scoping (e.g. internal/admin callers); both visible.
    bus = await _make_bus_with_two_users(db_client)
    results = await bus.search_agents(query="help")
    ids = {r.agent_id for r in results}
    assert {"agent_a", "agent_b"} <= ids


@pytest.mark.asyncio
async def test_search_unknown_requester_returns_nothing(db_client):
    bus = await _make_bus_with_two_users(db_client)
    results = await bus.search_agents(query="help", requester_agent_id="agent_ghost")
    assert results == []  # unknown owner → leak nothing


@pytest.mark.asyncio
async def test_send_to_agent_rejects_cross_user(db_client):
    bus = await _make_bus_with_two_users(db_client)
    with pytest.raises(PermissionError):
        await bus.send_to_agent(from_agent="agent_a", to_agent="agent_b", content="hi")


@pytest.mark.asyncio
async def test_send_to_agent_allows_same_user(db_client):
    await _seed_agent(db_client, "agent_a", "userA")
    await _seed_agent(db_client, "agent_a2", "userA")
    bus = LocalMessageBus(db_client._backend)
    msg_id = await bus.send_to_agent(from_agent="agent_a", to_agent="agent_a2", content="hi")
    assert msg_id  # same-owner DM is allowed


@pytest.mark.asyncio
async def test_create_channel_rejects_cross_user_member(db_client):
    bus = await _make_bus_with_two_users(db_client)
    with pytest.raises(PermissionError):
        await bus.create_channel(
            name="grp", members=["agent_a", "agent_b"], channel_type="group"
        )
