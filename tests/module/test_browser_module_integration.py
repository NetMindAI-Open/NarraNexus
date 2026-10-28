"""
@file_name: test_browser_module_integration.py
@author:
@date: 2026-09-22
@description: Browser MCP caller scope, prompt assembly and optional host mounting.
"""
from __future__ import annotations

import asyncio
import base64
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import request_ctx
from starlette.datastructures import Headers

from narranexus.platform.context_runtime.context_runtime import ContextRuntime
from narranexus.platform.module_system import agent_id_headers, parse_bearer_identity, stamp_identity_token
from narranexus.platform.module_system.module_runner import ModuleRunner
from narranexus.platform.schema import ContextData
from narranexus_plugins.browser_module import browser_module


@contextmanager
def caller(headers):
    token = request_ctx.set(SimpleNamespace(request=SimpleNamespace(headers=Headers(headers))))
    try:
        yield
    finally:
        request_ctx.reset(token)


class ScopedSession:
    """Service contract double: operations can only run inside a caller scope."""

    is_open = True

    def __init__(self):
        self._scope = ContextVar("test_browser_scope", default=None)
        self.calls = []
        self.bindings = []
        self.control = SimpleNamespace(to_dict=self.control_state)
        self.pages = SimpleNamespace(snapshot=lambda: {
            "pages": [{"id": "p1", "title": "First", "url": "https://example.com", "opener_id": None}],
            "active_page_id": "p1",
        })

    @property
    def scope(self):
        return self._scope.get()

    def bind_scope(self, *, turn_id, thread_id):
        assert turn_id and thread_id
        self._scope.set((turn_id, thread_id))
        self.bindings.append((turn_id, thread_id))

    def record(self, operation):
        assert self.scope is not None, "cached session used without current caller scope"
        self.calls.append((operation, self.scope))
        return {"outcome": "OK"}

    def control_state(self):
        self.record("control")
        return {"holder": "agent", "agent_waiting": False}

    async def navigate(self, url, *, new_page=False):
        return self.record("open")

    async def select_page(self, page_id):
        return self.record("select")

    async def close_page(self, page_id):
        return self.record("close")

    async def read_page(self, *, selector=None, offset=0):
        return self.record("read")

    async def run_script(self, script):
        return self.record("run")

    async def act(self, action, **arguments):
        return self.record("act")


@pytest.fixture
def browser(monkeypatch):
    session = ScopedSession()
    service = SimpleNamespace(
        session_for=lambda agent_id: session,
        open_session=AsyncMock(return_value=(session, None)),
        status=lambda: SimpleNamespace(to_dict=lambda: {"state": "ready", "reason": None}),
        require_ready=lambda: None,
        request_login=AsyncMock(return_value={"outcome": "OK", "handoff": "completed", "login_state": "unverified"}),
        policy_for=AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {
            "default_origin_policy": {"full_cdp_access": "deny"}, "origins": {}, "allow_history_access": False,
            "grants": [["https://private.example", "uploads", "thread_other"]],
        })),
    )
    monkeypatch.setattr(browser_module, "get_service", lambda: service)
    module = browser_module.BrowserModule("agent_1", "user_1", database_client=object())
    return module, service, session


async def runtime_headers(module, *, event="evt_1", narrative="nar_1", user="user_1", extra=None):
    runtime = ContextRuntime("agent_1", user, database_client=object(), event_id=event)
    ctx = ContextData(agent_id="agent_1", user_id=user, narrative_id=narrative,
                      input_content="Browse", working_source="chat", extra_data=extra or {})
    instance = SimpleNamespace(module_class="BrowserModule", instance_id="browser_1", module=module)
    messages, servers, *_ = await runtime.build_input_for_framework(
        [], "System", [instance], ctx,
    )
    return servers["browser_module"]["headers"], messages


async def call(mcp, name, headers, **arguments):
    with caller(headers):
        return await mcp._tool_manager.call_tool(name, {"agent_id": "agent_wrong", **arguments})


