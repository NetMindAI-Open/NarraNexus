"""
@file_name: test_step_1_fast_select.py
@date: 2026-08-06
@description: step_1_fast_select — the fast-mode replacement for step_1.

Locks (2026-08-14 anchor-first ordering + new-thread gate):
- A live session anchor (durable chat) is reused by default — regardless
  of age (2026-05-20: session timeout removed; time never judges) — and
  BM25 may steal the turn only via the strong-floor probe
  (against_live_anchor=True), labeled "bm25_fast_override" when it lands
  on a different thread.
- With a live anchor, create_fast never fires (measured decision —
  see test_live_anchor_never_creates); new threads arrive anchorless,
  via a strong override, or from the next full-path turn.
- Full miss: durable creates (CRUD only), ephemeral runs bare with zero
  session reads/writes.
- The user's ChatModule instance is ensured on every pick (history and
  persistence hang off it).
- Every decision writes a best-effort RoutingAudit row (audit_fast) that
  carries top1_raw — the calibration data for the override floor.
- Retrieval text honors the trigger's clean anchor over raw input.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import importlib

# The steps package re-exports the function under the same name, which
# shadows the submodule on attribute access — resolve the module itself.
mod = importlib.import_module(
    "narranexus.platform.agent_runtime._agent_runtime_steps.step_1_fast_select"
)
from narranexus.platform.agent_runtime._agent_runtime_steps.context import RunContext


def _ctx(**overrides):
    base = dict(
        agent_id="agent_a",
        user_id="user_u",
        input_content="raw execution prompt",
        working_source="chat",
    )
    base.update(overrides)
    return RunContext(**base)


def _narrative(nid: str, name: str = "N", is_special: str = "other"):
    return SimpleNamespace(
        id=nid,
        is_special=is_special,
        narrative_info=SimpleNamespace(name=name, current_summary="s"),
    )


def _probe(narrative=None, top1=None):
    """Shape of narrative_service.select_fast's FastSelectResult."""
    return SimpleNamespace(narrative=narrative, top1_raw=top1)


