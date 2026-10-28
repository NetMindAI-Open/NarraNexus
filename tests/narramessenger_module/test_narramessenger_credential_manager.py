"""
@file_name: test_narramessenger_credential_manager.py
@date: 2026-08-11
@description: Unit tests for NarramessengerCredentialManager's reverse
lookups.

Why this file exists:
    The prewarm endpoint (a follow-up task) calls into the credential
    manager with the agent's Matrix identity, or — in future — the
    platform's ``agent_profile_id``. Both need a reverse lookup that the
    manager didn't have before (queries only filtered by ``agent_id``).
    This file locks the two new lookups' round-trip + empty-string-guard
    behaviour, using the same `db_client` fixture pattern as
    telegram_module/test_telegram_credential_manager.py.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)


@pytest.mark.asyncio
async def test_get_by_matrix_user_id_roundtrip(db_client):
    mgr = NarramessengerCredentialManager(db_client)
    await mgr.upsert(
        NarramessengerCredential(
            agent_id="agent_x", bearer_token="tok", matrix_user_id="@agent-x:hs"
        )
    )

    cred = await mgr.get_by_matrix_user_id("@agent-x:hs")
    assert cred is not None and cred.agent_id == "agent_x"
    assert cred.bearer_token == "tok"  # decoded

    assert await mgr.get_by_matrix_user_id("@nobody:hs") is None
    assert await mgr.get_by_matrix_user_id("") is None


@pytest.mark.asyncio
async def test_get_by_profile_id_roundtrip(db_client):
    mgr = NarramessengerCredentialManager(db_client)
    await mgr.upsert(
        NarramessengerCredential(
            agent_id="agent_y", bearer_token="t", nexus_profile_id="prof-1"
        )
    )

    cred = await mgr.get_by_profile_id("prof-1")
    assert cred is not None and cred.agent_id == "agent_y"

    assert await mgr.get_by_profile_id("") is None
