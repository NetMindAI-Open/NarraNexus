"""
@file_name: test_mcp_auth.py
@author:
@date: 2026-08-10
@description: Tests for module/identity/mcp_auth.py — the ASGI auth middleware
on every module MCP server, and the NX_MCP_AUTH_MODE gating.

Pins the rollout contract:
- off (default): byte-identical behaviour, contextvar stays None
- audit: verifies + logs, NEVER rejects
- enforce: tool-call POSTs without a valid token are 401'd; GETs (SSE
  handshake) and /health stay open; a missing public key fails OPEN with a
  warning (deploy misconfiguration must not take the data plane down)
- a bearer whose declared user_id disagrees with the token's sub is invalid
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from narranexus.platform.module_system._mcp_identity import agent_id_headers
from narranexus.platform.module_system.identity.mcp_auth import (
    IdentityAuthMiddleware,
    auth_mode,
    verified_caller,
)
from narranexus.platform.module_system.identity.tokens import ISSUER_LOCAL, sign_identity_token

AGENT = "agent_39b2b72b823b"


@pytest.fixture(autouse=True)
def _clean_module_state():
    """The module keeps process-global aggregation/cache dicts; leaking them
    between tests is a classic ordering hazard (round-3 review, minor #5)."""
    from narranexus.platform.module_system.identity import mcp_auth

    yield
    mcp_auth._owner_cache.clear()
    mcp_auth._tokenless_counts.clear()


def _keypair() -> tuple[bytes, bytes]:
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def _provision(tmp_path, monkeypatch) -> bytes:
    """Write a public key file, point the verifier at it, return the private PEM."""
    priv_pem, pub_pem = _keypair()
    pub_file = tmp_path / "identity_ed25519.pub"
    pub_file.write_bytes(pub_pem)
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(pub_file))
    return priv_pem


def _app() -> Starlette:
    async def echo(request):
        ident = verified_caller()
        return PlainTextResponse(ident.user_id if ident else "anon")

    return Starlette(
        routes=[
            Route("/messages/", echo, methods=["GET", "POST"]),
            Route("/sse", echo, methods=["GET"]),
            Route("/health", echo, methods=["GET", "POST"]),
        ],
        middleware=[Middleware(IdentityAuthMiddleware)],
    )


def _headers(priv_pem: bytes, user_id: str = "usr_1") -> dict:
    token = sign_identity_token(user_id, priv_pem, issuer=ISSUER_LOCAL)
    return agent_id_headers(AGENT, user_id=user_id, identity_token=token)


# ---------------------------------------------------------------------------
# mode parsing
# ---------------------------------------------------------------------------


def test_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("NX_MCP_AUTH_MODE", raising=False)
    assert auth_mode() == "off"


def test_unknown_mode_reads_as_audit(monkeypatch):
    # A typo'd mode must surface (audit logs) rather than silently disable auth.
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enfroce")
    assert auth_mode() == "audit"


# ---------------------------------------------------------------------------
# off — byte-identical
# ---------------------------------------------------------------------------


def test_off_passes_everything_and_verifies_nothing(monkeypatch):
    monkeypatch.delenv("NX_MCP_AUTH_MODE", raising=False)
    client = TestClient(_app())
    r = client.post("/messages/")
    assert r.status_code == 200
    assert r.text == "anon"


# ---------------------------------------------------------------------------
# audit — verify, log, never reject
# ---------------------------------------------------------------------------


def test_audit_never_rejects_but_exposes_verified_identity(tmp_path, monkeypatch):
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    client = TestClient(_app())

    assert client.post("/messages/").status_code == 200  # tokenless: allowed
    r = client.post("/messages/", headers=_headers(priv))
    assert r.status_code == 200
    assert r.text == "usr_1"  # verified identity reached the handler


