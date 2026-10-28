"""
@file_name: skill.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for skill contributions (slot ``content.skills``).

A skill is a directory with a ``SKILL.md`` (the OpenClaw format and its
aliases). Plugins point at such directories; the skill module scans them
like workspace skills and records ``kind`` so the catalog can tell a
plugin-shipped skill from a marketplace or hand-written one.

Contract version: ``API_VERSIONS["skill"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

SkillKind = Literal["plugin", "marketplace", "workspace", "builtin"]


@dataclass(frozen=True)
class SkillSpec:
    path: Path
    kind: SkillKind = "plugin"

    def __post_init__(self) -> None:
        if self.kind not in ("plugin", "marketplace", "workspace", "builtin"):
            raise ValueError(f"unknown skill kind {self.kind!r}")

    @property
    def manifest_path(self) -> Path:
        return self.path / "SKILL.md"


@runtime_checkable
class SkillWorkspace(Protocol):
    """One agent's skills directory as the platform uses it (service ``skills.workspaces``).

    The bundle importer, the marketplace install pipeline / registry and the
    skill sync service drive a workspace through exactly these members; the
    builtin skill module's ``SkillModule`` is the implementation.
    """

    skills_dir: Path | None

    def list_skills(self, include_disabled: bool = False) -> Any: ...
    def get_skill(self, name: str) -> Any: ...
    def read_skill_meta(self, name: str) -> Any: ...
    def merge_skill_meta(self, name: str, fields: Any) -> Any: ...
    def parse_skill_package(self, skill_root: Path) -> Any: ...
    def extract_skill_package(self, zip_path: Path, temp_dir: Path) -> Any: ...
    # Returns (skill_roots: list[Path], canonical_url) — a LIST of roots
    # (2026-09-09: multi-skill repos); InstallPipeline iterates it.
    def fetch_github_repo(self, url: str, branch: Any, temp_dir: Path) -> Any: ...
    def install_from_dir(self, *args: Any, **kwargs: Any) -> Any: ...
    # Returns list[SkillInfo], one per skill the repo ships (never empty).
    def install_from_github(self, url: str, branch: Any) -> Any: ...
    def install_skill(self, zip_path: Path, *, target_dir_name: Any = None) -> Any: ...
    def remove_skill(self, name: str) -> Any: ...


__all__ = ["SkillKind", "SkillSpec", "SkillWorkspace"]
