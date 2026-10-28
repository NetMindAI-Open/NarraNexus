"""
@file_name: test_data_access.py
@author:
@date: 2026-08-10
@description: AgentDataStore seam — REAL parity between DirectStore and
HttpStore, the backend 200+success:false failure contract, the identity-header
forwarding whitelist, and the composition root's env gate.

Parity here means: the same scenario produces the same RETURN STRING from both
implementations. The fake backend below implements the actual route semantics
(backend/routes/agents/awareness.py with create_missing=false): failure is
HTTP 200 + {"success": false, "error": ...}; non-2xx only ever comes from the
transport/middleware layer (e.g. the Q6 identity 401).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from narranexus.platform.module_system.data_access import (
    DirectStore,
    HttpStore,
    get_agent_data_store,
)
from narranexus.platform.module_system.data_access import store as st
from narranexus.platform.module_system.data_access.factory import current_identity_headers

AGENT = "agent_39b2b72b823b"


# ---------------------------------------------------------------------------
# A fake backend implementing the REAL route contract, and a fake db world
# giving DirectStore the same known agents — parity = same scenario, same string.
# ---------------------------------------------------------------------------


def _patch_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, **kw):
            kw.pop("transport", None)
            super().__init__(transport=transport, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


def _http_store(monkeypatch, known_agents, upsert_ok=True, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="rejected")
        agent_id = request.url.path.split("/")[3]
        assert request.url.params.get("create_missing") == "false"
        if agent_id not in known_agents:
            return httpx.Response(200, json={
                "success": False,
                "error": f"No AwarenessModule instance found for agent_id={agent_id}",
            })
        if not upsert_ok:
            return httpx.Response(200, json={
                "success": False, "error": "Failed to update awareness",
            })
        return httpx.Response(200, json={"success": True, "awareness": "x"})

    _patch_http(monkeypatch, handler)
    return HttpStore("http://backend:8000")


def _direct_store(known_agents):
    store = DirectStore()

    async def fake_db():
        return object()

    async def fake_instance_id(db, agent_id):
        return f"aware_{agent_id}" if agent_id in known_agents else None

    store._db = fake_db  # type: ignore[method-assign]
    store._awareness_instance_id = fake_instance_id  # type: ignore[method-assign]
    return store


@pytest.fixture
def _upsert_spy(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeAwarenessRepo:
        def __init__(self, db):
            pass

        async def get_by_instance(self, instance_id):
            # update_awareness READS before it writes now: the model rewrites the
            # whole profile and its prescribed format omits the platform identity
            # record, so the previous copy is needed to carry that section over.
            # None = no stored profile, which is the shape these parity tests use.
            return None

        async def upsert(self, instance_id, awareness):
            calls.append((instance_id, awareness))
            return True

    monkeypatch.setattr(
        "narranexus.platform.repository.InstanceAwarenessRepository", FakeAwarenessRepo
    )
    return calls


# ---------------------------------------------------------------------------
# parity: same scenario, same string
# ---------------------------------------------------------------------------


def test_parity_success(monkeypatch, _upsert_spy):
    direct = _direct_store({AGENT})
    http = _http_store(monkeypatch, {AGENT})

    d = asyncio.run(direct.update_awareness(AGENT, "hello"))
    h = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert d == h == st._AWARENESS_OK
    assert _upsert_spy == [(f"aware_{AGENT}", "hello")]


def test_parity_unknown_agent_is_an_error_on_both(monkeypatch, _upsert_spy):
    """The direct path treats an unknown agent_id as an ERROR — the Http path
    must too, NOT auto-create an instance for it (pre-review C2: the route's
    convenience auto-create is opted out via create_missing=false)."""
    direct = _direct_store(set())
    http = _http_store(monkeypatch, set())

    d = asyncio.run(direct.update_awareness(AGENT, "hello"))
    h = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert d == h == st._no_instance_msg(AGENT)
    assert _upsert_spy == []


def test_http_backend_reported_failure_is_not_success(monkeypatch):
    """Pre-review C1: the route reports failure as HTTP 200 + success:false —
    a status-code-only check would call this 'updated successfully'."""
    http = _http_store(monkeypatch, {AGENT}, upsert_ok=False)
    out = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert out == "Error: Failed to update awareness"


def test_http_transport_rejection_degrades_in_band(monkeypatch):
    """Pre-review I3: a middleware-layer 401/5xx (e.g. the Q6 identity gate
    before keys are provisioned) must come back as a readable string, never
    escape the MCP tool as an exception."""
    http = _http_store(monkeypatch, {AGENT}, status=401)
    out = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert out == "Error: awareness backend rejected the call (401)"


def test_http_unreachable_backend_degrades_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route to backend")

    _patch_http(monkeypatch, boom)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.update_awareness(AGENT, "hello"))
    assert out == "Error: awareness backend unreachable"


def test_direct_resolves_instance_through_repository(monkeypatch, _upsert_spy):
    """The one piece of real logic DirectStore carries — instance resolution —
    exercised against the repository seam instead of being stubbed away
    (pre-review I1)."""

    class _Inst:
        instance_id = "aware_real"

    class FakeInstanceRepo:
        def __init__(self, db):
            pass

        async def get_by_agent(self, agent_id, module_class):
            assert module_class == "AwarenessModule"
            return [_Inst()] if agent_id == AGENT else []

    monkeypatch.setattr(
        "narranexus.platform.repository.InstanceRepository", FakeInstanceRepo
    )

    async def fake_db():
        return object()

    store = DirectStore()
    store._db = fake_db  # type: ignore[method-assign]
    assert asyncio.run(store.update_awareness(AGENT, "hi")) == st._AWARENESS_OK
    assert asyncio.run(
        store.update_awareness("agent_nobody", "hi")
    ) == st._no_instance_msg("agent_nobody")


# ---------------------------------------------------------------------------
# identity-header forwarding whitelist
# ---------------------------------------------------------------------------


def test_identity_header_whitelist(monkeypatch):
    ambient = {
        "x-narranexus-agent-id": AGENT,
        "x-narranexus-team-id": "team_1",
        "authorization": "Bearer nx-agent:agent_a~chat",
        "cookie": "session=SECRET",
        "x-forwarded-for": "1.2.3.4",
        "content-type": "application/json",
    }
    monkeypatch.setattr(
        "narranexus.platform.module_system._mcp_identity._ambient_headers", lambda: ambient
    )
    kept = current_identity_headers()
    assert kept == {
        "x-narranexus-agent-id": AGENT,
        "x-narranexus-team-id": "team_1",
        "authorization": "Bearer nx-agent:agent_a~chat",
    }


def test_identity_headers_empty_without_ambient_request(monkeypatch):
    monkeypatch.setattr(
        "narranexus.platform.module_system._mcp_identity._ambient_headers", lambda: None
    )
    assert current_identity_headers() == {}


# ---------------------------------------------------------------------------
# composition root
# ---------------------------------------------------------------------------


def test_factory_default_is_direct(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)
    assert isinstance(get_agent_data_store(), DirectStore)


def test_factory_cloud_is_http(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    store = get_agent_data_store(identity_headers={"authorization": "Bearer nx-agent:a"})
    assert isinstance(store, HttpStore)
    assert store._base == "http://backend:8000"
    assert store._headers == {"authorization": "Bearer nx-agent:a"}


def test_http_store_sends_identity_headers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    store = HttpStore(
        "http://backend:8000",
        identity_headers={"Authorization": "Bearer nx-agent:a~chat"},
    )
    asyncio.run(store.update_awareness(AGENT, "x"))
    assert seen["auth"] == "Bearer nx-agent:a~chat"


# ---------------------------------------------------------------------------
# general_memory migration (PR-3): remember + memory_retain parity
# ---------------------------------------------------------------------------


class _Hit:
    """Minimal memory hit matching what memory.format_memory_hits reads."""

    @staticmethod
    def make(kind, text, tags=None, source_ref=None):
        h = _Hit()
        h.kind = kind
        rec = type("R", (), {})()
        rec.content_text = text
        rec.created_at = None
        rec.tags = tags or []
        rec.source_ref = source_ref
        h.record = rec
        return h


def _memory_direct(monkeypatch, hits=None, record_id="mem_1", retain_ok=True, grep_hits=None, grep_truncated=False):
    """DirectStore with MemoryCoordinator/MemoryEngine stubbed. The stub records
    the ``limit`` it actually received in ``store._seen`` so a test can assert
    DirectStore clamped it the same way HttpStore does."""
    store = DirectStore()
    store._seen = {}  # type: ignore[attr-defined]

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]

    class FakeCoord:
        def __init__(self, engine):
            pass

        async def remember(self, query, limit):
            store._seen["limit"] = limit  # type: ignore[attr-defined]
            return hits or []

        async def grep_memory(self, pattern, regex, limit):
            store._seen["grep_limit"] = limit  # type: ignore[attr-defined]
            store._seen["grep_regex"] = regex  # type: ignore[attr-defined]
            return (grep_hits or [], grep_truncated)

    class FakeEngine:
        def __init__(self, db, agent_id):
            pass

        async def retain(self, record):
            if not retain_ok:
                raise RuntimeError("db down")
            return type("Rec", (), {"record_id": record_id})()

    monkeypatch.setattr("narranexus.platform.memory.MemoryCoordinator", FakeCoord)
    monkeypatch.setattr("narranexus.platform.memory.MemoryEngine", FakeEngine)
    return store


def _memory_http(monkeypatch, remember_body=None, retain_body=None, seen=None, grep_body=None):
    """HttpStore whose backend echoes what the REAL routes return. When
    ``remember_body`` is a list of hits, the handler renders it through the
    same ``format_memory_hits`` the route calls — so a passing parity assertion
    proves DirectStore and the route agree by construction, not because the
    test fed both the same literal."""
    from narranexus.platform.memory import format_memory_hits

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["limit"] = request.url.params.get("limit")
        if request.url.path.endswith("/memory/remember"):
            if isinstance(remember_body, list):
                q = request.url.params.get("query")
                return httpx.Response(200, json={
                    "success": True, "query": q,
                    "memories": format_memory_hits(remember_body),
                })
            return httpx.Response(200, json=remember_body)
        if request.url.path.endswith("/memory/retain"):
            return httpx.Response(200, json=retain_body)
        if request.url.path.endswith("/memory/grep"):
            if seen is not None:
                seen["grep_regex"] = request.url.params.get("regex")
            if isinstance(grep_body, list):
                p = request.url.params.get("pattern")
                return httpx.Response(200, json={
                    "success": True, "pattern": p,
                    "matches": format_memory_hits(grep_body), "truncated": False,
                })
            return httpx.Response(200, json=grep_body)
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    return HttpStore("http://backend:8000")


def test_remember_parity(monkeypatch):
    # Includes a source_ref hit so the one branching field in
    # format_memory_hits (`if r.source_ref: item["source"] = ...`) is exercised
    # on BOTH paths — the branch a divergent copy would silently drop.
    hits = [
        _Hit.make("observation", "the sky is blue", tags=["fact"]),
        _Hit.make("job", "ship the release", source_ref={"kind": "job", "id": "job_9"}),
    ]
    expected = {
        "success": True, "query": "sky",
        "memories": [
            {"kind": "observation", "memory": "the sky is blue", "when": None, "tags": ["fact"]},
            {"kind": "job", "memory": "ship the release", "when": None, "tags": [],
             "source": {"kind": "job", "id": "job_9"}},
        ],
    }
    direct = _memory_direct(monkeypatch, hits=hits)
    d = asyncio.run(direct.remember("agent_a", "sky", 15))
    assert d == expected

    # The http handler renders the SAME hits through the route's real
    # format_memory_hits, so this equality is a genuine cross-path check.
    http = _memory_http(monkeypatch, remember_body=hits)
    h = asyncio.run(http.remember("agent_a", "sky", 15))
    assert h == expected == d


def test_remember_limit_clamped_identically_on_both(monkeypatch):
    # limit=200 is a common LLM overreach ("recall everything"). Both paths
    # must clamp to the route's max=100 — local must not return a result the
    # cloud path would 422 on. This is the parity fix for the reviewer's
    # out-of-bounds finding.
    direct = _memory_direct(monkeypatch, hits=[])
    asyncio.run(direct.remember("agent_a", "sky", 200))
    assert direct._seen["limit"] == 100  # type: ignore[attr-defined]

    seen = {}
    http = _memory_http(monkeypatch, remember_body=[], seen=seen)
    out = asyncio.run(http.remember("agent_a", "sky", 200))
    assert out["success"] is True
    assert seen["limit"] == "100"  # reached the backend clamped, no 422


def test_remember_empty_and_overlong_query_rejected_on_both(monkeypatch):
    direct = _memory_direct(monkeypatch, hits=[])
    http = _memory_http(monkeypatch, remember_body=[])
    empty = {"success": False, "error": "query is empty", "memories": []}
    assert asyncio.run(direct.remember("agent_a", "  ", 15)) == empty
    assert asyncio.run(http.remember("agent_a", "", 15)) == empty  # rejected pre-send

    long_q = "a" * 513
    long_err = {"success": False, "error": "query too long (max 512 chars)", "memories": []}
    assert asyncio.run(direct.remember("agent_a", long_q, 15)) == long_err
    assert asyncio.run(http.remember("agent_a", long_q, 15)) == long_err


def test_retain_parity(monkeypatch):
    direct = _memory_direct(monkeypatch, record_id="mem_42")
    d = asyncio.run(direct.memory_retain("agent_a", "remember this", "MEMORY.md"))
    assert d == {"success": True, "record_id": "mem_42"}

    http = _memory_http(monkeypatch, retain_body={"success": True, "record_id": "mem_42"})
    h = asyncio.run(http.memory_retain("agent_a", "remember this", "MEMORY.md"))
    assert h == d


def test_retain_empty_content_is_rejected_on_both(monkeypatch):
    direct = _memory_direct(monkeypatch)
    http = _memory_http(monkeypatch)
    err = {"success": False, "error": "content is empty"}
    assert asyncio.run(direct.memory_retain("agent_a", "   ", "")) == err
    # HttpStore rejects before any request (no fake needed for the empty path).
    assert asyncio.run(http.memory_retain("agent_a", "", "")) == err


def test_retain_overlong_content_rejected_on_both(monkeypatch):
    # The "import a MEMORY.md slice" use case can exceed 64KB. Both paths must
    # reject identically instead of local writing / cloud silently 422-ing.
    direct = _memory_direct(monkeypatch)
    http = _memory_http(monkeypatch)
    big = "x" * 65537
    err = {"success": False, "error": "content too long (max 65536 chars)"}
    assert asyncio.run(direct.memory_retain("agent_a", big, "")) == err
    assert asyncio.run(http.memory_retain("agent_a", big, "")) == err  # rejected pre-send


def test_remember_http_transport_failure_degrades_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.remember("agent_a", "x", 15))
    assert out["success"] is False
    assert "unreachable" in out["error"]
    assert out["memories"] == []  # tool's own failure shape


def test_remember_http_422_is_actionable(monkeypatch):
    # A 422 (route pydantic bound) must surface as an argument-fixable message,
    # not lumped with 401/502 "backend rejected" — else the model blind-retries.
    def bad_args(request):
        return httpx.Response(422, json={"detail": "validation error"})

    _patch_http(monkeypatch, bad_args)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.remember("agent_a", "x", 15))
    assert out["success"] is False
    assert "invalid arguments" in out["error"]
    assert out["memories"] == []


def test_retain_http_401_degrades_in_band(monkeypatch):
    def gate(request):
        return httpx.Response(401, text="identity required")

    _patch_http(monkeypatch, gate)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.memory_retain("agent_a", "fact", ""))
    assert out["success"] is False
    assert "401" in out["error"]


# ---------------------------------------------------------------------------
# social writes (PR-4): extract / merge / delete parity
# ---------------------------------------------------------------------------


class _Inst:
    def __init__(self, instance_id):
        self.instance_id = instance_id


def _social_direct(monkeypatch, *, has_instance=True, method_result=None,
                   search_result=None, recall_result=None, stats_result=None):
    """DirectStore with InstanceRepository + SocialNetworkModule stubbed. The
    fake module's methods return the matching *_result so a test pins the exact
    dict DirectStore forwards, and record (name, kwargs) in store._calls."""
    store = DirectStore()
    store._calls = {}  # type: ignore[attr-defined]

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]

    class FakeInstanceRepo:
        def __init__(self, db):
            pass

        async def get_by_agent(self, agent_id, module_class):
            return [_Inst("social_1")] if has_instance else []

    class FakeSocial:
        def __init__(self, agent_id, database_client, instance_id):
            pass

        async def extract_and_update_entity_info(self, **kw):
            store._calls["method"] = ("extract", kw)  # type: ignore[attr-defined]
            return method_result

        async def merge_entities(self, **kw):
            store._calls["method"] = ("merge", kw)  # type: ignore[attr-defined]
            return method_result

        async def delete_entity(self, **kw):
            store._calls["method"] = ("delete", kw)  # type: ignore[attr-defined]
            return method_result

        async def search_network(self, **kw):
            store._calls["method"] = ("search", kw)  # type: ignore[attr-defined]
            return search_result

        async def recall_entity_info(self, entity_id, instance_id):
            store._calls["method"] = ("recall", {"entity_id": entity_id, "instance_id": instance_id})  # type: ignore[attr-defined]
            return recall_result

        async def get_agent_stats(self, **kw):
            store._calls["method"] = ("stats", kw)  # type: ignore[attr-defined]
            return stats_result

    monkeypatch.setattr("narranexus.platform.repository.InstanceRepository", FakeInstanceRepo)
    monkeypatch.setattr(
        "narranexus_plugins.social_network_module.SocialNetworkModule", FakeSocial
    )
    return store


def _social_http(monkeypatch, route_body, status=200):
    """HttpStore whose backend echoes a social write route's response shape
    (failures under the route family's ``error`` key, per _normalize_write_result)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=route_body)

    _patch_http(monkeypatch, handler)
    return HttpStore("http://backend:8000")


def test_social_extract_success_parity(monkeypatch):
    ok = {"success": True, "message": "Entity info updated successfully"}
    d = _social_direct(monkeypatch, method_result=ok)
    dr = asyncio.run(d.extract_entity_info(AGENT, "user_x", {"entity_name": "X"}, "merge"))
    assert dr == ok
    # The route leaves a success body untouched (_normalize_write_result only
    # rewrites failures), and so does HttpStore's _write_message_key.
    h = _social_http(monkeypatch, route_body=ok)
    hr = asyncio.run(h.extract_entity_info(AGENT, "user_x", {"entity_name": "X"}, "merge"))
    assert hr == ok == dr


def test_social_no_instance_parity(monkeypatch):
    from narranexus_plugins.social_network_module import social_instance_not_found_msg

    expected = {"success": False, "message": social_instance_not_found_msg(AGENT)}
    d = _social_direct(monkeypatch, has_instance=False)
    dr = asyncio.run(d.delete_entity(AGENT, "user_x"))
    assert dr == expected
    # The backend route returns the SAME string (shared social_instance_not_
    # found_msg) but under the family's `error` key; HttpStore restores the
    # tool's `message` key -> byte-identical to DirectStore. This is the whole
    # point of aligning the route wording onto the shared source.
    route_body = {"success": False, "error": social_instance_not_found_msg(AGENT)}
    h = _social_http(monkeypatch, route_body=route_body)
    hr = asyncio.run(h.delete_entity(AGENT, "user_x"))
    assert hr == expected == dr


def test_social_merge_method_failure_parity(monkeypatch):
    fail = {"success": False, "message": "Source entity not found: s1"}
    d = _social_direct(monkeypatch, method_result=fail)
    dr = asyncio.run(d.merge_entities(AGENT, "s1", "t1", True))
    assert dr == fail
    # Route normalized the method's `message` failure to `error`; HttpStore's
    # _write_message_key is the exact inverse.
    h = _social_http(monkeypatch, route_body={"success": False, "error": "Source entity not found: s1"})
    hr = asyncio.run(h.merge_entities(AGENT, "s1", "t1", True))
    assert hr == fail == dr


def test_social_http_unreachable_is_message_keyed(monkeypatch):
    # Transport degradation must use the social tool's `message` failure key,
    # not `_parse_dict`'s default `error`, so the agent sees a uniform shape.
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.extract_entity_info(AGENT, "user_x", {}, "merge"))
    assert out["success"] is False
    assert "message" in out and "error" not in out
    assert "unreachable" in out["message"]


