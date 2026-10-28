from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_skill_directory_is_declared(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        spec = host.registry("content.skills").get("__PLUGIN_PKG___skill")
        assert spec.manifest_path.is_file()
