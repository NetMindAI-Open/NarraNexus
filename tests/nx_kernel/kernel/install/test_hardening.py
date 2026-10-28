"""
@file_name: test_hardening.py
@author: Bin Liang
@date: 2026-09-07
@description: Install-path hardening: a download aborts the moment it passes MAX_ASSET_BYTES (not after it is in memory), archives cannot expand past their budget, the dependency installer's subprocess sees the proxy/CA variables but never PYTHONPATH/VIRTUAL_ENV, and a plugin that declares permissions is installed DISABLED until acknowledged (an upgrade that widens them re-arms the gate).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from narranexus.kernel.plugins.install import deps, sources
from narranexus.kernel.plugins.install.installer import InstallError, Installer
from narranexus.kernel.plugins.install.sources import LocalSource, SourceError, extract_tarball, extract_zip
from narranexus.kernel.plugins.lifecycle import RegistryStore

from .conftest import make_tar, make_zip


class _CountingStream(httpx.SyncByteStream):
    """A real streaming body: it yields chunks lazily and records what the
    server actually produced, so a client that reads the whole body before
    checking its size can be told apart from one that aborts mid-stream."""

    def __init__(self, chunk: bytes, chunks: int, served: list[int]) -> None:
        self.chunk, self.chunks, self.served = chunk, chunks, served

    def __iter__(self):
        for _ in range(self.chunks):
            self.served.append(len(self.chunk))
            yield self.chunk


def test_download_aborts_mid_stream_at_the_byte_limit(monkeypatch):
    """The limit must stop the TRANSFER, not merely report on a finished one.

    The only assertion that can tell the two implementations apart is that the
    server produced FEWER bytes than the payload: reading the whole body and
    then raising raises ``SourceError`` just the same. No content-length header
    here, so the precheck cannot short-circuit the stream (its own test below).
    """
    monkeypatch.setattr(sources, "MAX_ASSET_BYTES", 1000)
    served: list[int] = []
    total = 500 * 10

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_CountingStream(b"x" * 500, 10, served), headers={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceError, match="exceeds"):
        sources._get(client, "https://example.test/big.zip", accept="application/octet-stream")
    assert 0 < sum(served) < total, f"the whole {total}-byte body was produced before the limit fired: {served}"


def test_download_refuses_an_oversized_content_length_without_reading_a_byte(monkeypatch):
    """A truthful ``content-length`` is refused before the body is streamed at all."""
    monkeypatch.setattr(sources, "MAX_ASSET_BYTES", 1000)
    served: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_CountingStream(b"x" * 500, 10, served),
                              headers={"content-length": "5000"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceError, match="exceeds"):
        sources._get(client, "https://example.test/big.zip", accept="application/octet-stream")
    assert served == []


@pytest.mark.parametrize("extract, make", [(extract_zip, make_zip), (extract_tarball, make_tar)])
def test_archive_expansion_is_bounded(tmp_path: Path, extract, make):
    """Both budgets, for BOTH archive formats: the tarball path carried the same
    two ``budget`` calls with no test at all, so deleting either was green."""
    bomb = make({f"f{i}.bin": b"\0" * 10_000 for i in range(5)})
    with pytest.raises(SourceError, match="expands past"):
        extract(bomb, tmp_path / "out", max_total_bytes=20_000)
    with pytest.raises(SourceError, match="more than"):
        extract(bomb, tmp_path / "out2", max_members=2)
    assert extract(bomb, tmp_path / "ok") == 5


def test_dependency_subprocess_env_is_an_allow_list(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ca.pem")
    monkeypatch.setenv("UV_INDEX_URL", "https://mirror")
    monkeypatch.setenv("PYTHONPATH", "/host/src")
    monkeypatch.setenv("VIRTUAL_ENV", "/host/.venv")
    monkeypatch.setenv("PIP_TARGET", "/host/site")
    env = deps.subprocess_env()
    assert env["HTTPS_PROXY"] == "http://proxy:3128" and env["SSL_CERT_FILE"] == "/etc/ca.pem" and env["UV_INDEX_URL"] == "https://mirror"
    assert env["PIP_NO_INPUT"] == "1" and "PATH" in env
    assert not {"PYTHONPATH", "VIRTUAL_ENV", "PIP_TARGET"} & set(env)


def _plugin(root: Path, pid: str, permissions: dict | None = None, version: str = "1.0.0") -> Path:
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "__init__.py").write_text("def activate(ctx):\n    pass\n")
    manifest = {"id": pid, "version": version, "displayName": pid, "minAppVersion": "0.0.0", "hosts": ["backend"], "backend": {"activate": True}, "activationEvents": ["onStartup"]}
    if permissions:
        manifest["permissions"] = permissions
    (root / "narranexus-plugin.json").write_text(json.dumps(manifest))
    return root


def test_declared_permissions_gate_enabling(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    store = RegistryStore(path=tmp_path / "home" / "registry.json", lkg=tmp_path / "home" / "registry.lkg.json")
    installer = Installer(store=store, host="1.19.0")
    plain = _plugin(tmp_path / "acme.plain", "acme.plain")
    installer.install(LocalSource(plain, mode="link"))
    assert store.read().plugins["acme.plain"].enabled is True
    needy = _plugin(tmp_path / "acme.needy", "acme.needy", {"subprocess": True, "network": ["*"]})
    installer.install(LocalSource(needy, mode="link"))
    rec = store.read().plugins["acme.needy"]
    assert rec.enabled is False and rec.state == "registered" and any("acknowledged" in w for w in rec.warnings)
    installer.install(LocalSource(needy, mode="link"), permissions_acknowledged=True, replace=True)
    assert store.read().plugins["acme.needy"].enabled is True
    # an upgrade that asks for more re-arms the gate
    _plugin(tmp_path / "acme.needy", "acme.needy", {"subprocess": True, "network": ["*"], "filesystem": ["~/Documents"]}, version="1.1.0")
    installer.upgrade("acme.needy")
    rec = store.read().plugins["acme.needy"]
    assert rec.enabled is False and rec.permissions_acknowledged is False


def test_blocklist_state_is_honest(tmp_path: Path, monkeypatch):
    """Never-fetched ≠ nothing blocked: the index raises, and the installer refuses REMOTE sources while the blocklist is unknown but still installs a local path."""
    import httpx

    from narranexus.kernel.plugins.install.index import Index, IndexUnavailable
    from narranexus.kernel.plugins.install.installer import InstallError

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    index = Index(cache_dir=tmp_path / "cache", client=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(IndexUnavailable):
        index.blocked()
    assert index.cached_blocked() is None
    # a cached copy IS an answer, even offline
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "blocked_versions.json").write_text(json.dumps({"acme.bad": {"below": "2.0.0", "reason": "leaks"}}))
    index2 = Index(cache_dir=tmp_path / "cache", client=httpx.Client(transport=httpx.MockTransport(down)))
    assert index2.blocked() == {"acme.bad": {"below": "2.0.0", "reason": "leaks"}}
    assert index2.cached_blocked() == {"acme.bad": {"below": "2.0.0", "reason": "leaks"}}

    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    store = RegistryStore(path=tmp_path / "home" / "registry.json", lkg=tmp_path / "home" / "registry.lkg.json")
    installer = Installer(store=store, host="1.19.0", blocked=None)
    with pytest.raises(InstallError, match="blocklist is unavailable"):
        installer.install("github:acme/whatever")
    local = _plugin(tmp_path / "acme.local", "acme.local")
    installer.install(LocalSource(local, mode="link"))
    assert "acme.local" in store.read().plugins


def test_index_ignores_ids_that_are_not_third_party(tmp_path: Path):
    import httpx

    from narranexus.kernel.plugins.install.index import Index

    payload = {"plugins": [{"id": "builtin.chat", "repo": "x/y"}, {"id": "Bad Id", "repo": "x/y"}, {"id": "acme.ok", "repo": "acme/ok"}]}

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.json"):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={})

    index = Index(cache_dir=tmp_path / "cache", client=httpx.Client(transport=httpx.MockTransport(serve)))
    assert [e.id for e in index.entries()] == ["acme.ok"]


def test_install_refuses_an_id_whose_slug_is_already_held(tmp_path: Path, monkeypatch):
    """``acme.auth-sso`` and ``acme.auth_sso`` share table prefix, settings env prefix and
    package name: the second one cannot be installed."""
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    store = RegistryStore(path=tmp_path / "home" / "registry.json", lkg=tmp_path / "home" / "registry.lkg.json")
    installer = Installer(store=store, host="1.19.0")
    installer.install(LocalSource(_plugin(tmp_path / "acme.auth-sso", "acme.auth-sso"), mode="link"))
    with pytest.raises(InstallError, match="already held by 'acme.auth-sso'"):
        installer.install(LocalSource(_plugin(tmp_path / "acme.auth_sso", "acme.auth_sso"), mode="link"))
    assert set(store.read().plugins) == {"acme.auth-sso"}
