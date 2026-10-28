"""
@file_name: test_bypass_anchor_gate.py
@date: 2026-08-20
@description: Skipping the LLM judge requires that the turn is STAYING PUT —
BM25's top-1 must be the thread the session is already anchored to.

WHY (prod, 2026-08-14..20, 26,922 routing-audit rows replayed byte-exact)

The floor+margin gate cannot be made right by tuning, because both numbers are
sums over a per-pool IDF table estimated on 9–100 documents:

  `[From Liam] 👊`, same agent, same text, 99 turns — top1 by pool size:
      19 -> 5.66   26 -> 3.35   34 -> 2.41   67 -> 1.09   100 -> 0.02
  RAW_FLOOR=3.0 flips somewhere between pool 26 and pool 34. Per-term
  contribution swings just as hard (2.89 -> 0.01), so "gate on the strongest
  term instead of the sum" inherits the same defect.

But the RISK is not spread across those numbers at all. Classifying every prod
exemption by what it did:

  | staying in the anchored thread | 9,229 | 92.5% |
  | switching to another thread    |   353 |  3.5% |
  | no anchor at all (first turn)  |   392 |  3.9% |

Hijacking can only come from the last two. B-7 p07 is the shape: one wrong
verdict put 20 of 22 turns into a stranger's thread, and the updater then
rewrote that thread's identity until the BM25 evidence was "correct".

So the necessary condition for skipping review becomes structural rather than
numeric: **a bypass may only keep a turn where it already was.** It reads no
score, so it cannot drift with pool size or query length — the one candidate
design in the study with zero cross-pool verdict flips.

The floor and margin conditions STAY, unchanged. Removing the margin (57.1%
balanced accuracy, and a lone scoring candidate gets margin=∞ by construction —
the weakest evidence scoring the highest) would LOOSEN the gate, and loosening
needs the real-pool arm. This batch moves the decision in one direction only:
strictly fewer bypasses than before, with no constant retuned.

SCOPE: `is_user_chat=True` only. `narrative_service.select` deliberately does
not advance `session.current_narrative_id` on background triggers, so cron jobs
and message-bus pings have no anchor by design — 388 of the 392 "no anchor"
exemptions are exactly those. Denying them would push a block of correct, broad-
evidence routings (max-term share p50 0.03, prod §R2.1) into the judge's
`no_topic` exit, which is a known-unfixed residual dump (R7 §7.3) and would risk
fragmentation with no arm to catch it. Background keeps the old rule and records
`background_scope` so prod accumulates the data for that decision.

Full method: specs/2026-08-20-bm25-gate-redesign-research.md §2.6 / §R2.4.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from narranexus.platform.narrative._narrative_impl.routing_gate import (
    BypassDecision,
    evaluate_bypass,
    evaluate_gate,
)
from narranexus.platform.narrative.models import ConversationSession
from narranexus.platform.narrative.narrative_service import NarrativeService
from narranexus.platform.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
)

FLOOR = 3.0
MARGIN = 2.0


def _passing_gate():
    """A score shape that clears floor and margin comfortably."""
    return evaluate_gate([12.0, 2.0], raw_floor=FLOOR, margin_ratio=MARGIN)


def _failing_gate():
    """Crowded — the score gate denies this on its own."""
    return evaluate_gate([12.0, 11.0], raw_floor=FLOOR, margin_ratio=MARGIN)


def _bypass(gate, **kw) -> BypassDecision:
    kw.setdefault("top1_narrative_id", "nar_a")
    kw.setdefault("anchor_narrative_id", "nar_a")
    kw.setdefault("is_user_chat", True)
    kw.setdefault("has_participant_narratives", False)
    return evaluate_bypass(gate, **kw)


# ---------------- the structural condition ----------------------------------


def test_staying_in_the_anchored_thread_may_skip_review() -> None:
    d = _bypass(_passing_gate(), top1_narrative_id="nar_a", anchor_narrative_id="nar_a")
    assert d.granted is True
    assert d.reason == "anchor_match"


def test_switching_threads_always_goes_to_the_judge() -> None:
    """3.5% of prod bypasses; 15 of the 31 hand-labelled bad ones live here."""
    d = _bypass(_passing_gate(), top1_narrative_id="nar_b", anchor_narrative_id="nar_a")
    assert d.granted is False
    assert d.reason == "anchor_miss"
    assert "nar_a" in d.detail and "nar_b" in d.detail, (
        "a routing complaint must be diagnosable from the audit row alone"
    )


def test_a_turn_with_no_anchor_cannot_stay_put() -> None:
    """A first turn has nothing to stay in, so there is nothing to confirm."""
    d = _bypass(_passing_gate(), anchor_narrative_id=None)
    assert d.granted is False
    assert d.reason == "no_anchor"


def test_the_score_gate_still_has_veto() -> None:
    """Q is a necessary condition ADDED to floor+margin, not a replacement."""
    d = _bypass(_failing_gate(), top1_narrative_id="nar_a", anchor_narrative_id="nar_a")
    assert d.granted is False
    assert d.reason == "score_gate"
    assert "crowded" in d.detail.lower(), "the gate's own numbers must survive"


def test_participants_still_force_the_judge() -> None:
    """P0-4: a hit on the user's OWN thread must not outrank an invitation."""
    d = _bypass(_passing_gate(), has_participant_narratives=True)
    assert d.granted is False
    assert d.reason == "participant_present"