@pytest.mark.asyncio
async def test_runtime_scope_survives_bearer_only_and_token_stamping(browser):
    module, _, _ = browser
    headers, _ = await runtime_headers(module)
    first = parse_bearer_identity(headers["Authorization"])
    assert first.event_id == "evt_1"
    assert first.thread_id
    assert headers["X-NarraNexus-Thread-Id"] == first.thread_id
    servers = {"browser": {"headers": headers}}
    stamp_identity_token(servers, "signed.token.value")
    assert parse_bearer_identity(servers["browser"]["headers"]["Authorization"]).thread_id == first.thread_id
    same, _ = await runtime_headers(module, event="evt_2")
    other, _ = await runtime_headers(module, narrative="nar_other")
    other_user, _ = await runtime_headers(module, user="user_other")
    other_room, _ = await runtime_headers(module, extra={"bus_channel_id": "ch_other"})
    assert parse_bearer_identity(same["Authorization"]).thread_id == first.thread_id
    for changed in (other, other_user, other_room):
        assert parse_bearer_identity(changed["Authorization"]).thread_id != first.thread_id


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments,operation", [
    ("browser_open", {"url": "https://example.com"}, "open"),
    ("browser_read", {}, "read"),
    ("browser_run", {"script": "document.title"}, "run"),
    ("browser_act", {"action": "fill", "selector": "input", "text": "Query"}, "act"),
    ("browser_control_state", {}, "control"),
    ("browser_status", {}, "control"),
    ("browser_request_install", {}, None),
    ("browser_tabs", {}, None),
    ("browser_tabs", {"action": "select", "page_id": "p1"}, "select"),
    ("browser_tabs", {"action": "close", "page_id": "p1"}, "close"),
])
async def test_every_tool_binds_the_current_scope_on_reused_sessions(browser, tool, arguments, operation):
    module, service, session = browser
    mcp = module.build_instrumented_mcp_server()
    for event, narrative in (("evt_1", "nar_1"), ("evt_2", "nar_1"), ("evt_3", "nar_2")):
        headers, _ = await runtime_headers(module, event=event, narrative=narrative)
        identity = parse_bearer_identity(headers["Authorization"])
        result = await call(mcp, tool, {"Authorization": headers["Authorization"]}, **arguments)
        assert result["outcome"] == "OK"
        assert session.bindings[-1] == (event, identity.thread_id)
        if operation:
            assert session.calls[-1] == (operation, (event, identity.thread_id))
        assert session.scope is None
    if tool == "browser_open":
        assert service.open_session.call_args.kwargs == {"turn_id": "evt_3", "thread_id": identity.thread_id}
        assert service.open_session.call_args.args == ("agent_1",)


@pytest.mark.asyncio
async def test_browser_rejects_missing_scope_before_touching_session(browser):
    module, service, session = browser
    mcp = module.build_instrumented_mcp_server()
    result = await call(mcp, "browser_open", agent_id_headers("agent_1"), url="https://example.com")
    assert result["outcome"] == "ERROR"
    assert result["code"] == "missing_caller_scope"
    service.open_session.assert_not_awaited()
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"action": "click", "selector": "button[type=submit]"},
    {"action": "click", "x": 0, "y": 20},
    {"action": "fill", "selector": "input[name=query]", "text": "Quote: ' and newline:\ntext"},
    {"action": "select", "selector": "select", "value": "option-value"},
    {"action": "press", "key": "Enter"},
    {"action": "press", "selector": "input", "key": "Control+A"},
    {"action": "scroll", "x": 0, "y": 0, "delta_x": 0, "delta_y": 500},
    {"action": "scroll", "selector": "main", "delta_y": 500},
])
async def test_routine_actions_preserve_arguments_and_refusal_contract(browser, arguments):
    module, _, session = browser
    headers, _ = await runtime_headers(module)
    session.act = AsyncMock(return_value={"outcome": "OK"})
    mcp = module.build_instrumented_mcp_server()
    assert (await call(mcp, "browser_act", headers, **arguments))["outcome"] == "OK"
    session.act.assert_awaited_once_with(**arguments)
    assert session.bindings[-1][0] == "evt_1"
    session.act.return_value = {"outcome": "ERROR", "message": "browser disconnected"}
    result = await call(mcp, "browser_act", headers, **arguments)
    assert result["outcome"] == "ERROR"
    assert result["message"] == "browser disconnected"


