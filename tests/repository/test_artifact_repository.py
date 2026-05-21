"""
@file_name: test_artifact_repository.py
@author: Bin Liang
@date: 2026-05-08
@description: TDD tests for ArtifactRepository (pointer model, 2026-05-14).

Uses real in-memory SQLite (via conftest db_client fixture).
Covers: create, update_pointer, set_pinned/unpin (incl. double-pin idempotency),
list_by_session/list_pinned/list_by_user, delete, bulk_delete.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact


def _make_artifact(
    artifact_id: str = "art_test0001",
    agent_id: str = "agent_1",
    user_id: str = "user_1",
    session_id: str | None = "ses_abc123",
    title: str = "My Chart",
    kind: str = "text/html",
    pinned: bool = False,
    file_path: str = "agent_1_user_1/chart/index.html",
    size_bytes: int = 1024,
) -> Artifact:
    now = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    return Artifact(
        artifact_id=artifact_id,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        title=title,
        kind=kind,
        pinned=pinned,
        file_path=file_path,
        size_bytes=size_bytes,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def repo(db_client):
    return ArtifactRepository(db_client)


@pytest.mark.asyncio
async def test_create_then_get_returns_same_artifact(repo):
    artifact = _make_artifact()
    await repo.create(artifact)

    fetched = await repo.get_by_id("art_test0001")
    assert fetched is not None
    assert fetched.artifact_id == "art_test0001"
    assert fetched.agent_id == "agent_1"
    assert fetched.user_id == "user_1"
    assert fetched.session_id == "ses_abc123"
    assert fetched.title == "My Chart"
    assert fetched.kind == "text/html"
    assert fetched.pinned is False
    assert fetched.file_path == "agent_1_user_1/chart/index.html"
    assert fetched.size_bytes == 1024


@pytest.mark.asyncio
async def test_update_pointer_overwrites_path_size_and_meta(repo):
    """update_pointer (the re-registration path) overwrites file_path/size_bytes
    and optionally title/description; kind is intentionally NOT changed."""
    await repo.create(_make_artifact(size_bytes=500))

    await repo.update_pointer(
        "art_test0001",
        file_path="agent_1_user_1/chart_v2/index.html",
        size_bytes=900,
        title="My Chart v2",
        description="updated",
    )
    fetched = await repo.get_by_id("art_test0001")
    assert fetched is not None
    assert fetched.file_path == "agent_1_user_1/chart_v2/index.html"
    assert fetched.size_bytes == 900
    assert fetched.title == "My Chart v2"
    assert fetched.description == "updated"
    assert fetched.kind == "text/html"  # untouched


@pytest.mark.asyncio
async def test_pin_clears_session_id(repo):
    await repo.create(_make_artifact(session_id="ses_xyz"))
    await repo.set_pinned("art_test0001", pinned=True)
    fetched = await repo.get_by_id("art_test0001")
    assert fetched is not None
    assert fetched.pinned is True
    assert fetched.session_id is None
    assert fetched.original_session_id == "ses_xyz"


@pytest.mark.asyncio
async def test_unpin_restores_original_session_id(repo):
    art = _make_artifact(artifact_id="art_unpin01", session_id="sess_42")
    await repo.create(art)
    await repo.set_pinned("art_unpin01", pinned=True)
    pinned = await repo.get_by_id("art_unpin01")
    assert pinned is not None
    assert pinned.session_id is None
    assert pinned.original_session_id == "sess_42"

    await repo.set_pinned("art_unpin01", pinned=False)
    unpinned = await repo.get_by_id("art_unpin01")
    assert unpinned is not None
    assert unpinned.session_id == "sess_42"
    assert unpinned.original_session_id is None
    assert unpinned.pinned is False


@pytest.mark.asyncio
async def test_double_pin_preserves_original_session_id(repo):
    """Re-pinning an already-pinned artifact must NOT overwrite
    original_session_id (COALESCE guard in set_pinned)."""
    art = _make_artifact(artifact_id="art_dbl", session_id="sess_1")
    await repo.create(art)

    await repo.set_pinned("art_dbl", pinned=True)
    after_first = await repo.get_by_id("art_dbl")
    assert after_first is not None
    assert after_first.original_session_id == "sess_1"

    await repo.set_pinned("art_dbl", pinned=True)
    after_second = await repo.get_by_id("art_dbl")
    assert after_second is not None
    assert after_second.original_session_id == "sess_1"  # not overwritten

    await repo.set_pinned("art_dbl", pinned=False)
    unpinned = await repo.get_by_id("art_dbl")
    assert unpinned is not None
    assert unpinned.session_id == "sess_1"
    assert unpinned.original_session_id is None


@pytest.mark.asyncio
async def test_list_by_session_excludes_pinned(repo):
    session_id = "ses_common"
    agent_id = "agent_2"
    await repo.create(_make_artifact(
        artifact_id="art_n01", agent_id=agent_id, session_id=session_id,
    ))
    await repo.create(_make_artifact(
        artifact_id="art_p01", agent_id=agent_id, session_id=session_id, pinned=True,
    ))

    session_artifacts = await repo.list_by_session(agent_id, session_id)
    assert {a.artifact_id for a in session_artifacts} == {"art_n01"}

    pinned_artifacts = await repo.list_pinned(agent_id)
    assert {a.artifact_id for a in pinned_artifacts} == {"art_p01"}


@pytest.mark.asyncio
async def test_delete_removes_row(repo):
    await repo.create(_make_artifact())
    await repo.delete("art_test0001")
    assert await repo.get_by_id("art_test0001") is None


@pytest.mark.asyncio
async def test_list_by_user_orders_by_recency(repo):
    user_id = "user_list_test"
    await repo.create(_make_artifact(
        artifact_id="art_l_01", user_id=user_id, title="oldest",
    ))
    await repo.create(_make_artifact(
        artifact_id="art_l_02", user_id=user_id, title="middle",
    ))
    await repo.create(_make_artifact(
        artifact_id="art_l_03", user_id=user_id, title="newest",
    ))
    await repo.create(_make_artifact(
        artifact_id="art_l_other", user_id="not_us", title="not for us",
    ))

    # Bump updated_at on art_l_03 so it sorts first deterministically.
    await repo.update_pointer(
        "art_l_03",
        file_path="agent_1_user_1/chart_b/index.html",
        size_bytes=2000,
    )

    rows = await repo.list_by_user(user_id)
    titles = [r.title for r in rows]
    assert titles[0] == "newest"
    assert "not for us" not in titles
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_bulk_delete_removes_specified_ids_only(repo):
    user_id = "user_bulk"
    await repo.create(_make_artifact(artifact_id="art_b_01", user_id=user_id))
    await repo.create(_make_artifact(artifact_id="art_b_02", user_id=user_id))
    await repo.create(_make_artifact(artifact_id="art_b_03", user_id=user_id))

    deleted = await repo.bulk_delete(["art_b_01", "art_b_02"])
    assert deleted == 2
    assert await repo.get_by_id("art_b_01") is None
    assert await repo.get_by_id("art_b_02") is None
    assert await repo.get_by_id("art_b_03") is not None


@pytest.mark.asyncio
async def test_bulk_delete_empty_list_is_noop(repo):
    assert await repo.bulk_delete([]) == 0
