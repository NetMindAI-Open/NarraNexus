from pathlib import Path

from fastapi.testclient import TestClient

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_hello_route_is_served(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        assert host.names("backend.routes") == ("__PLUGIN_PKG__",)
        client = TestClient(host.test_app())
        assert client.get("/api/x/__PLUGIN_ID__/hello").json()["plugin"] == "__PLUGIN_ID__"