@pytest.mark.asyncio
async def test_scoped_paginated_reads_forward_only_data_and_describe_continuation(browser):
    module, _, session = browser
    headers, _ = await runtime_headers(module)
    session.read_page = AsyncMock(return_value={"outcome": "OK", "data": {
        "text": "next page", "offset": 20000, "total": 50000, "next_offset": 40000,
    }})
    mcp = module.build_instrumented_mcp_server()
    selector = '[data-label="quoted value"]'
    result = await call(mcp, "browser_read", headers, selector=selector, offset=20000)
    session.read_page.assert_awaited_once_with(selector=selector, offset=20000)
    assert result["data"]["next_offset"] == 40000
    tool = next(tool for tool in mcp._tool_manager.list_tools() if tool.name == "browser_read")
    assert "selector" in tool.parameters["properties"] and "offset" in tool.parameters["properties"]
    assert "next_offset" in tool.description and "browser_run" not in tool.description


def test_scopes_and_artifact_owner_are_not_model_parameters(browser):
    module, _, _ = browser
    mcp = module.build_instrumented_mcp_server()
    for tool in mcp._tool_manager.list_tools():
        assert not {"turn_id", "thread_id", "user_id"} & tool.parameters["properties"].keys()


@pytest.mark.asyncio
async def test_gathered_runtime_status_is_in_the_actual_turn_prompt(browser):
    module, service, _ = browser
    service.status = lambda: SimpleNamespace(to_dict=lambda: {"state": "absent", "reason": "no-executable"})
    ctx = ContextData(agent_id="agent_1", input_content="Browse")
    await module.gather(ctx)
    runtime = ContextRuntime("agent_1", "user_1", database_client=object(), event_id="evt_1")
    instance = SimpleNamespace(module_class="BrowserModule", instance_id="browser_1", module=module)
    messages, *_ = await runtime.build_input_for_framework([], "System", [instance], ctx)
    assert "absent" in messages[-1]["content"]
    assert "no-executable" in messages[-1]["content"]
    assert "settings/browser" in messages[-1]["content"]
    assert '"full_cdp_access": "deny"' in messages[-1]["content"]
    assert "private.example" not in messages[-1]["content"]
    assert "unverified" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_unrouted_turns_do_not_share_a_conversation_scope(browser):
    module, _, _ = browser
    first, _ = await runtime_headers(module, narrative=None, event="evt_first")
    second, _ = await runtime_headers(module, narrative=None, event="evt_second")
    assert parse_bearer_identity(first["Authorization"]).thread_id
    assert parse_bearer_identity(first["Authorization"]).thread_id != parse_bearer_identity(second["Authorization"]).thread_id
    missing, _ = await runtime_headers(module, event="")
    assert parse_bearer_identity(missing["Authorization"]).thread_id is None


@pytest.mark.asyncio
async def test_status_preserves_install_guidance_when_policy_is_unavailable(browser):
    module, service, _ = browser
    headers, _ = await runtime_headers(module)
    service.status = lambda: SimpleNamespace(to_dict=lambda: {"state": "absent", "reason": "no-executable"})
    service.require_ready = lambda: {"outcome": "NEEDS_HUMAN", "human_action": {"where": "settings/browser"}}
    service.policy_for = AsyncMock(side_effect=ConnectionError("policy database unavailable"))
    result = await call(module.build_instrumented_mcp_server(), "browser_status", headers)
    assert result["outcome"] == "NEEDS_HUMAN"
    assert result["state"] == "absent"
    assert result["configured_policy"]["state"] == "unknown"
    assert result["human_action"]["where"] == "settings/browser"


