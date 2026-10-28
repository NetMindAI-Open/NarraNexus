"""
@file_name: test_channel_store.py
@author:
@date: 2026-08-10
@description: ChannelCredentialStore seam — REAL parity between DirectStore
and HttpStore (PR-A, blueprint P2 #2), the backend endpoint's 200 raw /
200 {"bound": false} contract, in-band error degradation, the identity-header
forward, and the composition root's env gate.

Mirrors tests/module/test_data_access.py's shape: a fake backend implementing
the ACTUAL route contract (200 raw dict / 200 {"bound": false}; non-2xx only
from transport/middleware), and a fake discord manager giving DirectStore the
same known agents, so parity means "same scenario, same return value" for a
real reason, not a coincidence of two independently-written fakes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from narranexus.platform.module_system.data_access import (
    ChannelDirectStore,
    ChannelHttpStore,
    get_channel_credential_store,
)
from narranexus.platform.module_system.data_access.factory import current_identity_headers

AGENT = "agent_39b2b72b823b"


def _patch_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, **kw):
            kw.pop("transport", None)
            super().__init__(transport=transport, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


# ---------------------------------------------------------------------------
# A fake discord credential manager + a fake backend implementing the REAL
# route contract (backend/routes/agents/channel_credentials.py).
# ---------------------------------------------------------------------------

_RAW = {
    "agent_id": AGENT,
    "bot_token": "super-secret-token",
    "bot_user_id": "9001",
    "bot_username": "Scout",
    "owner_user_id": "1234",
    "owner_name": "Alice",
    "enabled": True,
    "created_at": "2026-08-10T00:00:00+00:00",
    "updated_at": "2026-08-10T00:00:00+00:00",
}


class _FakeCred:
    def __init__(self, raw):
        self._raw = raw

    def to_raw_dict(self):
        return dict(self._raw)


class _FakeDiscordManager:
    """Stands in for DiscordCredentialManager — DirectStore constructs one
    per call via `_manager_class`, so this patches the class the seam
    resolves, not the seam itself (a real dispatch exercise, not a stub)."""

    def __init__(self, db):
        self.db = db

    async def get(self, agent_id):
        return _FakeCred(_RAW) if agent_id == AGENT else None


def _direct_store(monkeypatch):
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: _FakeDiscordManager if channel == "discord" else (_ for _ in ()).throw(
            ValueError(f"unknown channel: {channel!r}")
        ),
    )

    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    return store


def _http_store(monkeypatch, *, known_agents=frozenset({AGENT}), status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="rejected")
        parts = request.url.path.split("/")
        # /api/agents/{agent_id}/channels/{channel}/credential
        agent_id = parts[3]
        channel = parts[5]
        assert channel == "discord"
        if agent_id not in known_agents:
            return httpx.Response(200, json={"bound": False})
        return httpx.Response(200, json=_RAW)

    _patch_http(monkeypatch, handler)
    return ChannelHttpStore("http://backend:8000")


# ---------------------------------------------------------------------------
# parity: same scenario, same raw dict
# ---------------------------------------------------------------------------


def test_parity_bound_agent_returns_the_same_raw_dict(monkeypatch):
    direct = _direct_store(monkeypatch)
    http = _http_store(monkeypatch)

    d = asyncio.run(direct.get_credential("discord", AGENT))
    h = asyncio.run(http.get_credential("discord", AGENT))
    assert d == h == _RAW
    assert d["bot_token"] == "super-secret-token"  # the RAW secret, not sanitised


def test_parity_unbound_agent_is_none_on_both(monkeypatch):
    direct = _direct_store(monkeypatch)
    http = _http_store(monkeypatch, known_agents=frozenset())

    # An agent that is bound on NEITHER side (the fake manager returns a cred
    # only for AGENT; the fake backend knows no agents) — parity = both None.
    unbound = "agent_never_bound00"
    assert asyncio.run(direct.get_credential("discord", unbound)) is None
    assert asyncio.run(http.get_credential("discord", unbound)) is None


def test_direct_unknown_channel_raises_a_clear_error(monkeypatch):
    direct = _direct_store(monkeypatch)
    with pytest.raises(ValueError, match="unknown channel"):
        asyncio.run(direct.get_credential("carrier_pigeon", AGENT))


# ---------------------------------------------------------------------------
# HttpStore: in-band degradation, never raises
# ---------------------------------------------------------------------------


def test_http_401_degrades_to_none_in_band(monkeypatch):
    http = _http_store(monkeypatch, status=401)
    out = asyncio.run(http.get_credential("discord", AGENT))
    assert out is None


def test_http_5xx_degrades_to_none_in_band(monkeypatch):
    http = _http_store(monkeypatch, status=503)
    out = asyncio.run(http.get_credential("discord", AGENT))
    assert out is None


def test_http_unreachable_backend_degrades_to_none_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route to backend")

    _patch_http(monkeypatch, boom)
    store = ChannelHttpStore("http://backend:8000")
    out = asyncio.run(store.get_credential("discord", AGENT))
    assert out is None


def test_http_non_json_response_degrades_to_none_in_band(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _patch_http(monkeypatch, handler)
    store = ChannelHttpStore("http://backend:8000")
    out = asyncio.run(store.get_credential("discord", AGENT))
    assert out is None


# ---------------------------------------------------------------------------
# get_agent_name parity
# ---------------------------------------------------------------------------


def test_get_agent_name_parity(monkeypatch):
    async def fake_get_one(table, filters):
        assert table == "agents"
        return {"agent_name": "Scout"} if filters["agent_id"] == AGENT else None

    class _FakeDb:
        get_one = staticmethod(fake_get_one)

    direct = ChannelDirectStore()

    async def fake_db():
        return _FakeDb()

    direct._db = fake_db  # type: ignore[method-assign]
    d = asyncio.run(direct.get_agent_name(AGENT))
    assert d == "Scout"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/channels/name")
        return httpx.Response(200, json={"agent_name": "Scout"})

    _patch_http(monkeypatch, handler)
    http = ChannelHttpStore("http://backend:8000")
    h = asyncio.run(http.get_agent_name(AGENT))
    assert h == d == "Scout"


def test_get_agent_owner_parity(monkeypatch):
    # created_by (workspace owner) — narra's media send + CLI need it; empty
    # when the agent row is missing. Direct reads `created_by`; Http GETs
    # /channels/owner -> owner_user_id.
    async def fake_get_one(table, filters):
        assert table == "agents"
        return {"created_by": "owner_u"} if filters["agent_id"] == AGENT else None

    class _FakeDb:
        get_one = staticmethod(fake_get_one)

    direct = ChannelDirectStore()

    async def fake_db():
        return _FakeDb()

    direct._db = fake_db  # type: ignore[method-assign]
    d = asyncio.run(direct.get_agent_owner(AGENT))
    assert d == "owner_u"
    assert asyncio.run(direct.get_agent_owner("nobody")) == ""  # missing row -> ""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/channels/owner")
        return httpx.Response(200, json={"owner_user_id": "owner_u"})

    _patch_http(monkeypatch, handler)
    http = ChannelHttpStore("http://backend:8000")
    h = asyncio.run(http.get_agent_owner(AGENT))
    assert h == d == "owner_u"


def test_get_agent_name_falls_back_to_agent_id_when_missing(monkeypatch):
    async def fake_get_one(table, filters):
        return None

    class _FakeDb:
        get_one = staticmethod(fake_get_one)

    direct = ChannelDirectStore()

    async def fake_db():
        return _FakeDb()

    direct._db = fake_db  # type: ignore[method-assign]
    assert asyncio.run(direct.get_agent_name(AGENT)) == AGENT

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    _patch_http(monkeypatch, handler)
    http = ChannelHttpStore("http://backend:8000")
    assert asyncio.run(http.get_agent_name(AGENT)) == AGENT


# ---------------------------------------------------------------------------
# identity-header forwarding
# ---------------------------------------------------------------------------


def test_http_store_forwards_identity_headers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_RAW)

    _patch_http(monkeypatch, handler)
    store = ChannelHttpStore(
        "http://backend:8000",
        identity_headers={"Authorization": "Bearer nx-agent:a~chat"},
    )
    asyncio.run(store.get_credential("discord", AGENT))
    assert seen["auth"] == "Bearer nx-agent:a~chat"


# ---------------------------------------------------------------------------
# composition root
# ---------------------------------------------------------------------------


def test_factory_default_is_direct(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)
    assert isinstance(get_channel_credential_store(), ChannelDirectStore)


def test_factory_cloud_is_http(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    store = get_channel_credential_store(identity_headers={"authorization": "Bearer nx-agent:a"})
    assert isinstance(store, ChannelHttpStore)
    assert store._base == "http://backend:8000"
    assert store._headers == {"authorization": "Bearer nx-agent:a"}


def test_factory_cloud_uses_ambient_identity_headers_by_default(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.factory.current_identity_headers",
        lambda: {"authorization": "Bearer nx-agent:ambient"},
    )
    store = get_channel_credential_store()
    assert store._headers == {"authorization": "Bearer nx-agent:ambient"}


# ---------------------------------------------------------------------------
# write leg (blueprint P2): bind / unbind / test_connection
# ---------------------------------------------------------------------------


def _http():
    return ChannelHttpStore("http://backend:8000")


def test_http_unbind_posts_the_route_and_returns_its_json(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"unbound": True}})

    _patch_http(monkeypatch, handler)
    out = asyncio.run(_http().unbind("discord", AGENT))
    assert seen["path"] == "/api/channels/discord/unbind"
    assert seen["body"] == {"agent_id": AGENT}
    assert out == {"success": True, "data": {"unbound": True}}


def test_http_bind_posts_agent_id_plus_fields(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _j
        seen["path"] = request.url.path
        seen["body"] = _j.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"bot_username": "b"}})

    _patch_http(monkeypatch, handler)
    out = asyncio.run(_http().bind("discord", AGENT, {"bot_token": "tok", "owner_user_id": "o"}))
    assert seen["path"] == "/api/channels/discord/bind"
    assert seen["body"] == {"agent_id": AGENT, "fields": {"bot_token": "tok", "owner_user_id": "o"}}
    assert out["success"] is True


def test_http_write_unreachable_degrades_to_success_false(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    out = asyncio.run(_http().unbind("discord", AGENT))
    assert out["success"] is False and "unreachable" in out["error"]


def test_http_write_5xx_degrades_to_success_false(monkeypatch):
    def reject(request):
        return httpx.Response(500, text="nope")

    _patch_http(monkeypatch, reject)
    out = asyncio.run(_http().unbind("discord", AGENT))
    assert out["success"] is False and "500" in out["error"]


def _direct_with_fake_manager(monkeypatch, *, unbind_result):
    class _FakeMgr:
        def __init__(self, db):
            pass

        async def unbind(self, agent_id):
            return unbind_result

    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: _FakeMgr,
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    return store


def test_direct_unbind_wraps_like_the_route(monkeypatch):
    ok = _direct_with_fake_manager(monkeypatch, unbind_result=True)
    assert asyncio.run(ok.unbind("discord", AGENT)) == {"success": True, "data": {"unbound": True}}
    missing = _direct_with_fake_manager(monkeypatch, unbind_result=False)
    r = asyncio.run(missing.unbind("discord", AGENT))
    assert r["success"] is False and "bound" in r["error"]


def test_direct_bind_delegates_to_do_bind(monkeypatch):
    captured = {}

    async def fake_do_bind(mgr, agent_id, **fields):
        captured["agent_id"] = agent_id
        captured["fields"] = fields
        return {"success": True, "data": {"ok": 1}}

    import narranexus_plugins.discord_module._discord_service as ds
    monkeypatch.setattr(ds, "do_bind", fake_do_bind)
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: (lambda db: object()),
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    out = asyncio.run(store.bind("discord", AGENT, {"bot_token": "t", "owner_user_id": "o"}))
    assert out == {"success": True, "data": {"ok": 1}}
    assert captured == {"agent_id": AGENT, "fields": {"bot_token": "t", "owner_user_id": "o"}}


def test_direct_bind_db_taker_passes_the_raw_db_not_a_manager(monkeypatch):
    # narramessenger's do_bind takes (db, agent_id, bind_command) — takes="db".
    captured = {}
    sentinel_db = object()

    async def fake_do_bind(db, agent_id, **fields):
        captured["db_is_sentinel"] = db is sentinel_db
        captured["fields"] = fields
        return {"success": True, "data": {"bound": 1}}

    import narranexus_plugins.narramessenger_module._narramessenger_service as ns
    monkeypatch.setattr(ns, "do_bind", fake_do_bind)
    store = ChannelDirectStore()

    async def fake_db():
        return sentinel_db

    store._db = fake_db  # type: ignore[method-assign]
    out = asyncio.run(store.bind("narramessenger", AGENT, {"bind_command": "paste-me"}))
    assert out == {"success": True, "data": {"bound": 1}}
    # the raw db was handed to do_bind (NOT a manager) and the field rode through
    assert captured == {"db_is_sentinel": True, "fields": {"bind_command": "paste-me"}}


def test_direct_test_connection_delegates_to_do_test(monkeypatch):
    async def fake_do_test(mgr, agent_id):
        return {"success": True, "data": {"live": True}}

    import narranexus_plugins.discord_module._discord_service as ds
    monkeypatch.setattr(ds, "do_test_connection", fake_do_test)
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: (lambda db: object()),
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    assert asyncio.run(store.test_connection("discord", AGENT)) == {"success": True, "data": {"live": True}}


@pytest.mark.parametrize("channel", ["wechat", "narramessenger"])
def test_direct_test_connection_unsupported_channel_raises_clearly(channel):
    # Both branches must give the SAME clear ValueError, not a KeyError:
    #   - wechat is NOT in _BIND_SERVICE at all (the `.get` -> None branch)
    #   - narramessenger IS in _BIND_SERVICE but its _service has no
    #     do_test_connection (the getattr -> None branch)
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="no test_connection"):
        asyncio.run(store.test_connection(channel, AGENT))


def test_http_test_connection_posts_the_test_route(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"success": True, "data": {"live": True}})

    _patch_http(monkeypatch, handler)
    out = asyncio.run(_http().test_connection("telegram", AGENT))
    assert seen["path"] == "/api/channels/telegram/test"
    assert out["success"] is True


def test_http_write_non_json_degrades_to_success_false(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _patch_http(monkeypatch, handler)
    out = asyncio.run(_http().unbind("discord", AGENT))
    assert out["success"] is False and "non-JSON" in out["error"]


# ---------------------------------------------------------------------------
# credential-mutation primitives (lark write leg): deep_merge + patch/put/delete
# ---------------------------------------------------------------------------


def test_deep_merge_recurses_dicts_and_replaces_scalars():
    from narranexus.platform.module_system.data_access.channel_store import deep_merge

    base = {"permission_state": {"a": 1, "b": 2}, "auth_status": "old", "x": {"k": 1}}
    patch = {"permission_state": {"b": 9, "c": 3}, "auth_status": "new", "x": "flat"}
    out = deep_merge(base, patch)
    assert out == {"permission_state": {"a": 1, "b": 9, "c": 3}, "auth_status": "new", "x": "flat"}
    assert base["permission_state"] == {"a": 1, "b": 2}  # base untouched (copy)


def _direct_with_write_spy(monkeypatch):
    calls = []

    class _Mgr:
        def __init__(self, db):
            pass

        async def apply_patch(self, agent_id, patch):
            calls.append(("apply_patch", agent_id, patch))

        async def save_raw(self, agent_id, raw):
            calls.append(("save_raw", agent_id, raw))

        async def delete_credential(self, agent_id):
            calls.append(("delete_credential", agent_id))

    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: _Mgr,
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    return store, calls


def test_direct_patch_put_delete_delegate_to_the_manager(monkeypatch):
    store, calls = _direct_with_write_spy(monkeypatch)
    assert asyncio.run(store.patch_credential("lark", AGENT, {"permission_state": {"k": 1}})) == {"success": True}
    assert asyncio.run(store.put_credential("lark", AGENT, {"app_id": "x"})) == {"success": True}
    assert asyncio.run(store.delete_credential("lark", AGENT)) == {"success": True, "data": {"deleted": True}}
    assert calls == [
        ("apply_patch", AGENT, {"permission_state": {"k": 1}}),
        ("save_raw", AGENT, {"app_id": "x"}),
        ("delete_credential", AGENT),
    ]


def test_direct_patch_degrades_runtime_failure_to_envelope(monkeypatch):
    # DirectStore must NOT raise on a runtime write failure — it degrades to
    # {"success": False, "error": ...} exactly like HttpStore, so the caller sees
    # the SAME failure shape on both legs (before this, apply_patch's exception
    # propagated on the local leg while the cloud leg returned an envelope, so a
    # cloud write failure silently read as success in the tool wrappers).
    class _Boom:
        def __init__(self, db):
            pass

        async def apply_patch(self, agent_id, patch):
            raise ValueError("no credential to patch")

    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: _Boom,
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    out = asyncio.run(store.patch_credential("lark", AGENT, {"a": 1}))
    assert out == {"success": False, "error": "no credential to patch"}


def test_direct_mutation_unsupported_channel_raises_clear_valueerror(monkeypatch):
    # A channel whose manager doesn't implement the write primitive gets a clear
    # ValueError (like test_connection), not a bare AttributeError → 500.
    class _NoWrites:
        def __init__(self, db):
            pass

    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: _NoWrites,
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="does not support credential apply_patch"):
        asyncio.run(store.patch_credential("discord", AGENT, {"a": 1}))


def test_direct_mutation_sanitizes_db_error_text(monkeypatch):
    # A non-ValueError (a DB/connection error mid-write) can carry host/port/user
    # in its text; the envelope must surface a STABLE code, not that text, since
    # it rides envelope → tool → model → user.
    class _DbErr:
        def __init__(self, db):
            pass

        async def apply_patch(self, agent_id, patch):
            raise RuntimeError("Can't connect to MySQL server on 'db-host' (111) user='narranexus'")

    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: _DbErr,
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    out = asyncio.run(store.patch_credential("lark", AGENT, {"a": 1}))
    assert out == {"success": False, "error": "write_failed"}
    assert "db-host" not in str(out) and "narranexus" not in str(out)


def test_direct_mutation_db_unavailable_is_a_stable_code(monkeypatch):
    # A pool-build failure (_db() raises) must degrade to a stable code too — its
    # text is the most likely to carry connection details.
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: (lambda db: object()),
    )
    store = ChannelDirectStore()

    async def boom_db():
        raise RuntimeError("Can't connect to MySQL server on 'db-host:3306'")

    store._db = boom_db  # type: ignore[method-assign]
    out = asyncio.run(store.patch_credential("lark", AGENT, {"a": 1}))
    assert out == {"success": False, "error": "db_unavailable"}
    assert "db-host" not in str(out)


def test_http_patch_put_delete_use_the_right_verbs_and_path(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    http = ChannelHttpStore("http://backend:8000")
    asyncio.run(http.patch_credential("lark", AGENT, {"auth_status": "x"}))
    asyncio.run(http.put_credential("lark", AGENT, {"app_id": "y"}))
    asyncio.run(http.delete_credential("lark", AGENT))
    p = f"/api/agents/{AGENT}/channels/lark/credential"
    assert seen == [
        ("PATCH", p, {"auth_status": "x"}),
        ("PUT", p, {"app_id": "y"}),
        ("DELETE", p, None),
    ]


def test_http_write_primitive_never_raises(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    out = asyncio.run(ChannelHttpStore("http://backend:8000").patch_credential("lark", AGENT, {"a": 1}))
    assert out["success"] is False and "unreachable" in out["error"]


# ---------------------------------------------------------------------------
# lark's typed bind (do_bind) + special unbind (do_unbind teardown)
# ---------------------------------------------------------------------------


def test_direct_lark_bind_dispatches_to_do_bind(monkeypatch):
    captured = {}

    async def fake_do_bind(mgr, agent_id, **fields):
        captured["fields"] = fields
        return {"success": True, "data": {"bound": True}}

    import narranexus_plugins.lark_module._lark_service as ls
    monkeypatch.setattr(ls, "do_bind", fake_do_bind)
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: (lambda db: object()),
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    out = asyncio.run(store.bind("lark", AGENT, {"app_id": "cli", "app_secret": "s", "brand": "feishu", "owner_email": ""}))
    assert out == {"success": True, "data": {"bound": True}}
    assert captured["fields"] == {"app_id": "cli", "app_secret": "s", "brand": "feishu", "owner_email": ""}


def test_direct_lark_unbind_uses_do_unbind_with_mgr_and_db(monkeypatch):
    # lark's unbind is do_unbind(mgr, agent_id, db) — credential + inbox teardown —
    # NOT the generic mgr.unbind; the seam must route to it and return its envelope.
    seen = {}
    sentinel_db = object()
    fake_mgr = object()

    async def fake_do_unbind(mgr, agent_id, db):
        seen["mgr_is"] = mgr is fake_mgr
        seen["db_is"] = db is sentinel_db
        seen["agent"] = agent_id
        return {"success": True, "data": {"unbound": True}}

    import narranexus_plugins.lark_module._lark_service as ls
    monkeypatch.setattr(ls, "do_unbind", fake_do_unbind)
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.channel_store._manager_class",
        lambda channel: (lambda db: fake_mgr),
    )
    store = ChannelDirectStore()

    async def fake_db():
        return sentinel_db

    store._db = fake_db  # type: ignore[method-assign]
    out = asyncio.run(store.unbind("lark", AGENT))
    assert out == {"success": True, "data": {"unbound": True}}
    assert seen == {"mgr_is": True, "db_is": True, "agent": AGENT}


def test_http_lark_unbind_is_byte_parity_with_direct(monkeypatch):
    # The route (/api/channels/lark/unbind) returns do_unbind's envelope VERBATIM, so the
    # HttpStore result must equal the DirectStore result byte-for-byte — assert
    # the WHOLE dict, not just success, against the real route body. Both the
    # success envelope and the no_credential failure must round-trip unchanged
    # (a reshaping route — the pre-2026-08-11 bug — would fail this).
    seen = {}
    current = {}

    # These are exactly what do_unbind returns (and hence what DirectStore.unbind
    # returns — see test_direct_lark_unbind_uses_do_unbind_with_mgr_and_db).
    ROUTE_BODIES = {
        "ok": {"success": True, "data": {"unbound": True}},
        "none": {"success": False, "error": "no_credential",
                 "message": "No Lark bot bound to this agent."},
    }

    # Patch httpx ONCE (nesting _patch_http would make the 2nd PatchedClient
    # subclass the 1st, so the first transport would win); vary the body here.
    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=current["body"])

    _patch_http(monkeypatch, handler)
    store = ChannelHttpStore("http://backend:8000")

    for key, body in ROUTE_BODIES.items():
        current["body"] = body
        out = asyncio.run(store.unbind("lark", AGENT))
        assert seen["path"] == "/api/channels/lark/unbind"
        assert out == body, f"{key}: HttpStore must return the route body verbatim"


def test_direct_narramessenger_unbind_goes_through_the_service_with_db(db_client):
    """narramessenger's do_unbind takes (db, agent_id) — the seam follows the
    descriptor's bind_takes for unbind too (4d.3), so the gateway-side unbind is
    not skipped by the uniform mgr.unbind path."""
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    asyncio.run(GenericCredentialStore(db_client).upsert("narramessenger", AGENT, {
        "matrix_homeserver_url": "https://hs", "matrix_user_id": "@bot:hs", "matrix_access_token": "tok",
    }, enabled=True))
    store = ChannelDirectStore()

    async def fake_db():
        return db_client

    store._db = fake_db  # type: ignore[method-assign]
    assert asyncio.run(store.unbind("narramessenger", AGENT)) == {"success": True, "unbound": True}
    assert asyncio.run(GenericCredentialStore(db_client).get("narramessenger", AGENT)) is None
    assert asyncio.run(store.unbind("narramessenger", AGENT)) == {"success": True, "unbound": False}
