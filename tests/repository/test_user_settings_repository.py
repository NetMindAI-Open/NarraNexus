"""
@file_name: test_user_settings_repository.py
@date: 2026-06-08
@description: opt-out defaults to False; set True/False round-trips.
"""
import pytest

from narranexus.platform.repository.user_settings_repository import (
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


@pytest.mark.asyncio
async def test_reply_language_default_none(db_client):
    repo = UserSettingsRepository(db_client)
    assert await repo.get_reply_language("nobody") is None


@pytest.mark.asyncio
async def test_reply_language_roundtrip_and_clear(db_client):
    repo = UserSettingsRepository(db_client)
    await repo.set_reply_language("u2", "zh")
    assert await repo.get_reply_language("u2") == "zh"
    await repo.set_reply_language("u2", "en")
    assert await repo.get_reply_language("u2") == "en"
    await repo.set_reply_language("u2", "")   # clear
    assert await repo.get_reply_language("u2") is None
    # coexists with the analytics flag on the same row
    await repo.set_analytics_opt_out("u2", True)
    assert await repo.is_analytics_opted_out("u2") is True
