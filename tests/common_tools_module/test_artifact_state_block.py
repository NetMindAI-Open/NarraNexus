"""
@file_name: test_artifact_state_block.py
@author: Bin Liang
@date: 2026-07-22
@description: The artifact state block's URL-tab handling.

A URL tab is surfaced to the agent with a "Read content.md" hint ONLY when the
snapshot exists on disk — legacy tabs (opened before the page-text feature)
have no content.md, so the block must fall back to the plain path rather than
pointing the agent at a missing file.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from xyz_agent_context.module.common_tools_module.common_tools_module import CommonToolsModule
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


async def _seed_url_tab(repo, artifact_id, slug):
    now = datetime.now(timezone.utc)
    await repo.create(Artifact(
        artifact_id=artifact_id, agent_id="agent_x", user_id="user_y",
        session_id=None, title="A page", kind="application/x-url",
        pinned=True, file_path=f"{WS_REL}/tabs/{slug}/page.url.json",
        size_bytes=10, created_at=now, updated_at=now,
    ))


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    repo = ArtifactRepository(db_client)
    mod = CommonToolsModule("agent_x", "user_y", db_client)
    yield {"base": base, "repo": repo, "mod": mod}


@pytest.mark.asyncio
async def test_url_tab_with_content_md_points_agent_at_it(env):
    await _seed_url_tab(env["repo"], "art_withtext", "aaa")
    d = env["base"] / WS_REL / "tabs" / "aaa"
    d.mkdir(parents=True)
    (d / "content.md").write_text("# A page\n\nthe text", encoding="utf-8")

    block = await env["mod"]._render_artifact_state_block()
    assert "tabs/aaa/content.md" in block
    assert "Read" in block


@pytest.mark.asyncio
async def test_legacy_url_tab_without_content_md_falls_back(env):
    # No content.md on disk (a tab from before the feature).
    await _seed_url_tab(env["repo"], "art_legacy", "bbb")

    block = await env["mod"]._render_artifact_state_block()
    # Must NOT point at a missing content.md; falls back to the plain path.
    assert "tabs/bbb/content.md" not in block
    assert "tabs/bbb/page.url.json" in block
