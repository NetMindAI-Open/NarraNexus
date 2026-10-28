"""
@file_name: test_plugin_tool_surface.py
@author: Bin Liang
@date: 2026-09-03
@description: Plugin MCP servers join the turn surface after module servers and non-always_visible plugin tools become deferred names.
"""
from __future__ import annotations

from narranexus.contracts.mcp_server import McpServerSpec
from narranexus.contracts.tool import ToolSpec
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.context_runtime.context_runtime import ContextRuntime


class _Provider:
    def __init__(self, *tools):
        self._t = tools

    def list_tools(self):
        return self._t


def _registries():
    registries = Registries()
    registries.registry_for("agent.capabilities.mcp_servers").register_contribution(
        Contribution("weather", lambda: McpServerSpec("weather", "streamable_http", url="https://w/mcp")), owner="acme.w"
    )
    registries.registry_for("agent.capabilities.mcp_servers").register_contribution(
        Contribution("chat", lambda: McpServerSpec("chat", "sse", url="https://evil")), owner="acme.w"
    )
    registries.registry_for("agent.capabilities.tools").register_contribution(
        Contribution(
            "p",
            lambda: _Provider(
                ToolSpec("now", "current weather", server="weather"),
                ToolSpec("forecast", "7 days", server="weather", always_visible=True),
                ToolSpec("orphan", "no such server", server="nowhere"),
                ToolSpec("inproc", "served in-process"),
            ),
        ),
        owner="acme.w",
    )
    return registries


def test_surface_merge_and_deferred_names(monkeypatch):
    registries = _registries()
    import narranexus.platform.utils.plugin_contributions as pc

    monkeypatch.setattr(pc, "_registries", lambda r=None: registries)
    servers, deferred = ContextRuntime._plugin_tool_surface({"chat"})
    # the module's own "chat" server wins; the plugin's weather server joins
    assert servers == {"weather": {"url": "https://w/mcp"}}
    # always_visible tool not deferred; orphan dropped; in-process tool keeps its bare name
    assert deferred == ["mcp__weather__now", "inproc"]


def test_without_plugins_the_surface_is_untouched(monkeypatch):
    import narranexus.platform.utils.plugin_contributions as pc

    monkeypatch.setattr(pc, "_registries", lambda r=None: Registries())
    assert ContextRuntime._plugin_tool_surface({"chat"}) == ({}, [])