def test_social_direct_forwards_args_to_the_right_method(monkeypatch):
    # FakeSocial records (method_name, kwargs); assert DirectStore forwards each
    # tool's params to the CORRECT method with the right keys — a swap
    # (source/target), a dropped update_mode/keep_target_name, or extract calling
    # merge would all be caught here.
    d = _social_direct(monkeypatch, method_result={"success": True, "message": "ok"})
    asyncio.run(d.merge_entities(AGENT, "src1", "tgt1", False))
    assert d._calls["method"] == (
        "merge",
        {"source_entity_id": "src1", "target_entity_id": "tgt1",
         "instance_id": "social_1", "keep_target_name": False},
    )

    d2 = _social_direct(monkeypatch, method_result={"success": True, "message": "ok"})
    asyncio.run(d2.extract_entity_info(AGENT, "ent9", {"entity_name": "Z"}, "replace"))
    assert d2._calls["method"] == (
        "extract",
        {"entity_id": "ent9", "instance_id": "social_1",
         "updates": {"entity_name": "Z"}, "update_mode": "replace"},
    )


def test_social_id_bounds_rejected_identically_on_both(monkeypatch):
    # The route enforces entity-id Field(min_length=1, max_length=128) as a 422;
    # both stores must mirror it so a local caller can't extract an empty-id
    # entity the cloud caller would 422 on (store.py parity invariant).
    d = _social_direct(monkeypatch, method_result={"success": True, "message": "x"})
    h = _social_http(monkeypatch, route_body={"success": True, "message": "x"})
    empty = {"success": False, "message": "entity id is empty"}
    assert asyncio.run(d.extract_entity_info(AGENT, "", {}, "merge")) == empty
    assert asyncio.run(h.extract_entity_info(AGENT, "", {}, "merge")) == empty  # rejected pre-send
    # DirectStore short-circuited before touching the module method.
    assert d._calls == {}

    long_id = "a" * 129
    long_err = {"success": False, "message": "entity id too long (max 128 chars)"}
    assert asyncio.run(d.merge_entities(AGENT, long_id, "t", True)) == long_err
    assert asyncio.run(h.merge_entities(AGENT, long_id, "t", True)) == long_err


