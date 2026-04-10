"""
@file_name: nac_doc_lib.py
@author: NexusAgent
@date: 2026-04-09
@description: Shared library for the NAC Doc system scripts (scaffold, check, audit).

Encodes include/exclude rules for mirroring source code into .nac_doc/mirror/,
provides frontmatter read/write helpers, and tree walking primitives.

Project-specific rules (INCLUDE_SPECS, EXCLUDED_* constants) live at the top
of this file — extract them to a config file later when packaging as a skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


# ============================================================================
# Project-specific rules — extract to config for cross-project reuse
# ============================================================================


@dataclass(frozen=True)
class IncludeSpec:
    """One mirrored source root: a repo-relative directory + extensions to include."""

    root: str
    extensions: tuple[str, ...]


# Top-level source trees that get mirrored into .nac_doc/mirror/
INCLUDE_SPECS: tuple[IncludeSpec, ...] = (
    IncludeSpec(root="src/xyz_agent_context", extensions=(".py",)),
    IncludeSpec(root="backend", extensions=(".py",)),
    IncludeSpec(root="frontend/src", extensions=(".tsx", ".ts")),
    IncludeSpec(root="tauri/src-tauri/src", extensions=(".rs",)),
)

# Directories (repo-relative) that are entirely excluded from mirroring.
# The directory itself gets no _overview.md and no individual mds.
EXCLUDED_DIRS: frozenset[str] = frozenset(
    [
        "tests",
        "frontend/src/types",  # Covered by one synthetic _overview.md (see OVERVIEW_ONLY_DIRS).
    ]
)

# Directory basenames that are always skipped regardless of depth (build artifacts).
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    [
        "__pycache__",
        "node_modules",
        "target",
        ".venv",
        "dist",
        "build",
        ".next",
    ]
)

# Dirs where only _overview.md is written; individual files inside are NOT
# required to have corresponding mds. Manually authored mds inside these dirs
# ARE still validated by check (no orphans).
# Pattern matches directory basenames.
OVERVIEW_ONLY_DIR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^_.*_impl$"),  # e.g. _module_impl, _narrative_impl, _event_impl
)

# Dirs that get a synthetic _overview.md but no individual file mds
# (repo-relative). These override INCLUDE_SPECS extension rules.
OVERVIEW_ONLY_DIRS: frozenset[str] = frozenset(
    [
        "frontend/src/types",
    ]
)


# ============================================================================
# Path helpers
# ============================================================================


def repo_root() -> Path:
    """Return the repo root (assumed to be parent of this scripts/ package)."""
    return Path(__file__).resolve().parent.parent


def mirror_root() -> Path:
    return repo_root() / ".nac_doc" / "mirror"


def mirror_path_for_code_file(code_file: Path) -> Path:
    """
    Given a repo-relative or absolute code file path, return the absolute path
    to its corresponding mirror md under .nac_doc/mirror/.
    """
    rel = _to_repo_relative(code_file)
    return mirror_root() / f"{rel}.md"


def mirror_path_for_dir(code_dir: Path) -> Path:
    """Given a directory, return the absolute path to its _overview.md under mirror/."""
    rel = _to_repo_relative(code_dir)
    return mirror_root() / rel / "_overview.md"


def code_file_for_mirror_path(mirror_md: Path) -> Path | None:
    """
    Inverse of mirror_path_for_code_file. Returns the expected code file path
    (absolute) if mirror_md is a single-file md, or None if it's an _overview.md.
    """
    rel = mirror_md.relative_to(mirror_root())
    if rel.name == "_overview.md":
        return None
    # Strip the trailing .md → that gives the code filename
    name = rel.name
    if not name.endswith(".md"):
        return None
    code_name = name[: -len(".md")]
    return repo_root() / rel.parent / code_name


def code_dir_for_overview_path(overview_md: Path) -> Path:
    """Given an _overview.md path, return the corresponding code directory."""
    rel = overview_md.relative_to(mirror_root())
    return repo_root() / rel.parent


def _to_repo_relative(p: Path) -> Path:
    if p.is_absolute():
        return p.relative_to(repo_root())
    return p


# ============================================================================
# Rule evaluation
# ============================================================================


def is_overview_only_dir(dir_path: Path) -> bool:
    """
    True if this directory is 'overview-only' — only _overview.md is required,
    individual file mds inside are NOT required.
    """
    try:
        rel = _to_repo_relative(dir_path)
        if str(rel).replace("\\", "/") in OVERVIEW_ONLY_DIRS:
            return True
    except ValueError:
        pass
    return any(pat.match(dir_path.name) for pat in OVERVIEW_ONLY_DIR_PATTERNS)


def is_excluded_dir(dir_path: Path) -> bool:
    """True if this directory is entirely excluded (no overview, no files)."""
    if dir_path.name in EXCLUDED_DIR_NAMES:
        return True
    try:
        rel = _to_repo_relative(dir_path)
        return str(rel).replace("\\", "/") in EXCLUDED_DIRS
    except ValueError:
        return False


def is_empty_or_pure_reexport_init(init_py: Path) -> bool:
    """
    True if the given __init__.py is empty or contains only re-export statements
    (imports + __all__ + docstring + blank lines). Such files are excluded from
    mirroring because they have no behavioral content to document.
    """
    if init_py.name != "__init__.py":
        return False
    try:
        text = init_py.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    # Strip docstring: module docstring must be the first statement, quoted.
    lines = text.splitlines()
    cleaned: list[str] = []
    in_docstring = False
    docstring_delim: str | None = None
    for line in lines:
        stripped = line.strip()
        if in_docstring:
            if docstring_delim and docstring_delim in stripped:
                in_docstring = False
            continue
        if not cleaned and (stripped.startswith('"""') or stripped.startswith("'''")):
            delim = stripped[:3]
            # Single-line docstring?
            if len(stripped) >= 6 and stripped.endswith(delim):
                continue
            in_docstring = True
            docstring_delim = delim
            continue
        if not stripped or stripped.startswith("#"):
            continue
        cleaned.append(stripped)

    if not cleaned:
        return True

    allowed_prefixes = ("import ", "from ")
    for line in cleaned:
        if any(line.startswith(p) for p in allowed_prefixes):
            continue
        if line.startswith("__all__"):
            continue
        # Any other statement → not pure re-export
        return False
    return True


