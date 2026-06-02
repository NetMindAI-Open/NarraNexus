"""
@file_name: test_framework_downgrade_audit.py
@author: Bin Liang
@date: 2026-05-29
@description: E2 — framework self-downgrade events are persisted to the
service_audit table (incident lesson #4/#5), not just logged.
"""
import pytest

from xyz_agent_context.agent_framework import openai_agents_sdk as oa
from xyz_agent_context.repository.service_audit_repository import ServiceAuditRepository


@pytest.mark.asyncio
async def test_downgrade_audit_writes_row(db_client, monkeypatch):
    # Point the audit helper's lazily-acquired client at the test DB.
    async def _fake_get_db_client():
        return db_client
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client", _fake_get_db_client
    )

    await oa._audit_framework_downgrade(
        "agents_sdk_blocklisted",
        {"base_url": "https://api.netmind.ai/v1", "model": "deepseek-chat", "error": "boom"},
    )

    repo = ServiceAuditRepository(db_client)
    rows = await repo.recent(service="llm_framework")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "agents_sdk_blocklisted"
    assert "deepseek-chat" in rows[0]["detail"]


@pytest.mark.asyncio
async def test_downgrade_audit_never_raises(monkeypatch):
    # If the DB client can't be acquired, the helper must swallow the error.
    async def _boom():
        raise RuntimeError("no db")
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client", _boom
    )
    # Must not raise.
    await oa._audit_framework_downgrade("x", {"k": "v"})
