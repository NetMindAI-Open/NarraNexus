"""
@file_name: test_social_write_routes.py
@author:
@date: 2026-08-10
@description: Route-level proof for the SocialNetworkModule write endpoints
(extract / merge / delete-entity / create-agent).

Follows the `test_channel_routes_owner_gate.py` fixture shape: one TestClient
per test, monkeypatching `backend.routes._ownership` for the ownership
decision and the route module's own collaborators (InstanceRepository,
SocialNetworkRepository, SocialNetworkModule, AgentRepository,
InstanceAwarenessRepository) for the data operations —
these routes replicate `_social_mcp_tools.py`'s tool bodies, not the DB
itself, so the tests pin behavior at that boundary.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import narranexus_plugins.social_network_module.routes as sn_routes
from narranexus_plugins.social_network_module.routes import router as social_network_router


# --------------------------------------------------------------------------- fakes


class _FakeInstance:
    def __init__(self, instance_id: str):
        self.instance_id = instance_id


class _FakeInstanceRepository:
    """Stands in for InstanceRepository — resolves a SocialNetworkModule
    instance for every agent except 'agent_no_instance'."""

    def __init__(self, db):
        self.db = db

    async def get_by_agent(self, agent_id: str, module_class: str):
        if agent_id == "agent_no_instance":
            return []
        return [_FakeInstance(f"social_{agent_id}")]

    async def create_instance(self, instance):
        return 1


class _FakeSocialNetworkModule:
    """Stands in for SocialNetworkModule — records the call and returns a
    canned result for whichever method the route delegates to. The real
    merge/delete business logic (tag union, description append, etc.) now
    lives on `SocialNetworkModule` itself and is proven separately in
    tests/social_network_module/test_merge_delete.py; these route-level
    tests only need to prove the route delegates with the right args and
    forwards the result (with 'message' normalized to 'error' on failure)."""

    last_call: dict[str, Any] | None = None
    result: dict[str, Any] = {"success": True, "message": "Entity info updated successfully", "entity_id": "x"}
    merge_last_call: dict[str, Any] | None = None
    merge_result: dict[str, Any] = {
        "success": True,
        "message": "Merged 'a' into 'b'",
        "target_entity_id": "b",
        "merged_tags": [],
    }
    delete_last_call: dict[str, Any] | None = None
    delete_result: dict[str, Any] = {"success": True, "message": "Entity deleted."}
    search_last_call: dict[str, Any] | None = None
    search_result: dict[str, Any] = {"success": True, "search_type": "keyword", "results": [], "count": 0}
    recall_last_call: dict[str, Any] | None = None
    recall_result: dict[str, Any] = {"success": True, "entity": {"entity_name": "Alice", "contact_info": {}}}
    stats_last_call: dict[str, Any] | None = None
    stats_result: list = []

    def __init__(self, agent_id, database_client, instance_id):
        self.agent_id = agent_id
        self.instance_id = instance_id

    async def extract_and_update_entity_info(self, entity_id, instance_id, updates, update_mode):
        type(self).last_call = {
            "entity_id": entity_id,
            "instance_id": instance_id,
            "updates": updates,
            "update_mode": update_mode,
        }
        return type(self).result

    async def merge_entities(self, source_entity_id, target_entity_id, instance_id, keep_target_name):
        type(self).merge_last_call = {
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "instance_id": instance_id,
            "keep_target_name": keep_target_name,
        }
        return type(self).merge_result

    async def delete_entity(self, entity_id, instance_id):
        type(self).delete_last_call = {"entity_id": entity_id, "instance_id": instance_id}
        return type(self).delete_result

    async def search_network(self, search_keyword, instance_id, search_type, top_k):
        type(self).search_last_call = {
            "search_keyword": search_keyword, "instance_id": instance_id,
            "search_type": search_type, "top_k": top_k,
        }
        return type(self).search_result

    async def recall_entity_info(self, entity_id, instance_id):
        type(self).recall_last_call = {"entity_id": entity_id, "instance_id": instance_id}
        return type(self).recall_result

    async def get_agent_stats(self, instance_id, sort_by, top_k, filter_tags):
        type(self).stats_last_call = {
            "instance_id": instance_id, "sort_by": sort_by, "top_k": top_k, "filter_tags": filter_tags,
        }
        return type(self).stats_result


class _FakeAgent:
    def __init__(self, agent_id: str, created_by: str | None, agent_name: str = "Creator"):
        self.agent_id = agent_id
        self.created_by = created_by
        self.agent_name = agent_name


class _FakeAgentRepository:
    def __init__(self, db, agents: dict[str, _FakeAgent]):
        self.db = db
        self.agents = agents
        self.added: dict | None = None

    async def get_agent(self, agent_id: str):
        return self.agents.get(agent_id)

    async def add_agent(self, **kwargs):
        self.added = kwargs


# --------------------------------------------------------------------------- fixture


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(sn_routes, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2", "agent_no_instance": "u1"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)
    monkeypatch.setattr(sn_routes, "InstanceRepository", _FakeInstanceRepository)
    # Never touch the real filesystem base path for create-agent.

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(social_network_router, prefix="/api/agents")
    return TestClient(app)


OWNER_HEADERS = {"x-test-user": "u1"}


# --------------------------------------------------------------------------- ownership gate


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/agents/agent_theirs/social-network/extract", {"entity_id": "e1", "updates": {}}),
        ("/api/agents/agent_theirs/social-network/merge", {"source_entity_id": "a", "target_entity_id": "b"}),
        ("/api/agents/agent_theirs/social-network/delete-entity", {"entity_id": "e1"}),
        (
            "/api/agents/agent_theirs/social-network/create-agent",
            {"new_agent_id": "agent_0123456789ab", "agent_name": "Scout", "awareness": "I am Scout"},
        ),
    ],
)
def test_non_owner_write_is_denied_by_the_canonical_helper(client, path, body):
    r = client.post(path, headers=OWNER_HEADERS, json=body)
    assert r.status_code == 403
    assert "Permission denied" in r.json()["detail"]


# --------------------------------------------------------------------------- extract


def test_extract_success_delegates_to_social_network_module(client, monkeypatch):
    _FakeSocialNetworkModule.result = {
        "success": True,
        "message": "Entity info updated successfully",
        "entity_id": "user_alice",
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/extract",
        headers=OWNER_HEADERS,
        json={"entity_id": "user_alice", "updates": {"entity_name": "Alice"}, "update_mode": "merge"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["entity_id"] == "user_alice"
    assert _FakeSocialNetworkModule.last_call == {
        "entity_id": "user_alice",
        "instance_id": "social_agent_mine",
        "updates": {"entity_name": "Alice"},
        "update_mode": "merge",
    }


def test_extract_underlying_failure_maps_message_to_error_key(client, monkeypatch):
    _FakeSocialNetworkModule.result = {
        "success": False,
        "message": "Error: updates must be a dictionary, got list",
        "entity_id": "user_bob",
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/extract",
        headers=OWNER_HEADERS,
        json={"entity_id": "user_bob", "updates": {}},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "Error: updates must be a dictionary, got list"
    assert "message" not in body


def test_extract_no_social_network_instance_reports_family_style_error(client):
    r = client.post(
        "/api/agents/agent_no_instance/social-network/extract",
        headers=OWNER_HEADERS,
        json={"entity_id": "user_x", "updates": {}},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "No SocialNetworkModule instance found for agent: agent_no_instance"


# --------------------------------------------------------------------------- merge


def test_merge_success_delegates_to_social_network_module(client, monkeypatch):
    _FakeSocialNetworkModule.merge_result = {
        "success": True,
        "message": "Merged 'entity_alice_lark' into 'user_alice_123'",
        "target_entity_id": "user_alice_123",
        "merged_tags": ["expert:ml", "engineer"],
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/merge",
        headers=OWNER_HEADERS,
        json={"source_entity_id": "entity_alice_lark", "target_entity_id": "user_alice_123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["target_entity_id"] == "user_alice_123"
    assert set(body["merged_tags"]) == {"expert:ml", "engineer"}
    assert _FakeSocialNetworkModule.merge_last_call == {
        "source_entity_id": "entity_alice_lark",
        "target_entity_id": "user_alice_123",
        "instance_id": "social_agent_mine",
        "keep_target_name": True,
    }


def test_merge_underlying_failure_maps_message_to_error_key(client, monkeypatch):
    _FakeSocialNetworkModule.merge_result = {
        "success": False,
        "message": "Source entity not found: ghost",
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/merge",
        headers=OWNER_HEADERS,
        json={"source_entity_id": "ghost", "target_entity_id": "user_alice_123"},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "Source entity not found: ghost"
    assert "message" not in body


# --------------------------------------------------------------------------- delete-entity


def test_delete_entity_success_delegates_to_social_network_module(client, monkeypatch):
    _FakeSocialNetworkModule.delete_result = {
        "success": True,
        "message": "Entity 'Junk Entry' (entity_junk) has been permanently deleted.",
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/delete-entity",
        headers=OWNER_HEADERS,
        json={"entity_id": "entity_junk"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "permanently deleted" in body["message"]
    assert _FakeSocialNetworkModule.delete_last_call == {
        "entity_id": "entity_junk",
        "instance_id": "social_agent_mine",
    }


def test_delete_missing_entity_returns_family_style_error(client, monkeypatch):
    _FakeSocialNetworkModule.delete_result = {"success": False, "message": "Entity not found: ghost"}
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/delete-entity",
        headers=OWNER_HEADERS,
        json={"entity_id": "ghost"},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "Entity not found: ghost"
    assert "message" not in body


# --------------------------------------------------------------------------- create-agent


class _FakeProvisionResult:
    def __init__(self, bootstrap_active: bool = True):
        self.bootstrap_active = bootstrap_active
        self.warnings: list[str] = []


def test_create_agent_success_delegates_to_provisioning_seam(client, monkeypatch):
    caller = _FakeAgent("agent_mine", created_by="u1", agent_name="Owner Agent")
    fake_agent_repo = _FakeAgentRepository(None, {"agent_mine": caller})
    monkeypatch.setattr(sn_routes, "AgentRepository", lambda db: fake_agent_repo)

    calls: list[dict[str, Any]] = []

    async def _fake_provision(db, **kwargs):
        calls.append(kwargs)
        return _FakeProvisionResult()

    monkeypatch.setattr(sn_routes, "provision_new_agent", _fake_provision)

    r = client.post(
        "/api/agents/agent_mine/social-network/create-agent",
        headers=OWNER_HEADERS,
        json={"new_agent_id": "agent_0123456789ab", "agent_name": "Scout", "awareness": "I am Scout, a research helper."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["agent_name"] == "Scout"
    # The route provisions the CALLER-minted id (parity), not one it generates.
    assert body["new_agent_id"] == "agent_0123456789ab"

    assert len(calls) == 1
    assert calls[0]["user_id"] == "u1"
    assert calls[0]["agent_name"] == "Scout"
    assert calls[0]["awareness"] == "I am Scout, a research helper."
    assert calls[0]["agent_id"] == "agent_0123456789ab"


def test_create_agent_surfaces_provisioning_warnings(client, monkeypatch):
    caller = _FakeAgent("agent_mine", created_by="u1", agent_name="Owner Agent")
    fake_agent_repo = _FakeAgentRepository(None, {"agent_mine": caller})
    monkeypatch.setattr(sn_routes, "AgentRepository", lambda db: fake_agent_repo)

    async def _fake_provision(db, **kwargs):
        res = _FakeProvisionResult()
        res.warnings = ["instance_factory: boom"]
        return res

    monkeypatch.setattr(sn_routes, "provision_new_agent", _fake_provision)
    r = client.post(
        "/api/agents/agent_mine/social-network/create-agent",
        headers=OWNER_HEADERS,
        json={"new_agent_id": "agent_0123456789ab", "agent_name": "Scout", "awareness": "aw"},
    )
    assert r.status_code == 200
    assert r.json()["warnings"] == ["instance_factory: boom"]


def test_create_agent_requires_new_agent_id(client):
    # new_agent_id is now a required body field (the caller mints it for parity);
    # omitting it must fail validation, not fall back to a route-generated id.
    r = client.post(
        "/api/agents/agent_mine/social-network/create-agent",
        headers=OWNER_HEADERS,
        json={"agent_name": "Scout", "awareness": "aw"},
    )
    assert r.status_code == 422


def test_create_agent_without_resolvable_owner_returns_family_style_error(client, monkeypatch):
    caller = _FakeAgent("agent_mine", created_by=None)
    fake_agent_repo = _FakeAgentRepository(None, {"agent_mine": caller})
    monkeypatch.setattr(sn_routes, "AgentRepository", lambda db: fake_agent_repo)

    async def _unreachable_provision(db, **kwargs):
        raise AssertionError("provision_new_agent must not be called without a resolvable owner")

    monkeypatch.setattr(sn_routes, "provision_new_agent", _unreachable_provision)

    r = client.post(
        "/api/agents/agent_mine/social-network/create-agent",
        headers=OWNER_HEADERS,
        json={"new_agent_id": "agent_0123456789ab", "agent_name": "Scout", "awareness": "I am Scout."},
    )
    body = r.json()
    assert body["success"] is False
    assert "Cannot determine your owner" in body["error"]


# --------------------------------------------------------------------------- read (seam-twin) endpoints


def test_recall_route_returns_search_network_raw_dict(client, monkeypatch):
    _FakeSocialNetworkModule.search_result = {"success": True, "results": [{"entity_id": "u1"}], "count": 1}
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)
    r = client.post(
        "/api/agents/agent_mine/social-network/recall",
        headers=OWNER_HEADERS, json={"search_keyword": "alice", "top_k": 5},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True, "results": [{"entity_id": "u1"}], "count": 1}
    assert _FakeSocialNetworkModule.search_last_call == {
        "search_keyword": "alice", "instance_id": "social_agent_mine", "search_type": "auto", "top_k": 5,
    }


def test_contact_route_shapes_via_format_contact_result(client, monkeypatch):
    _FakeSocialNetworkModule.recall_result = {
        "success": True, "entity": {"entity_name": "Alice", "contact_info": {"email": "a@x.com"}},
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)
    r = client.post(
        "/api/agents/agent_mine/social-network/contact",
        headers=OWNER_HEADERS, json={"entity_id": "u1"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "success": True, "entity_id": "u1", "entity_name": "Alice", "contact_info": {"email": "a@x.com"},
    }


def test_stats_route_shapes_via_format_stats_result(client, monkeypatch):
    _FakeSocialNetworkModule.stats_result = [{"entity_name": "Bob"}]
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)
    r = client.post(
        "/api/agents/agent_mine/social-network/stats",
        headers=OWNER_HEADERS, json={"sort_by": "frequent", "top_k": 10, "filter_tags": ["expert:fe"]},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True, "sort_by": "frequent", "count": 1, "results": [{"entity_name": "Bob"}]}
    assert _FakeSocialNetworkModule.stats_last_call == {
        "instance_id": "social_agent_mine", "sort_by": "frequent", "top_k": 10, "filter_tags": ["expert:fe"],
    }


def test_read_route_no_instance_is_message_keyed(client, monkeypatch):
    # Reads return the tool shape verbatim (message key), NOT the write routes'
    # normalized error key.
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)
    r = client.post(
        "/api/agents/agent_no_instance/social-network/recall",
        headers=OWNER_HEADERS, json={"search_keyword": "x"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["message"] == "No SocialNetworkModule instance found for agent: agent_no_instance"
    assert body["results"] == []
    assert "error" not in body


def test_read_route_non_owner_is_denied(client):
    r = client.post(
        "/api/agents/agent_theirs/social-network/stats",
        headers=OWNER_HEADERS, json={"sort_by": "recent"},
    )
    assert r.status_code == 403
    assert "Permission denied" in r.json()["detail"]



def test_create_agent_rejects_path_traversal_id(client):
    # A caller-minted new_agent_id becomes a workspace path segment; the pattern
    # must reject "../.." so it can't traverse into another tenant's directory.
    r = client.post(
        "/api/agents/agent_mine/social-network/create-agent",
        headers=OWNER_HEADERS,
        json={"new_agent_id": "../u2/agent_x", "agent_name": "Scout", "awareness": "aw"},
    )
    assert r.status_code == 422
