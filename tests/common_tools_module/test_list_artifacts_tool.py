"""
@file_name: test_list_artifacts_tool.py
@date: 2026-08-18
@description: TDD for the list_artifacts MCP tool (spec artifact-events §4).

Contract:
- Visible surface = list_for_agent_context(agent_id): own pinned ∪ every
  team the agent belongs to. Derived from membership server-side — no
  parameter can WIDEN it.
- kind / team_id / title_contains are narrowing filters only; a team_id the
  agent is not a member of yields zero rows by construction.
- Paged (50/page) with a truthful header: total count + page position.
- Line format matches the state block (`- `art_...` [kind] 'title' → path`)
  so the agent learns nothing new.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from narranexus_plugins.common_tools_module._common_tools_impl.artifact_tool import (
    LIST_ARTIFACTS_PAGE_SIZE,
    list_artifacts_impl,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


async def _seed(repo, artifact_id, *, title="doc", kind="text/markdown",
                team_id=None, age_minutes=0):
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    await repo.create(Artifact(
        artifact_id=artifact_id, agent_id="agent_x", user_id="user_y",
        session_id=None, title=title, kind=kind, pinned=team_id is None,
        team_id=team_id, file_path=f"{WS_REL}/{artifact_id}.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    repo = ArtifactRepository(db_client)
    yield {"db": db_client, "repo": repo}


@pytest.mark.asyncio
async def test_lists_own_and_team_artifacts(env):
    await env["db"].insert("team_members", {"team_id": "team_1", "agent_id": "agent_x"})
    await _seed(env["repo"], "art_mine", title="mine")
    await _seed(env["repo"], "art_team", title="ours", team_id="team_1")

    out = await list_artifacts_impl(env["db"], agent_id="agent_x", user_id="user_y")

    assert "art_mine" in out and "art_team" in out
    assert "2" in out  # truthful total


@pytest.mark.asyncio
async def test_kind_filter_narrows(env):
    await _seed(env["repo"], "art_md", kind="text/markdown")
    await _seed(env["repo"], "art_html", kind="text/html", title="page")

    out = await list_artifacts_impl(
        env["db"], agent_id="agent_x", user_id="user_y", kind="text/html")

    assert "art_html" in out and "art_md" not in out


@pytest.mark.asyncio
async def test_team_filter_cannot_widen(env):
    """A team the agent is NOT in yields nothing — the parameter only
    narrows the membership-derived surface."""
    await _seed(env["repo"], "art_mine")

    out = await list_artifacts_impl(
        env["db"], agent_id="agent_x", user_id="user_y", team_id="team_alien")

    assert "art_mine" not in out
    assert "0" in out


@pytest.mark.asyncio
async def test_title_contains_is_case_insensitive(env):
    await _seed(env["repo"], "art_q3", title="Q3 Revenue Report")
    await _seed(env["repo"], "art_memo", title="memo")

    out = await list_artifacts_impl(
        env["db"], agent_id="agent_x", user_id="user_y", title_contains="revenue")

    assert "art_q3" in out and "art_memo" not in out


@pytest.mark.asyncio
async def test_paging_header_and_bounds(env):
    total = LIST_ARTIFACTS_PAGE_SIZE + 3
    for i in range(total):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    page1 = await list_artifacts_impl(env["db"], agent_id="agent_x", user_id="user_y", page=1)
    page2 = await list_artifacts_impl(env["db"], agent_id="agent_x", user_id="user_y", page=2)

    assert page1.count("- `art_") == LIST_ARTIFACTS_PAGE_SIZE
    assert page2.count("- `art_") == 3
    assert str(total) in page1 and str(total) in page2


@pytest.mark.asyncio
async def test_line_format_matches_state_block(env):
    await _seed(env["repo"], "art_fmt", title="fmt")

    out = await list_artifacts_impl(env["db"], agent_id="agent_x", user_id="user_y")

    assert "- `art_fmt` [text/markdown] 'fmt' → `art_fmt.md`" in out
