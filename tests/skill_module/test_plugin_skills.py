"""
@file_name: test_plugin_skills.py
@author: Bin Liang
@date: 2026-09-03
@description: content.skills contributions are listed after workspace skills, tagged source_type=plugin, and never shadow a workspace skill.
"""
from __future__ import annotations

from pathlib import Path

from narranexus.contracts.skill import SkillSpec
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus_plugins.skill_module.skill_module import SkillModule


def _skill(dir_: Path, name: str, desc: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    return dir_


def _registries(*dirs: Path):
    registries = Registries()
    reg = registries.registry_for("content.skills")
    for i, d in enumerate(dirs):
        reg.register_contribution(Contribution(f"s{i}", (lambda d=d: SkillSpec(d))), owner="acme.skills")
    return registries


def test_plugin_skills_follow_workspace_and_workspace_wins(tmp_path: Path, monkeypatch):
    import narranexus.platform.utils.plugin_contributions as pc

    ws = tmp_path / "workspace"
    plugin_dir = tmp_path / "plugin"
    _skill(ws / "skills" / "alpha", "alpha", "workspace alpha")
    _skill(plugin_dir / "alpha", "alpha", "plugin alpha")
    _skill(plugin_dir / "zeta", "zeta", "plugin zeta")
    monkeypatch.setattr(pc, "_registries", lambda r=None: _registries(plugin_dir / "alpha", plugin_dir / "zeta"))

    module = SkillModule("agent_test", "user_test", None)
    module.skills_dir = ws / "skills"
    skills = module._scan_skills()
    assert [(s.name, s.source_type) for s in skills] == [("alpha", None), ("zeta", "plugin")]
    assert skills[0].description == "workspace alpha"
    assert skills[1].path == str(plugin_dir / "zeta")


def test_plugin_skills_listed_even_without_a_workspace(tmp_path: Path, monkeypatch):
    import narranexus.platform.utils.plugin_contributions as pc

    plugin_dir = tmp_path / "plugin"
    _skill(plugin_dir / "zeta", "zeta", "plugin zeta")
    monkeypatch.setattr(pc, "_registries", lambda r=None: _registries(plugin_dir / "zeta"))
    module = SkillModule("agent_test", None, None)
    assert module.skills_dir is None
    assert [s.name for s in module._scan_skills()] == ["zeta"]


def test_no_plugins_no_change(tmp_path: Path, monkeypatch):
    import narranexus.platform.utils.plugin_contributions as pc

    monkeypatch.setattr(pc, "_registries", lambda r=None: Registries())
    module = SkillModule("agent_test", None, None)
    assert module._scan_skills() == []
