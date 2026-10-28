"""
@file_name: guards.py
@author: Bin Liang
@date: 2026-09-03
@description: The minimal guardrail set (spec §11.4) as pure functions: paths, ids, kinds, extensions, budgets, cooldowns, protected targets.

Everything here fails closed: a path that is not provably inside the
agent's plugin dev directory is refused, an id with the reserved prefix is
refused, a kind outside the template list is refused, and a budget that is
exhausted is refused with the time until it resets.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from narranexus.contracts.distribution import is_builtin_id
from narranexus.contracts import PluginError
from narranexus.kernel.plugins.manifest import PLUGIN_ID_RE

PROTECTED_PLUGIN_IDS = frozenset({"builtin.nexus_plugins_module"})
ALLOWED_EXTENSIONS = frozenset({".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".ts", ".tsx", ".js", ".css", ".nxbundle"})
FORBIDDEN_NAMES = frozenset({".git", "pyenv", "node_modules", "__pycache__"})
MAX_EDIT_BYTES = 512 * 1024
REGISTER_BUDGET_PER_WINDOW = 3
BUDGET_WINDOW_S = 10 * 60
APPROVAL_TIMEOUT_S = 10 * 60
MAX_CONSECUTIVE_ROLLBACKS = 2


class GuardError(PluginError):
    pass


def check_plugin_id(plugin_id: str) -> str:
    if not PLUGIN_ID_RE.match(plugin_id):
        raise GuardError(f"invalid plugin id {plugin_id!r}: expected <publisher>.<name> in [a-z0-9_.-]")
    if is_builtin_id(plugin_id):
        raise GuardError("the builtin. prefix is reserved for the host's own plugins")
    if plugin_id in PROTECTED_PLUGIN_IDS:
        raise GuardError(f"{plugin_id} is protected")
    return plugin_id


def check_not_protected(plugin_id: str) -> str:
    if plugin_id in PROTECTED_PLUGIN_IDS or is_builtin_id(plugin_id):
        raise GuardError(f"{plugin_id} is protected: builtin plugins cannot be changed by an agent")
    return plugin_id


def check_kinds(kinds: list[str], available: list[str]) -> list[str]:
    bad = [k for k in kinds if k not in available]
    if bad:
        raise GuardError(f"unknown kind(s) {bad}; available: {available}")
    if not kinds:
        raise GuardError("at least one kind is required")
    return kinds


def dev_root(workspace: Path) -> Path:
    return workspace / "plugins"


def check_inside(root: Path, candidate: Path) -> Path:
    """``candidate`` (may not exist yet) must resolve inside ``root``; symlinks that escape are refused.

    The existing prefix of ``candidate`` is resolved (following symlinks) and
    the not-yet-existing tail is appended; the result must be ``root`` or
    below it. ``root`` itself may not exist yet either.
    """
    root_r = root.resolve()
    parent = candidate
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    resolved = parent.resolve() / candidate.relative_to(parent) if parent != candidate else candidate.resolve()
    if root_r != resolved and root_r not in resolved.parents:
        raise GuardError(f"{candidate} is outside {root}" if not candidate.exists() or candidate.resolve() == candidate else f"{candidate} escapes {root} (symlink)")
    return resolved


def check_edit_path(plugin_dir: Path, rel: str, content: str) -> Path:
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
        raise GuardError(f"invalid relative path {rel!r}")
    p = Path(rel)
    if any(part in FORBIDDEN_NAMES for part in p.parts):
        raise GuardError(f"{rel}: directory {set(p.parts) & FORBIDDEN_NAMES} is not editable")
    if p.suffix not in ALLOWED_EXTENSIONS and p.name not in ("SKILL.md", "narranexus-plugin.json", "versions.json"):
        raise GuardError(f"{rel}: extension {p.suffix or '(none)'} is not allowed; allowed: {sorted(ALLOWED_EXTENSIONS)}")
    if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
        raise GuardError(f"{rel}: content exceeds {MAX_EDIT_BYTES // 1024} KiB")
    return check_inside(plugin_dir, plugin_dir / p)


@dataclass
class Budget:
    """Per-agent register/activate budget with a cooldown window."""

    events: list[float]
    consecutive_rollbacks: int = 0
    manual_required: bool = False

    def check(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.manual_required:
            raise GuardError("two consecutive rollbacks: further register/activate needs a human (factory page)")
        recent = [t for t in self.events if now - t < BUDGET_WINDOW_S]
        if len(recent) >= REGISTER_BUDGET_PER_WINDOW:
            wait = int(BUDGET_WINDOW_S - (now - min(recent)))
            raise GuardError(f"budget of {REGISTER_BUDGET_PER_WINDOW} register/activate per {BUDGET_WINDOW_S // 60} min exhausted; retry in {wait}s")

    def spend(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.events = [t for t in self.events if now - t < BUDGET_WINDOW_S] + [now]

    def rollback(self) -> None:
        self.consecutive_rollbacks += 1
        if self.consecutive_rollbacks >= MAX_CONSECUTIVE_ROLLBACKS:
            self.manual_required = True

    def success(self) -> None:
        self.consecutive_rollbacks = 0


__all__ = [
    "ALLOWED_EXTENSIONS",
    "APPROVAL_TIMEOUT_S",
    "BUDGET_WINDOW_S",
    "Budget",
    "GuardError",
    "MAX_CONSECUTIVE_ROLLBACKS",
    "PROTECTED_PLUGIN_IDS",
    "REGISTER_BUDGET_PER_WINDOW",
    "check_edit_path",
    "check_inside",
    "check_kinds",
    "check_not_protected",
    "check_plugin_id",
    "dev_root",
]
