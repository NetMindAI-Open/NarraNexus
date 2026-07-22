"""Wiring tests for the narra_cli / narra_guide MCP tools.

The underlying pieces (validate/sanitize, NarraCliClient, fetch_guide) have their
own suites; these assert the @mcp.tool() functions glue them correctly — a
blocked command short-circuits before spawning, a valid command is sanitized and
forwarded, and narra_guide serves the fetched doc.
"""
from xyz_agent_context.module.narramessenger_module import (
    _narramessenger_mcp_tools as mt,
)


class _FakeMCP:
    """Captures the @mcp.tool()-decorated functions by name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _register_tools():
    mcp = _FakeMCP()
    mt.register_narramessenger_mcp_tools(mcp)
    return mcp.tools


async def test_narra_cli_blocked_command_short_circuits(monkeypatch):
    called = {"n": 0}

    async def fake_run(agent_id, args, *, db):
        called["n"] += 1
        return {"success": True}

    monkeypatch.setattr(mt, "run_narra_cli", fake_run)
    narra_cli = _register_tools()["narra_cli"]

    out = await narra_cli("agent_x", "configure --endpoint http://evil.test")
    assert out["success"] is False
    assert out["error"] == "invalid_command"  # sanitize raised before spawning
    assert "configure" in out["message"]      # the block reason is preserved
    assert called["n"] == 0  # never reached run_narra_cli / spawned anything


async def test_narra_cli_valid_command_is_sanitized_and_forwarded(monkeypatch):
    seen = {}

    async def fake_run(agent_id, args, *, db):
        seen["agent_id"] = agent_id
        seen["args"] = args
        return {"success": True, "data": []}

    async def fake_db():
        return object()

    monkeypatch.setattr(mt, "run_narra_cli", fake_run)
    monkeypatch.setattr(mt.XYZBaseModule, "get_mcp_db_client", fake_db)
    narra_cli = _register_tools()["narra_cli"]

    out = await narra_cli("agent_x", 'im messages --room-id !r:h --keyword "a b"')
    assert out["success"] is True
    assert seen["agent_id"] == "agent_x"
    # shlex-parsed argv, quotes handled.
    assert seen["args"] == ["im", "messages", "--room-id", "!r:h", "--keyword", "a b"]


async def test_narra_cli_empty_command_rejected():
    narra_cli = _register_tools()["narra_cli"]
    out = await narra_cli("agent_x", "   ")
    assert out["success"] is False


async def test_narra_guide_serves_curated_reference(monkeypatch):
    monkeypatch.setattr(mt, "get_guide", lambda: "# curated narra-cli reference")
    narra_guide = _register_tools()["narra_guide"]

    out = await narra_guide("agent_x")
    assert out["success"] is True
    assert "curated narra-cli reference" in out["guide"]