def test_host_mounts_browser_stream_only_with_browser_tools():
    for names in (("other_module",), ("browser_module",)):
        host = ModuleRunner._build_host_server([(name, FastMCP(name)) for name in names], 0)
        paths = [route.path for route in host.config.app.routes]
        assert any("browser" in path and "stream" in path for path in paths) == ("browser_module" in names)


@pytest.mark.parametrize("enabled", [False, True])
def test_host_owns_browser_service_only_when_enabled(monkeypatch, enabled):
    from starlette.testclient import TestClient
    from narranexus.platform.browser import browser_service

    service = SimpleNamespace(session_for=lambda _: None, close=AsyncMock(), login_control_changed=AsyncMock())
    factory = Mock(return_value=service)
    monkeypatch.setattr(browser_service, "get_shared_service", factory)
    name = "browser_module" if enabled else "other_module"
    host = ModuleRunner._build_host_server([(name, FastMCP(name))], 0)
    with TestClient(host.config.app):
        assert factory.call_count == int(enabled)
        service.close.assert_not_awaited()
    assert service.close.await_count == int(enabled)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments", [
    ("browser_read", {}), ("browser_run", {"script": "document.title"}),
    ("browser_act", {"action": "click", "selector": "button"}),
    ("browser_control_state", {}), ("browser_request_login", {}),
    ("browser_save_evidence", {}),
])
async def test_missing_runtime_and_missing_session_have_actionable_envelopes(browser, tool, arguments):
    module, service, _ = browser
    headers, _ = await runtime_headers(module)
    service.session_for = lambda _: None
    mcp = module.build_instrumented_mcp_server()
    refusal = {"outcome": "NEEDS_HUMAN", "human_action": {"where": "settings/browser", "action": "install_browser"}}
    service.require_ready = lambda: refusal
    assert await call(mcp, tool, headers, **arguments) == refusal
    service.require_ready = lambda: None
    result = await call(mcp, tool, headers, **arguments)
    assert result["outcome"] == "ERROR"
    assert result["code"] == "session_not_open"


@pytest.mark.asyncio
async def test_login_publishes_request_in_trusted_scope_without_claiming_success(browser):
    module, service, session = browser
    headers, _ = await runtime_headers(module)
    result = await call(module.build_instrumented_mcp_server(), "browser_request_login", headers, reason="Verification required")
    assert result["outcome"] == "OK"
    assert result["handoff"] == "completed" and result["login_state"] == "unverified"
    service.request_login.assert_awaited_once_with(
        "agent_1", reason="Verification required", turn_id="evt_1",
        thread_id=parse_bearer_identity(headers["Authorization"]).thread_id,
    )
    assert session.bindings[-1][0] == "evt_1"


@pytest.mark.asyncio
async def test_operation_errors_are_envelopes_and_cancellation_still_propagates(browser):
    module, _, session = browser
    headers, _ = await runtime_headers(module)
    mcp = module.build_instrumented_mcp_server()
    session.read_page = AsyncMock(side_effect=ConnectionError("Browser disconnected"))
    result = await call(mcp, "browser_read", headers)
    assert result["outcome"] == "ERROR"
    assert "disconnected" in result["message"]
    session.read_page = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await call(mcp, "browser_read", headers)
    assert session.scope is None


