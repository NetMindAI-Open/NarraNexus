"""
@file_name: test_builtin_skills.py
@author: NetMind.AI
@date: 2026-07-10
@description: Tests for the built-in skills mechanism on SkillModule.

Why this file exists:
    Built-in skills (e.g. `officecli`) ship vendored in the repo and are
    materialized into each agent workspace on run. The invariants that must
    hold:
      1. Materialize copies the skill into `skills/<name>/` and tags it
         `builtin: true` in `.skill_meta.json`.
      2. Materialize is idempotent AND disable-aware — it never resurrects a
         skill the user disabled (moved to `skills/.disabled/<name>/`) or one
         already present.
      3. `_scan_skills` / `_parse_skill_md` surface `builtin=True`.
      4. Built-in skills reject removal (they'd re-materialize anyway).
      5. Built-in skills are excluded from backup discovery.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xyz_agent_context.module.skill_module import skill_module as sm_mod
from xyz_agent_context.module.skill_module.skill_module import SkillModule
from xyz_agent_context.settings import settings


@pytest.fixture()
def fake_builtin(tmp_path, monkeypatch) -> Path:
    """Point BUILTIN_SKILLS_DIR at a synthetic one-skill source tree."""
    src_root = tmp_path / "builtin_src"
    demo = src_root / "demo-skill"
    demo.mkdir(parents=True)
    (demo / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A demo built-in.\n---\n\n# demo\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sm_mod, "BUILTIN_SKILLS_DIR", src_root)
    return src_root


def _make_module(tmp_path, monkeypatch) -> SkillModule:
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "ws"))
    return SkillModule(agent_id="a1", user_id="u1")


def test_materialize_copies_and_tags_builtin(tmp_path, monkeypatch, fake_builtin):
    sm = _make_module(tmp_path, monkeypatch)
    sm._materialize_builtin_skills()

    dest = sm.skills_dir / "demo-skill"
    assert (dest / "SKILL.md").exists()
    meta = json.loads((dest / ".skill_meta.json").read_text(encoding="utf-8"))
    assert meta["builtin"] is True
    assert meta["source_type"] == "builtin"


def test_materialize_is_idempotent(tmp_path, monkeypatch, fake_builtin):
    sm = _make_module(tmp_path, monkeypatch)
    sm._materialize_builtin_skills()
    # Second run must not raise and must not duplicate.
    sm._materialize_builtin_skills()
    dest = sm.skills_dir / "demo-skill"
    assert dest.exists()
    assert len([d for d in sm.skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]) == 1


def test_materialize_respects_disabled(tmp_path, monkeypatch, fake_builtin):
    sm = _make_module(tmp_path, monkeypatch)
    # User disabled it earlier: it lives under .disabled/ and must NOT resurrect.
    disabled = sm.skills_dir / ".disabled" / "demo-skill"
    disabled.mkdir(parents=True)
    (disabled / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")

    sm._materialize_builtin_skills()

    assert not (sm.skills_dir / "demo-skill").exists()


def test_scan_surfaces_builtin_flag(tmp_path, monkeypatch, fake_builtin):
    sm = _make_module(tmp_path, monkeypatch)
    sm._materialize_builtin_skills()
    skills = sm._scan_skills()
    demo = next(s for s in skills if s.name == "demo-skill")
    assert demo.builtin is True


def test_list_skills_materializes_for_fresh_agent(tmp_path, monkeypatch, fake_builtin):
    """The API/UI path (list_skills) must surface built-ins even before the
    agent has ever run — list_skills materializes on its own."""
    sm = _make_module(tmp_path, monkeypatch)
    # No prior _materialize / hook_data_gathering call — simulate a fresh agent.
    skills = sm.list_skills(include_disabled=True)
    demo = next(s for s in skills if s.name == "demo-skill")
    assert demo.builtin is True


def test_remove_builtin_raises(tmp_path, monkeypatch, fake_builtin):
    sm = _make_module(tmp_path, monkeypatch)
    sm._materialize_builtin_skills()
    with pytest.raises(ValueError, match="built-in"):
        sm.remove_skill("demo-skill")
    # Still present after the rejected removal.
    assert (sm.skills_dir / "demo-skill").exists()


def test_remove_non_builtin_still_works(tmp_path, monkeypatch, fake_builtin):
    sm = _make_module(tmp_path, monkeypatch)
    sm.skills_dir.mkdir(parents=True, exist_ok=True)
    user_skill = sm.skills_dir / "user-skill"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text("---\nname: user-skill\n---\n", encoding="utf-8")
    assert sm.remove_skill("user-skill") is True
    assert not user_skill.exists()


def test_officecli_is_vendored_in_repo():
    """The real officecli built-in must ship in the repo with a SKILL.md."""
    officecli_md = sm_mod.BUILTIN_SKILLS_DIR / "officecli" / "SKILL.md"
    assert officecli_md.exists()
    assert "name: officecli" in officecli_md.read_text(encoding="utf-8")
