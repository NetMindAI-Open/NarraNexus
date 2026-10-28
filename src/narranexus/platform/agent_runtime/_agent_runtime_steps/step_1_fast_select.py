"""
@file_name: step_1_fast_select.py
@date: 2026-08-06
@description: Fast-mode replacement for step_1 — anchor-first narrative pick, no LLM.

Fast mode must reach the agent loop without paying step_1's synchronous
LLM round trip (ContinuityDetector + the retrieval LLM tier). This step
keeps the narrative "cheaply present" instead of bypassing it. No LLM
ever runs here.

Selection order mirrors the full path's continuity-first shape:

* A live session anchor (``session.current_narrative_id``, durable chat
  surfaces only) is reused by default — regardless of age, per the
  2026-05-20 session-timeout removal (narrative/config.py) — and a BM25
  top-1 may steal the turn away from it only above the strong
  ``FAST_ANCHOR_OVERRIDE_FLOOR``. Raw BM25 scales with query length, so
  short follow-ups stay in their thread while a long, topic-rich message
  can still switch.
* With a live anchor the fast path never CREATES a narrative — a
  measured decision, not an oversight: BM25 cannot separate "new topic"
  from "elliptical continuation" in CJK (numbers on
  ``FAST_ANCHOR_OVERRIDE_FLOOR`` in narrative/config.py), and a
  misfiled turn is recoverable (next full-path turn re-routes) while a
  fragmented thread is not. New threads arrive anchorless, via a strong
  override onto an existing thread, or from the next full-path turn.
* Without a live anchor, BM25 top-1 above the noise floor picks the
  background directly.

What happens on a full miss is the surface's call, carried by
``TurnProfile.narrative_persistence``:

* ``"ephemeral"`` (voice, F28): the turn runs bare — no creation, no
  session writes — so a normal follow-up message continuity-checks
  exactly as if the voice turn never happened.
* ``"durable"`` (persisted human chat surfaces): the miss falls through
  to a CRUD-only narrative creation, and the session continuity anchor
  is kept consistent on hit, reuse and create alike. A fast chat turn
  must never vanish from history — both history endpoints are
  narrative-scoped, and step_4 persists nothing for a bare turn.

Every decision leaves a RoutingAudit row (best-effort) with
``selection_method="fast"`` — same evidence contract as the full
select(), so routing/latency questions are answerable from the DB.

What it still MUST do on every pick: ensure the user's ChatModule
instance on the narrative (chat history assembly and turn persistence
both hang off that instance — skipping it would silently break memory).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, AsyncGenerator, Optional

from loguru import logger

from narranexus.platform.narrative.narrative_service import (
    is_reusable_anchor,
    resolve_retrieval_text,
)
from narranexus.platform.schema import ProgressMessage, ProgressStatus

from .step_1_select_narrative import (
    _ensure_user_chat_instance,
    _is_user_chat,
    _trigger_label,
)

if TYPE_CHECKING:
    from narranexus.platform.narrative import NarrativeService, SessionService

    from .context import RunContext


def _is_durable(ctx: "RunContext") -> bool:
    profile = ctx.turn_profile
    # Direct attribute access on purpose: ctx.turn_profile is always an
    # in-process TurnProfile. A missing field should raise loudly, not
    # silently degrade to the run-bare (history-losing) branch.
    return profile is not None and profile.narrative_persistence == "durable"


async def _anchor_session(
    ctx: "RunContext",
    session_service: Optional["SessionService"],
    narrative_id: str,
    query: str,
) -> None:
    """Keep the chat continuity anchor consistent for durable fast turns.

    Mirrors the four writes the full select() performs for user-chat
    runs. Without this, the next non-fast turn would judge continuity
    against a half-stale anchor (last_response moved by step_4 while
    current_narrative_id still points at the previous thread). The
    ``_is_user_chat`` guard is load-bearing: background traffic must
    never clobber the anchor a real user message expects.
    """
    if session_service is None or ctx.session is None or not _is_user_chat(ctx):
        return
    from datetime import datetime, timezone

    ctx.session.last_query = query
    ctx.session.current_narrative_id = narrative_id
    ctx.session.query_count += 1
    ctx.session.last_query_time = datetime.now(timezone.utc)
    await session_service.save_session(ctx.session)
    logger.debug(f"[step_1_fast] session anchored to narrative={narrative_id}")


async def step_1_fast_select(
    ctx: "RunContext",
    narrative_service: "NarrativeService",
    session_service: Optional["SessionService"] = None,
) -> AsyncGenerator[ProgressMessage, None]:
    """Pick the background narrative for a fast turn (anchor-first, no LLM).

    Fills ``ctx.narrative_list`` and ``ctx.user_chat_instances``. On a
    full miss, ephemeral profiles run the turn bare; durable profiles
    create the narrative (CRUD only) so the turn persists.
    """
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection (fast)",
        description="Anchor-first direct pick...",
        status=ProgressStatus.RUNNING,
        substeps=[],
    )

    if ctx.cancellation:
        ctx.cancellation.raise_if_cancelled()

    # Same retrieval-text rule as the full path — shared helper, so the
    # value written into session.last_query below can never diverge in
    # shape from what full turns write.
    query = resolve_retrieval_text(
        (ctx.trigger_extra_data or {}).get("retrieval_anchor"), ctx.input_content
    )

    is_chat = _is_user_chat(ctx)
    durable_chat = _is_durable(ctx) and is_chat

    anchor_id: Optional[str] = (
        getattr(ctx.session, "current_narrative_id", None)
        if durable_chat and ctx.session is not None
        else None
    )

    # keyword_ms must mean the same thing full-path rows mean ("BM25 pool
    # load + rank", models.py) — accumulate probe durations only, never
    # the CRUD load/create that may follow.
    _t0 = time.monotonic()
    probe = await narrative_service.select_fast(
        ctx.agent_id, ctx.user_id, query, against_live_anchor=bool(anchor_id)
    )
    keyword_ms = int((time.monotonic() - _t0) * 1000)
    narrative = probe.narrative
    top1_raw = probe.top1_raw

    retrieval_method = "bm25_fast"
    is_new = False
    if narrative is not None and anchor_id and narrative.id != anchor_id:
        # Cleared the strong override floor on a DIFFERENT thread — the
        # one decision that steals the turn away from the live anchor.
        # Distinct audit label so the floor can be calibrated from data.
        retrieval_method = "bm25_fast_override"
    elif narrative is None and anchor_id:
        # No steal-grade hit: reuse the live thread. Deliberately NO
        # create-on-silence here — measured on real BM25 distributions,
        # CJK cannot distinguish "new topic" from "elliptical
        # continuation" (rationale + numbers on FAST_ANCHOR_OVERRIDE_FLOOR
        # in narrative/config.py), and a misfiled turn is recoverable
        # while a fragmented thread is not.
        narrative = await narrative_service.load_narrative_from_db(anchor_id)
        if narrative is not None and not is_reusable_anchor(narrative):
            # A legacy default-bucket anchor is not a thread to reuse — the
            # slow path already refuses to continue one (slice 5), and
            # re-pinning it here every fast turn would undo that with no
            # judge and no self-healing exit (independent review 2026-08-21).
            # Treat it exactly like a vanished anchor row.
            logger.info(
                f"[step_1_fast] anchor {anchor_id} is a default bucket — "
                "retrying anchorless instead of reusing it"
            )
            narrative = None
        if narrative is not None:
            retrieval_method = "session_fast"
        else:
            # Anchor row vanished — retry anchorless at the noise floor.
            _t1 = time.monotonic()
            probe = await narrative_service.select_fast(
                ctx.agent_id, ctx.user_id, query
            )
            keyword_ms += int((time.monotonic() - _t1) * 1000)
            narrative = probe.narrative
            top1_raw = probe.top1_raw
    if narrative is None and durable_chat:
        narrative = await narrative_service.create_fast(
            ctx.agent_id, ctx.user_id, query
        )
        retrieval_method = "bm25_fast_created"
        is_new = True
        logger.info(f"[step_1_fast] miss — created narrative={narrative.id}")

    if narrative is None:
        ctx.narrative_list = []
        if _is_durable(ctx) and not is_chat:
            # fast_for only marks human chat surfaces durable, so this
            # fires only if that classification and _is_user_chat ever
            # diverge — make the non-persisted miss visible instead of
            # silently running bare. (A HIT on such a turn still
            # persists through the chat instance as usual.)
            logger.warning(
                "[step_1_fast] durable profile on non-user-chat source "
                f"{ctx.working_source!r} — a miss on this turn will NOT persist"
            )
        logger.info("[step_1_fast] no candidate — running bare")
        await narrative_service.audit_fast(
            ctx.agent_id,
            ctx.user_id,
            query,
            retrieval_method="bm25_fast",
            chosen_narrative_id=None,
            trigger=_trigger_label(ctx),
            is_user_chat=is_chat,
            keyword_ms=keyword_ms,
            top1_raw=top1_raw,
        )
        yield ProgressMessage(
            step="1",
            title="📚 Narrative Selection (fast)",
            description="No matching narrative — running without background",
            status=ProgressStatus.COMPLETED,
            details={"retrieval_method": "bm25_fast", "hit": False},
        )
        return

    ctx.narrative_list = [narrative]
    ctx.substeps_1.append(
        f"[1.1] ✓ {narrative.narrative_info.name} ({retrieval_method})"
    )

    # History and persistence hang off the user's ChatModule instance in
    # the selected narrative — the fast path must keep that invariant.
    try:
        chat_instance_id = await _ensure_user_chat_instance(
            ctx.agent_id, ctx.user_id, narrative.id
        )
        ctx.user_chat_instances = {narrative.id: chat_instance_id}
    except Exception as e:  # noqa: BLE001 — degraded turn beats a dead turn
        logger.warning(f"[step_1_fast] chat-instance ensure failed: {e}")

    if durable_chat:
        await _anchor_session(ctx, session_service, narrative.id, query)

    await narrative_service.audit_fast(
        ctx.agent_id,
        ctx.user_id,
        query,
        retrieval_method=retrieval_method,
        chosen_narrative_id=narrative.id,
        trigger=_trigger_label(ctx),
        is_user_chat=is_chat,
        keyword_ms=keyword_ms,
        is_new=is_new,
        top1_raw=top1_raw,
    )

    logger.info(f"[step_1_fast] narrative={narrative.id} method={retrieval_method}")
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection (fast)",
        description=f"Background: {narrative.narrative_info.name}",
        status=ProgressStatus.COMPLETED,
        details={"retrieval_method": retrieval_method, "hit": True},
    )
