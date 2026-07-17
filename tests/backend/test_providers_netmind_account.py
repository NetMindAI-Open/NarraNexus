"""
@file_name: test_providers_netmind_account.py
@author:
@date: 2026-07-16
@description: GET /api/providers attaches the captured NetMind account email
per provider (so Settings shows which account a key belongs to).
"""
import pytest

import backend.routes.providers as prov_routes


@pytest.mark.asyncio
async def test_attach_netmind_accounts(db_client, monkeypatch):
    await db_client.insert("user_providers", {
        "provider_id": "p1", "user_id": "u1", "name": "p1",
        "source": "netmind", "protocol": "anthropic", "auth_type": "api_key",
        "netmind_account_email": "alice@example.com",
        "netmind_account_id": "acct_123",
    })

    async def fake_db():
        return db_client
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client", fake_db
    )

    data = {"providers": {"p1": {"name": "p1"}, "p_other": {"name": "x"}}}
    out = await prov_routes._attach_netmind_accounts("u1", data)

    assert out["providers"]["p1"]["netmind_account_email"] == "alice@example.com"
    # A provider with no captured account gets no field (not None, absent).
    assert "netmind_account_email" not in out["providers"]["p_other"]


@pytest.mark.asyncio
async def test_attach_best_effort_on_db_error(monkeypatch):
    async def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client", boom
    )
    data = {"providers": {"p1": {"name": "p1"}}}
    # Must NOT raise — display enrichment is best-effort.
    out = await prov_routes._attach_netmind_accounts("u1", data)
    assert out["providers"]["p1"] == {"name": "p1"}
