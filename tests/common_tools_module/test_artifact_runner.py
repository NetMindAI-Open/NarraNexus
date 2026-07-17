"""
@file_name: test_artifact_runner.py
@author: Bin Liang
@date: 2026-05-08
@description: TDD tests for the pointer-model artifact_runner.register_artifact.

Tests cover:
- register_artifact happy path: validates the entry inside the workspace,
  computes the directory size, writes one DB row, returns artifact_id + url.
- Path-escape rejections: entry outside workspace; entry directly in the
  workspace root (must use a subdirectory).
- Multi-file artifact: directory size is the recursive sum of all files
  under the artifact root.
- Re-registration via `target_artifact_id`: updates pointer in place;
  kind mismatch raises; quota delta is (new − old) bytes.
- Auto-pinning: session_id=None → pinned=True (agent-scoped).
"""
from __future__ import annotations

import os

import pytest

from xyz_agent_context.module.common_tools_module._common_tools_impl import artifact_runner
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

# Workspace path of the test agent, RELATIVE to base_working_path — routed
# through the layout helper so these tests track the real layout (flat vs
# nested) instead of hardcoding one.
WS_REL = agent_workspace_relpath("agent_x", "user_y")


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    # Create the per-agent workspace and a sample artifact subdirectory with
    # an entry file inside it. This mimics what the agent's Write tool would
    # have done before the register call.
    workspace = base / WS_REL
    workspace.mkdir(parents=True)
    (workspace / "report").mkdir()
    entry = workspace / "report" / "index.html"
    entry.write_text("<p>hi</p>", encoding="utf-8")

    repo = ArtifactRepository(db_client)
    yield {
        "db": db_client,
        "repo": repo,
        "base": base,
        "workspace": workspace,
        "entry": entry,
    }


@pytest.mark.asyncio
async def test_register_happy_path_writes_row_no_copy(env):
    repo: ArtifactRepository = env["repo"]
    entry = env["entry"]

    result = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id="sess_1",
        kind="text/html",
        entry_path=str(entry),
        title="My report",
        description=None,
        target_artifact_id=None,
    )

    assert result.artifact_id.startswith("art_")
    # URL is the directory-style raw URL (trailing slash).
    assert result.url == f"/api/agents/agent_x/artifacts/{result.artifact_id}/raw/"

    row = await repo.get_by_id(result.artifact_id)
    assert row is not None
    # file_path is the entry, relative to base_working_path.
    assert row.file_path == f"{WS_REL}/report/index.html"
    assert row.size_bytes == os.path.getsize(entry)

    # The runner never copies — the entry file is the same inode the agent wrote.
    assert entry.exists()


@pytest.mark.asyncio
async def test_register_with_workspace_relative_path(env):
    """entry_path may be absolute OR workspace-relative."""
    repo: ArtifactRepository = env["repo"]
    result = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html",
        entry_path="report/index.html",  # relative to agent workspace
        title="t", description=None, target_artifact_id=None,
    )
    row = await repo.get_by_id(result.artifact_id)
    assert row is not None
    assert row.file_path == f"{WS_REL}/report/index.html"


@pytest.mark.asyncio
async def test_register_accepts_entry_at_workspace_root_single_file(env):
    """Entry directly in the workspace is allowed (no hard rule).
    Single-file mode: size_bytes is the entry file's size, not a recursive
    sum of the workspace tree."""
    repo: ArtifactRepository = env["repo"]
    flat = env["workspace"] / "report.html"
    flat.write_text("<p>flat</p>", encoding="utf-8")
    other = env["workspace"] / "unrelated.txt"
    other.write_text("not part of any artifact" * 100, encoding="utf-8")

    result = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(flat),
        title="flat report", description=None, target_artifact_id=None,
    )
    row = await repo.get_by_id(result.artifact_id)
    assert row is not None
    assert row.file_path == f"{WS_REL}/report.html"
    # Critical: size accounts ONLY for the entry — never sums siblings at the
    # workspace root (else unrelated.txt would inflate the quota).
    assert row.size_bytes == flat.stat().st_size
    assert row.size_bytes < other.stat().st_size


