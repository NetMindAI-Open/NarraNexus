"""
@file_name: sources.py
@author: Bin Liang
@date: 2026-09-03
@description: Where a plugin comes from: a GitHub Release (fixed asset set), a GitHub repo ref (tarball), or a local directory.

Every source produces the same ``FetchResult``: a directory holding
``narranexus-plugin.json`` (+ ``backend/``, ``frontend/dist/``), the sha256
of each fetched asset, and the ``lifecycle.Source`` record to persist.
Archives are extracted with a path-traversal guard (a member escaping the
destination aborts the fetch). Network goes through an injectable
``httpx.Client`` so tests use ``MockTransport``.
"""
from __future__ import annotations

import io
from contextlib import contextmanager
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol

import httpx

from narranexus.contracts import PluginError
from narranexus.kernel.plugins.install.integrity import sha256_file
from narranexus.kernel.plugins.lifecycle import Source as SourceRecord
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME

GITHUB_API = "https://api.github.com"
RELEASE_ASSETS = (MANIFEST_FILENAME, "backend.zip", "plugin.js", "styles.css", "versions.json")
DOWNLOAD_TIMEOUT_S = 60.0
MAX_ASSET_BYTES = 200 * 1024 * 1024
# What an archive may EXPAND to (a 200 MB zip bomb must not fill the disk) and how many members it may have.
MAX_EXTRACT_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000


class SourceError(PluginError):
    pass


@dataclass
class FetchResult:
    root: Path  # directory containing the manifest
    record: SourceRecord
    assets_sha256: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    mode: str = "copy"  # copy | link


class Source(Protocol):
    def fetch(self, staging: Path, client: httpx.Client | None = None) -> FetchResult: ...

    def describe(self) -> str: ...


def _safe_extract_member(dest: Path, member_name: str) -> Path:
    target = (dest / member_name).resolve()
    if dest.resolve() not in target.parents and target != dest.resolve():
        raise SourceError(f"archive member {member_name!r} escapes the destination")
    return target


class _ExtractBudget:
    """Total bytes and member count an archive may expand to. Declared sizes
    are checked first (cheap), and the bytes actually written are counted too
    (a header can lie)."""

    def __init__(self, max_total_bytes: int, max_members: int) -> None:
        self.max_total_bytes, self.max_members = max_total_bytes, max_members
        self.total = 0
        self.members = 0

    def member(self, name: str, declared: int) -> None:
        self.members += 1
        if self.members > self.max_members:
            raise SourceError(f"archive has more than {self.max_members} members; refused")
        if declared < 0 or self.total + declared > self.max_total_bytes:
            raise SourceError(f"archive expands past {self.max_total_bytes // (1024 * 1024)} MB at {name!r}; refused")

    def copy(self, src: Any, out: Any, name: str) -> None:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                return
            self.total += len(chunk)
            if self.total > self.max_total_bytes:
                raise SourceError(f"archive expands past {self.max_total_bytes // (1024 * 1024)} MB at {name!r}; refused")
            out.write(chunk)