def test_social_http_forwards_to_the_right_route_and_body(monkeypatch):
    # The cloud-only side: assert HttpStore POSTs to the correct endpoint with
    # the correct body. Catches a wrong path (/delete_entity vs the real
    # /delete-entity), a source/target swap, or a dropped keep_target_name/
    # update_mode — none of which the DirectStore forwarding test can reach.
    # One handler records every request (patching httpx per-call would chain the
    # MockTransport subclasses and the wrong one would win).
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.url.path, body))
        return httpx.Response(200, json={"success": True, "message": "ok"})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")

    asyncio.run(h.merge_entities(AGENT, "src1", "tgt1", False))
    asyncio.run(h.delete_entity(AGENT, "ent9"))
    asyncio.run(h.extract_entity_info(AGENT, "ent9", {"entity_name": "Z"}, "replace"))

    assert seen[0][0].endswith("/social-network/merge")
    assert seen[0][1] == {
        "source_entity_id": "src1", "target_entity_id": "tgt1", "keep_target_name": False,
    }
    assert seen[1][0].endswith("/social-network/delete-entity")
    assert seen[1][1] == {"entity_id": "ent9"}
    assert seen[2][0].endswith("/social-network/extract")
    assert seen[2][1] == {
        "entity_id": "ent9", "updates": {"entity_name": "Z"}, "update_mode": "replace",
    }


def test_social_direct_db_failure_stays_in_band(monkeypatch):
    # DirectStore invariant (module docstring): only ever return a dict. A local
    # db failure during instance resolution must degrade to the tool's message
    # shape, NOT escape as an exception — else the same fault is a message dict
    # on HttpStore ("backend unreachable") but a raised error on Direct.
    class BoomRepo:
        def __init__(self, db):
            pass

        async def get_by_agent(self, agent_id, module_class):
            raise RuntimeError("db locked")

    monkeypatch.setattr("narranexus.platform.repository.InstanceRepository", BoomRepo)
    store = DirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    # Both a write and a read must degrade in-band (reads share _social_module).
    out = asyncio.run(store.delete_entity(AGENT, "ent9"))
    assert out["success"] is False
    assert "message" in out and "error" not in out
    assert "db locked" in out["message"]

    read = asyncio.run(store.search_social_network(AGENT, "alice", "auto", 5))
    assert read["success"] is False
    assert "message" in read and "error" not in read
    assert "db locked" in read["message"]
    assert read["results"] == []


# ---------------------------------------------------------------------------
# social reads (PR-5): search / get_contact_info / get_agent_social_stats
# ---------------------------------------------------------------------------


def test_social_search_parity(monkeypatch):
    body = {"success": True, "search_type": "keyword", "results": [{"entity_id": "u1"}], "count": 1}
    d = _social_direct(monkeypatch, search_result=body)
    dr = asyncio.run(d.search_social_network(AGENT, "alice", "auto", 5))
    assert dr == body  # search_network's dict passes through raw
    h = _social_http(monkeypatch, route_body=body)
    hr = asyncio.run(h.search_social_network(AGENT, "alice", "auto", 5))
    assert hr == body == dr


def test_social_contact_parity(monkeypatch):
    # Both DirectStore and the route shape recall_entity_info via the SAME
    # format_contact_result — feed the http side the real function's output so
    # the equality is a genuine cross-path check.
    from narranexus_plugins.social_network_module import format_contact_result

    recall = {"success": True, "entity": {"entity_name": "Alice", "contact_info": {"email": "a@x.com"}}}
    expected = {"success": True, "entity_id": "u1", "entity_name": "Alice", "contact_info": {"email": "a@x.com"}}
    d = _social_direct(monkeypatch, recall_result=recall)
    dr = asyncio.run(d.get_contact_info(AGENT, "u1"))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=format_contact_result("u1", recall))
    hr = asyncio.run(h.get_contact_info(AGENT, "u1"))
    assert hr == expected == dr


