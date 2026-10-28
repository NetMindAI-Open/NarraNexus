import asyncio
from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_settings_resolve_with_defaults(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        ctx = asyncio.run(host.activate())
        assert ctx.settings.get("interval_minutes") == 15
        ctx.settings.set("region", "us")
        assert ctx.settings.get("region") == "us"