def extract_zip(data: bytes, dest: Path, *, max_total_bytes: int = MAX_EXTRACT_BYTES, max_members: int = MAX_ARCHIVE_MEMBERS) -> int:
    budget = _ExtractBudget(max_total_bytes, max_members)
    count = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        # A truncated or corrupt release asset. Every other refusal in this
        # module is a SourceError — the install pipeline classifies and
        # words those; a bare BadZipFile reached the user as a raw exception
        # unrelated to the plugin source it came from.
        raise SourceError(f"archive is not a valid zip file ({exc}); refused") from exc
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            budget.member(info.filename, info.file_size)
            target = _safe_extract_member(dest, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                budget.copy(src, out, info.filename)
            count += 1
    return count


def extract_tarball(data: bytes, dest: Path, *, strip_first_dir: bool = True, max_total_bytes: int = MAX_EXTRACT_BYTES, max_members: int = MAX_ARCHIVE_MEMBERS) -> int:
    budget = _ExtractBudget(max_total_bytes, max_members)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            if member.issym() or member.islnk():
                raise SourceError(f"archive member {member.name!r} is a link; refused")
            name = member.name
            if strip_first_dir:
                parts = name.split("/", 1)
                if len(parts) < 2:
                    continue
                name = parts[1]
            budget.member(name, member.size)
            target = _safe_extract_member(dest, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            with extracted as src, target.open("wb") as out:
                budget.copy(src, out, name)
            count += 1
    return count


def _get(client: httpx.Client, url: str, *, accept: str = "application/vnd.github+json") -> bytes:
    """GET ``url`` streamed to bytes, aborting the moment the body passes MAX_ASSET_BYTES.

    Reading the whole body and THEN checking its size (the previous shape) is
    no limit at all: a 5 GB release asset was in memory before the check ran.
    """
    try:
        with client.stream("GET", url, headers={"Accept": accept, "User-Agent": "narranexus-plugins"}, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            if resp.status_code == 404:
                raise SourceError(f"{url}: not found")
            if resp.status_code >= 400:
                raise SourceError(f"{url}: HTTP {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_ASSET_BYTES:
                raise SourceError(f"{url}: asset exceeds {MAX_ASSET_BYTES // (1024 * 1024)} MB")
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_ASSET_BYTES:
                    raise SourceError(f"{url}: asset exceeds {MAX_ASSET_BYTES // (1024 * 1024)} MB")
            return bytes(buf)
    except httpx.HTTPError as exc:
        raise SourceError(f"{url}: {exc}") from exc


def _get_json(client: httpx.Client, url: str) -> Any:
    import json

    try:
        return json.loads(_get(client, url).decode("utf-8"))
    except ValueError as exc:
        raise SourceError(f"{url}: not JSON: {exc}") from exc


def _validate_repo(repo: str) -> str:
    parts = repo.strip().strip("/").split("/")
    if len(parts) != 2 or not all(p and all(c.isalnum() or c in "-_." for c in p) for p in parts):
        raise SourceError(f"repo must be 'owner/name', got {repo!r}")
    if any(p in (".", "..") or p.startswith(".") for p in parts):
        raise SourceError(f"repo must be 'owner/name', got {repo!r}")  # 'owner/..' would rewrite the API path
    return "/".join(parts)


@contextmanager
def _http(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """The caller's client as-is, or a client of our own that is closed on exit."""
    if client is not None:
        yield client
        return
    own = httpx.Client()
    try:
        yield own
    finally:
        own.close()


@dataclass(frozen=True)
class GitHubReleaseSource:
    """A GitHub Release with the fixed asset set; ``tag`` == manifest version (mismatch is a warning, tag wins)."""

    repo: str
    tag: str = ""  # empty → latest release

    def describe(self) -> str:
        return f"github:{self.repo}@{self.tag or 'latest'}"

    def fetch(self, staging: Path, client: httpx.Client | None = None) -> FetchResult:
        with _http(client) as http:
            return self._fetch(staging, http)

    def _fetch(self, staging: Path, client: httpx.Client) -> FetchResult:
        repo = _validate_repo(self.repo)
        url = f"{GITHUB_API}/repos/{repo}/releases/{'tags/' + self.tag if self.tag else 'latest'}"
        release = _get_json(client, url)
        tag = str(release.get("tag_name") or self.tag)
        assets = {a["name"]: a for a in release.get("assets", []) if "name" in a}
        if MANIFEST_FILENAME not in assets:
            raise SourceError(f"release {repo}@{tag} has no {MANIFEST_FILENAME} asset")
        staging.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        warnings: list[str] = []
        for name in RELEASE_ASSETS:
            asset = assets.get(name)
            if asset is None:
                continue
            data = _get(client, asset["browser_download_url"], accept="application/octet-stream")
            if name == "backend.zip":
                extract_zip(data, staging / "backend_tmp")
                inner = staging / "backend_tmp"
                # a zip may contain backend/ at its root or the package files directly
                src = inner / "backend" if (inner / "backend").is_dir() else inner
                shutil.move(str(src), str(staging / "backend"))
                shutil.rmtree(inner, ignore_errors=True)
                (staging / "backend.zip").write_bytes(data)
            elif name in ("plugin.js", "styles.css"):
                (staging / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
                (staging / "frontend" / "dist" / name).write_bytes(data)
            else:
                (staging / name).write_bytes(data)
            hashes[name] = sha256_file(staging / name) if (staging / name).exists() else sha256_file(staging / "frontend" / "dist" / name)
        import json

        version = str(json.loads((staging / MANIFEST_FILENAME).read_text(encoding="utf-8")).get("version", ""))
        if version and version != tag.lstrip("v"):
            warnings.append(f"manifest version {version} differs from release tag {tag}; the tag is authoritative")
        return FetchResult(
            root=staging,
            record=SourceRecord(type="github", repo=repo, tag=tag, assets_sha256=hashes),
            assets_sha256=hashes,
            warnings=warnings,
        )


@dataclass(frozen=True)
class GitHubRepoSource:
    """A repository ref (default branch when empty) fetched as a tarball; the commit sha is recorded."""

    repo: str
    ref: str = ""

    def describe(self) -> str:
        return f"github-repo:{self.repo}@{self.ref or 'default'}"

    def fetch(self, staging: Path, client: httpx.Client | None = None) -> FetchResult:
        with _http(client) as http:
            return self._fetch(staging, http)

    def _fetch(self, staging: Path, client: httpx.Client) -> FetchResult:
        repo = _validate_repo(self.repo)
        ref = self.ref
        if not ref:
            ref = str(_get_json(client, f"{GITHUB_API}/repos/{repo}").get("default_branch") or "main")
        commit = str(_get_json(client, f"{GITHUB_API}/repos/{repo}/commits/{ref}").get("sha") or "")
        data = _get(client, f"{GITHUB_API}/repos/{repo}/tarball/{ref}", accept="application/octet-stream")
        staging.mkdir(parents=True, exist_ok=True)
        extract_tarball(data, staging)
        if not (staging / MANIFEST_FILENAME).is_file():
            raise SourceError(f"{repo}@{ref} has no {MANIFEST_FILENAME} at the repository root")
        hashes = {MANIFEST_FILENAME: sha256_file(staging / MANIFEST_FILENAME)}
        return FetchResult(
            root=staging,
            record=SourceRecord(type="github_repo", repo=repo, ref=ref, commit=commit, assets_sha256=hashes),
            assets_sha256=hashes,
        )


@dataclass(frozen=True)
class LocalSource:
    """A directory on this machine: ``link`` keeps it in place (developers, agents); ``copy`` copies it into the plugin home."""

    path: Path
    mode: str = "link"

    def describe(self) -> str:
        return f"local:{self.path} ({self.mode})"

    def fetch(self, staging: Path, client: httpx.Client | None = None) -> FetchResult:
        root = Path(self.path).expanduser().resolve()
        if not (root / MANIFEST_FILENAME).is_file():
            raise SourceError(f"{root} has no {MANIFEST_FILENAME}")
        if self.mode == "copy":
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(root, staging, symlinks=False, ignore=shutil.ignore_patterns("pyenv", "__pycache__", ".git", "node_modules"))
            root = staging
        elif self.mode != "link":
            raise SourceError(f"unknown local mode {self.mode!r}")
        hashes = {MANIFEST_FILENAME: sha256_file(root / MANIFEST_FILENAME)}
        return FetchResult(root=root, record=SourceRecord(type="local", assets_sha256=hashes), assets_sha256=hashes, mode=self.mode)


def parse_source_spec(spec: str) -> Source:
    """``owner/repo``, ``owner/repo@1.2.0`` (release), ``owner/repo#ref`` (repo), ``/abs/path`` or ``./dir`` (local link)."""
    text = spec.strip()
    if text.startswith(("/", "./", "~", "../")) or Path(text).is_dir():
        return LocalSource(Path(text))
    if text.startswith("https://github.com/"):
        text = text[len("https://github.com/"):].rstrip("/")
        if text.endswith(".git"):
            text = text[:-4]
    if "#" in text:
        repo, ref = text.split("#", 1)
        return GitHubRepoSource(repo, ref)
    if "@" in text:
        repo, tag = text.split("@", 1)
        return GitHubReleaseSource(repo, tag)
    return GitHubReleaseSource(text)


__all__ = [
    "FetchResult",
    "GITHUB_API",
    "GitHubReleaseSource",
    "GitHubRepoSource",
    "LocalSource",
    "RELEASE_ASSETS",
    "Source",
    "SourceError",
    "extract_tarball",
    "extract_zip",
    "parse_source_spec",
]
