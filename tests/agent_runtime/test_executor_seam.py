"""
@file_name: test_executor_seam.py
@date: 2026-06-17
@description: Agent-loop Executor seam — config marshaling across the
network boundary, driver selection, and remote NDJSON streaming.

The executor extraction moves step-3 (claude/codex spawn) into its own
service. The provider creds normally ride a ContextVar; these tests lock
that they survive the serialize→ship→apply round-trip, that the driver
factory picks remote vs local correctly, and that the remote driver
parses the NDJSON event stream (and re-raises executor-side errors).
"""
from __future__ import annotations

import pytest

# Importing the package registers the local claude_code/codex_cli drivers.
import xyz_agent_context.agent_framework  # noqa: F401
from xyz_agent_context.agent_framework.agent_loop_driver import (
    get_agent_loop_driver,
)
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
    snapshot_user_config,
)
from xyz_agent_context.agent_framework.remote_agent_loop_driver import (
    RemoteAgentLoopDriver,
)
from xyz_agent_context.agent_runtime.executor_protocol import (
    apply_provider_configs,
    build_agent_loop_request,
    serialize_provider_configs,
)


def test_provider_config_roundtrip():
    """Scoped creds must survive serialize → (ship) → apply unchanged."""
    set_user_config(
        claude=ClaudeConfig(api_key="claude-scoped", base_url="https://x", model="m1", auth_type="bearer_token"),
        openai=OpenAIConfig(api_key="oa-scoped", base_url="https://o"),
        codex=CodexConfig(api_key="codex-scoped", auth_type="api_key"),
    )
    wire = serialize_provider_configs()
    assert wire["claude"]["api_key"] == "claude-scoped"
    assert wire["codex"]["api_key"] == "codex-scoped"

    # Simulate the executor side: clear, then apply from the wire payload.
    set_user_config(claude=ClaudeConfig(), openai=OpenAIConfig(), codex=CodexConfig())
    apply_provider_configs(wire)

    snap = snapshot_user_config()
    assert snap["claude"].api_key == "claude-scoped"
    assert snap["claude"].auth_type == "bearer_token"
    assert snap["codex"].api_key == "codex-scoped"
    assert snap["openai"].api_key == "oa-scoped"


def test_build_request_has_no_cancellation_and_carries_configs():
    set_user_config(claude=ClaudeConfig(api_key="k"), openai=OpenAIConfig(), codex=CodexConfig())
    req = build_agent_loop_request(
        framework="claude_code", working_path="/ws/agent_x",
        messages=[{"role": "user", "content": "hi"}],
        mcp_server_urls={"chat": "http://localhost:7804/mcp"},
        extra_env={"FOO": "1"},
    )
    assert req["framework"] == "claude_code"
    assert req["working_path"] == "/ws/agent_x"
    assert req["mcp_server_urls"]["chat"].endswith("/mcp")
    assert req["extra_env"] == {"FOO": "1"}
    assert "cancellation" not in req
    assert req["provider_configs"]["claude"]["api_key"] == "k"


def test_factory_local_when_executor_url_unset(monkeypatch):
    monkeypatch.delenv("AGENT_EXECUTOR_URL", raising=False)
    d = get_agent_loop_driver("claude_code", working_path="/ws")
    assert not isinstance(d, RemoteAgentLoopDriver)


def test_factory_remote_when_executor_url_set(monkeypatch):
    monkeypatch.setenv("AGENT_EXECUTOR_URL", "http://agent-executor:8020")
    d = get_agent_loop_driver("claude_code", working_path="/ws/agent_x")
    assert isinstance(d, RemoteAgentLoopDriver)
    assert d.framework == "claude_code"
    assert d.working_path == "/ws/agent_x"
    assert d._url == "http://agent-executor:8020/agent-loop"


def test_per_user_executor_url_param_overrides_env(monkeypatch):
    # The broker-resolved per-user URL wins over the static env var.
    monkeypatch.setenv("AGENT_EXECUTOR_URL", "http://static:8020")
    d = get_agent_loop_driver(
        "claude_code", executor_url="http://nx-exec-alice:8020", working_path="/ws/a"
    )
    assert isinstance(d, RemoteAgentLoopDriver)
    assert d._url == "http://nx-exec-alice:8020/agent-loop"


def test_executor_url_none_falls_back_to_local(monkeypatch):
    # No broker URL + no env → in-process driver (local/desktop).
    monkeypatch.delenv("AGENT_EXECUTOR_URL", raising=False)
    d = get_agent_loop_driver("claude_code", executor_url=None, working_path="/ws/a")
    assert not isinstance(d, RemoteAgentLoopDriver)


# ---------- remote streaming (mock aiohttp) ----------

