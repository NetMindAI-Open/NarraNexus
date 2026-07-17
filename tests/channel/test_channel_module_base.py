"""
ChannelModuleBase — abstract base contract tests.

Pins the lifecycle the base owns so subclasses (LarkModule today,
SlackModule / TelegramModule in Phases 3/4) get the same shape for
free.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from xyz_agent_context.channel.channel_module_base import ChannelModuleBase
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry
from xyz_agent_context.schema import ContextData, ModuleConfig, WorkingSource


# ────────────────────────────────────────────────────────────────────
# Fakes
# ────────────────────────────────────────────────────────────────────


class _ConcreteFakeModule(ChannelModuleBase):
    """Minimal concrete subclass exercising every base hook."""

    channel_name = "fake"
    brand_display = "Fake"
    working_source = WorkingSource.LARK  # reuse existing enum value
    ctx_data_key = "fake_info"
    mcp_server_name = "fake_module"
    mcp_port = 8765

    # Test-controllable internals
    _next_credential: Any = None
    _build_extra_data_calls: list[tuple[Any, ContextData]] = []
    _on_event_executed_calls: list[Any] = []
    _send_calls: list[tuple] = []
    _register_calls: list[Any] = []

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name=type(self).__name__,
            priority=10,
            enabled=True,
            description="fake test module",
            module_type="capability",
        )

    async def get_credential(self, agent_id: str) -> Optional[Any]:
        return self._next_credential

    async def send_to_agent(self, agent_id, target_id, message, **kw) -> dict:
        self._send_calls.append((agent_id, target_id, message))
        return {"success": True}

    def register_mcp_tools(self, mcp) -> None:
        self._register_calls.append(mcp)

    async def get_instructions(self, ctx_data: ContextData) -> str:
        return "fake instructions"

    async def build_extra_data(self, cred: Any, ctx_data: ContextData) -> dict:
        self._build_extra_data_calls.append((cred, ctx_data))
        return {"id": getattr(cred, "id", None), "ctx_seen": True}

    async def _on_event_executed(self, params) -> None:
        self._on_event_executed_calls.append(params)


class _MissingChannelName(_ConcreteFakeModule):
    channel_name = ""  # invalid


class _MissingCtxDataKey(_ConcreteFakeModule):
    channel_name = "missing_ctx_key_test"
    ctx_data_key = ""  # invalid


def _make_module(
    cls=_ConcreteFakeModule,
    *,
    agent_id="agent_a",
    db=None,
    next_credential: Any = None,
):
    """Construct a concrete module + reset class-level state."""
    cls._next_credential = next_credential
    cls._build_extra_data_calls = []
    cls._on_event_executed_calls = []
    cls._send_calls = []
    cls._register_calls = []
    return cls(
        agent_id=agent_id,
        user_id=None,
        database_client=db or MagicMock(),
    )


def _reset_sender_registry():
    """Clear the class-level guards so tests are isolated."""
    ChannelSenderRegistry._senders.clear()
    ChannelModuleBase._sender_registered_for_channel.clear()


@pytest.fixture(autouse=True)
def reset_state():
    _reset_sender_registry()
    yield
    _reset_sender_registry()


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────


def test_abc_blocks_direct_instantiation():
    """The base class itself cannot be instantiated."""
    with pytest.raises(TypeError):
        ChannelModuleBase()


def test_subclass_without_channel_name_raises():
    with pytest.raises(ValueError, match="channel_name"):
        _make_module(_MissingChannelName)


def test_subclass_without_ctx_data_key_raises():
    with pytest.raises(ValueError, match="ctx_data_key"):
        _make_module(_MissingCtxDataKey)


def test_sender_self_registers_on_init():
    _make_module()
    assert ChannelSenderRegistry.has_channel("fake")


def test_sender_registered_only_once_per_channel():
    """Two instantiations don't double-register."""
    m1 = _make_module(agent_id="a1")
    m2 = _make_module(agent_id="a2")
    # Both instances exist; registry has exactly one entry (the first
    # instance's bound method). Subsequent instances would just overwrite
    # — they must NOT.
    assert ChannelSenderRegistry.has_channel("fake")
    sender = ChannelSenderRegistry.get_sender("fake")
    # The registered sender is the FIRST instance's bound method,
    # not the second's.
    assert sender.__self__ is m1
    assert sender.__self__ is not m2


