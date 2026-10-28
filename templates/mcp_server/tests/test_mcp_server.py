from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_mcp_server_is_declared(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        assert host.registry("agent.capabilities.mcp_servers").get("__PLUGIN_PKG__").url == "https://example.com/mcp"
