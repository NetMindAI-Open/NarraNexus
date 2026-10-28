"""
@file_name: test_skill_required_env_single_source.py
@author: Bin Liang
@date: 2026-09-08
@description: The MCP tool `skill_list_required_env` and the UI/route path
(SkillInfo.requires_env via get_skill) must resolve a skill's required env
vars from ONE function — GitHub #115: the tool only read .skill_meta.json,
so an installed-but-never-studied skill (no meta `requires`) answered "no
required environment variables" while the Skills panel listed the vars
declared in SKILL.md frontmatter / mentioned in its body.
"""
from __future__ import annotations

import json

import pytest

from narranexus.platform.settings import settings
from narranexus_plugins.skill_module._skill_mcp_tools import create_skill_mcp_server
from narranexus_plugins.skill_module.skill_module import SkillModule

_FRONTMATTER_SKILL = """---
name: weather
description: Weather lookups
metadata:
  openclaw:
    requires:
      env: [WEATHER_KEY]
      bins: [curl]
---
# Weather
Call the API with `$WEATHER_KEY`.
"""

_BODY_ONLY_SKILL = """---
name: notes
description: Notes sync
---
# Notes
Set NOTES_API_TOKEN=... before running the sync script.
"""


@pytest.fixture
def module(tmp_path, monkeypatch) -> SkillModule:
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    sm = SkillModule(agent_id="a1", user_id="u1")
    sm.skills_dir.mkdir(parents=True)
    return sm


def _write_skill(sm: SkillModule, dirname: str, skill_md: str, meta: dict | None = None):
    d = sm.skills_dir / dirname
    d.mkdir()
    (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
    if meta is not None:
        (d / ".skill_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def _mcp_tool(name: str):
    mcp = create_skill_mcp_server()
    return {t.name: t for t in mcp._tool_manager.list_tools()}[name].fn


def test_unstudied_skill_with_frontmatter_env_is_reported(module):
    _write_skill(module, "weather", _FRONTMATTER_SKILL)  # no .skill_meta.json at all

    assert module.get_skill_requirements("weather") == {"env": ["WEATHER_KEY"], "bins": ["curl"]}


def test_unstudied_skill_with_body_only_env_is_reported(module):
    _write_skill(module, "notes", _BODY_ONLY_SKILL, meta={"source_type": "github"})

    assert module.get_skill_requirements("notes")["env"] == ["NOTES_API_TOKEN"]


def test_studied_skill_meta_requirements_are_included(module):
    # The study wrote requires into meta; frontmatter declares another var.
    # The UI shows the union — the tool must report exactly the same set.
    _write_skill(
        module,
        "weather",
        _FRONTMATTER_SKILL,
        meta={"study_status": "completed", "requires": {"env": ["WEATHER_ACCOUNT"], "bins": []}},
    )

    reqs = module.get_skill_requirements("weather")
    assert reqs["env"] == ["WEATHER_ACCOUNT", "WEATHER_KEY"]
    assert reqs == {
        "env": module.get_skill("weather").requires_env,
        "bins": module.get_skill("weather").requires_bins,
    }


def test_skill_without_requirements_reports_empty_lists(module):
    _write_skill(module, "plain", "---\nname: plain\ndescription: nothing\n---\nJust text.\n")

    assert module.get_skill_requirements("plain") == {"env": [], "bins": []}


def test_unknown_skill_is_none_and_the_tool_says_not_installed(module, monkeypatch):
    # None, not empty lists: a typo must not read as "nothing to configure".
    assert module.get_skill_requirements("nope") is None
    monkeypatch.setattr(
        "narranexus_plugins.skill_module._skill_mcp_tools._get_skill_module",
        lambda agent_id, user_id: module,
    )
    import asyncio

    text = asyncio.run(_mcp_tool("skill_list_required_env")("a1", "u1", "nope"))
    assert "not installed" in text and "no required environment variables" not in text


def test_directory_without_skill_md_is_still_listed_from_its_meta(module):
    # Agent-created dirs (meta first, SKILL.md later) stay in the skills
    # table with the meta's description / study state / studied requirements.
    d = module.skills_dir / "draft"
    d.mkdir()
    (d / ".skill_meta.json").write_text(
        json.dumps(
            {
                "description": "half-written",
                "builtin": False,
                "study_status": "completed",
                "requires": {"env": ["DRAFT_KEY"], "bins": ["jq"]},
            }
        ),
        encoding="utf-8",
    )
    (module.skills_dir / "bare").mkdir()  # nothing at all inside

    by_name = {s.name: s for s in module.list_skills(include_disabled=True)}
    assert by_name["draft"].description == "half-written"
    assert by_name["draft"].study_status == "completed"
    assert by_name["draft"].requires_env == ["DRAFT_KEY"] and by_name["draft"].requires_bins == ["jq"]
    assert by_name["draft"].builtin is False
    assert by_name["bare"].description == "(No SKILL.md found)"
    assert module.get_skill_requirements("draft") == {"env": ["DRAFT_KEY"], "bins": ["jq"]}


def test_mcp_tool_and_route_path_agree_for_an_unstudied_skill(module, monkeypatch):
    _write_skill(module, "weather", _FRONTMATTER_SKILL)
    monkeypatch.setattr(
        "narranexus_plugins.skill_module._skill_mcp_tools._get_skill_module",
        lambda agent_id, user_id: module,
    )

    ui_env = module.get_skill("weather").requires_env
    assert ui_env == ["WEATHER_KEY"]

    import asyncio

    text = asyncio.run(_mcp_tool("skill_list_required_env")("a1", "u1", "weather"))
    assert "no required environment variables" not in text
    assert "WEATHER_KEY: ✗ not configured" in text


def test_mcp_tool_reports_configured_status_for_a_saved_var(module, monkeypatch):
    _write_skill(module, "weather", _FRONTMATTER_SKILL)
    monkeypatch.setattr(
        "narranexus_plugins.skill_module._skill_mcp_tools._get_skill_module",
        lambda agent_id, user_id: module,
    )
    module.set_skill_env_config("weather", {"WEATHER_KEY": "abc"})

    import asyncio

    text = asyncio.run(_mcp_tool("skill_list_required_env")("a1", "u1", "weather"))
    assert "WEATHER_KEY: ✓ configured" in text


def test_skill_install_tool_reports_failed_siblings_next_to_successes(monkeypatch):
    from narranexus.platform.marketplace._skill_marketplace_impl.install_pipeline import InstallResult
    from narranexus.platform.marketplace import skill_marketplace_service as svc_mod
    from narranexus.platform.schema.skill_schema import SkillInfo

    ok = InstallResult(status="installed", skill=SkillInfo(name="alpha", description="", path="/x/alpha"))
    bad = InstallResult(status="failed", skill=None, skill_name="evil", error="Security scan rejected this skill package (curl_pipe_sh)")

    async def _fake_install_from_url(self, agent_id, user_id, url, branch="main"):
        return [ok, bad]

    monkeypatch.setattr(svc_mod.SkillMarketplaceService, "install_from_url", _fake_install_from_url)
    import asyncio

    text = asyncio.run(_mcp_tool("skill_install")("a1", "u1", "https://github.com/acme/mixed"))
    assert "Installed skill 'alpha'" in text
    assert "'evil' was NOT installed: Security scan rejected" in text
    assert "\n" in text