class PageCdp:
    """The external browser transport is the only fake in the policy integration."""

    is_open = True

    def __init__(self):
        self.url = "https://example.com/"
        self.commands = []

    async def call(self, method, params=None, **kwargs):
        self.commands.append(method)
        if method == "Page.navigate":
            self.url = params["url"]
            return {}
        if method == "Runtime.evaluate":
            value = self.url if params["expression"] == "location.href" else {
                "title": "Example", "text": "Page content", "x": 12, "y": 24,
            }
            return {"result": {"value": value}}
        if method.startswith("Input."):
            return {}
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(b"\x89PNG\r\n\x1a\nevidence").decode()}
        raise AssertionError(f"Unexpected CDP method: {method}")


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, {}, {
    "default_origin_policy": {"access": "deny"},
    "origins": {"https://www.zhihu.com": {"access": "ask"}, "https://another.example": {"access": "deny"}},
    "denials": [["https://www.zhihu.com", "access", "turn:evt_1"]],
}])
async def test_default_access_needs_no_site_approval_across_conversations(browser, monkeypatch, db_client, stored):
    from narranexus.platform.browser.browser_service import BrowserService
    from narranexus.platform.browser._browser_impl import runtime_launch
    from narranexus.platform.browser._browser_impl.session import BrowserSession
    from narranexus.platform.utils.db import db_factory

    module, _, _ = browser
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    if stored is not None:
        await db_client.insert("instance_browser_policies", {
            "agent_id": "agent_1", "policy_json": json.dumps(stored),
        })
    service = BrowserService(locate=lambda: Path("/test/chromium"), probe=lambda _: "test")
    cdp = PageCdp()

    async def launch(**kwargs):
        return BrowserSession(cdp=cdp, policy=kwargs["policy"], audit=lambda _: None,
                              turn_id=kwargs["turn_id"], thread_id=kwargs["thread_id"],
                              policy_provider=kwargs["policy_provider"])

    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    monkeypatch.setattr(browser_module, "get_service", lambda: service)
    mcp = module.build_instrumented_mcp_server()
    headers, _ = await runtime_headers(module)
    assert (await call(mcp, "browser_open", headers, url="https://www.zhihu.com/"))["outcome"] == "OK"
    assert (await call(mcp, "browser_read", headers))["outcome"] == "OK"
    assert (await call(mcp, "browser_act", headers, action="fill", selector="input", text="Query"))["outcome"] == "OK"
    next_headers, _ = await runtime_headers(module, event="evt_next", narrative="nar_other")
    assert (await call(mcp, "browser_open", next_headers, url="https://another.example/"))["outcome"] == "OK"
    assert (await call(mcp, "browser_run", next_headers, script="document.title"))["outcome"] == "REJECTED"


