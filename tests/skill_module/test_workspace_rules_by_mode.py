"""
@file_name: test_workspace_rules_by_mode.py
@author: Bin Liang
@date: 2026-04-20
@description: SkillModule renders different workspace rules per deployment
mode (Bug 5 step 3/4).

Cloud mode: the strict block — workspace-only, global installs blocked,
no credentials outside the skill dir.

Local mode: the relaxed block — acknowledges the user's own machine,
allows global installs with an advisory "tell the user" recommendation.

Default (mode missing on ctx_data): cloud, because cloud is the stricter
set and never-letting-a-local-prompt-escape-to-cloud matters more than
the reverse.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.skill_module.skill_module import (
    SKILL_INSTRUCTIONS_TEMPLATE,
    SkillModule,
    WORKSPACE_RULES_CLOUD,
    WORKSPACE_RULES_LOCAL,
    _resolve_workspace_rules,
)
from xyz_agent_context.schema import ContextData


# -------- constants differ and carry the right language -----------------


def test_cloud_and_local_rules_are_distinct():
    assert WORKSPACE_RULES_CLOUD != WORKSPACE_RULES_LOCAL


def test_cloud_rules_mention_sandbox_and_global_block():
    cloud = WORKSPACE_RULES_CLOUD.lower()
    assert "blocked" in cloud
    assert "global" in cloud
    # Scoped pip install guidance
    assert "--target" in cloud or "--user" in cloud


def test_local_rules_mention_user_machine_and_advisory_install():
    local = WORKSPACE_RULES_LOCAL.lower()
    assert "user's own machine" in local or "own machine" in local
    # Advisory transparency — tell the user what gets installed globally
    assert "mention" in local or "tell the user" in local


def test_template_uses_workspace_rules_placeholder():
    assert "{workspace_rules}" in SKILL_INSTRUCTIONS_TEMPLATE


# -------- resolver picks the right block by mode ------------------------


def _ctx(mode: str | None) -> ContextData:
    ctx = ContextData(agent_id="a", user_id="u", input_content="x")
    ctx.deployment_mode = mode
    return ctx


def test_resolver_returns_cloud_block_for_cloud():
    assert _resolve_workspace_rules(_ctx("cloud")) == WORKSPACE_RULES_CLOUD


def test_resolver_returns_local_block_for_local():
    assert _resolve_workspace_rules(_ctx("local")) == WORKSPACE_RULES_LOCAL


def test_resolver_defaults_to_cloud_when_mode_missing():
    """Stricter fallback — we never want a local-style prompt accidentally
    rendered into a cloud agent because BasicInfoModule failed to run."""
    assert _resolve_workspace_rules(_ctx(None)) == WORKSPACE_RULES_CLOUD


# -------- get_instructions renders mode-specific rules ------------------


@pytest.mark.asyncio
async def test_get_instructions_renders_cloud_rules_when_cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.settings.settings.base_working_path",
        str(tmp_path),
    )
    module = SkillModule(agent_id="a", user_id="u")
    ctx = _ctx("cloud")
    ctx.extra_data = {"skills_table": "", "skills_count": 0}
    rendered = await module.get_instructions(ctx)
    # Cloud-specific language
    assert "blocked" in rendered.lower()
    assert "global" in rendered.lower()
    # Local advisory phrasing should NOT leak into cloud
    assert "own machine" not in rendered.lower()


@pytest.mark.asyncio
async def test_get_instructions_renders_local_rules_when_local(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.settings.settings.base_working_path",
        str(tmp_path),
    )
    module = SkillModule(agent_id="a", user_id="u")
    ctx = _ctx("local")
    ctx.extra_data = {"skills_table": "", "skills_count": 0}
    rendered = await module.get_instructions(ctx)
    # Local-specific language
    assert "own machine" in rendered.lower() or "user's own" in rendered.lower()