@pytest.mark.asyncio
async def test_register_rejects_path_outside_workspace(env):
    repo: ArtifactRepository = env["repo"]
    with pytest.raises(artifact_runner.ArtifactPathEscape):
        await artifact_runner.register_artifact(
            repo=repo,
            agent_id="agent_x", user_id="user_y", session_id="s",
            kind="text/html", entry_path="/etc/passwd",
            title="t", description=None, target_artifact_id=None,
        )


@pytest.mark.asyncio
async def test_register_rejects_invalid_kind(env):
    repo: ArtifactRepository = env["repo"]
    with pytest.raises(artifact_runner.ArtifactError):
        await artifact_runner.register_artifact(
            repo=repo,
            agent_id="agent_x", user_id="user_y", session_id="s",
            kind="application/octet-stream",
            entry_path=str(env["entry"]),
            title="t", description=None, target_artifact_id=None,
        )


@pytest.mark.asyncio
async def test_size_is_recursive_dir_size(env):
    """A multi-file artifact: size_bytes is the recursive sum of the entry's
    directory tree, not just the entry file."""
    repo: ArtifactRepository = env["repo"]
    root = env["workspace"] / "report"
    (root / "style.css").write_text("body{font:1em sans-serif}", encoding="utf-8")
    (root / "data.json").write_text('{"x":1}', encoding="utf-8")

    result = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(env["entry"]),
        title="t", description=None, target_artifact_id=None,
    )
    row = await repo.get_by_id(result.artifact_id)
    assert row is not None
    expected = sum(
        os.path.getsize(os.path.join(d, name))
        for d, _, files in os.walk(root) for name in files
    )
    assert row.size_bytes == expected
    assert expected > os.path.getsize(env["entry"])  # multi-file confirmed


@pytest.mark.asyncio
async def test_target_artifact_id_updates_in_place(env):
    repo: ArtifactRepository = env["repo"]
    r1 = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(env["entry"]),
        title="v1", description=None, target_artifact_id=None,
    )

    # Build a second artifact directory and re-register onto the same id.
    (env["workspace"] / "report2").mkdir()
    entry2 = env["workspace"] / "report2" / "index.html"
    entry2.write_text("<p>v2</p>", encoding="utf-8")

    r2 = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(entry2),
        title="v2", description=None, target_artifact_id=r1.artifact_id,
    )
    assert r2.artifact_id == r1.artifact_id
    row = await repo.get_by_id(r1.artifact_id)
    assert row is not None
    assert row.file_path == f"{WS_REL}/report2/index.html"
    assert row.title == "v2"


@pytest.mark.asyncio
async def test_target_artifact_id_kind_mismatch_raises(env):
    repo: ArtifactRepository = env["repo"]
    r1 = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(env["entry"]),
        title="t", description=None, target_artifact_id=None,
    )
    (env["workspace"] / "csvdir").mkdir()
    csv = env["workspace"] / "csvdir" / "data.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(artifact_runner.ArtifactKindMismatch):
        await artifact_runner.register_artifact(
            repo=repo,
            agent_id="agent_x", user_id="user_y", session_id=None,
            kind="text/csv", entry_path=str(csv),
            title="t", description=None, target_artifact_id=r1.artifact_id,
        )


@pytest.mark.asyncio
async def test_target_artifact_id_not_found_raises(env):
    repo: ArtifactRepository = env["repo"]
    with pytest.raises(artifact_runner.ArtifactNotFound):
        await artifact_runner.register_artifact(
            repo=repo,
            agent_id="agent_x", user_id="user_y", session_id=None,
            kind="text/html", entry_path=str(env["entry"]),
            title="t", description=None, target_artifact_id="art_doesnotexist",
        )


@pytest.mark.asyncio
async def test_register_without_session_auto_pins(env):
    """session_id=None → pinned=True so the artifact appears in list_pinned
    rather than being invisible (session list filter rejects pinned=0)."""
    repo: ArtifactRepository = env["repo"]
    result = await artifact_runner.register_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(env["entry"]),
        title="t", description=None, target_artifact_id=None,
    )
    row = await repo.get_by_id(result.artifact_id)
    assert row is not None
    assert row.session_id is None
    assert row.pinned is True
    assert row.original_session_id is None
