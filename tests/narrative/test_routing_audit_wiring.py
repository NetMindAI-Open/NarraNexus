"""
@file_name: test_routing_audit_wiring.py
@date: 2026-08-07
@description: `NarrativeService.select()` must actually emit an audit row.

`test_routing_audit.py` proves the audit record is replay-sufficient. It says
nothing about whether production ever writes one — and a table that is correct
but unwritten is exactly the state narrative routing was already in (the
decision existed only in a ProgressMessage and loguru).

The continuity branch gets its own test because it is the path with no BM25
pool and no gate, so it is the one an "audit lives in the retrieval tier"
implementation would silently skip — while being the branch that most needs a
trail: a false `is_continuous` re-uses `session.current_narrative_id` with no
topic check, and each turn writes that id straight back.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from narranexus.platform.narrative.models import ConversationSession
from narranexus.platform.narrative.narrative_service import NarrativeService
from narranexus.platform.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
)

pytestmark = pytest.mark.asyncio

AGENT = "agent_wiring"
USER = "user_wiring"


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)
    # The retrieval tier reaches for the shared factory client in a few spots
    # (default-narrative bootstrap, participant query); point them at the test DB.
    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client",
        _const(db_client),
    )
    # Stub the LLM arbitration tier. Without this these tests make REAL
    # provider calls (observed: an 18s run against minimax/minimax-m2.5 with a
    # structured-output fallback) — slow, non-deterministic, and red in CI
    # where no credentials exist. What is under test here is whether the
    # decision gets recorded, not what the judge decides.
    async def _stub_judge(**kw):
        return {"matched_type": "none", "matched_id": None, "reason": "stubbed"}

    monkeypatch.setattr(svc._retrieval, "_llm_judge_unified", _stub_judge)
    return svc


def _const(value):
    async def _get():
        return value
    return _get


def _session(**kw):
    now = datetime.now(timezone.utc)
    return ConversationSession(
        session_id="sess_wiring", user_id=USER, agent_id=AGENT,
        created_at=now, last_query_time=now, **kw
    )


async def test_retrieval_path_writes_an_audit_row(service, db_client):
    """A cold turn (no session) goes through BM25 and must be recorded.

    The pool is seeded here with a real thread on purpose. Until the C-1
    bucket governance (2026-08-16) this test needed no such setup: the eight
    default narratives were bootstrapped on the first turn, so the pool was
    never empty and the assertion below passed for a reason that had nothing
    to do with the wiring under test. Buckets no longer enter the pool, which
    makes that accident visible — an (agent,user) with no threads now has a
    genuinely empty pool, and an empty pool is the truth, not a missed capture.
    """
    existing = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本排障", description="",
    )

    await service.select(AGENT, USER, "帮我排查一下部署脚本报错", session=None, trigger="chat")

    rows = await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT)
    assert rows, "select() went through the retrieval tier but wrote no audit row"
    row = rows[0]
    assert row["trigger"] == "chat"
    assert row["selection_method"], "outcome not stamped on the audit row"
    assert row["candidates"], "the BM25 pool was not captured"
    # Pool completeness: every candidate that shaped IDF/avgdl must be on the
    # row, or a replay reproduces different scores than the live decision did.
    assert existing.id in {c["narrative_id"] for c in row["candidates"]}
    assert not any(c["is_default"] for c in row["candidates"]), (
        "default buckets must not reach the pool under C-1 governance"
    )


async def test_continuity_path_writes_an_audit_row(service, db_client, monkeypatch):
    """The continuity short-circuit has no pool, but must still be recorded."""
    narrative = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本排障", description="",
    )

    class _AlwaysContinuous:
        async def detect(self, **kw):
            from narranexus.platform.narrative.models import ContinuityResult
            return ContinuityResult(is_continuous=True, confidence=0.91, reason="same goal")

    monkeypatch.setattr(service, "_get_continuity_detector", lambda: _AlwaysContinuous())

    await service.select(
        AGENT, USER, "那第二步呢",
        session=_session(last_query="部署脚本报错", current_narrative_id=narrative.id),
        trigger="chat",
    )

    rows = await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT)
    assert rows, "the continuity branch wrote no audit row"
    row = rows[0]
    assert row["selection_method"] == "continuous"
    assert row["continuity_ran"]
    assert row["continuity_is_continuous"]
    assert row["chosen_narrative_id"] == narrative.id
    # confidence is computed by the detector and then dropped on the floor by
    # the live selection logic; the audit is the only place it survives.
    assert row["continuity_confidence"] == pytest.approx(0.91)


async def test_participants_reach_the_audit_from_the_live_call_site(
    service, db_client, monkeypatch
):
    """The call site must thread participants in, not just be able to.

    A unit test of `_build_pool_record` cannot catch this: pass the participants
    directly and it behaves. The defect lives one level up — recording the
    pool BEFORE the participant merge, which is what the first cut did.
    """
    invited = await service.create_narrative(
        agent_id=AGENT, user_id="someone_else", title="别人邀请我进来的任务",
    )
    monkeypatch.setattr(
        service._retrieval, "_get_participant_narratives",
        lambda **kw: _coro([invited]),
    )

    await service.select(AGENT, USER, "那个任务进展如何", session=None, trigger="chat")

    rows = await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT)
    flagged = [c for c in rows[0]["candidates"] if c["is_participant"]]
    assert flagged, (
        "the participant candidate never reached the audit — the pool was "
        "frozen before the P0-4 merge"
    )
    assert flagged[0]["narrative_id"] == invited.id


def _coro(value):
    async def _run(**kw):
        return value
    return _run()


async def test_audit_failure_does_not_break_selection(service, db_client, monkeypatch):
    """A broken audit table must not cost the user their turn."""
    async def _boom(*a, **k):
        raise RuntimeError("audit table is gone")

    monkeypatch.setattr(NarrativeRoutingAuditRepository, "record", _boom)

    result = await service.select(AGENT, USER, "随便问点什么", session=None, trigger="chat")
    assert result.narratives, "selection failed because the audit write failed"
