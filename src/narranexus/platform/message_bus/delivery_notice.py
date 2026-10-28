"""
@file_name: delivery_notice.py
@author: NarraNexus
@date: 2026-08-13
@description: The room line the platform writes when a turn delivered nothing.

A bus turn ends in one of three ways, and until now only the first was visible:

1. it produced a reply, which was posted — the room shows it;
2. it produced a reply the platform then FAILED to post — backend green,
   billing charged, room empty;
3. it produced nothing to deliver at all — same empty room.

Cases 2 and 3 are the ones a user cannot tell apart from "the agent ignored
me", and neither can they be told apart from each other by looking, which is
why they are separate message types rather than one vague "no reply" line. The
distinction is real: (2) is OUR fault and retryable, (3) is the agent's own
turn coming up empty.

The A2A shape of (3) is the expensive one. In a team room a silence is merely
confusing; in an agent↔agent DM the peer that asked is BLOCKED on the answer,
so its notice carries a mention and wakes it — a hanging errand resolves itself
instead of waiting forever (PRD 2026-08-04 §四, "A2A 空回复").

Deliberately NOT folding the NexusPower monologue into the reply as a fallback
for (3). The monologue contract promises the agent its plain text is never
DELIVERED — visible to its owner as working narration, but addressed to no
one. Relaying it to a peer would deliver deliberation the agent never
addressed to anyone (see run_collector.collect_run's ``include_monologue``).
Saying "nothing was delivered" is honest and leaks nothing.

Every function here is BEST-EFFORT and returns a verdict instead of raising:
the delivery-failure notice travels the very path that just failed, so it
failing too is the expected case. The caller falls back to the owner's inbox.
"""

from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

from narranexus.platform.agent_framework.llm.failure import redact_secrets

# A turn that reached nobody: no reply text, and no delivery tool called.
UNDELIVERED_MSG_TYPE = "system_undelivered"

# The reply existed; posting it into the room failed.
DELIVERY_FAILED_MSG_TYPE = "system_delivery_failed"

# Cap on the error echoed into a transcript every member can read. Tighter than
# the owner-inbox notice's MAX_REDACTED_ERROR_LEN (500) because this text lands
# in a strictly wider audience — the two numbers are independent, not a shared
# budget.
MAX_NOTICE_ERROR_LEN = 300

# English fallbacks. The frontend renders these lines from an i18n key (the
# database cannot know the reader's language); `content` is what text-only
# consumers — a log, an export, a future IM mirror — get to read.
_UNDELIVERED_TEXT = "This turn ended without delivering a reply."
_DELIVERY_FAILED_TEXT = "The reply could not be posted to this conversation."
_PROCESSING_FAILED_TEXT = (
    "This message could not be processed by the recipient after {attempts} "
    "attempts and will not be retried."
)


async def announce_undelivered(
    bus: Any,
    channel_id: str,
    agent_id: str,
    *,
    mentions: Optional[List[str]] = None,
    root_run_id: Optional[str] = None,
) -> bool:
    """Say that ``agent_id``'s turn delivered nothing. True if the line landed.

    Called for A2A DMs only since 2026-09-03 — a team-room silence is marked
    on the member's activity row instead (`activity.note_silent_turn`), never
    posted. ``mentions`` names the peer that asked when that peer IS blocked
    and only a message wakes it; None when the silence answered our own
    errand and nobody is waiting.
    """
    return await _post(
        bus, channel_id, agent_id,
        content=_UNDELIVERED_TEXT,
        msg_type=UNDELIVERED_MSG_TYPE,
        mentions=mentions,
        root_run_id=root_run_id,
    )


async def announce_delivery_failure(
    bus: Any,
    channel_id: str,
    agent_id: str,
    *,
    error: str,
    root_run_id: Optional[str] = None,
) -> bool:
    """Say that the reply existed but could not be posted. True if it landed.

    Never mentions anyone: the reply is lost either way, and waking the room
    over our own write failure adds turns without adding information.

    The reason is carried because "it failed" without "why" is the kind of
    transparency that still leaves the user guessing — redacted first, since
    provider SDKs echo the offending credential back inside the error body and
    this string becomes a permanent transcript row.
    """
    return await _post(
        bus, channel_id, agent_id,
        content=f"{_DELIVERY_FAILED_TEXT} ({redact_secrets(error, MAX_NOTICE_ERROR_LEN)})",
        msg_type=DELIVERY_FAILED_MSG_TYPE,
        mentions=None,
        root_run_id=root_run_id,
    )


async def announce_processing_failure(
    bus: Any,
    channel_id: str,
    agent_id: str,
    *,
    error: str,
    attempts: int,
    mentions: Optional[List[str]],
    root_run_id: Optional[str] = None,
) -> bool:
    """Say that ``agent_id`` could not RUN the message at all — the poison
    threshold was reached — and wake whoever sent it. True if the line landed.

    The fourth way a bus turn can end, missing from the header's list until
    2026-09-09: the recipient's runtime raised before the turn produced
    anything (a worker crashing on import, upstream NetMindAI-Open/NarraNexus#106),
    three times, and the message was then dropped from the queue forever. The
    recipient OWNER's inbox learned of it; the SENDER — an agent that had just
    told its user the work was under way — did not, and kept waiting.

    Written into the conversation the sender is watching, with the sender
    mentioned, so its next turn opens on the failure (``system_messages.
    trigger_label`` names it) instead of on silence. Carries the reason for
    the same transparency argument as ``announce_delivery_failure``, redacted
    for the same reason. Reuses ``DELIVERY_FAILED_MSG_TYPE``: to every reader
    it IS a delivery failure, the platform's rather than the model's, and the
    frontend already renders that type with warning weight.
    """
    return await _post(
        bus, channel_id, agent_id,
        content=(
            f"{_PROCESSING_FAILED_TEXT.format(attempts=attempts)} "
            f"({redact_secrets(error, MAX_NOTICE_ERROR_LEN)})"
        ),
        msg_type=DELIVERY_FAILED_MSG_TYPE,
        mentions=mentions,
        root_run_id=root_run_id,
    )


async def _post(
    bus: Any,
    channel_id: str,
    agent_id: str,
    *,
    content: str,
    msg_type: str,
    mentions: Optional[List[str]],
    root_run_id: Optional[str],
) -> bool:
    try:
        await bus.send_message(
            from_agent=agent_id,
            to_channel=channel_id,
            content=content,
            msg_type=msg_type,
            mentions=mentions,
            # Keeps this notice inside the tree that produced it, so a cascade
            # stop reaches whatever the notice goes on to wake. Every other bus
            # send carries it for the same reason.
            root_run_id=root_run_id,
        )
        return True
    except Exception as e:  # noqa: BLE001 — a notice may never become the new failure
        logger.warning(
            f"[delivery-notice] could not post {msg_type} for {agent_id} "
            f"in {channel_id}: {e}"
        )
        return False


__all__ = [
    "DELIVERY_FAILED_MSG_TYPE",
    "UNDELIVERED_MSG_TYPE",
    "announce_delivery_failure",
    "announce_processing_failure",
    "announce_undelivered",
]
