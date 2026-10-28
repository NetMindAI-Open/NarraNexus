"""Wiring tests for the narra_cli / narra_guide MCP tools.

The underlying pieces (validate/sanitize, NarraCliClient, fetch_guide) have their
own suites; these assert the @mcp.tool() functions glue them correctly — a
blocked command short-circuits before spawning, a valid command is sanitized and
forwarded, and narra_guide serves the fetched doc.
"""
from narranexus_plugins.narramessenger_module import (
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

    out = await narra_cli("agent_x", "configure --show")
    assert out["success"] is False
    assert out["error"] == "invalid_command"  # sanitize raised before spawning
    assert "configure" in out["message"]      # the block reason is preserved
    assert called["n"] == 0  # never reached run_narra_cli / spawned anything


async def test_narra_cli_valid_command_is_sanitized_and_forwarded(monkeypatch):
    seen = {}

    async def fake_run(agent_id, args):
        seen["agent_id"] = agent_id
        seen["args"] = args
        return {"success": True, "data": []}

    # narra_cli now calls run_narra_cli(agent_id, args) — no db (it reads the
    # bearer + workspace owner through the ChannelCredentialStore seam).
    monkeypatch.setattr(mt, "run_narra_cli", fake_run)
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


async def test_narra_send_media_works_in_a_process_without_an_http_host(monkeypatch):
    """narra_send_media runs in the MCP process, which exposes no WebHost.

    2026-09-11 prod: ``host_settings()`` raised ``UnknownEntry: service
    'host.web' is not exposed`` there, so the tool could not send any file.
    """
    from types import SimpleNamespace

    from narranexus.contracts.services import WEB_HOST
    from narranexus.contracts.web import MAX_UPLOAD_BYTES_ENV
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    monkeypatch.delitem(KERNEL_REGISTRIES.services._services, WEB_HOST.id, raising=False)
    monkeypatch.setenv(MAX_UPLOAD_BYTES_ENV, "4321")

    async def fake_cred(agent_id):
        return SimpleNamespace(matrix_access_token="tok", matrix_homeserver_url="https://hs")

    async def fake_owner(agent_id):
        return "user_owner"

    seen = {}

    async def fake_send(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "event_id": "$e"}

    monkeypatch.setattr(mt, "_get_credential", fake_cred)
    monkeypatch.setattr(mt, "_get_owner", fake_owner)
    monkeypatch.setattr(mt, "send_media_impl", fake_send)
    narra_send_media = _register_tools()["narra_send_media"]

    out = await narra_send_media("agent_x", "!r:h", "out/photo.png")
    assert out == {"ok": True, "event_id": "$e"}
    assert seen["max_bytes"] == 4321
    assert seen["owner_id"] == "user_owner"
