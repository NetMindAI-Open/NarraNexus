"""
@file_name: anchor_rules.py
@author: NetMind.AI
@date: 2026-08-27
@description: The one definition of "may a turn simply stay on this anchor",
              plus the session-elapsed helper both deciders feed the prompt.

Moved out of narrative_service.py so the merged-path orchestration
(merged_select.py, private impl) can consume them without importing the
service layer upward. narrative_service re-exports `is_reusable_anchor`
unchanged — its public consumers (step_1_fast_select) keep their import.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import ConversationSession


def is_reusable_anchor(narrative) -> bool:
    """Is this anchored narrative a thread a turn may simply stay on?

    THE one definition, consumed by all anchor-reuse decision points —
    the continuity guard in ``select()``, the no-topic landing in
    ``_land_no_topic_turn``, ``step_1_fast_select``'s session reuse, and the
    merged path's anchor slot. The independent review (2026-08-21,
    Important #3) caught the fast path missing the check the slow path had:
    sessions still anchored to a legacy default bucket (26.4% of prod user
    turns at C-1 ship time) were re-pinned to the bucket every fast turn while
    the slow path pushed them out — two paths fighting over the same
    invariant, because it lived as two literals.

    A default bucket stops being a reusable thread when C-1 governance is on;
    with the rollback flag flipped, buckets are containers again and reuse is
    the old, intended behaviour.
    """
    from ..config import config

    if narrative is None:
        return False
    return not (
        narrative.is_special == "default"
        and not config.NARRATIVE_DEFAULT_BUCKETS_ENABLED
    )


def minutes_since(session: Optional["ConversationSession"]) -> Optional[float]:
    """Minutes since the previous turn, or None when there was none.

    Naive timestamps are read as UTC — the same guard the continuity tier
    applies, for the same reason: a naive `last_query_time` from an older row
    would otherwise make the subtraction raise, and the merged call would fail
    into its fallback for a formatting reason.
    """
    if session is None or session.last_query_time is None:
        return None
    last = session.last_query_time
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 60.0


def advance_session_anchor(
    session: Optional["ConversationSession"],
    query_text: str,
    narratives,
    is_user_chat: bool,
) -> None:
    """Move the continuity anchor to the thread this turn landed on.

    Only user-initiated runs (chat) write `last_query` /
    `current_narrative_id` — background trigger runs (job / message_bus /
    lark / callback) must leave them untouched so the NEXT user message gets
    its continuity judged against the previous user exchange rather than
    against whatever cron job or bus ping ran in between.

    ONE definition because both routing paths must obey it identically. The
    anchor rule living as two literals is exactly how the fast path and the
    slow path ended up fighting over the same invariant (independent review,
    2026-08-21, Important #3), and a second decider is a second chance at
    that. Moved here from the service (round 5, I3): it is an anchor rule,
    and its siblings already live in this module.
    """
    if not (session and narratives and is_user_chat):
        return
    session.last_query = query_text
    session.current_narrative_id = narratives[0].id
    session.query_count += 1
    session.last_query_time = datetime.now(timezone.utc)