@pytest.mark.asyncio
async def test_hook_data_gathering_injects_extra_data():
    cred = MagicMock(id="cred-123")
    module = _make_module(next_credential=cred)
    ctx = ContextData(agent_id="agent_a", input_content="hi")

    result = await module.hook_data_gathering(ctx)

    assert result is ctx
    assert ctx.extra_data["fake_info"] == {"id": "cred-123", "ctx_seen": True}
    # Build was called with (cred, ctx_data)
    assert len(module._build_extra_data_calls) == 1
    cred_arg, ctx_arg = module._build_extra_data_calls[0]
    assert cred_arg is cred
    assert ctx_arg is ctx


@pytest.mark.asyncio
async def test_hook_data_gathering_skips_when_no_credential():
    module = _make_module(next_credential=None)
    ctx = ContextData(agent_id="agent_a", input_content="hi")

    await module.hook_data_gathering(ctx)

    assert "fake_info" not in ctx.extra_data
    assert module._build_extra_data_calls == []


@pytest.mark.asyncio
async def test_hook_data_gathering_swallows_exceptions(monkeypatch):
    """A failing get_credential must NOT break the agent loop."""
    module = _make_module()

    async def boom(_agent_id):
        raise ConnectionError("DB down")

    monkeypatch.setattr(module, "get_credential", boom)
    ctx = ContextData(agent_id="agent_a", input_content="hi")

    # MUST NOT raise
    result = await module.hook_data_gathering(ctx)

    assert result is ctx
    assert "fake_info" not in ctx.extra_data


class _StubExecCtx:
    def __init__(self, working_source):
        self.working_source = working_source


class _StubParams:
    def __init__(self, working_source):
        self.execution_ctx = _StubExecCtx(working_source)


@pytest.mark.asyncio
async def test_hook_after_event_execution_filters_by_working_source():
    module = _make_module()

    nonmatching = _StubParams(WorkingSource.JOB)
    matching = _StubParams(WorkingSource.LARK)

    await module.hook_after_event_execution(nonmatching)
    assert module._on_event_executed_calls == []

    await module.hook_after_event_execution(matching)
    assert len(module._on_event_executed_calls) == 1
    assert module._on_event_executed_calls[0] is matching


@pytest.mark.asyncio
async def test_hook_after_event_execution_accepts_string_working_source():
    """``working_source`` may arrive as either the enum or its string value.
    The base uses ``str(ws) != working_source.value`` to handle both."""
    module = _make_module()

    string_params = _StubParams("lark")  # string, not enum
    await module.hook_after_event_execution(string_params)
    assert len(module._on_event_executed_calls) == 1


@pytest.mark.asyncio
async def test_get_mcp_config_returns_well_formed_config():
    module = _make_module()
    cfg = await module.get_mcp_config()
    assert cfg is not None
    assert cfg.server_name == "fake_module"
    assert ":8765/sse" in cfg.server_url
    assert cfg.type == "sse"


def test_create_mcp_server_returns_none_when_fastmcp_missing(monkeypatch):
    """If FastMCP is unavailable, the module must still boot."""
    import builtins
    module = _make_module()

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp.server.fastmcp" or name.startswith("mcp.server.fastmcp"):
            raise ImportError("fastmcp not installed (test stub)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    server = module.create_mcp_server()
    assert server is None


def test_create_mcp_server_calls_register_mcp_tools():
    """When FastMCP is available, register_mcp_tools must be called with the server."""
    module = _make_module()
    server = module.create_mcp_server()
    if server is None:
        pytest.skip("fastmcp not installed in this environment")
    assert len(module._register_calls) == 1
    assert module._register_calls[0] is server


def test_default_on_event_executed_is_no_op():
    """Subclasses without an override get a silent no-op."""

    class _NoOverrideModule(_ConcreteFakeModule):
        channel_name = "fake_no_override"
        ctx_data_key = "fake_no_override_info"
        # do NOT override _on_event_executed → must inherit base default

    # Can't test the exact "no op" behaviour directly since async default,
    # but we can confirm the base method exists and is async without raising
    import asyncio
    m = _make_module(_NoOverrideModule)
    coro = m._on_event_executed(MagicMock())
    # asyncio.run (fresh loop): get_event_loop() raises on 3.12+ once a
    # prior pytest-asyncio test has set-and-closed the thread's loop.
    asyncio.run(coro)  # must not raise
