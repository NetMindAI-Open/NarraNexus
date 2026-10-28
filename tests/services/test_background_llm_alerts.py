"""
@file_name: test_background_llm_alerts.py
@date: 2026-07-07
@description: Tests for the background LLM failure alerter — the surface that
turns a previously-silent credential 401 into a DB audit row + a de-duplicated,
redacted owner inbox notice.
"""

import pytest

from narranexus.platform.services import background_llm_alerts as alerts


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
def _wire(monkeypatch, db_client):
    _FakeInboxRepo.created = []
    _FakeAuditor.errors = []

    # A REAL (in-memory) database: the cooldown is a table now, not a
    # process-local map, so the dedup test exercises the persisted window.
    async def _db():
        return db_client

    monkeypatch.setattr(alerts, "get_db_client", _db)
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
async def test_key_binding_outage_only_audits_without_credential_notice():
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="narrative_update",
        error="Error code: 503 - {'detail': 'API key channel binding is temporarily unavailable'}",
        source_id="nar_1",
    )
    assert len(_FakeAuditor.errors) == 1
    assert _FakeAuditor.errors[0][1]["category"] == "generic"
    assert _FakeInboxRepo.created == []


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


@pytest.mark.asyncio
async def test_cooldown_survives_a_process_restart(db_client):
    """2026-09-09: the window is persisted — a second process (or the same
    one after a restart) must not re-notify inside it."""
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="narrative_update",
        error="401 unauthorized", source_id="nar_1",
    )
    rows = await db_client.get("owner_notice_cooldowns", {"agent_id": "agt_1"})
    assert [(r["target"], r["category"]) for r in rows] == [("nar_1", "provider_credential")]
    # Nothing in-process to reset any more; the row alone suppresses.
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="narrative_update",
        error="401 unauthorized", source_id="nar_1",
    )
    assert len(_FakeInboxRepo.created) == 1


@pytest.mark.asyncio
async def test_an_unreadable_cooldown_fails_open(monkeypatch):
    class _Dead:
        async def get_one(self, *_a, **_k):
            raise RuntimeError("db down")

        async def update(self, *_a, **_k):
            raise RuntimeError("db down")

        async def insert(self, *_a, **_k):
            raise RuntimeError("db down")

    async def _db():
        return _Dead()

    monkeypatch.setattr(alerts, "get_db_client", _db)
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="narrative_update",
        error="401 unauthorized", source_id="nar_1",
    )
    assert len(_FakeInboxRepo.created) == 1   # notified, not silenced


# ── out-of-credit is owner-actionable too ───────────────────────────────────
#
# 2026-09-07 prod: an owner's NetMind balance was empty and the team summary
# failed with "balance not enough" for three days. That is not a credential
# error, so the alert used to stop at the audit row — the one failure the owner
# alone could fix never reached them.


@pytest.mark.asyncio
async def test_an_empty_balance_notifies_the_owner(db_client):
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="team_summary",
        error=RuntimeError("Error code: 400 - balance not enough"), source_id="team_1",
    )
    assert len(_FakeAuditor.errors) == 1
    assert _FakeAuditor.errors[0][1]["category"] == "provider_balance"
    assert len(_FakeInboxRepo.created) == 1
    msg = _FakeInboxRepo.created[0]
    assert "Top up" in msg["content"]
    assert "team_summary" in msg["title"]
    rows = await db_client.get("owner_notice_cooldowns", {"agent_id": "usr_owner"})
    assert [(r["target"], r["category"]) for r in rows] == [("owner", "provider_balance")]


@pytest.mark.asyncio
async def test_an_empty_balance_is_one_notice_per_owner_across_sources():
    """An empty balance is one fact about the owner's account; every narrative,
    entity and team of every agent failing on it must not each page them."""
    err = RuntimeError("Error code: 400 - balance not enough")
    for agent_id, source, source_id in [
        ("agt_1", "narrative_update", "nar_1"),
        ("agt_1", "narrative_update", "nar_2"),
        ("agt_1", "entity_dedup", "Alice"),
        ("agt_2", "team_summary", "team_1"),
    ]:
        await alerts.alert_background_llm_failure(
            agent_id=agent_id, owner_user_id="usr_owner", source=source,
            error=err, source_id=source_id,
        )
    assert len(_FakeAuditor.errors) == 4  # every failure still leaves its trace
    assert len(_FakeInboxRepo.created) == 1
    # Another owner is a different account: notified separately.
    await alerts.alert_background_llm_failure(
        agent_id="agt_3", owner_user_id="usr_other", source="narrative_update",
        error=err, source_id="nar_9",
    )
    assert len(_FakeInboxRepo.created) == 2


@pytest.mark.asyncio
async def test_credential_notices_stay_per_source():
    for source_id in ("nar_1", "nar_2"):
        await alerts.alert_background_llm_failure(
            agent_id="agt_1", owner_user_id="usr_owner", source="narrative_update",
            error="401 unauthorized", source_id=source_id,
        )
    assert len(_FakeInboxRepo.created) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        "403 Forbidden: your credit balance is too low",
        "API key sk-abc has insufficient balance",
    ],
)
async def test_balance_wins_over_a_credential_lookalike(error):
    """Same order as `classify_self_serviceable`: out-of-credit first. These
    bodies also match the broad credential patterns (a bare 403, "API key")."""
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="narrative_update",
        error=error, source_id="nar_1",
    )
    assert _FakeAuditor.errors[0][1]["category"] == "provider_balance"
    content = _FakeInboxRepo.created[0]["content"]
    assert "Top up" in content
    assert "API key and base URL" not in content
    assert "balance/quota error" in content


@pytest.mark.asyncio
async def test_a_spent_free_tier_gets_the_free_tier_remedy():
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="team_summary",
        error="ExceededBudget: budget has been exceeded", source_id="team_1",
    )
    assert len(_FakeInboxRepo.created) == 1
    content = _FakeInboxRepo.created[0]["content"]
    assert "Nexus Pro" in content
    assert "Top up" not in content


@pytest.mark.asyncio
async def test_a_credential_failure_keeps_the_credential_remedy():
    await alerts.alert_background_llm_failure(
        agent_id="agt_1", owner_user_id="usr_owner", source="narrative_update",
        error="401 unauthorized", source_id="nar_1",
    )
    content = _FakeInboxRepo.created[0]["content"]
    assert "API key and base URL" in content
    assert "Top up" not in content