class _FakeContent:
    """Mimics ``aiohttp.StreamReader``'s ``iter_any()`` surface.

    2026-07-09: the driver moved from ``async for line in resp.content``
    to ``async for chunk in resp.content.iter_any()`` to bypass aiohttp's
    128 KiB line-length ceiling. This fake only needs to expose the
    ``iter_any()`` method the new code uses; the async iterator protocol
    is no longer touched by the driver."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def iter_any(self):
        async def _gen():
            for c in self._chunks:
                yield c
        return _gen()


class _FakeResp:
    def __init__(self, chunks):
        self._content = _FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    @property
    def content(self):
        return self._content


class _FakeSession:
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json=None):
        return _FakeResp(self._chunks)


def _patch_aiohttp(monkeypatch, chunks):
    """Patch aiohttp so the driver sees ``chunks`` as its stream body.

    ``chunks`` is a list of bytes objects. Each element is delivered as
    one call to the ``iter_any()`` iterator — so tests can either pass
    one-line-per-chunk (the historical shape) or split a single line
    across multiple chunks (the more realistic transport shape)."""
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(chunks))
    monkeypatch.setattr(aiohttp, "ClientTimeout", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_remote_driver_yields_events(monkeypatch):
    _patch_aiohttp(monkeypatch, [
        b'{"event": {"type": "text", "delta": "hello "}}\n',
        b'{"event": {"type": "text", "delta": "world"}}\n',
    ])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {})]
    assert out == [{"type": "text", "delta": "hello "}, {"type": "text", "delta": "world"}]


class _CancelledToken:
    is_cancelled = True   # bool @property shape, like the real CancellationToken


class _LiveToken:
    is_cancelled = False


@pytest.mark.asyncio
async def test_remote_driver_honours_cancellation_property(monkeypatch):
    """Regression: is_cancelled is a bool @property, not a method. A
    cancelled token must stop the stream WITHOUT raising TypeError
    ('bool' object is not callable)."""
    _patch_aiohttp(monkeypatch, [
        b'{"event": {"type": "text", "delta": "a"}}\n',
        b'{"event": {"type": "text", "delta": "b"}}\n',
    ])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {}, cancellation=_CancelledToken())]
    assert out == []  # stopped before yielding anything, no TypeError


@pytest.mark.asyncio
async def test_remote_driver_live_token_yields_all(monkeypatch):
    _patch_aiohttp(monkeypatch, [b'{"event": {"x": 1}}\n'])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {}, cancellation=_LiveToken())]
    assert out == [{"x": 1}]


@pytest.mark.asyncio
async def test_remote_driver_raises_on_error_line(monkeypatch):
    _patch_aiohttp(monkeypatch, [
        b'{"event": {"type": "text", "delta": "partial"}}\n',
        b'{"error": {"type": "RuntimeError", "message": "boom"}}\n',
    ])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    got = []
    with pytest.raises(RuntimeError, match="boom"):
        async for e in d.agent_loop([], {}):
            got.append(e)
    assert got == [{"type": "text", "delta": "partial"}]


# ---------- 2026-07-09: LineTooLong incident regressions ----------

@pytest.mark.asyncio
async def test_remote_driver_handles_event_line_over_128kib(monkeypatch):
    """Direct regression for the 2026-07-08 multimodal-large-file incident.

    A single NDJSON event line carrying a base64 image is 150-400 KiB.
    aiohttp's ``async for line in resp.content`` raises ``LineTooLong``
    at 131,072 bytes, which is what killed the executor connection and
    let the fallback helper LLM cover it up with a fake reply. The fix
    uses ``iter_any()`` + manual buffering; this test locks in that a
    200 KiB event line now passes through intact.
    """
    payload = "x" * (200 * 1024)  # 200 KiB, well past the 128 KiB aiohttp ceiling
    line = (
        b'{"event": {"type": "tool_call_output_item", "output": "'
        + payload.encode()
        + b'"}}\n'
    )
    _patch_aiohttp(monkeypatch, [line])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {})]
    assert len(out) == 1
    assert out[0]["type"] == "tool_call_output_item"
    assert len(out[0]["output"]) == 200 * 1024


@pytest.mark.asyncio
async def test_remote_driver_reassembles_line_split_across_chunks(monkeypatch):
    """Realistic transport shape: TCP fragments a large event across
    several ``iter_any()`` yields, and the newline may land inside one
    of them. The manual accumulator must stitch a single event out of
    N chunks, not treat each chunk as its own line."""
    line = b'{"event": {"type": "text", "delta": "hello world across chunks"}}\n'
    # Split into 4 arbitrary chunks, with the newline inside the last one.
    chunks = [line[0:20], line[20:40], line[40:55], line[55:]]
    _patch_aiohttp(monkeypatch, chunks)
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {})]
    assert out == [{"type": "text", "delta": "hello world across chunks"}]


@pytest.mark.asyncio
async def test_remote_driver_multiple_events_in_one_chunk(monkeypatch):
    """A single ``iter_any()`` chunk may hold several complete NDJSON
    lines. The accumulator must yield all of them, not just the first."""
    two_lines = (
        b'{"event": {"type": "a"}}\n'
        b'{"event": {"type": "b"}}\n'
    )
    _patch_aiohttp(monkeypatch, [two_lines])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {})]
    assert out == [{"type": "a"}, {"type": "b"}]


@pytest.mark.asyncio
async def test_remote_driver_raises_when_line_exceeds_max_bytes(monkeypatch):
    """Belt-and-suspenders: a genuinely malformed stream (no newline
    ever arrives, buffer grows past ``_MAX_STREAM_BYTES``) must fail
    fast rather than eat memory. We lower the ceiling in this test so
    the assertion doesn't require 50 MB of test data."""
    from xyz_agent_context.agent_framework import remote_agent_loop_driver as m

    monkeypatch.setattr(m, "_MAX_STREAM_BYTES", 1024)  # 1 KiB test ceiling
    junk = b"x" * 2000  # 2 KiB, no newline
    _patch_aiohttp(monkeypatch, [junk])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    with pytest.raises(RuntimeError, match="event line exceeded"):
        async for _ in d.agent_loop([], {}):
            pass


@pytest.mark.asyncio
async def test_remote_driver_yields_trailing_line_without_newline(monkeypatch):
    """If the executor's stream ends without a final ``\\n``, the last
    event MUST still be yielded — losing it is what caused the
    reasoning-tail drops in the original incident's fallback path."""
    _patch_aiohttp(monkeypatch, [
        b'{"event": {"type": "text", "delta": "first"}}\n',
        b'{"event": {"type": "text", "delta": "tail (no newline)"}}',
    ])
    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {})]
    assert out == [
        {"type": "text", "delta": "first"},
        {"type": "text", "delta": "tail (no newline)"},
    ]