def test_no_candidates_at_all_is_reported_as_such() -> None:
    """An empty pool is a more useful reason than "the scores were weak"."""
    gate = evaluate_gate([], raw_floor=FLOOR, margin_ratio=MARGIN)
    d = _bypass(gate, top1_narrative_id=None)
    assert d.granted is False
    assert d.reason == "no_candidates"


# ---------------- scope: background triggers have no anchor by design -------


def test_background_triggers_keep_the_old_rule() -> None:
    d = _bypass(_passing_gate(), is_user_chat=False, anchor_narrative_id=None)
    assert d.granted is True
    assert d.reason == "background_scope"


def test_background_triggers_are_not_judged_against_a_human_anchor() -> None:
    """The anchor belongs to the last USER exchange — a job is not a switch."""
    d = _bypass(
        _passing_gate(),
        is_user_chat=False,
        top1_narrative_id="nar_job",
        anchor_narrative_id="nar_human_chat",
    )
    assert d.granted is True
    assert d.reason == "background_scope"


def test_background_triggers_still_obey_the_score_gate() -> None:
    d = _bypass(_failing_gate(), is_user_chat=False, anchor_narrative_id=None)
    assert d.granted is False
    assert d.reason == "score_gate"


# ---------------- end to end, through the live call site --------------------

AGENT = "agent_bypass"
USER = "user_bypass"


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)

    async def _get():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _get)

    async def _stub_judge(**kw):
        return {"matched_type": "none", "matched_id": None, "reason": "stubbed"}

    monkeypatch.setattr(svc._retrieval, "_llm_judge_unified", _stub_judge)

    class _NotContinuous:
        async def detect(self, **kw):
            from narranexus.platform.narrative.models import ContinuityResult

            return ContinuityResult(
                is_continuous=False, confidence=0.9, reason="stub: not continuous"
            )

    monkeypatch.setattr(svc, "_get_continuity_detector", lambda: _NotContinuous())
    return svc


def _session(**kw):
    now = datetime.now(timezone.utc)
    return ConversationSession(
        session_id="sess_bypass", user_id=USER, agent_id=AGENT,
        created_at=now, last_query_time=now, **kw
    )


async def _seed_the_bare_hi_magnet(service):
    """Reproduce prod audit 3277: a short thread literally named `hi`.

    A greeting created a thread, the updater named it after the greeting, and
    every later `hi` then matched that thread as the single scoring candidate —
    margin ∞ by construction, top1 4.05, judge never consulted. The long
    filler threads are what push `hi`'s in-pool df to 1 and its dl below avgdl,
    which is the whole reason the score clears the floor.
    """
    magnet = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="hi", description="",
    )
    others = []
    for i in range(9):
        others.append(
            await service.create_narrative(
                agent_id=AGENT, user_id=USER,
                title=f"部署脚本报错排查与回归验证 第{i}轮",
                description=(
                    "排查部署脚本在灰度环境下的报错，覆盖依赖安装、环境变量注入、"
                    "数据库迁移与回归验证的完整链路，并记录每一步的结论与后续动作。"
                ),
            )
        )
    return magnet, others


@pytest.mark.asyncio
async def test_bare_hi_no_longer_hijacks_a_thread_it_is_not_anchored_to(
    service, db_client
):
    """The wild specimen. `hi` still clears floor+margin — and is still reviewed."""
    magnet, others = await _seed_the_bare_hi_magnet(service)

    await service.select(
        AGENT, USER, "hi",
        session=_session(
            last_query="部署脚本报错怎么查", current_narrative_id=others[0].id
        ),
        trigger="chat", is_user_chat=True,
    )

    row = (await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT))[0]
    # The specimen must actually reproduce, or this test passes for free.
    assert row["bypass_score_gate"], (
        f"fixture did not reproduce the specimen: top1={row['gate_top1_raw']} "
        f"margin={row['gate_margin']} — floor+margin were supposed to PASS"
    )
    assert row["gate_top1_raw"] >= FLOOR
    assert not row["gate_short_circuit"], "bare `hi` skipped the judge again"
    assert row["bypass_reason"] == "anchor_miss"
    assert row["selection_method"] != "high_confidence"


