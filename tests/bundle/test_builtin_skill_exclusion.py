"""
@file_name: test_builtin_skill_exclusion.py
@author: NetMind.AI
@date: 2026-07-10
@description: Built-in skills must never leave a machine as user data — they
ship with the app and re-materialize on the target. This pins the two pure
filesystem cores of that exclusion:
  - skill_backup._dir_is_builtin (drives list_unbackedup filtering)
  - builder._builtin_skill_relpaths (drives workspace-tar exclusion)
"""

from __future__ import annotations

import json
from pathlib import Path

from xyz_agent_context.bundle import skill_backup
from xyz_agent_context.bundle import builder


def _write_skill(root: Path, name: str, *, builtin: bool) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    meta = {"source_type": "builtin" if builtin else "github"}
    if builtin:
        meta["builtin"] = True
    (d / ".skill_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_dir_is_builtin_true_and_false(tmp_path):
    b = _write_skill(tmp_path, "officecli", builtin=True)
    u = _write_skill(tmp_path, "user-skill", builtin=False)
    assert skill_backup._dir_is_builtin(b) is True
    assert skill_backup._dir_is_builtin(u) is False


def test_dir_is_builtin_missing_meta(tmp_path):
    d = tmp_path / "no-meta"
    d.mkdir()
    assert skill_backup._dir_is_builtin(d) is False


def test_builtin_skill_relpaths_collects_only_builtins(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "officecli", builtin=True)
    _write_skill(skills, "user-skill", builtin=False)
    roots = builder._builtin_skill_relpaths(tmp_path)
    assert roots == {"skills/officecli"}


def test_builtin_skill_relpaths_no_skills_dir(tmp_path):
    assert builder._builtin_skill_relpaths(tmp_path) == set()
