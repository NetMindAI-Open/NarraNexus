"""
@file_name: test_common_tools_mcp_dispatch.py
@author: Bin Liang
@date: 2026-04-21
@description: Test that create_common_tools_mcp_server dispatches to the
correct web_search backend based on BRAVE_API_KEY.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.common_tools_module import _common_tools_mcp_tools as factory


@pytest.mark.asyncio
async def test_without_brave_key_registers_ddgs_tool(monkeypatch):
    """No BRAVE_API_KEY → DDGS-backed tool is registered, named web_search."""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    # Track which register() was called
    register_calls: list[str] = []

    def fake_ddgs_register(mcp):
        register_calls.append("ddgs")
        @mcp.tool()
        async def web_search(queries: list[str], max_results_per_query: int = 5) -> str:
            return "ddgs-stub"

    def fake_brave_register(mcp, api_key):
        register_calls.append("brave")

    from narranexus_plugins.common_tools_module._common_tools_impl import (
        web_search_ddgs_tool as ddgs_tool,
        web_search_brave_tool as brave_tool,
    )
    monkeypatch.setattr(ddgs_tool, "register", fake_ddgs_register)
    monkeypatch.setattr(brave_tool, "register", fake_brave_register)

    mcp = factory.create_common_tools_mcp_server()

    assert register_calls == ["ddgs"]
    tools = await mcp.list_tools()
    assert any(t.name == "web_search" for t in tools)


@pytest.mark.asyncio
async def test_with_brave_key_registers_brave_tool(monkeypatch):
    """BRAVE_API_KEY set → Brave-backed tool is registered, named web_search."""
    monkeypatch.setenv("BRAVE_API_KEY", "tvly-test-key")

    register_calls: list[tuple] = []

    def fake_ddgs_register(mcp):
        register_calls.append(("ddgs",))

    def fake_brave_register(mcp, api_key):
        register_calls.append(("brave", api_key))
        @mcp.tool()
        async def web_search(queries: list[str], max_results_per_query: int = 5) -> str:
            return "brave-stub"

    from narranexus_plugins.common_tools_module._common_tools_impl import (
        web_search_ddgs_tool as ddgs_tool,
        web_search_brave_tool as brave_tool,
    )
    monkeypatch.setattr(ddgs_tool, "register", fake_ddgs_register)
    monkeypatch.setattr(brave_tool, "register", fake_brave_register)

    mcp = factory.create_common_tools_mcp_server()

    assert register_calls == [("brave", "tvly-test-key")]
    tools = await mcp.list_tools()
    assert any(t.name == "web_search" for t in tools)


@pytest.mark.asyncio
async def test_empty_string_brave_key_treated_as_missing(monkeypatch):
    """BRAVE_API_KEY='' (empty string) → treated as unset, use DDGS.

    Common deployment pitfall: set the env var but leave it blank. We
    don't want to silently fail auth calls against Brave in that case.
    """
    monkeypatch.setenv("BRAVE_API_KEY", "")

    register_calls: list[str] = []

    def fake_ddgs_register(mcp):
        register_calls.append("ddgs")
        @mcp.tool()
        async def web_search(queries: list[str], max_results_per_query: int = 5) -> str:
            return "ddgs"

    def fake_brave_register(mcp, api_key):
        register_calls.append("brave")

    from narranexus_plugins.common_tools_module._common_tools_impl import (
        web_search_ddgs_tool as ddgs_tool,
        web_search_brave_tool as brave_tool,
    )
    monkeypatch.setattr(ddgs_tool, "register", fake_ddgs_register)
    monkeypatch.setattr(brave_tool, "register", fake_brave_register)

    factory.create_common_tools_mcp_server()
    assert register_calls == ["ddgs"]


@pytest.mark.asyncio
async def test_whitespace_only_brave_key_treated_as_missing(monkeypatch):
    """BRAVE_API_KEY='   ' → treated as unset."""
    monkeypatch.setenv("BRAVE_API_KEY", "   ")

    register_calls: list[str] = []

    def fake_ddgs_register(mcp):
        register_calls.append("ddgs")
        @mcp.tool()
        async def web_search(queries: list[str], max_results_per_query: int = 5) -> str:
            return "ddgs"

    def fake_brave_register(mcp, api_key):
        register_calls.append("brave")

    from narranexus_plugins.common_tools_module._common_tools_impl import (
        web_search_ddgs_tool as ddgs_tool,
        web_search_brave_tool as brave_tool,
    )
    monkeypatch.setattr(ddgs_tool, "register", fake_ddgs_register)
    monkeypatch.setattr(brave_tool, "register", fake_brave_register)

    factory.create_common_tools_mcp_server()
    assert register_calls == ["ddgs"]
