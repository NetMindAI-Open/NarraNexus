from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_worker_is_declared_for_the_workers_host(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home", role="workers") as host:
        spec = host.registry("backend.workers").get("__PLUGIN_PKG___sync")
        assert spec.host == "workers" and spec.name == "__PLUGIN_PKG___sync"