# ============================================================================
# Tree walking
# ============================================================================


@dataclass
class MirrorPlan:
    """Result of walking the source trees: what scaffold/check operate on."""

    # Absolute paths of code files that MUST have a mirror md.
    required_file_mds: list[Path] = field(default_factory=list)
    # Absolute paths of directories that MUST have a mirror _overview.md.
    required_dir_overviews: list[Path] = field(default_factory=list)


def walk_source_trees() -> MirrorPlan:
    """
    Walk the configured INCLUDE_SPECS and compute the set of code files and
    directories that require mirror mds. Applies all exclusion rules.
    """
    plan = MirrorPlan()
    root = repo_root()

    for spec in INCLUDE_SPECS:
        spec_root = root / spec.root
        if not spec_root.exists():
            continue
        for dir_path, file_paths in _walk_with_pruning(spec_root):
            # Directory-level overview required (unless excluded entirely)
            if not is_excluded_dir(dir_path):
                plan.required_dir_overviews.append(dir_path)
            # If this dir is overview-only or excluded, don't require file mds
            if is_overview_only_dir(dir_path) or is_excluded_dir(dir_path):
                continue
            for f in file_paths:
                if f.suffix not in spec.extensions:
                    continue
                if is_empty_or_pure_reexport_init(f):
                    continue
                plan.required_file_mds.append(f)

    return plan


def _walk_with_pruning(root: Path) -> Iterator[tuple[Path, list[Path]]]:
    """
    Walk `root` recursively, yielding (dir, [files]) tuples and pruning
    directories that match EXCLUDED_DIR_NAMES.
    """
    if not root.is_dir():
        return
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        files: list[Path] = []
        for entry in entries:
            if entry.is_dir():
                if entry.name in EXCLUDED_DIR_NAMES:
                    continue
                stack.append(entry)
            elif entry.is_file():
                files.append(entry)
        yield current, files


# ============================================================================
# Frontmatter read/write
# ============================================================================


FRONTMATTER_DELIM = "---"


def parse_frontmatter(md_text: str) -> tuple[dict[str, str], str]:
    """
    Parse a simple YAML-ish frontmatter block at the top of an md file.
    Returns (frontmatter_dict, body). If no frontmatter, returns ({}, md_text).

    Supports only flat string values — no nested lists/dicts. Enough for NAC Doc.
    """
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIM:
        return {}, md_text
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            end_idx = i
            break
    if end_idx is None:
        return {}, md_text
    fm: dict[str, str] = {}
    for line in lines[1:end_idx]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    body = "\n".join(lines[end_idx + 1 :])
    return fm, body


def render_frontmatter(fm: dict[str, str]) -> str:
    """Render a frontmatter dict back to a `---\\nkey: value\\n---` block."""
    lines = [FRONTMATTER_DELIM]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines.append(FRONTMATTER_DELIM)
    return "\n".join(lines)