@pytest.mark.asyncio
async def test_cached_session_refreshes_script_permissions_without_gating_browsing(browser, monkeypatch):
    from narranexus.platform.browser.browser_service import BrowserService
    from narranexus.platform.browser._browser_impl import runtime_launch
    from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy
    from narranexus.platform.browser._browser_impl.session import BrowserSession

    module, _, _ = browser
    policy = BrowserPolicy()
    cdp, audit = PageCdp(), []
    service = BrowserService(locate=lambda: Path("/test/chromium"), probe=lambda _: "test")
    service.policy_for = AsyncMock(return_value=policy)

    async def launch(**kwargs):
        return BrowserSession(cdp=cdp, policy=kwargs["policy"], audit=audit.append,
                              turn_id=kwargs["turn_id"], thread_id=kwargs["thread_id"],
                              policy_provider=kwargs["policy_provider"])

    launcher = AsyncMock(side_effect=launch)
    monkeypatch.setattr(runtime_launch, "launch_session", launcher)
    monkeypatch.setattr(browser_module, "get_service", lambda: service)
    mcp = module.build_instrumented_mcp_server()
    headers, _ = await runtime_headers(module)
    assert (await call(mcp, "browser_open", headers, url=cdp.url))["outcome"] == "OK"
    next_headers, _ = await runtime_headers(module, event="evt_2", narrative="nar_other")
    assert (await call(mcp, "browser_read", next_headers))["outcome"] == "OK"
    assert (await call(mcp, "browser_run", next_headers, script="document.title"))["outcome"] == "REJECTED"
    policy.origins["https://example.com"] = OriginPolicy(full_cdp_access="allow")
    assert (await call(mcp, "browser_run", next_headers, script="document.title"))["outcome"] == "OK"
    policy.origins["https://example.com"] = OriginPolicy(full_cdp_access="deny")
    assert (await call(mcp, "browser_run", next_headers, script="document.title"))["outcome"] == "REJECTED"
    assert (await call(mcp, "browser_read", next_headers))["outcome"] == "OK"
    assert audit[-1]["turn_id"] == "evt_2"
    launcher.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_http_unrestricted_browsing_login_and_evidence_roundtrip(browser, monkeypatch, db_client, tmp_path):
    """Real service/store/routes/artifacts; only Chromium and runtime discovery are faked."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from backend import auth
    from backend.routes import browser as routes, _ownership
    from narranexus.platform.browser.browser_service import BrowserService
    from narranexus.platform.browser._browser_impl import runtime_launch
    from narranexus.platform.browser._browser_impl.session import BrowserSession
    from narranexus.platform.repository.artifact_repository import ArtifactRepository
    from narranexus.platform.settings import settings
    from narranexus.platform.utils.db import db_factory
    from narranexus_plugins.browser_module._browser_module_impl import evidence

    module, _, _ = browser
    await db_client.insert("agents", {"agent_id": "agent_1", "agent_name": "Browser test", "created_by": "user_1"})
    await db_client.insert("instance_browser_policies", {
        "agent_id": "agent_1", "policy_json": json.dumps({"default_origin_policy": {"access": "ask"}}),
    })
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db_client))
    monkeypatch.setattr(_ownership, "get_db_client", AsyncMock(return_value=db_client))
    monkeypatch.setattr(auth, "_is_cloud_mode", lambda: False)
    cdp = PageCdp()
    service = BrowserService(locate=lambda: Path("/test/chromium"), probe=lambda _: "test")
    # Separate service instances reproduce the backend/MCP process boundary.
    monkeypatch.setattr(routes, "_service", BrowserService(locate=lambda: None))
    monkeypatch.setattr(browser_module, "get_service", lambda: service)

    async def launch(**kwargs):
        return BrowserSession(cdp=cdp, policy=kwargs["policy"], audit=lambda _: None,
                              turn_id=kwargs["turn_id"], thread_id=kwargs["thread_id"],
                              policy_provider=kwargs["policy_provider"])

    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/browser")
    app.middleware("http")(auth.auth_middleware)
    headers, _ = await runtime_headers(module)
    mcp = module.build_instrumented_mcp_server()
    first = await call(mcp, "browser_open", headers, url=cdp.url)
    assert first["outcome"] == "OK"
    assert "Page.navigate" in cdp.commands
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-User-Id": "user_1"}) as client:
        pending = await client.get("/api/browser/notices/agent_1")
        assert pending.status_code == 200
        assert pending.json()["pending"] == []

    assert (await call(mcp, "browser_open", headers, url=cdp.url))["outcome"] == "OK"
    later, _ = await runtime_headers(module, event="evt_2")
    assert (await call(mcp, "browser_act", later, action="fill", selector="input", text="Query"))["outcome"] == "OK"
    assert (await call(mcp, "browser_act", later, action="press", key="Enter"))["outcome"] == "OK"
    assert "Input.dispatchKeyEvent" in cdp.commands
    assert (await call(mcp, "browser_run", later, script="document.title"))["outcome"] == "REJECTED"
    login = asyncio.create_task(call(mcp, "browser_request_login", later))
    session = service.session_for("agent_1")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"X-User-Id": "user_1"}) as client:
            async with asyncio.timeout(3):
                while True:
                    pending = (await client.get("/api/browser/notices/agent_1")).json()["pending"]
                    if pending:
                        break
                    await asyncio.sleep(0.01)
            assert pending[0]["kind"] == "login" and pending[0]["session_id"] == "agent_1"
            forbidden = await client.get("/api/browser/notices/agent_1", headers={"X-User-Id": "user_other"})
            assert forbidden.status_code == 403
        assert not login.done()
        assert await session.take_control("human_panel")
        await service.login_control_changed("agent_1", session, "human_panel", "take")
        assert await session.release_control("human_panel")
        await service.login_control_changed("agent_1", session, "human_panel", "release")
        result = await asyncio.wait_for(login, 3)
        assert result["outcome"] == "OK" and result["handoff"] == "completed"
        assert result["data"]["title"]
        assert await routes.get_service().pending_notices("agent_1") == []
    finally:
        login.cancel()
        await asyncio.gather(login, return_exceptions=True)

    workspace = tmp_path / "user_1" / "agent_1"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    monkeypatch.setattr(evidence, "resolve_agent_workspace_cwd", AsyncMock(return_value=workspace))
    monkeypatch.setattr(evidence, "get_db_client", AsyncMock(return_value=db_client))
    saved = await call(mcp, "browser_save_evidence", later)
    assert saved["outcome"] == "OK"
    record = await ArtifactRepository(db_client).get_by_id(saved["artifact"]["artifact_id"])
    assert record is not None and record.user_id == "user_1" and record.agent_id == "agent_1"
    assert (tmp_path / record.file_path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    count = cdp.commands.count("Page.captureScreenshot")
    other, _ = await runtime_headers(module, event="evt_3", narrative="nar_other")
    assert (await call(mcp, "browser_act", other, action="click", selector="button"))["outcome"] == "OK"
    assert (await call(mcp, "browser_save_evidence", other))["outcome"] == "OK"
    assert cdp.commands.count("Page.captureScreenshot") == count + 1
    assert await routes.get_service().pending_notices("agent_1") == []


@pytest.mark.asyncio
async def test_concurrent_browser_calls_keep_separate_audit_scopes(browser):
    from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
    from narranexus.platform.browser._browser_impl.session import BrowserSession

    module, service, _ = browser
    first_headers, _ = await runtime_headers(module, event="evt_one")
    second_headers, _ = await runtime_headers(module, event="evt_two", narrative="nar_other")
    audit = []
    session = BrowserSession(cdp=PageCdp(), policy=BrowserPolicy(), audit=audit.append,
                             turn_id="", thread_id="")
    service.session_for = lambda _: session
    mcp = module.build_instrumented_mcp_server()
    results = await asyncio.gather(call(mcp, "browser_read", first_headers), call(mcp, "browser_read", second_headers))
    assert all(result["outcome"] == "OK" for result in results)
    assert {(row["turn_id"], row["thread_id"]) for row in audit} == {
        ("evt_one", parse_bearer_identity(first_headers["Authorization"]).thread_id),
        ("evt_two", parse_bearer_identity(second_headers["Authorization"]).thread_id),
    }


@pytest.mark.asyncio
async def test_screenshot_is_registered_by_existing_artifact_service(browser, monkeypatch, db_client, tmp_path):
    from narranexus.platform.settings import settings
    from narranexus.platform.repository.artifact_repository import ArtifactRepository
    from narranexus_plugins.browser_module._browser_module_impl import evidence

    module, _, session = browser
    workspace = tmp_path / "user_1" / "agent_1"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    monkeypatch.setattr(evidence, "resolve_agent_workspace_cwd", AsyncMock(return_value=workspace))
    monkeypatch.setattr(evidence, "get_db_client", AsyncMock(return_value=db_client))
    content = b"\x89PNG\r\n\x1a\n" + b"evidence"

    async def screenshot():
        session.record("screenshot")
        return {"outcome": "OK", "mime_type": "image/png", "data": base64.b64encode(content).decode()}

    session.capture_screenshot = screenshot
    headers, _ = await runtime_headers(module)
    result = await call(module.build_instrumented_mcp_server(), "browser_save_evidence", headers, title="Receipt")
    assert result["outcome"] == "OK"
    record = await ArtifactRepository(db_client).get_by_id(result["artifact"]["artifact_id"])
    assert record is not None
    assert record.user_id == "user_1" and record.agent_id == "agent_1"
    assert record.kind == "image/png" and record.title == "Receipt"
    assert (tmp_path / record.file_path).read_bytes() == content
    assert "data" not in result
    assert session.calls[-1][0] == "screenshot"


@pytest.mark.asyncio
async def test_denied_screenshot_never_reaches_artifact_storage(browser, monkeypatch):
    from narranexus_plugins.browser_module._browser_module_impl import evidence

    module, _, session = browser
    headers, _ = await runtime_headers(module)
    session.capture_screenshot = AsyncMock(return_value={"outcome": "REJECTED", "message": "Origin denied"})
    save = AsyncMock()
    monkeypatch.setattr(evidence, "save_evidence", save)
    result = await call(module.build_instrumented_mcp_server(), "browser_save_evidence", headers)
    assert result["outcome"] == "REJECTED"
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_session_capture_contract_and_url_validation():
    from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
    from narranexus.platform.browser._browser_impl.session import BrowserSession

    policy, cdp = BrowserPolicy.from_dict({"default_origin_policy": {"access": "deny"}}), PageCdp()
    session = BrowserSession(cdp=cdp, policy=policy, audit=lambda _: None, turn_id="", thread_id="")
    session.bind_scope(turn_id="evt_1", thread_id="thread_1")
    capture = await session.capture_screenshot()
    assert capture["outcome"] == "OK"
    assert capture["mime_type"] == "image/png"
    assert base64.b64decode(capture["data"]).startswith(b"\x89PNG\r\n\x1a\n")
    session.bind_scope(turn_id="evt_2", thread_id="thread_1")
    assert (await session.capture_screenshot())["outcome"] == "OK"
    count = cdp.commands.count("Page.captureScreenshot")
    cdp.url = "file:///private/data"
    assert (await session.capture_screenshot())["outcome"] == "REJECTED"
    assert cdp.commands.count("Page.captureScreenshot") == count


@pytest.mark.asyncio
async def test_failed_artifact_registration_removes_the_new_screenshot(monkeypatch, tmp_path):
    from narranexus_plugins.browser_module._browser_module_impl import evidence

    monkeypatch.setattr(evidence, "resolve_agent_workspace_cwd", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(evidence, "get_db_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(evidence, "ArtifactService", lambda _: SimpleNamespace(register=AsyncMock(side_effect=ConnectionError("storage unavailable"))))
    capture = {"outcome": "OK", "mime_type": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\nprivate").decode()}
    with pytest.raises(ConnectionError, match="storage unavailable"):
        await evidence.save_evidence(agent_id="agent_1", identity=SimpleNamespace(user_id="user_1", event_id="evt_1"),
                                     capture=capture, title="Evidence")
    assert list(tmp_path.iterdir()) == []


def test_tool_descriptions_state_the_real_input_limits(browser):
    """The agent learns the bounds from the tool descriptions, which restate the
    constants in prose — pin them together. Characters, not bytes: a model
    sizing multi-byte text by KiB would under-fill."""
    from narranexus.platform.browser._browser_impl.limits import (
        MAX_FIELD_TEXT_CHARS,
        MAX_SCRIPT_CHARS,
        MAX_SELECTOR_CHARS,
    )

    module, _, _ = browser
    mcp = module.build_instrumented_mcp_server()
    act = mcp._tool_manager.get_tool("browser_act").description
    run = mcp._tool_manager.get_tool("browser_run").description
    assert f"up to {MAX_FIELD_TEXT_CHARS} characters" in act
    assert f"up to {MAX_SELECTOR_CHARS}" in act
    assert f"to {MAX_SCRIPT_CHARS} characters" in run
    assert "KiB" not in act + run
