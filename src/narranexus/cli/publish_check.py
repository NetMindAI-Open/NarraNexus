"""
@file_name: publish_check.py
@author: Bin Liang
@date: 2026-09-03
@description: The release checklist for a plugin directory (bronze quality bar, spec §16.3) as a list of problems.
"""
from __future__ import annotations

import json
from pathlib import Path

from narranexus.contracts import ManifestError
from narranexus.kernel.plugins.manifest import load_manifest
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME, VERSIONS_FILENAME
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins


def publish_check(root: Path) -> list[str]:
    problems: list[str] = []
    mpath = root / MANIFEST_FILENAME
    if not mpath.is_file():
        return [f"{MANIFEST_FILENAME} is missing"]
    try:
        manifest = load_manifest(mpath, tree=slot_tree_with_builtins())
    except ManifestError as exc:
        return [f"manifest invalid: {exc}"]
    if manifest.is_builtin:
        problems.append("id uses the reserved builtin. prefix")
    if not manifest.description:
        problems.append("description is empty")
    if not manifest.license:
        problems.append("license is empty")
    if not (root / "README.md").is_file():
        problems.append("README.md is missing")
    versions = root / VERSIONS_FILENAME
    if versions.is_file():
        try:
            data = json.loads(versions.read_text(encoding="utf-8"))
            if manifest.version not in data:
                problems.append(f"{VERSIONS_FILENAME} has no entry for {manifest.version}")
        except ValueError:
            problems.append(f"{VERSIONS_FILENAME} is not valid JSON")
    else:
        problems.append(f"{VERSIONS_FILENAME} is missing (silver+)")
    if manifest.backend is not None:
        pkg = root / manifest.backend.package
        if not (pkg / "__init__.py").is_file():
            problems.append(f"backend package {manifest.backend.package}/ has no __init__.py")
    if manifest.frontend is not None:
        entry = root / "frontend" / "dist" / manifest.frontend.entry.replace("frontend/dist/", "")
        if not entry.is_file():
            problems.append(f"frontend entry {manifest.frontend.entry} is not built (commit dist/, D12)")
        elif not manifest.frontend.integrity:
            problems.append("frontend.integrity is empty; set the sha256 SRI of the built bundle")
    if not (root / "tests").is_dir() or not any((root / "tests").glob("test_*.py")):
        problems.append("tests/ has no test_*.py (silver+)")
    if manifest.quality in ("silver", "gold"):
        if not (root / "CHANGELOG.md").is_file():
            problems.append("CHANGELOG.md is missing (silver+)")
        if not any([manifest.permissions.network, manifest.permissions.filesystem, manifest.permissions.subprocess, manifest.permissions.env]):
            problems.append("permissions are not declared (silver+ must declare, even if empty lists)")
    return problems


__all__ = ["publish_check"]