def test_social_contact_not_found_parity(monkeypatch):
    from narranexus_plugins.social_network_module import format_contact_result

    recall = {"success": False, "message": "No information found for entity: u9"}
    expected = {"success": False, "message": "No information found for entity: u9"}
    d = _social_direct(monkeypatch, recall_result=recall)
    dr = asyncio.run(d.get_contact_info(AGENT, "u9"))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=format_contact_result("u9", recall))
    hr = asyncio.run(h.get_contact_info(AGENT, "u9"))
    assert hr == expected == dr


def test_social_stats_parity(monkeypatch):
    from narranexus_plugins.social_network_module import format_stats_result

    stats_list = [{"entity_name": "Bob", "interaction_count": 3}]
    expected = {"success": True, "sort_by": "recent", "count": 1, "results": stats_list}
    d = _social_direct(monkeypatch, stats_result=stats_list)
    dr = asyncio.run(d.get_agent_social_stats(AGENT, "recent", 5, None))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=format_stats_result("recent", stats_list))
    hr = asyncio.run(h.get_agent_social_stats(AGENT, "recent", 5, None))
    assert hr == expected == dr


def test_social_read_no_instance_parity(monkeypatch):
    from narranexus_plugins.social_network_module import social_instance_not_found_msg

    # search/stats no-instance carry results:[]; contact does NOT (matches tools).
    search_exp = {"success": False, "message": social_instance_not_found_msg(AGENT), "results": []}
    d = _social_direct(monkeypatch, has_instance=False)
    assert asyncio.run(d.search_social_network(AGENT, "x", "auto", 5)) == search_exp
    h = _social_http(monkeypatch, route_body=dict(search_exp))
    assert asyncio.run(h.search_social_network(AGENT, "x", "auto", 5)) == search_exp

    contact_exp = {"success": False, "message": social_instance_not_found_msg(AGENT)}
    d2 = _social_direct(monkeypatch, has_instance=False)
    assert asyncio.run(d2.get_contact_info(AGENT, "u1")) == contact_exp


def test_social_read_bounds_parity(monkeypatch):
    empty = {"success": False, "message": "search_keyword is empty", "results": []}
    d = _social_direct(monkeypatch, search_result={"success": True, "results": []})
    h = _social_http(monkeypatch, route_body={"success": True, "results": []})
    assert asyncio.run(d.search_social_network(AGENT, "", "auto", 5)) == empty
    assert asyncio.run(h.search_social_network(AGENT, "", "auto", 5)) == empty  # rejected pre-send
    # top_k=200 clamps to 100 on both; DirectStore records the kwargs it passed.
    asyncio.run(d.search_social_network(AGENT, "alice", "auto", 200))
    assert d._calls["method"] == (
        "search",
        {"search_keyword": "alice", "instance_id": "social_1", "search_type": "auto", "top_k": 100},
    )


def test_social_http_read_forwards_to_the_right_route_and_body(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.url.path, body))
        return httpx.Response(200, json={"success": True, "results": []})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")

    asyncio.run(h.search_social_network(AGENT, "alice", "auto", 5))
    asyncio.run(h.get_contact_info(AGENT, "u1"))
    asyncio.run(h.get_agent_social_stats(AGENT, "frequent", 10, ["expert:fe"]))

    assert seen[0][0].endswith("/social-network/recall")
    assert seen[0][1] == {"search_keyword": "alice", "search_type": "auto", "top_k": 5}
    assert seen[1][0].endswith("/social-network/contact")
    assert seen[1][1] == {"entity_id": "u1"}
    assert seen[2][0].endswith("/social-network/stats")
    assert seen[2][1] == {"sort_by": "frequent", "top_k": 10, "filter_tags": ["expert:fe"]}


# ---------------------------------------------------------------------------
# social create_agent (PR-6): provisioning through the seam
# ---------------------------------------------------------------------------


def _create_agent_direct(monkeypatch, *, caller_created_by="u1", caller_exists=True, warnings=None):
    """DirectStore with AgentRepository + provision_new_agent stubbed."""
    store = DirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]

    class FakeAgent:
        created_by = caller_created_by
        agent_name = "Creator"

    class FakeAgentRepo:
        def __init__(self, db):
            pass

        async def get_agent(self, agent_id):
            return FakeAgent() if caller_exists else None

    class FakeProvisionResult:
        def __init__(self):
            self.warnings = warnings or []

    async def fake_provision(db, *, agent_id, user_id, agent_name, agent_description, awareness):
        return FakeProvisionResult()

    monkeypatch.setattr("narranexus.platform.repository.AgentRepository", FakeAgentRepo)
    monkeypatch.setattr("narranexus.platform.bootstrap.provision.provision_new_agent", fake_provision)
    return store


def test_create_agent_success_parity(monkeypatch):
    from narranexus_plugins.social_network_module import format_create_agent_success

    expected = format_create_agent_success("Scout", "agent_new123", [])
    d = _create_agent_direct(monkeypatch)
    dr = asyncio.run(d.create_agent("agent_creator", "agent_new123", "Scout", "I am Scout", ""))
    assert dr == expected
    # The route builds the SAME dict via the shared format_create_agent_success
    # for the same (agent_name, new_agent_id) — parity holds because the id is an
    # input, not independently minted per path.
    h = _social_http(monkeypatch, route_body=format_create_agent_success("Scout", "agent_new123", []))
    hr = asyncio.run(h.create_agent("agent_creator", "agent_new123", "Scout", "I am Scout", ""))
    assert hr == expected == dr


def test_create_agent_warnings_surfaced_on_both(monkeypatch):
    from narranexus_plugins.social_network_module import format_create_agent_success

    expected = format_create_agent_success("Scout", "agent_n", ["instance_factory: boom"])
    assert expected["warnings"] == ["instance_factory: boom"]
    d = _create_agent_direct(monkeypatch, warnings=["instance_factory: boom"])
    dr = asyncio.run(d.create_agent("agent_creator", "agent_n", "Scout", "aw", ""))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected


def test_create_agent_no_owner_parity(monkeypatch):
    from narranexus_plugins.social_network_module import CREATE_AGENT_NO_OWNER_MSG

    expected = {"success": False, "message": CREATE_AGENT_NO_OWNER_MSG}
    d = _create_agent_direct(monkeypatch, caller_created_by=None)
    assert asyncio.run(d.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected
    # A caller that doesn't exist at all takes the same branch.
    d2 = _create_agent_direct(monkeypatch, caller_exists=False)
    assert asyncio.run(d2.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected
    # Route returns the same string under `error`; HttpStore restores `message`.
    h = _social_http(monkeypatch, route_body={"success": False, "error": CREATE_AGENT_NO_OWNER_MSG})
    assert asyncio.run(h.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected


def test_create_agent_http_forwards_the_minted_id_and_fields(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, _json.loads(request.content)))
        return httpx.Response(200, json={"success": True, "new_agent_id": "x", "agent_name": "Scout", "message": "ok"})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.create_agent("agent_creator", "agent_new123", "Scout", "I am Scout", "desc"))
    assert seen[0][0].endswith("/social-network/create-agent")
    assert seen[0][1] == {
        "new_agent_id": "agent_new123", "agent_name": "Scout",
        "awareness": "I am Scout", "agent_description": "desc",
    }


# ---------------------------------------------------------------------------
# basic_info narrative/event (PR-7): view_narrative / view_event / switch
# ---------------------------------------------------------------------------


class _FakeNarrDb:
    """Minimal AsyncDatabaseClient stand-in for the _narrative_reads helpers."""

    def __init__(self, *, narratives=None, events=None, links=None, memories=None):
        self._narratives = narratives or {}   # narrative_id -> row
        self._events = events or {}            # event_id -> row
        self._links = links or []              # instance_narrative_links rows
        self._memories = memories or {}        # instance_id -> memory row

    async def get_one(self, table, filters):
        if table == "narratives":
            return self._narratives.get(filters["narrative_id"])
        if table == "events":
            row = self._events.get(filters["event_id"])
            if row and row.get("agent_id") != filters.get("agent_id"):
                return None
            return row
        raise AssertionError(table)

    async def get(self, table, filters, limit=None, order_by=None):
        assert table == "instance_narrative_links"
        return [r for r in self._links if r["narrative_id"] == filters["narrative_id"]]

    async def get_by_ids(self, table, col, ids):
        assert table == "instance_json_format_memory_chat"
        # Real contract: order-preserving, MISSING ids padded with None.
        return [self._memories.get(i) for i in ids]


def _basic_direct(monkeypatch, db):
    store = DirectStore()

    async def fake_db():
        return db

    store._db = fake_db  # type: ignore[method-assign]
    return store


def test_view_narrative_parity(monkeypatch):
    from narranexus_plugins.basic_info_module._narrative_reads import fetch_narrative_view

    db = _FakeNarrDb(
        narratives={"nar_1": {"agent_id": AGENT, "narrative_info": {"name": "Trip", "description": "d", "current_summary": "s"}, "topic_keywords": ["k"]}},
        links=[{"narrative_id": "nar_1", "instance_id": "chat_a", "created_at": "2026-01-01"}],
        memories={"chat_a": {"instance_id": "chat_a", "memory": {"messages": [{"role": "user", "content": "hi", "meta_data": {"timestamp": "2026-01-01T00:00:00", "event_id": "evt_1"}}]}}},
    )
    expected = asyncio.run(fetch_narrative_view(db, AGENT, "nar_1"))
    assert expected["success"] is True and expected["name"] == "Trip" and expected["message_count"] == 1

    d = _basic_direct(monkeypatch, db)
    dr = asyncio.run(d.view_narrative(AGENT, "nar_1"))
    assert dr == expected
    # the route returns the SAME fetch_narrative_view dict → http passes it through
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.view_narrative(AGENT, "nar_1")) == expected == dr


