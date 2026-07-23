"""
@file_name: test_shared_workspace_paths.py
@date: 2026-07-20
@description: Per-user shared-area path helpers live under {base}/{user_id}, so
same-user agents (and per-user Executor mounts) can Read them.
"""

from __future__ import annotations

from xyz_agent_context.utils.workspace_paths import (
    agent_workspace_path,
    bus_files_dir,
    team_shared_dir,
    user_shared_root,
)

BASE = "/tmp/wsbase"
USER = "user_x"


def test_shared_root_under_user_dir():
    assert user_shared_root(USER, base=BASE).as_posix() == f"{BASE}/{USER}/_shared"


def test_bus_files_dir_layout():
    assert bus_files_dir(USER, base=BASE).as_posix() == f"{BASE}/{USER}/_shared/bus_files"


def test_team_shared_dir_layout():
    assert team_shared_dir(USER, "team_42", base=BASE).as_posix() == f"{BASE}/{USER}/_shared/teams/team_42"


def test_shared_area_is_sibling_not_inside_agent_workspace():
    # The shared area must not sit inside any single agent's workspace, else one
    # agent would "own" it and the sandbox write-root would swallow it.
    agent_ws = agent_workspace_path("agent_a", USER, base=BASE)
    shared = user_shared_root(USER, base=BASE)
    assert not str(shared).startswith(str(agent_ws) + "/")
    assert shared.parent == agent_ws.parent  # both under {base}/{user}
