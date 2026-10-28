from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_bundle_is_listed(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        spec = host.registry("content.bundles").get("__PLUGIN_PKG___team")
        assert spec.id == "__PLUGIN_ID__.team" and spec.path.is_file()
