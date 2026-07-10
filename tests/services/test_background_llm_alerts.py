"""
@file_name: test_background_llm_alerts.py
@date: 2026-07-07
@description: Tests for the background LLM failure alerter — the surface that
turns a previously-silent credential 401 into a DB audit row + a de-duplicated,
redacted owner inbox notice.
"""

import pytest

from xyz_agent_context.services import background_llm_alerts as alerts


class _FakeInboxRepo:
    created: list = []

    def __init__(self, db):
        self._db = db

    async def create_message(self, **kwargs):
        _FakeInboxRepo.created.append(kwargs)
        return len(_FakeInboxRepo.created)


class _FakeAuditor:
    errors: list = []

    def __init__(self, service):
        self.service = service

    async def error(self, detail=None):
        _FakeAuditor.errors.append((self.service, detail))


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    _FakeInboxRepo.created = []
    _FakeAuditor.errors = []
    alerts.reset_alert_state()

    async def _fake_db():
        return object()

    monkeypatch.setattr(alerts, "get_db_client", _fake_db)
    monkeypatch.setattr(alerts, "InboxRepository", _FakeInboxRepo)
    monkeypatch.setattr(alerts, "ServiceAuditor", _FakeAuditor)
    yield


@pytest.mark.asyncio
async def test_credential_failure_writes_audit_and_inbox_redacted():
    await alerts.alert_background_llm_failure(
        agent_id="agt_1",
        owner_user_id="usr_owner",
        source="narrative_update",
        error="Incorrect API key provided: sk-proj-secretKEYfXQA",
        source_id="nar_1",
    )
    # Audit row always written.
    assert len(_FakeAuditor.errors) == 1
    # Owner inbox notice written for a credential-class failure.
    assert len(_FakeInboxRepo.created) == 1
    msg = _FakeInboxRepo.created[0]
    assert msg["user_id"] == "usr_owner"
    # The raw key must never reach the inbox.
    assert "sk-proj-secretKEYfXQA" not in msg["content"]
    assert "sk-***" in msg["content"]


@pytest.mark.asyncio
async def test_dedup_within_cooldown_writes_one_inbox_row():
    for _ in range(3):
        await alerts.alert_background_llm_failure(
            agent_id="agt_1",
            owner_user_id="usr_owner",
            source="narrative_update",
            error="401 unauthorized",
            source_id="nar_1",
        )
    # Cooldown collapses the burst to a single owner notice...
    assert len(_FakeInboxRepo.created) == 1
    # ...but every occurrence is still recorded in the audit trail.
    assert len(_FakeAuditor.errors) == 3


@pytest.mark.asyncio
async def test_non_credential_failure_audits_but_no_inbox():
    await alerts.alert_background_llm_failure(
        agent_id="agt_1",
        owner_user_id="usr_owner",
        source="narrative_update",
        error="connection reset by peer",
        source_id="nar_1",
    )
    assert len(_FakeAuditor.errors) == 1
    assert len(_FakeInboxRepo.created) == 0


@pytest.mark.asyncio
async def test_missing_owner_still_audits():
    await alerts.alert_background_llm_failure(
        agent_id="agt_1",
        owner_user_id=None,
        source="entity_summary",
        error="401 unauthorized",
        source_id="",
    )
    assert len(_FakeAuditor.errors) == 1
    assert len(_FakeInboxRepo.created) == 0
