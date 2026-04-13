"""
@file_name: test_auth_local.py
@description: T06+T09 — get_local_user_id bootstrap + lifespan bind assertion.
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_returns_singleton_when_user_exists():
    from backend.auth import get_local_user_id

    mock_db = AsyncMock()
    mock_db.get_one.return_value = {"user_id": "alice"}
    with patch(
        "xyz_agent_context.utils.db_factory.get_db_client",
        new=AsyncMock(return_value=mock_db),
    ):
        uid = await get_local_user_id()
    assert uid == "alice"


@pytest.mark.asyncio
async def test_creates_local_default_when_table_empty():
    from backend.auth import get_local_user_id

    mock_db = AsyncMock()
    mock_db.get_one.return_value = None
    mock_db.insert = AsyncMock()
    with patch(
        "xyz_agent_context.utils.db_factory.get_db_client",
        new=AsyncMock(return_value=mock_db),
    ):
        uid = await get_local_user_id()
    assert uid == "local-default"
    mock_db.insert.assert_called_once()
    args, kwargs = mock_db.insert.call_args
    assert args[0] == "users"
    assert args[1]["user_id"] == "local-default"
    assert args[1]["user_type"] == "local"


# --- T09: lifespan bind assertion ------------------------------------------

def test_lifespan_bind_assertion_accepts_loopback_via_env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr("sys.argv", ["prog"])
    from backend.main import _assert_local_bind_is_loopback

    _assert_local_bind_is_loopback(is_cloud_mode=False)


def test_lifespan_bind_assertion_accepts_loopback_via_argv(monkeypatch):
    monkeypatch.delenv("DASHBOARD_BIND_HOST", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
    )
    from backend.main import _assert_local_bind_is_loopback

    _assert_local_bind_is_loopback(is_cloud_mode=False)


def test_lifespan_bind_assertion_rejects_non_loopback(monkeypatch):
    monkeypatch.setenv("DASHBOARD_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr("sys.argv", ["prog"])
    from backend.main import _assert_local_bind_is_loopback

    with pytest.raises(SystemExit):
        _assert_local_bind_is_loopback(is_cloud_mode=False)


def test_lifespan_bind_assertion_skipped_in_cloud(monkeypatch):
    monkeypatch.setenv("DASHBOARD_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr("sys.argv", ["prog"])
    from backend.main import _assert_local_bind_is_loopback

    _assert_local_bind_is_loopback(is_cloud_mode=True)  # no-op


def test_warn_if_multi_worker(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    from backend.main import _warn_if_multi_worker

    # loguru → stdlib fallback path; simplest: just ensure no crash and function returns
    _warn_if_multi_worker()
