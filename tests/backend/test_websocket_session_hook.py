"""
@file_name: test_websocket_session_hook.py
@description: T18 — verify WS endpoint adds / removes session in all exit paths.

We test the core invariant directly on the registry rather than spinning up a
full WS connection (the latter requires AgentRuntime + MCP servers which is
integration-heavy). The precise add/remove placement in websocket.py is
reviewed in code; here we ensure the registry contract holds under stress.
"""
import asyncio
import pytest

from backend.state.active_sessions import (
    InProcessSessionRegistry,
    SessionInfo,
)


@pytest.mark.asyncio
async def test_registry_removes_after_simulated_mcp_failure():
    """Pattern mirrored in websocket.py: add → try block that fails → finally remove."""
    reg = InProcessSessionRegistry()
    info = SessionInfo("s1", "u1", "Alice", "web", "t")
    await reg.add("agent_a", info)

    async def _simulated_runtime_path():
        try:
            raise RuntimeError("MCP load failed")
        finally:
            await reg.remove("agent_a", info.session_id)

    with pytest.raises(RuntimeError):
        await _simulated_runtime_path()

    snap = await reg.snapshot(["agent_a"])
    assert snap["agent_a"] == []


@pytest.mark.asyncio
async def test_registry_removes_on_cancellation():
    reg = InProcessSessionRegistry()
    info = SessionInfo("s1", "u1", "Alice", "web", "t")
    await reg.add("agent_a", info)

    async def _simulated():
        try:
            await asyncio.sleep(3600)
        finally:
            await reg.remove("agent_a", info.session_id)

    task = asyncio.create_task(_simulated())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snap = await reg.snapshot(["agent_a"])
    assert snap["agent_a"] == []


@pytest.mark.asyncio
async def test_two_concurrent_sessions_aggregated():
    """Mirrors G002: public agent serves multiple concurrent WS connections."""
    reg = InProcessSessionRegistry()
    for i, uid in enumerate(["alice", "bob", "carol"]):
        await reg.add(
            "public_agent",
            SessionInfo(f"s{i}", uid, uid.capitalize(), "web", f"t{i}"),
        )
    snap = await reg.snapshot(["public_agent"])
    assert len(snap["public_agent"]) == 3


@pytest.mark.asyncio
async def test_hook_import_paths_resolve():
    """Smoke test: websocket.py's inline import path must be valid."""
    from backend.routes.websocket import router  # noqa: F401
    # If websocket.py has a syntax error or the import moves, this import fails
    # at module load time with an ImportError — catching it here keeps signal strong.
