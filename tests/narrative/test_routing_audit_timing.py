"""
@file_name: test_routing_audit_timing.py
@date: 2026-08-14
@description: Narrative selection records WHERE its seconds went, per decision.

`[TIMED] narrative.*` has split narrative selection into six spans for a while
— into loguru only. `[turn-timing]`'s `setup_s` says the setup phase is the largest single block
of a turn, but not which tier inside it, and the log
lines that would say cannot be aggregated and do not survive a restart
(incident lesson #5).

`narrative_routing_audit` already writes one row per decision with the full
evidence trail. Four millisecond columns join the cost to the decision it paid
for, which is the question worth asking: not "how slow is narrative selection"
but "how slow is it WHEN IT SHORT-CIRCUITS vs when it calls the judge".

Pinned here:

* the four spans land as integer milliseconds on the audit row,
* a short-circuited decision records no judge cost — a NULL that means "did not
  run", not a zero that would drag the judge's average toward nothing,
* continuity that never ran leaves NULL for the same reason,
* the columns are advisory: a timing failure never disturbs the routing result.
"""
from __future__ import annotations

import pytest

from narranexus.platform.narrative.models import RoutingAudit
from narranexus.platform.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
)


def _audit(**kw) -> RoutingAudit:
    base = dict(agent_id="agent_x", user_id="usr_1", query_text="q")
    base.update(kw)
    return RoutingAudit(**base)


def test_the_model_carries_the_four_spans():
    a = _audit(continuity_ms=3941, retrieve_ms=4703, keyword_ms=4, judge_ms=4690)
    assert a.continuity_ms == 3941
    assert a.retrieve_ms == 4703
    assert a.keyword_ms == 4
    assert a.judge_ms == 4690


def test_unmeasured_spans_default_to_none_not_zero():
    """NULL means "this tier did not run". Zero would mean "it ran instantly".

    A short-circuited decision skips the judge entirely; storing 0 there makes
    every "how long does arbitration take" query answer far too low — and the
    whole point of joining cost to decision is to compare those two paths.
    """
    a = _audit()
    assert a.continuity_ms is None
    assert a.retrieve_ms is None
    assert a.keyword_ms is None
    assert a.judge_ms is None


def test_the_row_carries_the_spans_through_to_the_table():
    row = NarrativeRoutingAuditRepository._to_row(
        _audit(continuity_ms=3941, retrieve_ms=4703, keyword_ms=4, judge_ms=4690)
    )
    assert row["continuity_ms"] == 3941
    assert row["retrieve_ms"] == 4703
    assert row["keyword_ms"] == 4
    assert row["judge_ms"] == 4690


def test_the_row_keeps_nulls_as_nulls():
    row = NarrativeRoutingAuditRepository._to_row(_audit())
    for col in ("continuity_ms", "retrieve_ms", "keyword_ms", "judge_ms"):
        assert row[col] is None, f"{col} was coerced away from NULL"


@pytest.mark.asyncio
async def test_the_spans_reach_the_database(db_client):
    repo = NarrativeRoutingAuditRepository(db_client)
    await repo.record(_audit(
        continuity_ms=3941, retrieve_ms=4703, keyword_ms=4, judge_ms=4690,
        selection_method="llm_unified",
    ))

    rows = await db_client.get("narrative_routing_audit", {"agent_id": "agent_x"})
    assert len(rows) == 1
    assert rows[0]["continuity_ms"] == 3941
    assert rows[0]["judge_ms"] == 4690


@pytest.mark.asyncio
async def test_a_short_circuit_row_has_no_judge_cost(db_client):
    """The comparison the columns exist to make, end to end."""
    repo = NarrativeRoutingAuditRepository(db_client)
    await repo.record(_audit(
        agent_id="agent_fast",
        continuity_ms=3900, retrieve_ms=9, keyword_ms=4,
        selection_method="high_confidence", gate_short_circuit=True,
    ))

    row = (await db_client.get("narrative_routing_audit", {"agent_id": "agent_fast"}))[0]
    assert row["judge_ms"] is None
    assert row["retrieve_ms"] == 9