def test_audit_invalid_token_passes_as_anonymous(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    other_priv, _ = _keypair()
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    r = TestClient(_app()).post("/messages/", headers=_headers(other_priv))
    assert r.status_code == 200
    assert r.text == "anon"


# ---------------------------------------------------------------------------
# enforce
# ---------------------------------------------------------------------------


def test_enforce_rejects_tokenless_post(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    assert TestClient(_app()).post("/messages/").status_code == 401


def test_enforce_rejects_forged_token(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    other_priv, _ = _keypair()
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    assert TestClient(_app()).post("/messages/", headers=_headers(other_priv)).status_code == 401


def test_enforce_accepts_valid_token(tmp_path, monkeypatch):
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    r = TestClient(_app()).post("/messages/", headers=_headers(priv))
    assert r.status_code == 200
    assert r.text == "usr_1"


def test_enforce_leaves_gets_and_health_open(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    client = TestClient(_app())
    assert client.get("/sse").status_code == 200          # SSE handshake
    assert client.get("/messages/").status_code == 200
    assert client.post("/health").status_code == 200      # probes


def test_enforce_rejects_user_id_field_mismatch(tmp_path, monkeypatch):
    # The self-declared bearer user_id must agree with the proven sub —
    # a mismatch is a forged field, not an unknown.
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    token = sign_identity_token("usr_real", priv, issuer=ISSUER_LOCAL)
    headers = agent_id_headers(AGENT, user_id="usr_other", identity_token=token)
    assert TestClient(_app()).post("/messages/", headers=headers).status_code == 401


def test_enforce_without_public_key_fails_open(tmp_path, monkeypatch):
    # Deploy misconfiguration (key not mounted) must degrade to audit
    # semantics — mcp is the data plane, not a place to take everyone down.
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(tmp_path / "missing.pub"))
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    assert TestClient(_app()).post("/messages/").status_code == 200


def test_contextvar_resets_between_requests(tmp_path, monkeypatch):
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    client = TestClient(_app())
    assert client.post("/messages/", headers=_headers(priv)).text == "usr_1"
    assert client.post("/messages/").text == "anon"
    assert verified_caller() is None


# ---------------------------------------------------------------------------
# wiring — every module server wears the middleware
# ---------------------------------------------------------------------------


def test_build_mcp_server_installs_identity_auth_middleware():
    from mcp.server.fastmcp import FastMCP

    from narranexus.platform.module_system.module_runner import ModuleRunner

    server = ModuleRunner._build_host_server([("probe_module", FastMCP("probe_module"))], 7999)
    installed = [m.cls for m in server.config.app.user_middleware]
    assert IdentityAuthMiddleware in installed


# ---------------------------------------------------------------------------
# OwnerScopedPolicy at the tool layer (_wrap_fn integration)
# ---------------------------------------------------------------------------


@pytest.fixture
def _policy_env(_policy_owner_stubs, monkeypatch):
    """_policy_owner_stubs + a verified caller in the connection ContextVar
    (the pre-per-message fallback path)."""
    from narranexus.platform.module_system.identity import mcp_auth
    from narranexus.platform.module_system.identity.tokens import VerifiedIdentity

    token = mcp_auth._verified_var.set(
        VerifiedIdentity(user_id="usr_1", issuer="narranexus-local", expires_at=2**33)
    )
    yield _policy_owner_stubs
    mcp_auth._verified_var.reset(token)


def _policy_server():
    from mcp.server.fastmcp import FastMCP

    from narranexus.platform.module_system._mcp_identity import install_caller_identity

    mcp = FastMCP("policy_module")
    ran: dict = {}

    @mcp.tool()
    async def dict_tool(agent_id: str) -> dict:
        ran["agent_id"] = agent_id
        return {"ok": True}

    @mcp.tool()
    async def text_tool(agent_id: str) -> str:
        ran["text_agent_id"] = agent_id
        return "ok"

    install_caller_identity(mcp)
    fns = {t.name: t.fn for t in mcp._tool_manager.list_tools()}
    return fns, ran


@pytest.mark.asyncio
async def test_enforce_denies_cross_owner_call(_policy_env, monkeypatch):
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    fns, ran = _policy_server()
    out = await fns["dict_tool"](agent_id="agent_of_usr2")
    assert out["success"] is False
    assert "does not own" in out["error"]
    assert ran == {}  # tool body never ran
    assert _policy_env and _policy_env[0]["event_type"] == "mcp_auth_denied"
    assert _policy_env[0]["user_id"] == "usr_1"


@pytest.mark.asyncio
async def test_enforce_denial_matches_text_tool_shape(_policy_env, monkeypatch):
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    fns, ran = _policy_server()
    out = await fns["text_tool"](agent_id="agent_of_usr2")
    assert isinstance(out, str) and "does not own" in out
    assert ran == {}


@pytest.mark.asyncio
async def test_enforce_allows_own_agent(_policy_env, monkeypatch):
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    fns, ran = _policy_server()
    out = await fns["dict_tool"](agent_id="agent_of_usr1")
    assert out == {"ok": True}
    assert ran["agent_id"] == "agent_of_usr1"
    assert _policy_env == []


@pytest.mark.asyncio
async def test_audit_records_but_allows_cross_owner_call(_policy_env, monkeypatch):
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    fns, ran = _policy_server()
    out = await fns["dict_tool"](agent_id="agent_of_usr2")
    assert out == {"ok": True}  # measured, not policed
    assert ran["agent_id"] == "agent_of_usr2"
    assert _policy_env and _policy_env[0]["detail"]["mode"] == "audit"


@pytest.mark.asyncio
async def test_unknown_agent_is_allowed(_policy_env, monkeypatch):
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    fns, ran = _policy_server()
    out = await fns["dict_tool"](agent_id="agent_unknown")
    assert out == {"ok": True}  # resolve_owner "" → the tool fails naturally
    assert _policy_env == []


@pytest.mark.asyncio
async def test_no_verified_identity_keeps_baseline(monkeypatch):
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    monkeypatch.setattr(
        "narranexus.platform.utils.deployment_mode.is_cloud_mode", lambda: True
    )
    fns, ran = _policy_server()
    out = await fns["dict_tool"](agent_id="agent_of_usr2")
    assert out == {"ok": True}  # no proof presented → fail-open baseline intact


@pytest.mark.asyncio
async def test_local_mode_is_single_tenant_noop(_policy_env, monkeypatch):
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    monkeypatch.setattr(
        "narranexus.platform.utils.deployment_mode.is_cloud_mode", lambda: False
    )
    fns, ran = _policy_server()
    out = await fns["dict_tool"](agent_id="agent_of_usr2")
    assert out == {"ok": True}
    assert _policy_env == []


def test_every_agent_id_tool_is_async():
    """The ownership policy awaits — it only guards the async wrapper. This
    invariant makes that complete coverage: a sync tool declaring agent_id
    would silently bypass the policy."""
    import inspect

    from narranexus.platform.module_system import module_registry

    offenders = []
    checked = 0
    for name, cls in module_registry.items():
        try:
            m = cls(agent_id="probe", user_id="probe", database_client=None)
            mcp = m.create_mcp_server()
        except Exception:
            continue
        if mcp is None:
            continue
        for tool in mcp._tool_manager.list_tools():
            fn = getattr(tool, "fn", None)
            if fn is None:
                continue
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):
                continue
            if "agent_id" in params and not inspect.iscoroutinefunction(fn):
                offenders.append(f"{name}.{tool.name}")
            if "agent_id" in params:
                checked += 1
    assert offenders == [], (
        f"sync tools declaring agent_id bypass OwnerScopedPolicy: {offenders}"
    )
    # Coverage floor: the except/continue above silently skips modules whose
    # constructor starts failing under database_client=None — without this,
    # the invariant could hollow out while staying green (93 tools today).
    assert checked >= 90, f"invariant only checked {checked} tools — coverage shrank"


# ---------------------------------------------------------------------------
# per-message proof (review Important #2): the policy must read the CURRENT
# tool call's headers, not the connection-time ContextVar snapshot
# ---------------------------------------------------------------------------


def test_policy_reads_per_message_proof_without_contextvar(
    tmp_path, monkeypatch, _policy_owner_stubs
):
    from tests.module.test_mcp_caller_identity import injected

    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")

    import asyncio

    fns, ran = _policy_server()
    # The ambient identity must NAME the target agent (injected identity wins
    # over the parameter — the pre-existing hardening) while the token proves
    # a user who does not own it.
    token = sign_identity_token("usr_1", priv, issuer=ISSUER_LOCAL)
    headers = agent_id_headers("agent_of_usr2", user_id="usr_1", identity_token=token)
    with injected(headers):  # ambient MCP request carries the proof
        out = asyncio.run(fns["dict_tool"](agent_id="agent_of_usr2"))
    assert out["success"] is False and "does not own" in out["error"]
    assert ran == {}


def test_per_message_verdict_is_final_over_stale_snapshot(
    tmp_path, monkeypatch, _policy_owner_stubs
):
    """An ambient request WITHOUT proof must leave the policy blind even if a
    connection-time snapshot exists — falling back there would resurrect the
    exact mismatch this fixes (proof from one source, facts from another)."""
    from tests.module.test_mcp_caller_identity import injected

    from narranexus.platform.module_system.identity import mcp_auth
    from narranexus.platform.module_system.identity.tokens import VerifiedIdentity

    _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")

    import asyncio

    fns, ran = _policy_server()
    stale = mcp_auth._verified_var.set(
        VerifiedIdentity(user_id="usr_1", issuer="narranexus-local", expires_at=2**33)
    )
    try:
        with injected(agent_id_headers("agent_of_usr2", user_id="usr_1")):  # no token
            out = asyncio.run(fns["dict_tool"](agent_id="agent_of_usr2"))
    finally:
        mcp_auth._verified_var.reset(stale)
    assert out == {"ok": True}  # no per-message proof → fail-open baseline
    assert ran["agent_id"] == "agent_of_usr2"


@pytest.fixture
def _policy_owner_stubs(monkeypatch):
    """Cloud mode + stubbed owner lookup/audit — no ContextVar involvement."""
    recorded: list[dict] = []
    owners = {"agent_of_usr1": "usr_1", "agent_of_usr2": "usr_2"}

    monkeypatch.setattr(
        "narranexus.platform.utils.deployment_mode.is_cloud_mode", lambda: True
    )

    async def fake_get_db_client():
        return object()

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", fake_get_db_client
    )

    class FakeAgentRepo:
        def __init__(self, db):
            pass

        async def resolve_owner(self, agent_id):
            return owners.get(agent_id, "")

    class FakeAuditRepo:
        def __init__(self, db):
            pass

        async def record(self, **kw):
            recorded.append(kw)

    monkeypatch.setattr("narranexus.platform.repository.AgentRepository", FakeAgentRepo)
    monkeypatch.setattr(
        "narranexus.platform.repository.executor_audit_repository.ExecutorAuditRepository",
        FakeAuditRepo,
    )
    from narranexus.platform.module_system.identity import mcp_auth

    mcp_auth._owner_cache.clear()
    return recorded


# ---------------------------------------------------------------------------
# real-transport integration (review Important #2): drive the ACTUAL merged
# app from _build_mcp_server over streamable HTTP and prove the per-message
# proof reaches the tool handler (no hand-built Starlette stand-in)
# ---------------------------------------------------------------------------


def _sse_json_payloads(text: str) -> list[dict]:
    import json

    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[len("data: "):]))
            except ValueError:
                pass
    return out


