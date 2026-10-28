"""
@file_name: test_skill_name_guard.py
@author: Bin Liang
@date: 2026-10-28
@description: ``skill_name`` is a request/tool argument that becomes
``skills_dir / skill_name``. A dot-led or separator-bearing name must never
resolve — ``..`` would otherwise point remove/disable/enable at the agent's
whole workspace.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.skill_module import SkillModule


@pytest.fixture
def module(tmp_path, monkeypatch):
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)
    m = SkillModule(agent_id="agent_x", user_id="user_y", database_client=None)
    m.skills_dir.mkdir(parents=True, exist_ok=True)
    (m.skills_dir.parent / "keep.txt").write_text("keep", encoding="utf-8")
    return m


@pytest.mark.parametrize("bad", ["..", ".", "...", ".disabled", "a/b", "a\\b", ""])
def test_mutating_methods_refuse_unsafe_names(module, bad):
    for fn in (module.remove_skill, module.disable_skill, module.enable_skill):
        with pytest.raises(ValueError):
            fn(bad)
    assert (module.skills_dir.parent / "keep.txt").exists()
    assert module.get_skill(bad) is None


def test_unsafe_frontmatter_name_still_resolves_by_scan_and_never_raises(module):
    """SKILL.md is agent-written. A frontmatter ``name: org/pdf`` must neither
    be joined onto the skills dir nor break env injection for every skill."""
    d = module.skills_dir / "pdf-tools"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: org/pdf\ndescription: x\n---\n# pdf\n", encoding="utf-8")
    # A real skill dir one level ABOVE skills_dir: if the unsafe name were
    # still joined directly, ``skills_dir / "../pdf-tools"`` would resolve to it.
    outside = module.skills_dir.parent / "pdf-tools"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: outside\ndescription: x\n---\n", encoding="utf-8")
    assert module._resolve_skill_dir("org/pdf") == d
    assert module._resolve_skill_dir("../pdf-tools") is None
    assert isinstance(module.get_all_skill_env_vars(), dict)
