"""
@file_name: test_resolve_skill_dir.py
@author: Bin Liang
@date: 2026-05-19
@description: Regression test for `_resolve_skill_dir` crashing when the
agent's skills directory has been deleted or never created.

Observed in EC2 mcp container logs 2026-05-19T04:35:13:
  FileNotFoundError: [Errno 2] No such file or directory:
  '/opt/narranexus/workspaces/agent_94360f6c4b98_user_xiong/skills'

Root cause: `_resolve_skill_dir` guards `if not self.skills_dir` but not
`self.skills_dir.exists()`. Falls into `for path in self.skills_dir.iterdir()`
which raises FileNotFoundError when the directory is missing.

`_scan_skills()` in the same module already does the correct
`if not self.skills_dir or not self.skills_dir.exists(): return []` —
this test pins the same guarantee for `_resolve_skill_dir`.
"""
from __future__ import annotations

from xyz_agent_context.module.skill_module.skill_module import SkillModule
from xyz_agent_context.settings import settings


def test_resolve_skill_dir_returns_none_when_skills_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    sm = SkillModule(agent_id="a1", user_id="u1")
    assert sm.skills_dir is not None
    assert not sm.skills_dir.exists()
    # Must NOT raise FileNotFoundError. Must return None.
    assert sm._resolve_skill_dir("anything") is None


def test_resolve_skill_dir_returns_none_when_user_id_none(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    sm = SkillModule(agent_id="a1", user_id=None)
    assert sm.skills_dir is None
    assert sm._resolve_skill_dir("anything") is None
