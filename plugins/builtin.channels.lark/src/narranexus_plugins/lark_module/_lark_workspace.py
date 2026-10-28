"""
@file_name: _lark_workspace.py
@date: 2026-04-16
@description: Per-agent workspace manager for HOME-based lark-cli isolation.

Each agent gets its own workspace directory. When lark-cli runs with
HOME set to this directory, it reads/writes ~/.lark-cli/ config and
cache inside the workspace — naturally isolating per agent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from loguru import logger

# Default base directory for agent workspaces
_DEFAULT_BASE = Path.home() / ".narranexus" / "lark_workspaces"


def _get_base_dir() -> Path:
    """Return base directory for workspaces (configurable via env var)."""
    return Path(os.environ.get("LARK_WORKSPACE_BASE", str(_DEFAULT_BASE)))


def build_profile_name(agent_name: str, agent_id: str) -> str:
    """Compose a stable, human-readable CLI profile name.

    Slugify agent_name (keep unicode word chars, collapse punctuation to
    dashes), cap at 40 chars, append first 8 chars of agent_id for
    uniqueness across same-named agents.

    Examples:
      ("My Lark Bot", "agent_a1b2c3d4") -> "my-lark-bot-a1b2c3d4"
      ("每日助手",      "agent_a1b2c3d4") -> "每日助手-a1b2c3d4"
      ("",             "agent_a1b2c3d4") -> "agent-a1b2c3d4"
    """
    slug = re.sub(r"[^\w\-]+", "-", (agent_name or "").strip(), flags=re.UNICODE)
    slug = slug.strip("-_").lower()
    slug = slug[:40] or "agent"
    short_id = agent_id.replace("agent_", "")[:8] or "anon"
    return f"{slug}-{short_id}"


def get_workspace_path(agent_id: str) -> Path:
    """Return the workspace path for an agent (does not create it)."""
    # Prevent path traversal
    safe_id = agent_id.replace("/", "_").replace("..", "_")
    return _get_base_dir() / safe_id


def ensure_workspace(agent_id: str) -> Path:
    """Create workspace directory if it doesn't exist. Returns the path."""
    workspace = get_workspace_path(agent_id)
    workspace.mkdir(parents=True, exist_ok=True)
    # Restrictive permissions (owner only)
    try:
        workspace.chmod(0o700)
    except OSError:
        pass  # Windows doesn't support chmod

    # macOS Keychain lookup expands `~/Library/Keychains/...` via $HOME at
    # call time. When we override HOME to the workspace, lark-cli (and the
    # Security framework under it) looks for the keychain in
    # `<workspace>/Library/Keychains/login.keychain-db` — which doesn't
    # exist, so macOS pops up "找不到钥匙串" on every bind.
    # Symlink Library/Keychains to the real user's keychain dir so lookups
    # find the real login.keychain-db without the user ever seeing a
    # dialog. Also symlink Library/Preferences so lark-cli's other
    # preference reads land in the right place.
    import sys
    if sys.platform == "darwin":
        real_home = Path(os.path.expanduser("~"))
        lib_dir = workspace / "Library"
        lib_dir.mkdir(exist_ok=True)
        for sub in ("Keychains", "Preferences"):
            link = lib_dir / sub
            target = real_home / "Library" / sub
            if not link.exists() and target.exists():
                try:
                    link.symlink_to(target)
                except OSError as e:
                    logger.warning(
                        f"ensure_workspace: failed to symlink Library/{sub} "
                        f"for {agent_id}: {e}"
                    )

    return workspace


def get_home_env(agent_id: str) -> dict[str, str]:
    """Return environment dict with HOME pointing to the workspace.

    Inherits the full parent env (needed for macOS Keychain access,
    Node.js paths, etc.) but overrides HOME for lark-cli config isolation.
    """
    workspace = ensure_workspace(agent_id)
    env = {**os.environ, "HOME": str(workspace)}
    return env


def cleanup_workspace(agent_id: str) -> None:
    """Remove workspace directory on unbind."""
    import shutil
    workspace = get_workspace_path(agent_id)
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
        logger.info(f"Cleaned up Lark workspace for {agent_id}")
