"""
@file_name: conftest.py
@author: Bin Liang
@date: 2026-09-03
@description: A fake GitHub (httpx MockTransport) with one release and one repo tarball, plus an on-disk plugin fixture.
"""
from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

MANIFEST = {
    "id": "acme.weather",
    "version": "1.2.0",
    "displayName": "Weather",
    "hosts": ["backend"],
    "backend": {"activate": True, "pip": ["httpx>=0.27"]},
    "permissions": {"network": ["api.weather.com"]},
}


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_tar(files: dict[str, bytes], prefix: str = "acme-weather-abc123") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def plugin_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    return home


@pytest.fixture
def fake_github():
    """Routes: release by tag/latest, asset downloads, repo default branch, commit sha, tarball."""
    manifest_bytes = json.dumps(MANIFEST).encode()
    backend_zip = make_zip({"backend/__init__.py": b"def activate(ctx):\n    pass\n"})
    plugin_js = b"export const plugin = {activate(){}};"
    tarball = make_tar({"narranexus-plugin.json": manifest_bytes, "backend/__init__.py": b"def activate(ctx):\n    pass\n"})
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url.endswith("/releases/tags/1.2.0") or url.endswith("/releases/latest"):
            return httpx.Response(200, json={
                "tag_name": "1.2.0",
                "assets": [
                    {"name": "narranexus-plugin.json", "browser_download_url": "https://dl/manifest"},
                    {"name": "backend.zip", "browser_download_url": "https://dl/backend.zip"},
                    {"name": "plugin.js", "browser_download_url": "https://dl/plugin.js"},
                ],
            })
        if url.endswith("/releases/tags/9.9.9"):
            return httpx.Response(404)
        if url == "https://dl/manifest":
            return httpx.Response(200, content=manifest_bytes)
        if url == "https://dl/backend.zip":
            return httpx.Response(200, content=backend_zip)
        if url == "https://dl/plugin.js":
            return httpx.Response(200, content=plugin_js)
        if url.endswith("/repos/acme/narranexus-weather"):
            return httpx.Response(200, json={"default_branch": "main"})
        if "/commits/" in url:
            return httpx.Response(200, json={"sha": "abc123"})
        if "/tarball/" in url:
            return httpx.Response(200, content=tarball)
        if url.endswith("/index.json"):
            return httpx.Response(200, json={"plugins": [{"id": "acme.weather", "repo": "acme/narranexus-weather", "tags": ["weather"], "kinds": ["routes"]}]})
        if url.endswith("/blocked_versions.json"):
            return httpx.Response(200, json={"acme.evil": {"below": "2.0.0", "reason": "steals keys"}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.calls = calls  # type: ignore[attr-defined]
    return client


def write_local_plugin(root: Path, manifest: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "narranexus-plugin.json").write_text(json.dumps(manifest or MANIFEST))
    (root / "backend").mkdir(exist_ok=True)
    (root / "backend" / "__init__.py").write_text("def activate(ctx):\n    pass\n")
    return root
