"""
@file_name: test_heal.py
@author: Bin Liang
@date: 2026-07-21
@description: Tests for the broken-pointer recovery strategy (ArtifactService.heal).

Covers every branch of the recovery sequence:
- artifact missing / owned by another agent → ArtifactNotFound
- pointer already valid → recovered, no re-registration
- caller-picked entry_path → re-register onto the same artifact_id
- caller-picked entry_path rejected → ArtifactError propagates
- workspace scan: unique match auto-recovers; zero and multiple matches
  return candidates without recovering
"""
from __future__ import annotations

import os

import pytest

from xyz_agent_context.artifact import (
    ArtifactNotFound,
    ArtifactPathEscape,
    ArtifactService,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    workspace = base / WS_REL
    workspace.mkdir(parents=True)
    (workspace / "report").mkdir()
    entry = workspace / "report" / "index.html"
    entry.write_text("<p>hi</p>", encoding="utf-8")

    service = ArtifactService(db_client)
    repo = ArtifactRepository(db_client)
    registered = await service.register(
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(entry),
        title="report", description=None, target_artifact_id=None,
    )
    yield {
        "db": db_client,
        "service": service,
        "repo": repo,
        "workspace": workspace,
        "entry": entry,
        "artifact_id": registered.artifact_id,
    }


@pytest.mark.asyncio
async def test_heal_unknown_artifact_raises_not_found(env):
    with pytest.raises(ArtifactNotFound):
        await env["service"].heal(
            agent_id="agent_x", user_id="user_y", artifact_id="art_missing",
        )


@pytest.mark.asyncio
async def test_heal_artifact_of_other_agent_raises_not_found(env):
    """Ownership mismatch is indistinguishable from absence (no existence leak)."""
    with pytest.raises(ArtifactNotFound):
        await env["service"].heal(
            agent_id="agent_other", user_id="user_y", artifact_id=env["artifact_id"],
        )


@pytest.mark.asyncio
async def test_heal_valid_pointer_short_circuits(env):
    """Entry still on disk → recovered immediately, pointer untouched."""
    before = await env["repo"].get_by_id(env["artifact_id"])
    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is True
    assert result.artifact is not None
    assert result.artifact.file_path == before.file_path
    assert "already valid" in result.message


@pytest.mark.asyncio
async def test_heal_with_picked_entry_path_reregisters(env):
    """User picked a candidate → pointer moves onto the picked path, same id."""
    env["entry"].unlink()  # break the pointer
    (env["workspace"] / "fresh").mkdir()
    fresh = env["workspace"] / "fresh" / "new.html"
    fresh.write_text("<p>new</p>", encoding="utf-8")

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
        entry_path="fresh/new.html",
    )
    assert result.recovered is True
    row = await env["repo"].get_by_id(env["artifact_id"])
    assert row.file_path == f"{WS_REL}/fresh/new.html"


@pytest.mark.asyncio
async def test_heal_with_bad_entry_path_propagates_error(env):
    """A rejected pick (outside workspace) surfaces the structured error."""
    env["entry"].unlink()
    with pytest.raises(ArtifactPathEscape):
        await env["service"].heal(
            agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
            entry_path="/etc/passwd",
        )


@pytest.mark.asyncio
async def test_heal_scan_unique_match_auto_recovers(env):
    """Broken pointer + exactly one kind-matching file → auto re-register."""
    env["entry"].unlink()
    (env["workspace"] / "rebuilt").mkdir()
    rebuilt = env["workspace"] / "rebuilt" / "index.html"
    rebuilt.write_text("<p>rebuilt</p>", encoding="utf-8")

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is True
    assert "auto-recovered" in result.message
    row = await env["repo"].get_by_id(env["artifact_id"])
    assert row.file_path == f"{WS_REL}/rebuilt/index.html"


@pytest.mark.asyncio
async def test_heal_scan_zero_matches_returns_empty_candidates(env):
    env["entry"].unlink()
    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is False
    assert result.candidates == []
    assert "no matching file" in result.message


@pytest.mark.asyncio
async def test_heal_scan_multiple_matches_returns_candidates_newest_first(env):
    env["entry"].unlink()
    (env["workspace"] / "a").mkdir()
    older = env["workspace"] / "a" / "older.html"
    older.write_text("<p>a</p>", encoding="utf-8")
    (env["workspace"] / "b").mkdir()
    newer = env["workspace"] / "b" / "newer.html"
    newer.write_text("<p>b</p>", encoding="utf-8")
    # Force a deterministic mtime ordering regardless of filesystem timing.
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is False
    paths = [c.workspace_path for c in result.candidates]
    assert paths == ["b/newer.html", "a/older.html"]
    # Not registered onto anything — pointer stays broken until the user picks.
    row = await env["repo"].get_by_id(env["artifact_id"])
    assert row.file_path == f"{WS_REL}/report/index.html"