def _service(**overrides):
    base = dict(
        select_fast=AsyncMock(return_value=_probe()),
        create_fast=AsyncMock(),
        load_narrative_from_db=AsyncMock(return_value=None),
        audit_fast=AsyncMock(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _session(**overrides):
    base = dict(
        session_id="sess_1",
        last_query="old query",
        current_narrative_id="nar_old",
        query_count=7,
        last_query_time=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _durable_profile():
    from narranexus.platform.schema.turn_profile import TurnProfile

    return TurnProfile.fast_for("chat")


def _ephemeral_profile():
    from narranexus.platform.schema.turn_profile import TurnProfile

    return TurnProfile.voice_fast()


async def _drain(gen):
    return [msg async for msg in gen]


# ── anchorless picks (no profile / ephemeral) ──────────────────────────


@pytest.mark.asyncio
async def test_hit_fills_ctx_and_ensures_chat_instance(monkeypatch):
    narrative = _narrative("nar_1")
    service = _service(
        select_fast=AsyncMock(
            return_value=_probe(narrative=narrative, top1=8.0)
        )
    )
    ensure = AsyncMock(return_value="chat_i1")
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", ensure)

    ctx = _ctx(trigger_extra_data={"retrieval_anchor": "[From Bob] weather?"})
    messages = await _drain(mod.step_1_fast_select(ctx, service))

    assert ctx.narrative_list == [narrative]
    assert ctx.user_chat_instances == {"nar_1": "chat_i1"}
    service.select_fast.assert_awaited_once_with(
        "agent_a", "user_u", "[From Bob] weather?", against_live_anchor=False
    )
    ensure.assert_awaited_once_with("agent_a", "user_u", "nar_1")
    assert messages[-1].status == "completed"
    assert messages[-1].details["retrieval_method"] == "bm25_fast"


@pytest.mark.asyncio
async def test_miss_runs_bare_without_profile(monkeypatch):
    service = _service()
    ensure = AsyncMock()
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", ensure)

    ctx = _ctx()
    await _drain(mod.step_1_fast_select(ctx, service))

    assert ctx.narrative_list == []
    assert ctx.user_chat_instances == {}
    ensure.assert_not_awaited()
    service.create_fast.assert_not_awaited()


def test_session_service_is_optional_and_defaults_none():
    # The ephemeral (voice) contract is behavioral, not structural:
    # session_service exists for the durable chat path but defaults to
    # None, and ephemeral profiles never touch it (tests below).
    params = inspect.signature(mod.step_1_fast_select).parameters
    assert params["session_service"].default is None


# ── durable chat: anchor-first ordering ─────────────────────────────────


@pytest.mark.asyncio
async def test_live_anchor_reused_by_default_regardless_of_age(monkeypatch):
    # 2026-05-20 rule: the anchor persists indefinitely — last_query_time
    # is None here and the anchor must still win over an untrusted miss
    # (short query: silence is not evidence of a new topic).
    reused = _narrative("nar_old", "Old")
    service = _service(load_narrative_from_db=AsyncMock(return_value=reused))
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    messages = await _drain(mod.step_1_fast_select(ctx, service, session_service))

    service.select_fast.assert_awaited_once_with(
        "agent_a", "user_u", "raw execution prompt", against_live_anchor=True
    )
    service.load_narrative_from_db.assert_awaited_once_with("nar_old")
    service.create_fast.assert_not_awaited()
    assert ctx.narrative_list == [reused]
    assert ctx.session.current_narrative_id == "nar_old"
    session_service.save_session.assert_awaited_once_with(ctx.session)
    assert messages[-1].details["retrieval_method"] == "session_fast"


@pytest.mark.asyncio
async def test_strong_bm25_hit_on_other_thread_is_labeled_override(monkeypatch):
    # select_fast only returns a narrative above the strong floor when
    # probing against a live anchor — landing on a DIFFERENT thread is
    # the steal decision and must be auditable as such.
    strong = _narrative("nar_visa", "Visa")
    service = _service(
        select_fast=AsyncMock(
            return_value=_probe(narrative=strong, top1=15.0)
        )
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    messages = await _drain(mod.step_1_fast_select(ctx, service, session_service))

    service.load_narrative_from_db.assert_not_awaited()
    assert ctx.narrative_list == [strong]
    assert ctx.session.current_narrative_id == "nar_visa"
    assert messages[-1].details["retrieval_method"] == "bm25_fast_override"


@pytest.mark.asyncio
async def test_strong_hit_on_anchor_itself_is_plain_bm25_fast(monkeypatch):
    anchor = _narrative("nar_old", "Old")
    service = _service(
        select_fast=AsyncMock(
            return_value=_probe(narrative=anchor, top1=15.0)
        )
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    messages = await _drain(mod.step_1_fast_select(ctx, service, session_service))

    assert ctx.narrative_list == [anchor]
    assert messages[-1].details["retrieval_method"] == "bm25_fast"


@pytest.mark.asyncio
async def test_live_anchor_never_creates(monkeypatch):
    # Measured decision (PR #307 verify round, real zh BM25 data): with a
    # live anchor, create_fast must never fire — BM25 cannot separate
    # "new topic" from "elliptical continuation" in CJK, and a misfiled
    # turn is recoverable while a fragmented thread (empty ChatModule
    # history mid-conversation) is not. Numbers in narrative/config.py.
    reused = _narrative("nar_old", "Old")
    service = _service(load_narrative_from_db=AsyncMock(return_value=reused))
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    service.create_fast.assert_not_awaited()
    assert ctx.narrative_list == [reused]


@pytest.mark.asyncio
async def test_no_anchor_miss_creates_and_anchors(monkeypatch):
    created = _narrative("nar_new", "New")
    service = _service(create_fast=AsyncMock(return_value=created))
    session_service = SimpleNamespace(save_session=AsyncMock())
    ensure = AsyncMock(return_value="chat_i9")
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", ensure)

    ctx = _ctx(
        session=_session(current_narrative_id=None),
        turn_profile=_durable_profile(),
    )
    messages = await _drain(mod.step_1_fast_select(ctx, service, session_service))

    service.select_fast.assert_awaited_once_with(
        "agent_a", "user_u", "raw execution prompt", against_live_anchor=False
    )
    service.create_fast.assert_awaited_once_with(
        "agent_a", "user_u", "raw execution prompt"
    )
    assert ctx.narrative_list == [created]
    assert ctx.user_chat_instances == {"nar_new": "chat_i9"}
    assert ctx.session.current_narrative_id == "nar_new"
    assert ctx.session.last_query == "raw execution prompt"
    assert ctx.session.query_count == 8
    assert ctx.session.last_query_time is not None
    session_service.save_session.assert_awaited_once_with(ctx.session)
    assert messages[-1].details["retrieval_method"] == "bm25_fast_created"


@pytest.mark.asyncio
async def test_vanished_anchor_row_retries_anchorless_then_creates(monkeypatch):
    created = _narrative("nar_new", "New")
    service = _service(create_fast=AsyncMock(return_value=created))
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    # Strong-floor probe, vanished-row load, then a noise-floor retry.
    assert service.select_fast.await_count == 2
    assert (
        service.select_fast.await_args_list[1].kwargs.get("against_live_anchor", False)
        is False
    )
    service.create_fast.assert_awaited_once()
    assert ctx.narrative_list == [created]


# ── ephemeral (voice) contract ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ephemeral_miss_stays_bare_and_never_touches_session(monkeypatch):
    service = _service()
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock())

    ctx = _ctx(session=_session(), turn_profile=_ephemeral_profile())
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    assert ctx.narrative_list == []
    # Ephemeral never reads the anchor: BM25 runs at the noise floor.
    service.select_fast.assert_awaited_once_with(
        "agent_a", "user_u", "raw execution prompt", against_live_anchor=False
    )
    service.load_narrative_from_db.assert_not_awaited()
    service.create_fast.assert_not_awaited()
    session_service.save_session.assert_not_awaited()
    assert ctx.session.current_narrative_id == "nar_old"


@pytest.mark.asyncio
async def test_durable_non_user_chat_never_touches_session(monkeypatch):
    # Background-ish sources must not overwrite the chat continuity anchor
    # even if a durable profile ever reaches them (fast_for whitelists
    # human surfaces, so this is the divergence guard).
    service = _service(
        select_fast=AsyncMock(
            return_value=_probe(narrative=_narrative("nar_hit"), top1=9.0)
        )
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(
        session=_session(), turn_profile=_durable_profile(), working_source="job"
    )
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    session_service.save_session.assert_not_awaited()
    assert ctx.session.current_narrative_id == "nar_old"


# ── audit contract ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_decision_writes_an_audit_row_with_score(monkeypatch):
    reused = _narrative("nar_old", "Old")
    service = _service(
        select_fast=AsyncMock(return_value=_probe(top1=1.2)),
        load_narrative_from_db=AsyncMock(return_value=reused),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    service.audit_fast.assert_awaited_once()
    kwargs = service.audit_fast.await_args.kwargs
    assert kwargs["retrieval_method"] == "session_fast"
    assert kwargs["chosen_narrative_id"] == "nar_old"
    assert kwargs["is_user_chat"] is True
    assert kwargs["is_new"] is False
    assert kwargs["top1_raw"] == 1.2
    assert isinstance(kwargs["keyword_ms"], int)


@pytest.mark.asyncio
async def test_bare_miss_also_writes_an_audit_row(monkeypatch):
    service = _service()
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock())

    ctx = _ctx()
    await _drain(mod.step_1_fast_select(ctx, service))

    service.audit_fast.assert_awaited_once()
    kwargs = service.audit_fast.await_args.kwargs
    assert kwargs["chosen_narrative_id"] is None
    assert kwargs["retrieval_method"] == "bm25_fast"
    assert kwargs["top1_raw"] is None


@pytest.mark.asyncio
async def test_bucket_anchor_is_not_reused_on_the_fast_path(monkeypatch):
    """Independent review 2026-08-21, Important #3: the slow path refuses to
    continue a default-bucket anchor (slice 5), but the fast path reused it
    unconditionally — re-pinning the session to the bucket every turn, with no
    judge and no self-healing exit. 26.4% of prod user turns had a bucket as
    their main narrative when C-1 shipped, so these sessions are real. A
    bucket anchor must be treated like a vanished anchor row: anchorless
    re-probe, then create for durable chat."""
    bucket = _narrative("nar_bucket", "GreetingAndCourtesy", is_special="default")
    created = _narrative("nar_new", "New")
    service = _service(
        load_narrative_from_db=AsyncMock(return_value=bucket),
        create_fast=AsyncMock(return_value=created),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(current_narrative_id="nar_bucket"),
               turn_profile=_durable_profile())
    messages = await _drain(mod.step_1_fast_select(ctx, service, session_service))

    # The bucket is never handed back as this turn's narrative...
    assert ctx.narrative_list == [created]
    # ...the anchorless retry ran (two select_fast calls: probe + retry)...
    assert service.select_fast.await_count == 2
    # ...and the session anchor was rewritten off the bucket.
    assert ctx.session.current_narrative_id == "nar_new"
    assert messages[-1].details["retrieval_method"] == "bm25_fast_created"
