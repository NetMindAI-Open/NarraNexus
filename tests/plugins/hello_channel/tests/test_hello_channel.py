from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_descriptor_trigger_and_module_register(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home", role="workers") as host:
        assert host.names("ingress.channels") == ("hello_channel",)
        assert host.names("ingress.triggers") == ("hello_channel",)
        assert host.names("agent.capabilities.modules") == ("HelloChannelModule",)
