"""
@file_name: validate.py
@author: Bin Liang
@date: 2026-09-03
@description: ``plugin_validate``: manifest + contract versions + dependencies + size, and the "declared vs actual" static scan of permissions.

The scan is deliberately simple and conservative (regex over the plugin's
Python/JS sources): outbound network (httpx/requests/urllib/aiohttp/socket/
fetch), subprocess/os.system, ``os.environ`` reads, and absolute-path file
access outside the workspace. A hit without a matching manifest permission
is a mismatch the user must accept explicitly (spec §10.4); the scan cannot
prove absence, and says so.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from narranexus.contracts import ManifestError
from narranexus.kernel.plugins.compat import Range, Version
from narranexus.kernel.plugins.manifest import Manifest, load_manifest
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME, VERSIONS_FILENAME
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins

_NETWORK = re.compile(r"\b(httpx|requests|urllib\.request|aiohttp|websockets|socket)\b|\bfetch\(|XMLHttpRequest")
_SUBPROCESS = re.compile(r"\bsubprocess\b|os\.system\(|os\.popen\(|asyncio\.create_subprocess")
_ENV = re.compile(r"os\.environ|os\.getenv\(|process\.env")
_ABS_FILE = re.compile(r"open\(\s*['\"]/|Path\(\s*['\"]/")
SCAN_SUFFIXES = (".py", ".ts", ".tsx", ".js")
MAX_SOURCE_BYTES = 5 * 1024 * 1024


@dataclass
class Validation:
    ok: bool
    plugin_id: str = ""
    version: str = ""
    problems: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)  # declared vs actual
    findings: dict[str, list[str]] = field(default_factory=dict)  # category -> files
    size_bytes: int = 0
    tree_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


HASH_EXCLUDED_DIRS = frozenset({"pyenv", "node_modules", "__pycache__", ".git", ".pytest_cache", ".test-home"})
HASH_EXCLUDED_FILES = frozenset({".test-report.json", ".plugin-changelog.jsonl"})


def tree_hash(root: Path) -> str:
    """Content hash of every source/config file (stable across machines; used as the diff/report key).

    Tooling artefacts (test home, report, changelog, caches) are excluded so
    running the tests or logging an edit does not change the hash of what
    was tested.
    """
    digest = hashlib.sha256()
    for path in sorted(
        p for p in root.rglob("*")
        if p.is_file() and not any(part in HASH_EXCLUDED_DIRS for part in p.parts) and p.name not in HASH_EXCLUDED_FILES
    ):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def scan_sources(root: Path) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {"network": [], "subprocess": [], "env": [], "absolute_file": []}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES or "pyenv" in path.parts or "node_modules" in path.parts:
            continue
        if path.stat().st_size > MAX_SOURCE_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = str(path.relative_to(root))
        if _NETWORK.search(text):
            findings["network"].append(rel)
        if _SUBPROCESS.search(text):
            findings["subprocess"].append(rel)
        if _ENV.search(text):
            findings["env"].append(rel)
        if _ABS_FILE.search(text):
            findings["absolute_file"].append(rel)
    return findings


def validate_plugin(root: Path, *, host_version: str, installed: dict[str, str] | None = None) -> Validation:
    mpath = root / MANIFEST_FILENAME
    if not mpath.is_file():
        return Validation(ok=False, problems=[f"{MANIFEST_FILENAME} is missing"])
    try:
        manifest: Manifest = load_manifest(mpath, tree=slot_tree_with_builtins(), host_version=host_version)
    except ManifestError as exc:
        return Validation(ok=False, problems=[str(exc)])
    v = Validation(ok=True, plugin_id=manifest.id, version=manifest.version)
    if manifest.is_builtin or manifest.protected:
        v.problems.append("builtin/protected plugins cannot be produced by an agent")
    for dep, spec in manifest.dependencies.items():
        have = (installed or {}).get(dep)
        if have is None:
            v.problems.append(f"dependency {dep} is not installed")
        else:
            try:
                if not Range.parse(spec).contains(Version.parse(have)):
                    v.problems.append(f"dependency {dep} {have} does not satisfy {spec}")
            except ValueError as exc:
                v.problems.append(f"dependency {dep}: {exc}")
    versions = root / VERSIONS_FILENAME
    if versions.is_file():
        try:
            data = json.loads(versions.read_text(encoding="utf-8"))
            if manifest.version not in data:
                v.problems.append(f"{VERSIONS_FILENAME} lacks {manifest.version}")
        except ValueError:
            v.problems.append(f"{VERSIONS_FILENAME} is not JSON")
    if manifest.backend is not None and not (root / manifest.backend.package / "__init__.py").is_file():
        v.problems.append(f"backend package {manifest.backend.package}/ has no __init__.py")
    v.findings = scan_sources(root)
    perms = manifest.permissions
    if v.findings["network"] and not perms.network:
        v.mismatches.append(f"network access in {v.findings['network']} but permissions.network is empty")
    if v.findings["subprocess"] and not perms.subprocess:
        v.mismatches.append(f"subprocess use in {v.findings['subprocess']} but permissions.subprocess is false")
    if v.findings["env"] and not perms.env:
        v.mismatches.append(f"environment reads in {v.findings['env']} but permissions.env is empty (use settings.secret instead)")
    if v.findings["absolute_file"] and "workspace" not in perms.filesystem and not perms.filesystem:
        v.mismatches.append(f"absolute file paths in {v.findings['absolute_file']} but permissions.filesystem is empty")
    v.size_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    v.tree_hash = tree_hash(root)
    v.ok = not v.problems
    return v


__all__ = ["Validation", "scan_sources", "tree_hash", "validate_plugin"]
