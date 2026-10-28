from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_tools_and_server_are_declared(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        provider = host.registry("agent.capabilities.tools").get("__PLUGIN_PKG__")
        assert [t.name for t in provider.list_tools()] == ["__PLUGIN_PKG___echo"]
        assert host.registry("agent.capabilities.mcp_servers").get("__PLUGIN_PKG__").transport == "stdio"
