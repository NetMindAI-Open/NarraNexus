"""
@file_name: test_deps_index_integrity.py
@author: Bin Liang
@date: 2026-09-03
@description: Dependency install is wheels-only with a deadline and refuses options; the index caches and survives offline; SRI/sha256 helpers.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from narranexus.kernel.plugins.install.deps import DepsError, build_command, install_deps
from narranexus.kernel.plugins.install.index import Index
from narranexus.kernel.plugins.install.integrity import sha256_file, sri_for


def test_build_command_is_wheels_only_with_pypi_and_extra_indexes(tmp_path: Path):
    cmd = build_command(("httpx>=0.27",), tmp_path, indexes=("https://my.index/simple",))
    assert "--only-binary=:all:" in cmd and "--target" in cmd and "https://pypi.org/simple" in cmd
    assert "--extra-index-url" in cmd and cmd[-1] == "httpx>=0.27"


def test_install_deps_uses_runner_and_refuses_bad_input(tmp_path: Path):
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"], seen["timeout"] = cmd, timeout
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    result = install_deps(["httpx>=0.27", " "], tmp_path / "deps", runner=runner, timeout_s=7)
    assert result is not None and result.requirements == ("httpx>=0.27",) and seen["timeout"] == 7
    assert install_deps([], tmp_path / "deps") is None
    with pytest.raises(DepsError, match="options"):
        install_deps(["--pre foo"], tmp_path / "deps", runner=runner)
    with pytest.raises(DepsError, match="https"):
        install_deps(["x"], tmp_path / "deps", indexes=("http://evil",), runner=runner)

    def failing(cmd, timeout):
        return subprocess.CompletedProcess(cmd, 1, "", "no wheel for x")

    with pytest.raises(DepsError, match="no wheel for x"):
        install_deps(["x"], tmp_path / "deps", runner=failing)

    def slow(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(DepsError, match="exceeded"):
        install_deps(["x"], tmp_path / "deps", runner=slow, timeout_s=1)


def test_index_caches_and_survives_offline(fake_github, tmp_path: Path):
    index = Index(cache_dir=tmp_path / "cache", base_url="https://idx", client=fake_github)
    assert [e.id for e in index.search("weath")] == ["acme.weather"]
    assert index.blocked() == {"acme.evil": {"below": "2.0.0", "reason": "steals keys"}}
    fetches = len(fake_github.calls)
    index.refresh()  # within ttl: served from cache
    assert len(fake_github.calls) == fetches
    offline = Index(cache_dir=tmp_path / "cache", base_url="https://idx", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))), ttl_s=0)
    assert offline.get("acme.weather") is not None  # stale cache beats no data
    assert offline.search("nothing") == ()


def test_integrity_helpers(tmp_path: Path):
    f = tmp_path / "plugin.js"
    f.write_bytes(b"hello")
    digest = sha256_file(f)
    # No source verification exists (integrity.py says so in its header); the
    # recorded hashes only detect local tampering, so there is no verify_*.
    from narranexus.kernel.plugins.install import integrity as integrity_mod

    assert not hasattr(integrity_mod, "verify_sha256")
    assert "NO SOURCE VERIFICATION" in (integrity_mod.__doc__ or "")
    assert sri_for(f).startswith("sha256-") and len(sri_for(f)) > 20
