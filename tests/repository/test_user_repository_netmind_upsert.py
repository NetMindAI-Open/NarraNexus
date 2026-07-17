"""
@file_name: test_user_repository_netmind_upsert.py
@author: NarraNexus
@date: 2026-06-11
@description: Tests for UserRepository.upsert_netmind_user (Phase 1 user-system
unification). NetMind login has no registration step: first login lazily
creates the local user row keyed by userSystemCode; later logins refresh
email / display_name and bump last_login_time.

Covers:
- first login creates the row (user_type=individual, active) -> is_new=True
- second login is an update, not a duplicate -> is_new=False
- email / display_name drift on NetMind's side is mirrored locally
- last_login_time is bumped on every upsert
- existing values survive when NetMind sends nothing better (None nickname)
"""
from __future__ import annotations

import pytest

from xyz_agent_context.repository.user_repository import UserRepository


_CODE = "f" * 32


@pytest.mark.asyncio
async def test_first_login_creates_user(db_client):
    repo = UserRepository(db_client)

    user, is_new = await repo.upsert_netmind_user(
        user_system_code=_CODE, email="a@x.com", display_name="Alice"
    )

    assert is_new is True
    assert user.user_id == _CODE
    assert user.email == "a@x.com"
    assert user.display_name == "Alice"
    assert user.user_type == "individual"
    assert user.status.value == "active"
    assert user.last_login_time is not None


@pytest.mark.asyncio
async def test_second_login_updates_not_duplicates(db_client):
    repo = UserRepository(db_client)
    await repo.upsert_netmind_user(_CODE, email="a@x.com", display_name="Alice")

    user, is_new = await repo.upsert_netmind_user(
        _CODE, email="new@x.com", display_name="Alice Renamed"
    )

    assert is_new is False
    assert user.email == "new@x.com"
    assert user.display_name == "Alice Renamed"
    users = await repo.list_users()
    assert len([u for u in users if u.user_id == _CODE]) == 1


@pytest.mark.asyncio
async def test_upsert_keeps_existing_fields_when_incoming_is_none(db_client):
    repo = UserRepository(db_client)
    await repo.upsert_netmind_user(_CODE, email="a@x.com", display_name="Alice")

    user, _ = await repo.upsert_netmind_user(_CODE, email="a@x.com", display_name=None)

    assert user.display_name == "Alice"  # not clobbered by None


@pytest.mark.asyncio
async def test_upsert_bumps_last_login_time(db_client):
    repo = UserRepository(db_client)
    first, _ = await repo.upsert_netmind_user(_CODE, email="a@x.com")
    second, _ = await repo.upsert_netmind_user(_CODE, email="a@x.com")

    assert second.last_login_time is not None
    assert second.last_login_time >= first.last_login_time


@pytest.mark.asyncio
async def test_pre_existing_local_user_is_upgraded_to_individual(db_client):
    """B4: a pure-local username user on a dual-mode install who later logs in
    with their Power account must become user_type='individual', or the billing
    gate (is_power_account) would keep denying them."""
    repo = UserRepository(db_client)
    # Pre-existing local row (as created by /api/auth/create-user).
    await repo.add_user(user_id=_CODE, user_type="local", display_name="Local Bob")

    user, is_new = await repo.upsert_netmind_user(
        _CODE, email="bob@x.com", display_name="Bob"
    )

    assert is_new is False  # row already existed
    assert user.user_type == "individual"  # upgraded
    assert user.email == "bob@x.com"