def test_real_streamable_transport_carries_proof_to_the_tool(tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP

    from narranexus.platform.module_system._mcp_identity import install_caller_identity
    from narranexus.platform.module_system.identity.mcp_auth import (
        verified_caller_for_tool_call,
    )
    from narranexus.platform.module_system.module_runner import ModuleRunner

    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")

    seen: dict = {}
    mcp = FastMCP("itest_module")

    @mcp.tool()
    async def probe(agent_id: str) -> dict:
        ident = verified_caller_for_tool_call()
        seen["verified_user"] = ident.user_id if ident else None
        return {"ok": True}

    install_caller_identity(mcp)
    server = ModuleRunner._build_host_server([("itest_module", mcp)], 7998)
    app = server.config.app
    endpoint = "/mcp/itest_module/mcp"  # the module's streamable endpoint under its mount (batch 5a)

    token = sign_identity_token("usr_1", priv, issuer=ISSUER_LOCAL)
    id_headers = agent_id_headers(AGENT, user_id="usr_1", identity_token=token)
    accept = {"Accept": "application/json, text/event-stream"}

    with TestClient(app) as client:
        # Door check first: enforce 401s a tokenless initialize POST.
        r = client.post(
            endpoint,
            headers=accept,
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
        assert r.status_code == 401

        r = client.post(
            endpoint,
            headers={**accept, **id_headers},
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
        assert r.status_code == 200, r.text
        session = {"mcp-session-id": r.headers["mcp-session-id"]}

        r = client.post(
            endpoint,
            headers={**accept, **id_headers, **session},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert r.status_code in (200, 202), r.text

        r = client.post(
            endpoint,
            headers={**accept, **id_headers, **session},
            json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "probe", "arguments": {"agent_id": AGENT}},
            },
        )
        assert r.status_code == 200, r.text
        payloads = _sse_json_payloads(r.text) or [r.json()]
        result = payloads[-1]["result"]
        assert not result.get("isError"), result

    # THE assertion this test exists for: the proof was readable inside the
    # tool body, per message, through the real transport's task topology.
    assert seen["verified_user"] == "usr_1"


# ---------------------------------------------------------------------------
# round-2 review: audit MUST measure tokenless calls; owner cache must not
# pin failure sentinels
# ---------------------------------------------------------------------------


def test_audit_measures_tokenless_posts(tmp_path, monkeypatch):
    """Review round 2 #1: audit's whole purpose is answering 'which callers
    still arrive tokenless' — a tokenless POST must leave a log line AND a
    sampled mcp_auth_tokenless audit row, not silence."""
    from loguru import logger as _logger

    rows = _audit_capture(tmp_path, monkeypatch)

    lines: list[str] = []
    sink_id = _logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        assert TestClient(_app()).post("/messages/").status_code == 200  # allowed
    finally:
        _logger.remove(sink_id)

    assert any("unauthenticated" in line for line in lines)
    assert rows and rows[0]["event_type"] == "mcp_auth_tokenless"
    assert rows[0]["detail"]["total"] == 1
    (entry,) = rows[0]["detail"]["counts"]
    assert entry["caller"] == "anonymous"
    assert entry["method"] == "POST" and entry["path"] == "/messages/"
    assert entry["n"] == 1 and "port" in entry


def test_owner_cache_never_pins_the_empty_sentinel(monkeypatch):
    """Review round 2 #2: resolve_owner's '' covers unknown AND query failure,
    and '' means fail-open — a transient db error must not pin 'allow' for
    60s. Only positive resolutions are cached."""
    import asyncio

    from narranexus.platform.module_system.identity import mcp_auth

    answers = ["", "usr_owner"]  # first call fails/unknown, second recovers
    calls = {"n": 0}

    class FlakyRepo:
        def __init__(self, db):
            pass

        async def resolve_owner(self, agent_id):
            calls["n"] += 1
            return answers.pop(0) if answers else "usr_owner"

    monkeypatch.setattr("narranexus.platform.repository.AgentRepository", FlakyRepo)
    mcp_auth._owner_cache.clear()

    assert asyncio.run(mcp_auth._resolve_owner_cached(object(), "agent_x")) == ""
    # NOT cached: the very next call re-queries and sees the real owner.
    assert asyncio.run(mcp_auth._resolve_owner_cached(object(), "agent_x")) == "usr_owner"
    # Positive result IS cached: a third call does not hit the repo again.
    assert asyncio.run(mcp_auth._resolve_owner_cached(object(), "agent_x")) == "usr_owner"
    assert calls["n"] == 2


def test_tokenless_measurement_names_the_declared_caller(tmp_path, monkeypatch):
    """Round-3 review #1: the audit worklist must answer WHO to onboard. An
    old-broker executor is exactly 'bearer present, identity_token missing' — its
    self-declared user_id keys the aggregation."""
    rows = _audit_capture(tmp_path, monkeypatch)

    # tokenless but NOT identity-less: the pre-#260 header shape.
    headers = agent_id_headers(AGENT, user_id="usr_legacy")
    r = TestClient(_app()).post("/messages/", headers=headers)
    assert r.status_code == 200
    (entry,) = rows[0]["detail"]["counts"]
    assert entry["caller"] == "usr_legacy" and entry["n"] == 1


def _audit_capture(tmp_path, monkeypatch):
    """audit mode + stubbed audit sink; returns the rows list."""
    from narranexus.platform.module_system.identity import mcp_auth

    _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    rows: list[dict] = []

    async def fake_get_db_client():
        return object()

    class FakeAuditRepo:
        def __init__(self, db):
            pass

        async def record(self, **kw):
            rows.append(kw)

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", fake_get_db_client
    )
    monkeypatch.setattr(
        "narranexus.platform.repository.executor_audit_repository.ExecutorAuditRepository",
        FakeAuditRepo,
    )
    monkeypatch.setattr(mcp_auth, "_tokenless_flush_deadline", 0.0)
    return rows


def test_bearer_only_caller_is_named_too(tmp_path, monkeypatch):
    """Round-4 minor #2: the codex shape — everything rides the bearer, no
    explicit X-NarraNexus-* headers — must still name the declared caller."""
    rows = _audit_capture(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer nx-agent:{AGENT}~chat~~~usr_bearer_only"}
    assert TestClient(_app()).post("/messages/", headers=headers).status_code == 200
    (entry,) = rows[0]["detail"]["counts"]
    assert entry["caller"] == "usr_bearer_only"


def test_placeholder_declared_user_reads_as_anonymous(tmp_path, monkeypatch):
    rows = _audit_capture(tmp_path, monkeypatch)
    headers = agent_id_headers(AGENT, user_id="current_user")
    assert TestClient(_app()).post("/messages/", headers=headers).status_code == 200
    (entry,) = rows[0]["detail"]["counts"]
    assert entry["caller"] == "anonymous"


def test_declared_caller_is_length_capped_and_cardinality_bounded(monkeypatch):
    """Round-4 review #1: the declared value is attacker-controlled and audit
    rejects nothing — both dimensions must be hard-capped."""
    import asyncio

    from narranexus.platform.module_system.identity import mcp_auth

    class _H(dict):
        def get(self, k, default=None):
            return super().get(k.lower(), default)

    huge = "u" * 10000
    capped = mcp_auth._declared_caller(_H({"x-narranexus-user-id": huge}))
    assert len(capped) == mcp_auth._DECLARED_MAX_LEN

    # Cardinality: fill the dict past the cap inside one window, varying BOTH
    # caller-controlled dimensions (caller AND path — round-5 #1: an overflow
    # key that kept path re-opened the hole on the other dimension). Extras
    # land in the ONE constant bucket; the total survives.
    monkeypatch.setattr(mcp_auth, "_tokenless_flush_deadline", float("inf"))
    mcp_auth._tokenless_counts.clear()
    extra = 50
    for i in range(mcp_auth._TOKENLESS_KEYS_MAX + extra):
        asyncio.run(
            mcp_auth._note_tokenless(f"usr_{i}", "POST", f"/made-up/{i}", 7801)
        )
    assert len(mcp_auth._tokenless_counts) == mcp_auth._TOKENLESS_KEYS_MAX + 1
    assert mcp_auth._tokenless_counts[mcp_auth._OVERFLOW_KEY] == extra
    total = sum(mcp_auth._tokenless_counts.values())
    assert total == mcp_auth._TOKENLESS_KEYS_MAX + extra
    # Path length is capped too — the middleware counts BEFORE routing, so a
    # made-up path of request-line size must not bloat an entry.
    asyncio.run(mcp_auth._note_tokenless("usr_x", "POST", "/p" * 5000, 7801))
    longest = max(len(k[2]) for k in mcp_auth._tokenless_counts)
    assert longest <= mcp_auth._PATH_MAX_LEN


def test_placeholder_explicit_header_falls_through_to_bearer(tmp_path, monkeypatch):
    """Round-5 minor #1: the canonical two-stage read — an explicit
    placeholder must not shadow a real bearer user_id off the worklist."""
    from narranexus.platform.module_system._mcp_identity import USER_ID_HEADER

    rows = _audit_capture(tmp_path, monkeypatch)
    headers = {
        USER_ID_HEADER: "current_user",
        "Authorization": f"Bearer nx-agent:{AGENT}~chat~~~usr_shadowed",
    }
    assert TestClient(_app()).post("/messages/", headers=headers).status_code == 200
    (entry,) = rows[0]["detail"]["counts"]
    assert entry["caller"] == "usr_shadowed"


def test_enforce_rejects_explicit_header_user_mismatch(tmp_path, monkeypatch):
    """Pre-push round-6 self-review: the forgery check must cover BOTH
    declaration channels — consumers read the explicit X-NarraNexus-User-Id
    first, so a mismatch there must 401 exactly like the bearer-field one."""
    from narranexus.platform.module_system._mcp_identity import (
        IDENTITY_TOKEN_HEADER,
        USER_ID_HEADER,
    )

    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    token = sign_identity_token("usr_real", priv, issuer=ISSUER_LOCAL)
    headers = {
        IDENTITY_TOKEN_HEADER: token,
        USER_ID_HEADER: "usr_other",  # forged on the channel readers prefer
        # bearer omits the user_id field entirely — the old check saw nothing
        "Authorization": f"Bearer nx-agent:{AGENT}",
    }
    assert TestClient(_app()).post("/messages/", headers=headers).status_code == 401


def test_placeholder_declarations_do_not_trip_the_mismatch(tmp_path, monkeypatch):
    from narranexus.platform.module_system._mcp_identity import (
        IDENTITY_TOKEN_HEADER,
        USER_ID_HEADER,
    )

    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    token = sign_identity_token("usr_real", priv, issuer=ISSUER_LOCAL)
    headers = {
        IDENTITY_TOKEN_HEADER: token,
        USER_ID_HEADER: "current_user",  # a guess, not a declaration
        "Authorization": f"Bearer nx-agent:{AGENT}",
    }
    r = TestClient(_app()).post("/messages/", headers=headers)
    assert r.status_code == 200
    assert r.text == "usr_real"
