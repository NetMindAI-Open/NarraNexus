from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_table_is_registered_with_the_plugin_prefix(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        assert host.tables == [("__TABLE_PREFIX__items", "__PLUGIN_ID__")]
