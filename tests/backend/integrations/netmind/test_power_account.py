"""
@file_name: test_power_account.py
@author: NarraNexus
@date: 2026-07-13
@description: Unit tests for integrations.netmind.power_account.is_power_account.

The users-table read is stubbed (monkeypatch get_db_client) so no DB is
touched. Verifies the fail-closed contract: only an existing row with
user_type == "individual" is a Power account.
"""
from __future__ import annotations

import pytest

import backend.integrations.netmind.power_account as pa


def _stub_db(monkeypatch, row):
    class _DB:
        async def get_one(self, table, filters):
            assert table == "users"
            assert filters == {"user_id": "u"}
            return row

    async def _get_db_client():
        return _DB()

    monkeypatch.setattr(pa, "get_db_client", _get_db_client)


@pytest.mark.asyncio
async def test_individual_user_is_power(monkeypatch):
    _stub_db(monkeypatch, {"user_id": "u", "user_type": "individual"})
    assert await pa.is_power_account("u") is True


@pytest.mark.asyncio
async def test_local_user_is_not_power(monkeypatch):
    _stub_db(monkeypatch, {"user_id": "u", "user_type": "local"})
    assert await pa.is_power_account("u") is False


@pytest.mark.asyncio
async def test_missing_row_is_not_power(monkeypatch):
    _stub_db(monkeypatch, None)
    assert await pa.is_power_account("u") is False


@pytest.mark.asyncio
async def test_empty_user_id_short_circuits(monkeypatch):
    # No DB call should happen for a falsy user_id.
    async def _boom():
        raise AssertionError("get_db_client must not be called for empty user_id")

    monkeypatch.setattr(pa, "get_db_client", _boom)
    assert await pa.is_power_account("") is False
