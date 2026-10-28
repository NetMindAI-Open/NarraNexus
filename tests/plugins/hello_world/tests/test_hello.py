from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_loads(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        assert host.plugin_id == "acme.hello_world"
