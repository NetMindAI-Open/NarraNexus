"""
@file_name: test_select_records_cost.py
@date: 2026-08-14
@description: `select()` actually FILLS continuity_ms / retrieve_ms.

Reviewed gap. `test_routing_audit_timing.py` proves "a RoutingAudit carrying
these values reaches the table"; every one of its cases hands the values in by
hand. Nothing ran the real assignment. Delete either line in
`NarrativeService.select` and the whole backend suite stays green while two
columns sit permanently NULL.

That failure is unusually quiet: NULL is the schema's "this tier did not run",
so a broken recorder is indistinguishable from a decision that legitimately
skipped the tier. It surfaces only when somebody opens the audit table to ask
where the seconds went — which is the one job these columns have.

`is not None`, never `> 0`: a stubbed detector can finish inside one
millisecond and `int(...*1000)` legitimately yields 0, which in this schema
means "ran, was fast".
"""
from __future__ import annotations

import pytest

from datetime import datetime

from narranexus.platform.narrative.models import (
    Narrative,
    NarrativeInfo,
    NarrativeSelectionResult,
    NarrativeType,
)


class _Session:
    """Just enough Session for `select` to run the continuity tier."""

    def __init__(self):
        self.last_query = "what did we decide yesterday?"
        self.last_response = "we decided to ship it"
        self.current_narrative_id = None
        self.query_count = 0
        self.last_query_time = None


class _ContinuityResult:
    is_continuous = False
    reason = "different topic"
    confidence = 0.9


class _Detector:
    async def detect(self, **kwargs):
        return _ContinuityResult()


def _narrative(nid: str = "nar_x") -> Narrative:
    now = datetime(2026, 8, 14, 0, 0, 0)
    return Narrative(
        id=nid,
        type=NarrativeType.CHAT,
        agent_id="agent_x",
        narrative_info=NarrativeInfo(
            name="N", description="d", current_summary="s", actors=[],
        ),
        event_ids=[],
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def service(monkeypatch, db_client):
    """A NarrativeService whose audit write is captured, not persisted.

    `db_client` is here so constructing the service has a backend to bind to;
    no assertion in this file goes through it. Persistence is
    `test_routing_audit_timing.py`'s job — this file only asks whether the two
    fields are ever ASSIGNED.
    """
    from narranexus.platform.narrative.narrative_service import NarrativeService

    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )
    svc = NarrativeService(agent_id="agent_x")

    # Continuity runs (so continuity_ms is measured) but says "not continuous",
    # which is what sends the flow into retrieval (so retrieve_ms is measured).
    monkeypatch.setattr(svc, "_get_continuity_detector", lambda: _Detector())

    async def _fake_retrieve(**kwargs):
        result = NarrativeSelectionResult(
            narratives=[_narrative()],
            selection_reason="stub",
            selection_method="llm_unified",
            is_new=False,
            retrieval_method="keyword",
        )
        from narranexus.platform.narrative.models import RoutingAudit

        result.audit = RoutingAudit(
            agent_id="agent_x", user_id="u1", query_text="q"
        )
        result.audit_snapshots = {}
        return result

    monkeypatch.setattr(svc._retrieval, "retrieve_top_k", _fake_retrieve)
    return svc


@pytest.mark.asyncio
async def test_select_records_both_tier_costs(service, monkeypatch):
    written = {}

    async def _capture(audit, snapshots):
        written["audit"] = audit

    monkeypatch.setattr(service, "_write_audit", _capture)

    await service.select(
        "agent_x", "u1", "a brand new question",
        session=_Session(), is_user_chat=True,
    )

    audit = written.get("audit")
    assert audit is not None, "no audit was written at all"
    assert audit.continuity_ms is not None, (
        "continuity_ms was never assigned — the column sits at NULL, which "
        "reads as 'the tier did not run'"
    )
    assert audit.retrieve_ms is not None, (
        "retrieve_ms was never assigned — same failure, same disguise"
    )


@pytest.mark.asyncio
async def test_a_turn_with_no_session_anchor_leaves_continuity_null(
    service, monkeypatch
):
    """NULL must still MEAN something. Without a session there is nothing to
    judge continuity against, the tier is skipped, and the column stays NULL —
    which is exactly why a broken recorder hides so well."""
    written = {}

    async def _capture(audit, snapshots):
        written["audit"] = audit

    monkeypatch.setattr(service, "_write_audit", _capture)

    await service.select(
        "agent_x", "u1", "a brand new question", session=None, is_user_chat=True,
    )

    assert written["audit"].continuity_ms is None
    assert written["audit"].retrieve_ms is not None, (
        "retrieval still ran, so its cost must still be recorded"
    )
