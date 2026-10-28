"""
@file_name: test_sources.py
@author: Bin Liang
@date: 2026-09-03
@description: Release/repo/local sources produce the same FetchResult shape; archives are traversal-guarded; specs parse.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.kernel.plugins.install.sources import (
    GitHubReleaseSource,
    GitHubRepoSource,
    LocalSource,
    SourceError,
    extract_tarball,
    extract_zip,
    parse_source_spec,
)

from .conftest import make_tar, make_zip, write_local_plugin


def test_release_fetch_places_manifest_backend_and_frontend(fake_github, tmp_path: Path):
    result = GitHubReleaseSource("acme/narranexus-weather", "1.2.0").fetch(tmp_path / "staging", fake_github)
    assert (result.root / "narranexus-plugin.json").is_file()
    assert (result.root / "backend" / "__init__.py").is_file()
    assert (result.root / "frontend" / "dist" / "plugin.js").is_file()
    assert set(result.assets_sha256) == {"narranexus-plugin.json", "backend.zip", "plugin.js"}
    assert result.record.type == "github" and result.record.tag == "1.2.0" and result.warnings == []


def test_release_missing_tag_and_repo_shape_errors(fake_github, tmp_path: Path):
    with pytest.raises(SourceError, match="not found"):
        GitHubReleaseSource("acme/narranexus-weather", "9.9.9").fetch(tmp_path / "s", fake_github)
    with pytest.raises(SourceError, match="owner/name"):
        GitHubReleaseSource("bad", "1.0.0").fetch(tmp_path / "s2", fake_github)


def test_repo_fetch_records_commit(fake_github, tmp_path: Path):
    result = GitHubRepoSource("acme/narranexus-weather").fetch(tmp_path / "staging", fake_github)
    assert result.record.type == "github_repo" and result.record.ref == "main" and result.record.commit == "abc123"
    assert (result.root / "backend" / "__init__.py").is_file()


def test_local_link_and_copy(tmp_path: Path):
    src = write_local_plugin(tmp_path / "dev" / "acme.weather")
    (src / "pyenv").mkdir()
    linked = LocalSource(src).fetch(tmp_path / "staging")
    assert linked.root == src.resolve() and linked.mode == "link"
    copied = LocalSource(src, mode="copy").fetch(tmp_path / "staging2")
    assert copied.root == tmp_path / "staging2" and (copied.root / "backend" / "__init__.py").is_file()
    assert not (copied.root / "pyenv").exists()  # deps are never copied from a dev tree
    with pytest.raises(SourceError, match="no narranexus-plugin.json"):
        LocalSource(tmp_path / "nothing").fetch(tmp_path / "s3")


def test_archives_refuse_traversal_and_links(tmp_path: Path):
    with pytest.raises(SourceError, match="escapes"):
        extract_zip(make_zip({"../evil.py": b"x"}), tmp_path / "z")
    with pytest.raises(SourceError, match="escapes"):
        extract_tarball(make_tar({"../../evil.py": b"x"}), tmp_path / "t")
    assert extract_tarball(make_tar({"a/b.txt": b"1"}), tmp_path / "ok") == 1
    assert (tmp_path / "ok" / "a" / "b.txt").read_text() == "1"


def test_a_corrupt_zip_is_a_source_error(tmp_path: Path):
    """A truncated backend.zip in a release must be refused the way every
    other bad source is (SourceError), not leak a bare BadZipFile past the
    install pipeline's classification."""
    with pytest.raises(SourceError, match="not a valid zip"):
        extract_zip(b"definitely not a zip", tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()


def test_parse_source_spec(tmp_path: Path):
    assert parse_source_spec("acme/weather@1.0.0") == GitHubReleaseSource("acme/weather", "1.0.0")
    assert parse_source_spec("https://github.com/acme/weather.git") == GitHubReleaseSource("acme/weather")
    assert parse_source_spec("acme/weather#dev") == GitHubRepoSource("acme/weather", "dev")
    assert isinstance(parse_source_spec(str(tmp_path)), LocalSource)