@pytest.mark.asyncio
async def test_staying_in_the_anchored_thread_still_skips_the_judge(service, db_client):
    """Guard against over-denial: Q must not collapse into 'never bypass'."""
    magnet, _others = await _seed_the_bare_hi_magnet(service)

    await service.select(
        AGENT, USER, "hi",
        session=_session(last_query="hi", current_narrative_id=magnet.id),
        trigger="chat", is_user_chat=True,
    )

    row = (await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT))[0]
    assert row["bypass_score_gate"]
    assert row["gate_short_circuit"], "a turn staying in its own thread was re-judged"
    assert row["bypass_reason"] == "anchor_match"
    assert row["selection_method"] == "high_confidence"
    assert row["chosen_narrative_id"] == magnet.id


@pytest.mark.asyncio
async def test_a_first_turn_is_always_reviewed(service, db_client):
    """3.9% of prod bypasses were first turns — the coldest possible evidence."""
    await _seed_the_bare_hi_magnet(service)

    await service.select(
        AGENT, USER, "hi", session=None, trigger="chat", is_user_chat=True
    )

    row = (await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT))[0]
    assert row["bypass_score_gate"]
    assert not row["gate_short_circuit"]
    assert row["bypass_reason"] == "no_anchor"


@pytest.mark.asyncio
async def test_the_score_gate_series_survives_for_calibration(service, db_client):
    """`bypass_score_gate` must keep recording floor+margin ALONE.

    Layer 2 (recalibrating the floor, or replacing it with a scale-free
    statistic) needs the unconditioned score-gate distribution to keep
    accumulating in prod. If Q's denial also zeroed that column, the series
    this decision depends on would stop the day Q shipped.
    """
    _magnet, others = await _seed_the_bare_hi_magnet(service)

    await service.select(
        AGENT, USER, "hi",
        session=_session(last_query="x", current_narrative_id=others[1].id),
        trigger="chat", is_user_chat=True,
    )

    row = (await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT))[0]
    assert row["bypass_score_gate"] is True or row["bypass_score_gate"] == 1
    assert not row["gate_short_circuit"]
    assert row["gate_top1_raw"] is not None
    # `gate_margin` is NULL here on purpose, and that is the specimen's
    # signature: `hi` is the only pool member that shares a token with the
    # query, so top2 is 0 and the margin is unbounded BY CONSTRUCTION. The old
    # rule read that as the strongest possible evidence. 8.6% of the real
    # agents' gated turns have this shape and a third of them were bypassed.
    assert row["gate_margin"] is None
    assert row["gate_top2_raw"] == 0


# ---------------- the instrument itself -------------------------------------


def test_the_bypass_columns_are_registered_on_both_dialects() -> None:
    """`auto_migrate` picks per backend; a missing dialect is a column that
    only exists on one of prod (MySQL) and the desktop build (SQLite)."""
    from narranexus.platform.utils.db.schema_registry import TABLES

    cols = {c.name: c for c in TABLES["narrative_routing_audit"].columns}
    for name in ("bypass_score_gate", "bypass_reason"):
        col = cols.get(name)
        assert col is not None, f"narrative_routing_audit.{name} not registered"
        assert col.sqlite_type, f"{name}: sqlite_type missing"
        assert col.mysql_type, f"{name}: mysql_type missing"


def test_the_bypass_columns_are_additive_and_nullable() -> None:
    """Binding rule #6: 26,922 existing prod rows predate both columns, so
    NOT NULL here would make `ALTER TABLE ADD COLUMN` fail on a live table."""
    from narranexus.platform.utils.db.schema_registry import TABLES

    cols = {c.name: c for c in TABLES["narrative_routing_audit"].columns}
    assert cols["bypass_score_gate"].nullable
    assert cols["bypass_reason"].nullable


def test_every_reason_code_fits_the_declared_column_width() -> None:
    """VARCHAR(32) truncates silently on MySQL under a non-strict sql_mode,
    which would corrupt the one column the next calibration round groups by."""
    codes = (
        "anchor_match", "anchor_miss", "no_anchor", "score_gate",
        "participant_present", "background_scope", "no_candidates",
    )
    assert max(len(c) for c in codes) <= 32

    seen = set()
    for gate, kw in (
        (_passing_gate(), {}),
        (_passing_gate(), {"top1_narrative_id": "nar_b"}),
        (_passing_gate(), {"anchor_narrative_id": None}),
        (_failing_gate(), {}),
        (_passing_gate(), {"has_participant_narratives": True}),
        (_passing_gate(), {"is_user_chat": False}),
        (evaluate_gate([], raw_floor=FLOOR, margin_ratio=MARGIN),
         {"top1_narrative_id": None}),
    ):
        seen.add(_bypass(gate, **kw).reason)
    assert seen == set(codes), (
        f"reason codes drifted from the documented set: {seen ^ set(codes)}"
    )
