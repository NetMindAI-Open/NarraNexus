"""
@file_name: test_user_display_name.py
@author: NarraNexus
@date: 2026-06-11
@description: UserRepository.get_display_name — the single resolver used
everywhere user identity is rendered into an agent prompt as a human name.
Returns the human display name, falling back to the user_id when there's no
display name (or no user). user_id stays an opaque scoping key everywhere
else; this is the one place it's turned into something a human reads.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.repository.user_repository import UserRepository


@pytest.mark.asyncio
async def test_returns_display_name(db_client):
    await db_client.insert("users", {
        "user_id": "a" * 32, "display_name": "Alice",
        "user_type": "individual", "status": "active",
    })
    repo = UserRepository(db_client)
    assert await repo.get_display_name("a" * 32) == "Alice"


@pytest.mark.asyncio
async def test_falls_back_to_user_id_when_no_display_name(db_client):
    await db_client.insert("users", {
        "user_id": "b" * 32, "user_type": "individual", "status": "active",
    })
    repo = UserRepository(db_client)
    assert await repo.get_display_name("b" * 32) == "b" * 32


@pytest.mark.asyncio
async def test_falls_back_to_user_id_when_user_missing(db_client):
    repo = UserRepository(db_client)
    assert await repo.get_display_name("ghost") == "ghost"


@pytest.mark.asyncio
async def test_empty_user_id_returns_empty(db_client):
    repo = UserRepository(db_client)
    assert await repo.get_display_name("") == ""
    assert await repo.get_display_name(None) == ""