def test_view_narrative_cross_tenant_is_not_found_on_both(monkeypatch):
    # The old raw-SQL tool returned ANY agent's narrative by id; the seam scopes
    # to the caller. A narrative owned by someone else reads as not-found.
    db = _FakeNarrDb(narratives={"nar_1": {"agent_id": "other_agent", "narrative_info": {}, "topic_keywords": []}})
    expected = {"success": False, "error": "narrative nar_1 not found"}
    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.view_narrative(AGENT, "nar_1")) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.view_narrative(AGENT, "nar_1")) == expected


def test_view_event_parity_and_agent_scoped(monkeypatch):
    from narranexus_plugins.basic_info_module._narrative_reads import fetch_event_view

    db = _FakeNarrDb(events={"evt_1": {"agent_id": AGENT, "narrative_id": "nar_1", "trigger": "manual", "trigger_source": "user", "env_context": {"input": "go"}, "final_output": "done", "event_log": "log", "created_at": "2026-01-01T00:00:00"}})
    expected = asyncio.run(fetch_event_view(db, AGENT, "evt_1"))
    # `time` is rendered in the agent owner's timezone with an explicit UTC
    # offset (2026-08-18) — same frame as the chat-history timeline, which
    # tells the agent to call view_event for this very event. This fake has no
    # `agents` table, so the owner lookup falls back to UTC and the offset
    # reads +00:00; the SHAPE is the contract, not the zone.
    # See tests/basic_info_module/test_narrative_reads_time_frame.py.
    assert expected == {"success": True, "event_id": "evt_1", "narrative_id": "nar_1", "trigger": "manual", "trigger_source": "user", "time": "2026-01-01 00:00 +00:00", "input": "go", "final_output": "done", "event_log": "log"}
    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.view_event(AGENT, "evt_1")) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.view_event(AGENT, "evt_1")) == expected
    # wrong agent → not found (get_one filters agent_id)
    assert asyncio.run(d.view_event("other", "evt_1")) == {"success": False, "error": "event evt_1 not found"}


def test_switch_narrative_parity(monkeypatch):
    db = _FakeNarrDb(narratives={"nar_1": {"agent_id": AGENT}})
    ok = {"success": True, "narrative_id": "nar_1", "message": "This turn will be attributed to this narrative."}
    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.switch_narrative(AGENT, "nar_1")) == ok
    h = _social_http(monkeypatch, route_body=ok)
    assert asyncio.run(h.switch_narrative(AGENT, "nar_1")) == ok
    # cross-tenant / missing → not found on both
    nf = {"success": False, "error": "narrative nar_x not found"}
    assert asyncio.run(d.switch_narrative(AGENT, "nar_x")) == nf


def test_basic_info_http_forwards_to_the_right_routes(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.view_narrative(AGENT, "nar_1"))
    asyncio.run(h.view_event(AGENT, "evt_1"))
    asyncio.run(h.switch_narrative(AGENT, "nar_1"))
    assert seen[0] == ("GET", f"/api/agents/{AGENT}/narratives/nar_1", None)
    assert seen[1] == ("GET", f"/api/agents/{AGENT}/events/evt_1", None)
    assert seen[2] == ("POST", f"/api/agents/{AGENT}/narratives/nar_1/switch", {})


def test_view_narrative_skips_memoryless_chat_instance(monkeypatch):
    # A chat_ instance can be LINKED (step_1) before its memory row is written
    # (step_5) — get_by_ids pads the missing row with None. The read must skip
    # it, not crash. Regression guard for the get_by_ids-None-contract bug.
    db = _FakeNarrDb(
        narratives={"nar_1": {"agent_id": AGENT, "narrative_info": {"name": "T"}, "topic_keywords": []}},
        links=[
            {"narrative_id": "nar_1", "instance_id": "chat_has_mem", "created_at": "1"},
            {"narrative_id": "nar_1", "instance_id": "chat_no_mem", "created_at": "2"},  # never got a memory row
        ],
        memories={"chat_has_mem": {"instance_id": "chat_has_mem", "memory": {"messages": [
            {"role": "user", "content": "hi", "meta_data": {"timestamp": "2026-01-01T00:00:00", "event_id": "e"}},
        ]}}},
    )
    d = _basic_direct(monkeypatch, db)
    out = asyncio.run(d.view_narrative(AGENT, "nar_1"))
    assert out["success"] is True
    assert out["message_count"] == 1  # chat_no_mem skipped, not a NoneType crash


# ---------------------------------------------------------------------------
# job reads (PR-8): job_retrieval_by_id / _semantic / _by_keywords
# ---------------------------------------------------------------------------


class _V:  # tiny .value holder for job_type / status
    def __init__(self, v):
        self.value = v


class _FakeJob:
    def __init__(self, job_id="job_1", agent_id=AGENT, description="D"):
        self.job_id = job_id
        self.agent_id = agent_id
        self.user_id = "u1"
        self.instance_id = "job_inst"
        self.title = "T"
        self.description = description
        self.payload = {}
        self.job_type = _V("one_off")
        self.trigger_config = None
        self.status = _V("active")
        self.notification_method = "none"
        self.next_run_at_local = None
        self.next_run_tz = "UTC"
        self.last_run_at_local = None
        self.last_run_tz = "UTC"
        self.related_entity_id = None
        self.narrative_id = None
        self.iteration_count = 0
        self.process = "proc"
        self.last_error = None
        self.created_at = None
        self.updated_at = None


def _patch_job_repo(monkeypatch, *, job=None, search_hits=None, keyword_hits=None):
    class _FakeJobRepo:
        def __init__(self, db):
            pass

        async def get_job(self, job_id):
            return job if (job and job.job_id == job_id) else None

        async def search_keyword(self, agent_id, query, user_id, status, limit):
            return search_hits or []

        async def search_by_keywords(self, agent_id, keywords, user_id, status, limit):
            return keyword_hits or []

    monkeypatch.setattr("narranexus_plugins.job_module._job_reads.JobRepository", _FakeJobRepo)


def test_job_by_id_parity(monkeypatch):
    from narranexus_plugins.job_module import fetch_job_by_id

    job = _FakeJob()
    _patch_job_repo(monkeypatch, job=job)
    db = object()
    expected = asyncio.run(fetch_job_by_id(db, AGENT, "job_1"))
    assert expected["success"] is True and expected["job"]["job_id"] == "job_1"
    assert expected["job"]["process"] == "proc"  # by_id-only extra field

    d = _basic_direct(monkeypatch, db)
    dr = asyncio.run(d.job_retrieval_by_id(AGENT, "job_1"))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_by_id(AGENT, "job_1")) == expected == dr


def test_job_by_id_cross_agent_is_access_denied(monkeypatch):
    job = _FakeJob(agent_id="other_agent")
    _patch_job_repo(monkeypatch, job=job)
    d = _basic_direct(monkeypatch, object())
    out = asyncio.run(d.job_retrieval_by_id(AGENT, "job_1"))
    assert out == {"success": False, "error": "Access denied: Job belongs to a different agent"}


def test_job_by_id_not_found(monkeypatch):
    _patch_job_repo(monkeypatch, job=None)
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_retrieval_by_id(AGENT, "job_x")) == {"success": False, "error": "Job not found: job_x"}


def test_job_semantic_parity_and_limit_clamp(monkeypatch):
    from narranexus_plugins.job_module import search_jobs_semantic

    job = _FakeJob()
    _patch_job_repo(monkeypatch, search_hits=[(job, 0.9)])
    db = object()
    expected = asyncio.run(search_jobs_semantic(db, AGENT, "news", None, None, 100))
    assert expected["success"] is True
    assert expected["jobs"][0]["similarity_score"] == 0.9

    d = _basic_direct(monkeypatch, db)
    # limit=500 must clamp to 100 (route le=100) on both paths
    dr = asyncio.run(d.job_retrieval_semantic(AGENT, "news", None, None, 500))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "news", None, None, 500)) == expected == dr


