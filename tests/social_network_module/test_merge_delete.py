"""
@file_name: test_merge_delete.py
@author:
@date: 2026-08-10
@description: Behavior tests for `SocialNetworkModule.merge_entities` /
`delete_entity` (pre-open review #2 — these used to be duplicated closures
inside `_social_mcp_tools.py`'s `create_social_network_mcp_server`, byte-
copied again into `backend/routes/agents/social_network.py`; now both call
these module methods, so the merge/delete semantics live in exactly one
place). Route-level delegation is covered separately in
`tests/backend/test_social_write_routes.py`.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.social_network_module.social_network_module import (
    SocialNetworkModule,
)


class _FakeEntity:
    def __init__(
        self,
        entity_id: str,
        entity_name: str = "",
        keywords: list[str] | None = None,
        identity_info: dict | None = None,
        contact_info: dict | None = None,
        related_job_ids: list[str] | None = None,
        entity_description: str = "",
        interaction_count: int = 0,
        last_interaction_time=None,
    ):
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.keywords = keywords or []
        self.identity_info = identity_info or {}
        self.contact_info = contact_info or {}
        self.related_job_ids = related_job_ids or []
        self.entity_description = entity_description
        self.interaction_count = interaction_count
        self.last_interaction_time = last_interaction_time


class _FakeRepo:
    """Stands in for SocialNetworkRepository — `known` maps entity_id ->
    _FakeEntity; missing ids resolve to None (not found)."""

    def __init__(self, known: dict[str, _FakeEntity]):
        self.known = known
        self.updated: tuple[str, dict] | None = None
        self.deleted: list[str] = []

    async def get_entity(self, entity_id: str, instance_id: str):
        return self.known.get(entity_id)

    async def update_entity_info(self, entity_id: str, instance_id: str, updates: dict):
        self.updated = (entity_id, updates)

    async def delete_entity(self, entity_id: str, instance_id: str):
        self.deleted.append(entity_id)


def _module_with_repo(repo: _FakeRepo) -> SocialNetworkModule:
    module = SocialNetworkModule(agent_id="agent_mine", database_client=None, instance_id="social_agent_mine")
    module._social_repo = repo  # bypass the real DB-backed repository
    return module


# --------------------------------------------------------------------------- merge_entities


@pytest.mark.asyncio
async def test_merge_unions_tags_sums_counts_and_deletes_source():
    source = _FakeEntity(
        "entity_alice_lark",
        entity_name="Alice (Lark)",
        keywords=["expert:ml", "engineer"],
        identity_info={"organization": "Acme"},
        entity_description="Met via Lark",
        interaction_count=3,
    )
    target = _FakeEntity(
        "user_alice_123",
        entity_name="Alice",
        keywords=["expert:ml"],
        identity_info={"position": "Lead"},
        entity_description="",
        interaction_count=5,
    )
    repo = _FakeRepo({"entity_alice_lark": source, "user_alice_123": target})
    module = _module_with_repo(repo)

    result = await module.merge_entities(
        source_entity_id="entity_alice_lark",
        target_entity_id="user_alice_123",
        instance_id="social_agent_mine",
    )

    assert result["success"] is True
    assert result["target_entity_id"] == "user_alice_123"
    assert set(result["merged_tags"]) == {"expert:ml", "engineer"}

    updated_entity_id, updates = repo.updated
    assert updated_entity_id == "user_alice_123"
    assert updates["identity_info"] == {"position": "Lead", "organization": "Acme"}
    assert updates["interaction_count"] == 8
    # Faithful to the original: an EMPTY target description takes the source
    # verbatim (the "(Merged from ...)" prefix is only added when the target
    # already had a description to append to).
    assert updates["entity_description"] == "Met via Lark"
    assert repo.deleted == ["entity_alice_lark"]


@pytest.mark.asyncio
async def test_merge_keep_target_name_false_uses_source_name():
    source = _FakeEntity("entity_a", entity_name="Source Name")
    target = _FakeEntity("entity_b", entity_name="Target Name")
    repo = _FakeRepo({"entity_a": source, "entity_b": target})
    module = _module_with_repo(repo)

    await module.merge_entities(
        source_entity_id="entity_a",
        target_entity_id="entity_b",
        instance_id="social_agent_mine",
        keep_target_name=False,
    )

    _, updates = repo.updated
    assert updates["entity_name"] == "Source Name"


@pytest.mark.asyncio
async def test_merge_missing_source_entity_reports_failure_and_deletes_nothing():
    target = _FakeEntity("user_alice_123", entity_name="Alice")
    repo = _FakeRepo({"user_alice_123": target})
    module = _module_with_repo(repo)

    result = await module.merge_entities(
        source_entity_id="ghost",
        target_entity_id="user_alice_123",
        instance_id="social_agent_mine",
    )

    assert result["success"] is False
    assert result["message"] == "Source entity not found: ghost"
    assert repo.deleted == []
    assert repo.updated is None


@pytest.mark.asyncio
async def test_merge_missing_target_entity_reports_failure():
    source = _FakeEntity("entity_a", entity_name="A")
    repo = _FakeRepo({"entity_a": source})
    module = _module_with_repo(repo)

    result = await module.merge_entities(
        source_entity_id="entity_a",
        target_entity_id="ghost",
        instance_id="social_agent_mine",
    )

    assert result["success"] is False
    assert result["message"] == "Target entity not found: ghost"
    assert repo.deleted == []


# --------------------------------------------------------------------------- delete_entity


@pytest.mark.asyncio
async def test_delete_existing_entity_succeeds():
    entity = _FakeEntity("entity_junk", entity_name="Junk Entry")
    repo = _FakeRepo({"entity_junk": entity})
    module = _module_with_repo(repo)

    result = await module.delete_entity(entity_id="entity_junk", instance_id="social_agent_mine")

    assert result["success"] is True
    assert "permanently deleted" in result["message"]
    assert repo.deleted == ["entity_junk"]


@pytest.mark.asyncio
async def test_delete_missing_entity_reports_failure():
    repo = _FakeRepo({})
    module = _module_with_repo(repo)

    result = await module.delete_entity(entity_id="ghost", instance_id="social_agent_mine")

    assert result["success"] is False
    assert result["message"] == "Entity not found: ghost"
    assert repo.deleted == []
