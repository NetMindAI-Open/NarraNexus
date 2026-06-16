"""
@file_name: test_user_settings_repository.py
@date: 2026-06-08
@description: opt-out defaults to False; set True/False round-trips.
"""
import pytest

from xyz_agent_context.repository.user_settings_repository import (
    UserSettingsRepository,
)


@pytest.mark.asyncio
async def test_default_not_opted_out(db_client):
    repo = UserSettingsRepository(db_client)
    assert await repo.is_analytics_opted_out("nobody") is False


@pytest.mark.asyncio
async def test_set_and_read_opt_out(db_client):
    repo = UserSettingsRepository(db_client)
    await repo.set_analytics_opt_out("u1", True)
    assert await repo.is_analytics_opted_out("u1") is True
    await repo.set_analytics_opt_out("u1", False)
    assert await repo.is_analytics_opted_out("u1") is False