def test_job_semantic_invalid_status_parity(monkeypatch):
    _patch_job_repo(monkeypatch, search_hits=[])
    from narranexus.platform.schema.job_schema import JobStatus
    _valid = ", ".join(sv.value for sv in JobStatus)
    expected = {"success": False, "error": f"Invalid status: bogus. Valid values: {_valid}"}
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_retrieval_semantic(AGENT, "q", None, "bogus", 10)) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "q", None, "bogus", 10)) == expected


def test_job_by_keywords_parity_and_truncation(monkeypatch):
    from narranexus_plugins.job_module import search_jobs_by_keywords

    long_desc = "x" * 250
    job = _FakeJob(description=long_desc)
    _patch_job_repo(monkeypatch, keyword_hits=[job])
    db = object()
    expected = asyncio.run(search_jobs_by_keywords(db, AGENT, ["news"], None, None, 20))
    assert expected["jobs"][0]["description"].endswith("...")  # >200 truncated
    assert len(expected["jobs"][0]["description"]) == 203

    d = _basic_direct(monkeypatch, db)
    dr = asyncio.run(d.job_retrieval_by_keywords(AGENT, ["news"], None, None, 20))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_by_keywords(AGENT, ["news"], None, None, 20)) == expected == dr


def test_job_http_forwards_to_the_right_routes(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.job_retrieval_by_id(AGENT, "job_1"))
    asyncio.run(h.job_retrieval_semantic(AGENT, "news", "u2", "active", 5))
    asyncio.run(h.job_retrieval_by_keywords(AGENT, ["a", "b"], None, None, 7))
    assert seen[0] == ("GET", f"/api/agents/{AGENT}/jobs/job_1", None)
    assert seen[1] == ("POST", f"/api/agents/{AGENT}/jobs/search-semantic",
                       {"query": "news", "user_id": "u2", "status": "active", "limit": 5})
    assert seen[2] == ("POST", f"/api/agents/{AGENT}/jobs/search-keywords",
                       {"keywords": ["a", "b"], "user_id": None, "status": None, "limit": 7})


def test_job_search_input_bounds_rejected_identically_on_both(monkeypatch):
    # Empty/over-long query and empty keywords must be rejected the SAME on both
    # stores — the route enforces them as 422, so DirectStore must pre-reject too
    # (else a local search succeeds on an input the cloud path 422s on).
    _patch_job_repo(monkeypatch, search_hits=[], keyword_hits=[])
    d = _basic_direct(monkeypatch, object())
    h = _social_http(monkeypatch, route_body={"success": True})

    empty_q = {"success": False, "error": "query is empty"}
    assert asyncio.run(d.job_retrieval_semantic(AGENT, "", None, None, 10)) == empty_q
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "", None, None, 10)) == empty_q  # rejected pre-send

    long_q = {"success": False, "error": "query too long (max 512 chars)"}
    assert asyncio.run(d.job_retrieval_semantic(AGENT, "a" * 513, None, None, 10)) == long_q
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "a" * 513, None, None, 10)) == long_q

    empty_kw = {"success": False, "error": "keywords is empty"}
    assert asyncio.run(d.job_retrieval_by_keywords(AGENT, [], None, None, 20)) == empty_kw
    assert asyncio.run(h.job_retrieval_by_keywords(AGENT, [], None, None, 20)) == empty_kw


def test_httpstore_percent_encodes_llm_supplied_path_ids(monkeypatch):
    # _seg guard: an id with '/' / '?' must be percent-encoded in the URL path
    # segment (else it retargets the request / diverges from DirectStore).
    import json as _json  # noqa: F401 (kept for symmetry with other handlers)

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        # raw_path is the wire-format (encoded) path; .path would decode it back.
        seen.append(request.url.raw_path.decode("ascii"))
        return httpx.Response(200, json={"success": False, "error": "not found"})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.job_retrieval_by_id(AGENT, "a/b?x"))
    asyncio.run(h.view_event(AGENT, "e/1"))
    asyncio.run(h.view_narrative(AGENT, "n/1"))
    assert seen[0] == f"/api/agents/{AGENT}/jobs/a%2Fb%3Fx"
    assert seen[1] == f"/api/agents/{AGENT}/events/e%2F1"
    assert seen[2] == f"/api/agents/{AGENT}/narratives/n%2F1"


# ---------------------------------------------------------------------------
# job_update (PR-8b): the write, via shared update_job_from_args
# ---------------------------------------------------------------------------


def _upd_fields(**kw):
    base = {"title": None, "description": None, "payload": None, "guidance_text": None,
            "trigger_config": None, "job_type": None, "next_run_time": None,
            "status": None, "related_entity_id": None}
    base.update(kw)
    return base


def _patch_job_writes(monkeypatch, *, job=None, update_result=None, capture=None):
    """Fake the two DB collaborators of update_job_from_args. Pass a `capture`
    dict to record the `updates` the shared build-updates logic hands to
    JobInstanceService.update_job (for asserting WHAT it computed, not just the
    return shape); pass `update_result` to pin the returned dict, else a default
    success dict derived from `updates` is returned."""
    class _FakeJobRepo:
        def __init__(self, db):
            pass

        async def get_job(self, job_id):
            return job if (job and job.job_id == job_id) else None

    class _FakeService:
        def __init__(self, db):
            pass

        async def update_job(self, job_id, updates, agent_id):
            if capture is not None:
                capture["updates"] = updates
            if update_result is not None:
                return update_result
            return {"success": True, "job_id": job_id,
                    "updated_fields": list(updates.keys()), "message": "Updated"}

    monkeypatch.setattr("narranexus_plugins.job_module._job_writes.JobRepository", _FakeJobRepo)
    monkeypatch.setattr("narranexus_plugins.job_module.job_service.JobInstanceService", _FakeService)


def test_job_update_parity(monkeypatch):
    from narranexus_plugins.job_module import update_job_from_args

    result = {"success": True, "job_id": "job_1", "updated_fields": ["title"], "message": "Updated"}
    _patch_job_writes(monkeypatch, job=_FakeJob(), update_result=result)
    db = object()
    fields = _upd_fields(title="New")
    expected = asyncio.run(update_job_from_args(db, AGENT, "job_1", **fields))
    assert expected == result

    d = _basic_direct(monkeypatch, db)
    dr = asyncio.run(d.job_update(AGENT, "job_1", fields))
    assert dr == result
    h = _social_http(monkeypatch, route_body=result)
    assert asyncio.run(h.job_update(AGENT, "job_1", fields)) == result == dr


def test_job_update_cross_agent_is_not_found(monkeypatch):
    # A job owned by a different agent reads as "not found" — no existence
    # oracle (the old tool leaked "does not belong to agent X").
    _patch_job_writes(monkeypatch, job=_FakeJob(agent_id="other_agent"))
    d = _basic_direct(monkeypatch, object())
    out = asyncio.run(d.job_update(AGENT, "job_1", _upd_fields(title="x")))
    assert out == {"success": False, "job_id": "job_1", "message": "Job job_1 not found"}


def test_job_update_invalid_status_parity(monkeypatch):
    _patch_job_writes(monkeypatch, job=_FakeJob())
    expected = {"success": False, "job_id": "job_1",
                "message": "Invalid status: bogus. Valid: active, paused, cancelled"}
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_update(AGENT, "job_1", _upd_fields(status="bogus"))) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_update(AGENT, "job_1", _upd_fields(status="bogus"))) == expected


def test_job_update_no_fields_parity(monkeypatch):
    _patch_job_writes(monkeypatch, job=_FakeJob())
    expected = {"success": False, "job_id": "job_1", "message": "No fields to update"}
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_update(AGENT, "job_1", _upd_fields())) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_update(AGENT, "job_1", _upd_fields())) == expected


def test_job_update_http_forwards_to_the_right_route(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode("ascii"), _json.loads(request.content)))
        return httpx.Response(200, json={"success": True, "job_id": "job_1", "updated_fields": [], "message": "ok"})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    fields = _upd_fields(title="New", status="paused")
    asyncio.run(h.job_update(AGENT, "job_1", fields))
    assert seen[0][0] == "POST"
    assert seen[0][1] == f"/api/agents/{AGENT}/jobs/job_1/update"
    assert seen[0][2] == fields


