"""
@file_name: test_raw_access.py
@author: Bin Liang
@date: 2026-07-21
@description: Tests for raw-content resolution (ArtifactService.resolve_raw_file).

Covers the 404/410 error contract and the path-confinement rules:
- entry + sibling asset resolution with media types
- artifact missing / agent mismatch → ArtifactNotFound (404)
- empty file_path pointer / entry off-disk / asset off-disk →
  ArtifactContentGone (410)
- sub-path escape attempts → ArtifactNotFound (404)
- workspace-root single-file mode: siblings refused, entry basename aliased
"""
from __future__ import annotations

import pytest

from xyz_agent_context.artifact import (
    ArtifactContentGone,
    ArtifactNotFound,
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
    root = workspace / "report"
    root.mkdir()
    entry = root / "index.html"
    entry.write_text("<p>hello</p>", encoding="utf-8")
    asset = root / "style.css"
    asset.write_text("body{font:1em monospace}", encoding="utf-8")

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
        "asset": asset,
        "artifact_id": registered.artifact_id,
    }


@pytest.mark.asyncio
async def test_resolve_entry_serves_kind_as_media_type(env):
    resolved = await env["service"].resolve_raw_file(
        agent_id="agent_x", artifact_id=env["artifact_id"],
    )
    assert resolved.path == str(env["entry"].resolve())
    assert resolved.is_entry is True
    assert resolved.media_type == "text/html"
    assert resolved.kind == "text/html"


@pytest.mark.asyncio
async def test_resolve_sibling_asset_guesses_mime(env):
    resolved = await env["service"].resolve_raw_file(
        agent_id="agent_x", artifact_id=env["artifact_id"], file_path="style.css",
    )
    assert resolved.path == str(env["asset"].resolve())
    assert resolved.is_entry is False
    assert resolved.media_type == "text/css"


@pytest.mark.asyncio
async def test_resolve_unknown_artifact_raises_404(env):
    with pytest.raises(ArtifactNotFound):
        await env["service"].resolve_raw_file(
            agent_id="agent_x", artifact_id="art_missing",
        )


@pytest.mark.asyncio
async def test_resolve_agent_mismatch_raises_404(env):
    """A token minted for another agent must not read this artifact."""
    with pytest.raises(ArtifactNotFound):
        await env["service"].resolve_raw_file(
            agent_id="agent_other", artifact_id=env["artifact_id"],
        )


@pytest.mark.asyncio
async def test_resolve_empty_pointer_raises_410(env):
    """Legacy row with no file_path → 410 (self-heal trigger), not 404."""
    await env["db"].update(
        "instance_artifacts", {"artifact_id": env["artifact_id"]}, {"file_path": ""},
    )
    with pytest.raises(ArtifactContentGone):
        await env["service"].resolve_raw_file(
            agent_id="agent_x", artifact_id=env["artifact_id"],
        )


@pytest.mark.asyncio
async def test_resolve_entry_missing_on_disk_raises_410(env):
    env["entry"].unlink()
    with pytest.raises(ArtifactContentGone):
        await env["service"].resolve_raw_file(
            agent_id="agent_x", artifact_id=env["artifact_id"],
        )


@pytest.mark.asyncio
async def test_resolve_asset_missing_on_disk_raises_410(env):
    with pytest.raises(ArtifactContentGone):
        await env["service"].resolve_raw_file(
            agent_id="agent_x", artifact_id=env["artifact_id"], file_path="nope.css",
        )


@pytest.mark.asyncio
async def test_resolve_subpath_escape_raises_404(env):
    """`..` traversal out of the artifact root is refused as a plain 404."""
    outside = env["workspace"] / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ArtifactNotFound):
        await env["service"].resolve_raw_file(
            agent_id="agent_x", artifact_id=env["artifact_id"],
            file_path="../secret.txt",
        )


@pytest.mark.asyncio
async def test_workspace_root_entry_refuses_siblings_serves_basename_alias(env):
    """Single-file mode: an entry at the workspace root serves only itself —
    sibling requests 404 (they would expose the whole workspace), but the
    entry's own basename resolves as an alias of the entry."""
    flat = env["workspace"] / "solo.html"
    flat.write_text("<p>solo</p>", encoding="utf-8")
    other = env["workspace"] / "unrelated.txt"
    other.write_text("private", encoding="utf-8")

    registered = await env["service"].register(
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(flat),
        title="solo", description=None, target_artifact_id=None,
    )

    resolved = await env["service"].resolve_raw_file(
        agent_id="agent_x", artifact_id=registered.artifact_id,
    )
    assert resolved.path == str(flat.resolve())

    alias = await env["service"].resolve_raw_file(
        agent_id="agent_x", artifact_id=registered.artifact_id, file_path="solo.html",
    )
    assert alias.path == str(flat.resolve())
    assert alias.is_entry is True

    with pytest.raises(ArtifactNotFound):
        await env["service"].resolve_raw_file(
            agent_id="agent_x", artifact_id=registered.artifact_id,
            file_path="unrelated.txt",
        )
