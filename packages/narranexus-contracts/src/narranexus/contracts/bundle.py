"""
@file_name: bundle.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for team-template bundles (slot ``content.bundles``).

A plugin ships ``.nxbundle`` files; the marketplace lists them as
installable templates and imports them through the same preflight →
confirm flow as downloaded ones. ``sha256`` is recorded so the marketplace
can show what changed on upgrade.

Contract version: ``API_VERSIONS["bundle"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BundleSpec:
    id: str
    path: Path
    sha256: str
    display_name: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("bundle id is required")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256.lower()):
            raise ValueError(f"bundle {self.id!r}: sha256 must be 64 hex chars")
        if self.path.suffix != ".nxbundle":
            raise ValueError(f"bundle {self.id!r}: path must end with .nxbundle")


__all__ = ["BundleSpec"]