def test_job_update_one_off_to_scheduled_recomputes_next_run(monkeypatch):
    # The load-bearing invariant this whole refactor protects: `effective_type`
    # must be resolved BEFORE compute_next_run. A one_off job switched to
    # scheduled with a cron MUST get a non-None next_run_time — if the ordering
    # regressed, compute_next_run would see the OLD one_off type, return None,
    # and silently zombify the job (pre-open review #4). This asserts the
    # computed `updates` directly so a reordering ships red, not green.
    from narranexus_plugins.job_module import update_job_from_args
    from narranexus.platform.schema import JobType

    job = _FakeJob()  # job_type == one_off, trigger_config is None
    captured: dict = {}
    _patch_job_writes(monkeypatch, job=job, capture=captured)

    fields = _upd_fields(job_type="scheduled", trigger_config={"cron": "0 8 * * *", "timezone": "UTC"})
    out = asyncio.run(update_job_from_args(object(), AGENT, "job_1", **fields))
    assert out["success"] is True

    updates = captured["updates"]
    assert updates["job_type"] == JobType.SCHEDULED          # new type won
    assert updates["next_run_time"] is not None              # computed with the NEW type, not one_off→None
    assert updates["next_run_at_local"] is not None
    assert updates["next_run_tz"] == "UTC"


# ---------------------------------------------------------------------------
# job_create / job_pause / job_cancel: the writes, via the shared helpers
# ---------------------------------------------------------------------------


def _create_fields(**kw):
    base = {"user_id": "u1", "title": "T", "description": "d", "job_type": "one_off",
            "trigger_config": {"run_at": "2026-09-01T09:00:00", "timezone": "UTC"},
            "payload": "p", "notification_method": "direct", "task_key": None,
            "depends_on_job_ids": None, "related_entity_id": None, "narrative_id": None,
            "confirm_new": False}
    base.update(kw)
    return base


def _patch_job_create(monkeypatch, *, result, capture=None):
    """Fake create_job_from_args' two collaborators: setup_mcp_llm_context (the
    owner LLM-context load, patched no-op) and JobInstanceService, so the parity
    tests don't need a DB or live LLM config."""
    import narranexus.platform.agent_framework.api_config as _api

    async def _noop(agent_id):
        return None

    monkeypatch.setattr(_api, "setup_mcp_llm_context", _noop)

    class _FakeService:
        def __init__(self, db):
            pass

        async def create_job_with_instance(self, **kw):
            if capture is not None:
                capture.update(kw)
            return result

    monkeypatch.setattr("narranexus_plugins.job_module.job_service.JobInstanceService", _FakeService)


def _patch_job_pause_cancel(monkeypatch, *, job, rows=1):
    class _FakeJobRepo:
        def __init__(self, db):
            pass

        async def get_job(self, job_id):
            return job if (job and job.job_id == job_id) else None

        async def pause_job(self, job_id):
            return rows

        async def cancel_job(self, job_id):
            return rows

    monkeypatch.setattr("narranexus_plugins.job_module._job_writes.JobRepository", _FakeJobRepo)


def test_job_create_parity(monkeypatch):
    result = {"success": True, "job_id": "job_1", "instance_id": "job_abc", "message": "Created"}
    _patch_job_create(monkeypatch, result=result)
    d = _basic_direct(monkeypatch, object())
    dr = asyncio.run(d.job_create(AGENT, _create_fields()))
    assert dr == result
    # The route returns create_job_from_args' dict verbatim (a success body is
    # untouched), so HttpStore matches DirectStore byte-for-byte.
    h = _social_http(monkeypatch, route_body=result)
    assert asyncio.run(h.job_create(AGENT, _create_fields())) == result == dr


def test_job_create_applies_task_key_on_success(monkeypatch):
    # task_key is stamped onto a successful result by the shared helper, not the
    # service — so it must appear on the DirectStore path.
    _patch_job_create(monkeypatch, result={"success": True, "job_id": "job_1"})
    d = _basic_direct(monkeypatch, object())
    dr = asyncio.run(d.job_create(AGENT, _create_fields(task_key="tk1")))
    assert dr["task_key"] == "tk1"


def test_job_pause_parity(monkeypatch):
    _patch_job_pause_cancel(monkeypatch, job=_FakeJob())
    expected = {"success": True, "job_id": "job_1", "status": "paused",
                "message": "Job paused successfully"}
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_pause(AGENT, "job_1")) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_pause(AGENT, "job_1")) == expected


def test_job_pause_cross_agent_is_not_found(monkeypatch):
    # No existence oracle — a job owned by another agent reads as "not found"
    # (matches job_update's posture; safer than the old "does not belong").
    _patch_job_pause_cancel(monkeypatch, job=_FakeJob(agent_id="other_agent"))
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_pause(AGENT, "job_1")) == {
        "success": False, "job_id": "job_1", "message": "Job job_1 not found"}


def test_job_cancel_parity(monkeypatch):
    # related_entity_id is None (the _FakeJob default) so the best-effort social
    # unlink is skipped — the cancel result shape is what parity asserts.
    _patch_job_pause_cancel(monkeypatch, job=_FakeJob())
    expected = {"success": True, "job_id": "job_1", "status": "cancelled",
                "message": "Job cancelled successfully"}
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_cancel(AGENT, "job_1")) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_cancel(AGENT, "job_1")) == expected


def test_job_write_http_forwards_to_the_right_routes(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"success": True, "message": "ok"})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.job_create(AGENT, _create_fields(title="X")))
    asyncio.run(h.job_pause(AGENT, "job_1"))
    asyncio.run(h.job_cancel(AGENT, "job_1"))
    assert seen[0][0:2] == ("POST", f"/api/agents/{AGENT}/jobs")
    assert seen[0][2]["title"] == "X"
    assert seen[1] == ("PUT", f"/api/agents/{AGENT}/jobs/job_1/pause", None)
    assert seen[2] == ("PUT", f"/api/agents/{AGENT}/jobs/job_1/cancel", None)


# --------------------------------------------------------------------------- update_agent_profile


def test_update_agent_profile_direct_delegates_to_shared_fn(monkeypatch):
    # DirectStore forwards to update_agent_profile_from_args and returns its
    # string verbatim (the shared fn's rename-transaction logic is covered
    # exhaustively in test_agent_profile_tool.py; here we pin the delegation).
    seen = {}

    async def fake_fn(db, agent_id, *, new_name, new_description):
        seen["args"] = (agent_id, new_name, new_description)
        return "Profile updated successfully (agent_name)."

    monkeypatch.setattr(
        "narranexus_plugins.awareness_module.update_agent_profile_from_args", fake_fn
    )
    d = _basic_direct(monkeypatch, object())
    out = asyncio.run(d.update_agent_profile(AGENT, "New", None))
    assert out == "Profile updated successfully (agent_name)."
    assert seen["args"] == (AGENT, "New", None)


def test_update_agent_profile_http_forwards_and_returns_message(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode("ascii"), _json.loads(request.content)))
        return httpx.Response(200, json={"message": "Profile updated successfully (agent_description)."})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    out = asyncio.run(h.update_agent_profile(AGENT, None, "a helpful reviewer"))
    assert out == "Profile updated successfully (agent_description)."
    assert seen[0][0] == "POST"
    assert seen[0][1] == f"/api/agents/{AGENT}/profile/update"
    assert seen[0][2] == {"new_name": None, "new_description": "a helpful reviewer"}


def test_update_agent_profile_http_unreachable_degrades(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    h = HttpStore("http://backend:8000")
    assert asyncio.run(h.update_agent_profile(AGENT, "X", None)) == "Error: profile backend unreachable"


def test_update_agent_profile_http_rejection_degrades(monkeypatch):
    # A >=400 comes from BEFORE the handler (the handler always answers 200) —
    # e.g. the identity gate. Must be an in-band string, never an exception.
    def reject(request):
        return httpx.Response(401, text="nope")

    _patch_http(monkeypatch, reject)
    h = HttpStore("http://backend:8000")
    assert asyncio.run(h.update_agent_profile(AGENT, "X", None)) == "Error: profile backend rejected the call (401)"


def test_update_agent_profile_http_no_message_key_is_an_error_string(monkeypatch):
    # A 2xx body missing "message" must degrade to a string, never surface a dict
    # (DirectStore only ever returns strings — parity).
    def handler(request):
        return httpx.Response(200, json={"unexpected": True})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    out = asyncio.run(h.update_agent_profile(AGENT, "X", None))
    assert isinstance(out, str) and out.startswith("Error:")


def test_update_agent_profile_http_non_json_degrades(monkeypatch):
    # A 2xx body that isn't JSON must degrade to a string, never raise.
    def handler(request):
        return httpx.Response(200, text="not json at all")

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    out = asyncio.run(h.update_agent_profile(AGENT, "X", None))
    assert out == "Error: profile backend returned a non-JSON response"


# --------------------------------------------------------------------------- get_chat_history


class _FakeChatDb:
    """Fake db for fetch_chat_history: get_one dispatches by table. `instances`
    maps instance_id -> owning agent_id; `memories` maps instance_id -> memory
    JSON string (or a non-JSON string to exercise the parse-error path)."""
    def __init__(self, instances: dict, memories: dict):
        self._inst = instances
        self._mem = memories

    async def get_one(self, table, filters):
        iid = filters["instance_id"]
        if table == "module_instances":
            aid = self._inst.get(iid)
            return {"instance_id": iid, "agent_id": aid} if aid is not None else None
        if table == "instance_json_format_memory_chat":
            if iid not in self._mem:
                return None
            return {"instance_id": iid, "memory": self._mem[iid]}
        return None


def _chat_memory(*msgs):
    import json as _json
    return _json.dumps({"messages": list(msgs)})


def test_get_chat_history_parity(monkeypatch):
    from narranexus_plugins.chat_module import fetch_chat_history

    db = _FakeChatDb(
        {"chat_1": AGENT},
        {"chat_1": _chat_memory(
            {"role": "user", "content": "hi", "meta_data": {"timestamp": "t1", "event_id": "e1"}},
            {"role": "assistant", "content": "hello"},
        )},
    )
    expected = asyncio.run(fetch_chat_history(db, AGENT, "chat_1", 20))
    assert expected["success"] is True and expected["total_messages"] == 2
    assert expected["messages"][0]["timestamp"] == "t1" and expected["messages"][0]["event_id"] == "e1"

    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.get_chat_history(AGENT, "chat_1", 20)) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.get_chat_history(AGENT, "chat_1", 20)) == expected


def test_get_chat_history_foreign_instance_is_empty_no_oracle(monkeypatch):
    # chat_1 belongs to other_agent; AGENT asking for it must get EMPTY history,
    # indistinguishable from an own instance with no messages — closes the IDOR.
    # Non-vacuous: without the instance-scope check fetch would return the secret.
    from narranexus_plugins.chat_module import fetch_chat_history

    db = _FakeChatDb(
        {"chat_1": "other_agent"},
        {"chat_1": _chat_memory({"role": "user", "content": "SECRET"})},
    )
    out = asyncio.run(fetch_chat_history(db, AGENT, "chat_1", 20))
    assert out["success"] is True and out["total_messages"] == 0 and out["messages"] == []
    assert "SECRET" not in str(out)
    # parity: DirectStore and the (echoing) route agree on the same empty shape
    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.get_chat_history(AGENT, "chat_1", 20)) == out


def test_get_chat_history_unknown_instance_is_empty(monkeypatch):
    from narranexus_plugins.chat_module import fetch_chat_history
    db = _FakeChatDb({}, {})
    out = asyncio.run(fetch_chat_history(db, AGENT, "chat_ghost", 20))
    assert out["success"] is True and out["messages"] == []


def test_get_chat_history_limit_tail_and_all(monkeypatch):
    from narranexus_plugins.chat_module import fetch_chat_history
    msgs = [{"role": "user", "content": str(i)} for i in range(10)]
    db = _FakeChatDb({"chat_1": AGENT}, {"chat_1": _chat_memory(*msgs)})
    out5 = asyncio.run(fetch_chat_history(db, AGENT, "chat_1", 5))
    assert out5["total_messages"] == 10 and len(out5["messages"]) == 5
    assert [m["content"] for m in out5["messages"]] == ["5", "6", "7", "8", "9"]
    out_all = asyncio.run(fetch_chat_history(db, AGENT, "chat_1", -1))
    assert len(out_all["messages"]) == 10
    out_zero = asyncio.run(fetch_chat_history(db, AGENT, "chat_1", 0))
    assert len(out_zero["messages"]) == 10  # limit<=0 means "all"


def test_get_chat_history_bad_json_is_a_format_error(monkeypatch):
    from narranexus_plugins.chat_module import fetch_chat_history
    db = _FakeChatDb({"chat_1": AGENT}, {"chat_1": "{not valid json"})
    out = asyncio.run(fetch_chat_history(db, AGENT, "chat_1", 20))
    assert out["success"] is False and "format error" in out["error"]


def test_get_chat_history_http_forwards_to_the_right_route(monkeypatch):
    import json as _json
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode("ascii"), _json.loads(request.content)))
        return httpx.Response(200, json={"success": True, "instance_id": "chat_1",
                                         "total_messages": 0, "messages": []})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.get_chat_history(AGENT, "chat_1", 7))
    assert seen[0][0] == "POST"
    assert seen[0][1] == f"/api/agents/{AGENT}/chat-history/by-instance"
    assert seen[0][2] == {"instance_id": "chat_1", "limit": 7}


def test_get_chat_history_http_unreachable_degrades(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")
    _patch_http(monkeypatch, boom)
    h = HttpStore("http://backend:8000")
    out = asyncio.run(h.get_chat_history(AGENT, "chat_1", 20))
    assert out["success"] is False and out["messages"] == [] and out["instance_id"] == "chat_1"


def test_get_chat_history_direct_db_failure_returns_dict_not_raise(monkeypatch):
    # DirectStore.get_chat_history must keep the "never raises" invariant: if
    # _db() (lazy MySQL pool build) raises, it returns the tool's failure dict,
    # matching what HttpStore/route degrade to. Non-vacuous — without the outer
    # try this raises RuntimeError instead of returning a dict.
    d = DirectStore()

    async def boom():
        raise RuntimeError("pool build failed")

    d._db = boom  # type: ignore[method-assign]
    out = asyncio.run(d.get_chat_history(AGENT, "chat_1", 20))
    assert out["success"] is False and out["messages"] == [] and out["total_messages"] == 0
    assert out["instance_id"] == "chat_1" and "pool build failed" in out["error"]


# --------------------------------------------------------------------------- grep_memory


def test_grep_memory_parity(monkeypatch):
    hits = [
        _Hit.make("chat", "line with order-123", tags=["ref"]),
        _Hit.make("job", "grep me", source_ref={"kind": "job", "id": "job_9"}),
    ]
    expected = {
        "success": True, "pattern": "order-123",
        "matches": [
            {"kind": "chat", "memory": "line with order-123", "when": None, "tags": ["ref"]},
            {"kind": "job", "memory": "grep me", "when": None, "tags": [],
             "source": {"kind": "job", "id": "job_9"}},
        ],
        "truncated": False,
    }
    d = _memory_direct(monkeypatch, grep_hits=hits)
    assert asyncio.run(d.grep_memory("agent_a", "order-123", False, 30)) == expected
    h = _memory_http(monkeypatch, grep_body=hits)
    assert asyncio.run(h.grep_memory("agent_a", "order-123", False, 30)) == expected


def test_grep_memory_limit_clamped_to_200_on_both(monkeypatch):
    d = _memory_direct(monkeypatch, grep_hits=[])
    asyncio.run(d.grep_memory("agent_a", "x", False, 9999))
    assert d._seen["grep_limit"] == 200  # grep's cap, NOT remember's 100

    seen: dict = {}
    h = _memory_http(monkeypatch, grep_body=[], seen=seen)
    asyncio.run(h.grep_memory("agent_a", "x", False, 9999))
    assert seen["limit"] == "200"


def test_grep_memory_empty_and_overlong_pattern_rejected_on_both(monkeypatch):
    empty = {"success": False, "error": "pattern is empty", "matches": []}
    toolong = {"success": False, "error": "pattern too long (max 256 chars)", "matches": []}
    d = _memory_direct(monkeypatch, grep_hits=[])
    assert asyncio.run(d.grep_memory("agent_a", "", False, 30)) == empty
    assert asyncio.run(d.grep_memory("agent_a", "x" * 257, False, 30)) == toolong
    h = _memory_http(monkeypatch, grep_body=[])
    # HttpStore pre-rejects locally (never hits the route), so it matches Direct.
    assert asyncio.run(h.grep_memory("agent_a", "", False, 30)) == empty
    assert asyncio.run(h.grep_memory("agent_a", "x" * 257, False, 30)) == toolong


def test_grep_memory_regex_flag_forwarded_lowercase(monkeypatch):
    seen: dict = {}
    h = _memory_http(monkeypatch, grep_body=[], seen=seen)
    asyncio.run(h.grep_memory("agent_a", "x", True, 30))
    assert seen["grep_regex"] == "true"  # FastAPI bool parser wants lowercase


def test_grep_memory_http_transport_failure_degrades_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    h = HttpStore("http://backend:8000")
    out = asyncio.run(h.grep_memory("agent_a", "x", False, 30))
    assert out["success"] is False and out["matches"] == []


def test_grep_memory_truncated_flag_propagates(monkeypatch):
    # When the engine reports a bounded-scan giveup (deadline/timeout), the seam
    # must surface truncated=True so the LLM doesn't read a partial result as a
    # complete "no match". Non-vacuous — without threading the flag it'd be False.
    d = _memory_direct(monkeypatch, grep_hits=[], grep_truncated=True)
    out = asyncio.run(d.grep_memory("agent_a", "x", True, 30))
    assert out["success"] is True and out["truncated"] is True
