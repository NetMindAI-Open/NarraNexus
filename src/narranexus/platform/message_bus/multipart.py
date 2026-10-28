"""
@file_name: multipart.py
@author:
@date: 2026-09-09
@description: A long bus message sent in ordered parts, and its reassembly.

Why parts at all. Nothing in the bus caps a message — `bus_messages.content`
is TEXT — but the SENDER's model does: a reply of ~4-5k characters is about
where a tool call's argument JSON runs into the output-token budget, and the
loop's only honest answer is "the tool was NOT executed, send it in smaller
pieces" (NexusPower `unparsed_call_result`). Without a contract for pieces,
"smaller pieces" means N separate bus messages, and the wake signal starts the
recipient's turn on piece 1 while piece 2 is still being generated: the
recipient answers a fragment, the sender's later pieces arrive as replies to
that answer, and a user reported exactly this on 8/31 — long replies "cut
off", the recipient's turn "empty", and "This turn ended without delivering a
reply" firing again and again.

The contract: `message_agent(text, part_index=i, part_count=n)`. Each part is
its own `bus_messages` row (so nothing is ever truncated or re-encoded), tied
to a group by `part_group` (= part 1's message_id, resolved at the write edge
by `LocalMessageBus._resolve_part_group`, which also refuses an out-of-order
part). On the recipient side the trigger calls `assemble` on a lane's batch:

* an INCOMPLETE group younger than `PART_ASSEMBLY_GRACE_SECONDS` is HELD — the
  lane returns without acking, and the last part's `wake_signal.bump` brings
  the poll loop back;
* a complete group collapses into ONE message: content is the parts joined
  with nothing in between (they are substrings, not paragraphs), attachments
  are the union, identity (message_id, event_id, root_run_id, turn source)
  is part 1's, `created_at` is the last part's (the ack cursor must pass every
  row), and `part_message_ids` lists every row so per-row bookkeeping
  (delivery receipts) still reaches each part;
* an incomplete group OLDER than the grace — or SUPERSEDED, i.e. the same
  sender has since opened a newer group in this channel, which the write edge
  guarantees the old one can never be continued past — is delivered as what
  arrived plus an explicit marker naming the missing parts. A sender whose
  turn died between parts must not block the recipient forever (iron rule
  #14: the platform is never the interruption source), and a silent gap would
  be the content loss iron rule #16 forbids.

PRECONDITION — DM lanes only, for now. `send_message(part_index=…)` is on the
protocol, but the only sender that uses it towards agents is `message_agent`
(a DM), plus the job-report fallback posting parts back to back. In a team
room a member's half-arrived group would make everything after it wait with
it (the ack invariant above), i.e. one member's slow model stalls the room
for up to the grace. Do not add the part fields to `message_team` before lane
starvation is solved at the lane level; `test_team_message_segments`'s exempt
set is the guard that currently keeps them off the room tool.

A lane batch is a LIMIT over the whole lane, not over one group, so a group
can be cut off by the batch edge. The caller says so (``batch_truncated``) and
an incomplete group in a truncated batch is then always HELD — never judged
stale or superseded on partial evidence; the rows before it are delivered and
acked, the next batch starts closer to the group, and when nothing before the
group can move the trigger re-reads the lane with a wider limit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from narranexus.platform.message_bus.schemas import BusMessage, canonical_ts
from narranexus.platform.utils.timezone import coerce_utc

#: One bus row's content ceiling, in UTF-8 bytes — under the column's MySQL
#: TEXT capacity (65,535) with room for the dialect's own overhead. Enforced
#: at the write edge (`LocalMessageBus.send_message`) for EVERY sender — the
#: peer DM tool, the team-room tool, the platform's own lines. Over it the
#: send is REFUSED with the part contract named, never truncated (iron rule
#: #16): the column would otherwise reject the row with an opaque 1406 or
#: silently keep a prefix, and both lose the tail.
MAX_BUS_MESSAGE_BYTES = 60_000

#: Upper bound on parts per message, validated at part 1 (a count over it is
#: refused before anything is stored). Kept far below
#: `local_bus.PENDING_BATCH_LIMIT_WIDE` so a whole group ALWAYS fits the
#: lane's widest read — without this, 600 hundred-byte parts would be a legal
#: group that no batch can ever see complete, and the lane would hold it
#: forever (review r2 C2). The byte budget below is the other, independent
#: bound; each refuses with its own reason.
MAX_MESSAGE_PARTS = 40

#: The whole reassembled message's ceiling, in UTF-8 bytes. Enforced at the
#: write edge (`LocalMessageBus._resolve_part_group`): a part that would push
#: its group past this is refused with "split into two separate messages",
#: never trimmed. Sized so the recipient's turn gets a long deliverable, not a
#: context-window overflow dressed up as a message: 200 KB is ~65k CJK or
#: ~200k ASCII characters, well inside any current context after the prompt's
#: own overhead.
MAX_MULTIPART_TOTAL_BYTES = 200_000

#: How long an incomplete group is held for its remaining parts, measured from
#: the NEWEST part that arrived. Generous on purpose: the sender is a model
#: mid-turn and a thinking model can take minutes between tool calls; the
#: only thing this bounds is how long a sender that DIED between parts can
#: keep the recipient waiting.
PART_ASSEMBLY_GRACE_SECONDS = 600

_MISSING_MARKER = (
    "\n\n[platform: this message was sent in {count} parts; part(s) {missing} "
    "never arrived within {grace}s and the rest is delivered as-is]"
)


def split_for_bus(content: str, *, max_bytes: int = MAX_BUS_MESSAGE_BYTES) -> List[str]:
    """Cut ``content`` into pieces of at most ``max_bytes`` UTF-8 bytes each,
    on character boundaries, nothing dropped: ``"".join(result) == content``.

    For PLATFORM writers that post a whole document in one go (a job report)
    and can therefore send the parts back to back. A model splits its own
    text; this is the same contract applied by code.
    """
    pieces: List[str] = []
    buf: List[str] = []
    size = 0
    for ch in content:
        n = len(ch.encode("utf-8"))
        if size + n > max_bytes and buf:
            pieces.append("".join(buf))
            buf, size = [], 0
        buf.append(ch)
        size += n
    if buf:
        pieces.append("".join(buf))
    return pieces


def too_many_parts_reason(count: int) -> str:
    """The agent-readable refusal for a part_count over `MAX_MESSAGE_PARTS`."""
    return (
        f"part_count={count} exceeds the maximum of {MAX_MESSAGE_PARTS} parts "
        f"per message. Nothing was sent. Use larger parts, or split the content "
        f"into two separate messages."
    )


def group_budget_reason(stored: int, this_part: int) -> str:
    """The agent-readable refusal for a part that would overflow the group."""
    return (
        f"this multipart message would reach {stored + this_part} bytes; one "
        f"message holds at most {MAX_MULTIPART_TOTAL_BYTES} in total. This part "
        f"was NOT stored. Finish the message within the budget, or split the "
        f"content into two separate messages."
    )


#: The remedies a caller appends to the oversize FACT. The fact is the bus's;
#: the way out depends on who is asking (review #389 I1): the peer DM tool has
#: part_index/part_count, the team-room tool does not, and a person typing in
#: the team chat has neither.
OVERSIZE_REMEDY_PARTS = (
    "Send it in ordered parts with part_index/part_count — each part under "
    "the limit — and the recipient receives it joined back into one message."
)
OVERSIZE_REMEDY_TEAM = (
    "Split it across several message_team calls, each under the limit."
)
OVERSIZE_REMEDY_HUMAN = "The message is too long; please split it and send it in pieces."


class BusMessageTooLarge(ValueError):
    """One row's content exceeds `MAX_BUS_MESSAGE_BYTES`. Raised by the write
    edge with the FACT only; each sender adds the remedy it actually has."""

    def __init__(self, size: int):
        self.size = size
        super().__init__(oversize_fact(size))


def oversize_fact(size: int) -> str:
    return (
        f"`text` is {size} bytes; one message holds at most "
        f"{MAX_BUS_MESSAGE_BYTES}. Nothing was sent."
    )


def oversize_reason(size: int, remedy: str = OVERSIZE_REMEDY_PARTS) -> str:
    """The refusal a caller shows: the bus's fact plus that caller's remedy."""
    return f"{oversize_fact(size)} {remedy}"


def _age_seconds(created_at, now: datetime) -> float:
    ts = coerce_utc(created_at)
    if ts is None:
        return float("inf")
    return (now - ts).total_seconds()


def _merge(parts: List[BusMessage], *, missing: List[int], grace: int) -> BusMessage:
    first, last = parts[0], parts[-1]
    content = "".join(p.content for p in parts)
    if missing:
        content += _MISSING_MARKER.format(
            count=first.part_count,
            missing=", ".join(str(i) for i in missing),
            grace=grace,
        )
    attachments: List[dict] = []
    for p in parts:
        attachments.extend(p.attachments or [])
    return first.model_copy(update={
        "content": content,
        "attachments": attachments or None,
        "created_at": last.created_at,
        "part_message_ids": [p.message_id for p in parts],
    })


def assemble(
    messages: List[BusMessage],
    *,
    now: Optional[datetime] = None,
    grace_seconds: int = PART_ASSEMBLY_GRACE_SECONDS,
    batch_truncated: bool = False,
) -> Tuple[List[BusMessage], bool]:
    """Collapse part rows in a lane batch into whole messages.

    ``messages`` must be in ``created_at`` ascending order (the pending
    query's order): "the sender's newest group" is read off the last group
    seen while walking the batch.

    ``batch_truncated`` — the caller's batch filled its LIMIT, so parts may
    exist beyond its edge: an incomplete group is then held regardless of
    age or supersession (a verdict on partial evidence would deliver a
    fragment and mis-label parts that are sitting in the table as missing).

    Returns ``(deliverable, held)``. ``deliverable`` is what the caller may
    hand to a turn NOW, sorted by ``created_at`` (a merged message sits at its
    LAST part's time, so the newest deliverable row is always the ack
    high-water). ``held`` is True when some rows were withheld because a
    group is still incomplete and young.

    The one invariant (review I3/I6): the lane's ack cursor advances to the
    newest DELIVERED row, and no held row may lie below it. So everything at
    or after the oldest held row is withheld too — an unrelated message that
    arrived after part 1 waits with the group rather than being delivered now
    and delivered again when the group completes. Everything before the
    oldest held row is delivered as usual.
    """
    now = now or datetime.now(timezone.utc)
    groups: Dict[str, List[BusMessage]] = {}
    # The newest group each sender opened in this batch: an older incomplete
    # group of the same sender is superseded (the write edge only ever
    # continues the sender's LATEST part), so holding for it would wait for
    # parts that can no longer be written.
    latest_group_of: Dict[str, str] = {}
    for m in messages:
        if m.part_group:
            groups.setdefault(m.part_group, []).append(m)
            latest_group_of[m.from_agent] = m.part_group

    merged: Dict[str, BusMessage] = {}
    held_groups: set = set()
    for group_id, parts in groups.items():
        parts.sort(key=lambda p: int(p.part_index or 0))
        count = int(parts[0].part_count or 0)
        have = {int(p.part_index or 0) for p in parts}
        missing = [i for i in range(1, count + 1) if i not in have]
        if missing:
            superseded = latest_group_of.get(parts[0].from_agent) != group_id
            newest = min(_age_seconds(p.created_at, now) for p in parts)
            if batch_truncated or (not superseded and newest < grace_seconds):
                held_groups.add(group_id)
                continue
        merged[group_id] = _merge(parts, missing=missing, grace=grace_seconds)

    # The held boundary. A COMPLETE group whose first part lies before it but
    # whose last part lies after it cannot be delivered (its merged row would
    # sit past the ack high-water) — and must not be dropped either: its first
    # part would then fall below the cursor and the group would come back
    # headless next poll (#389 I5). So such a group joins the held set and
    # the boundary moves down to its first part; repeat until stable (the
    # set only grows, the boundary only moves down, groups are finite).
    held_from: Optional[str] = None
    while True:
        held_from = (
            min(canonical_ts(m.created_at) for m in messages if m.part_group in held_groups)
            if held_groups else None
        )
        if held_from is None:
            break
        straddling = {
            gid for gid, whole in merged.items()
            if gid not in held_groups
            and canonical_ts(whole.created_at) >= held_from
            and any(
                canonical_ts(m.created_at) < held_from
                for m in messages if m.part_group == gid
            )
        }
        if not straddling:
            break
        held_groups |= straddling

    out: List[BusMessage] = []
    emitted: set = set()
    for m in messages:
        if held_from is not None and canonical_ts(m.created_at) >= held_from:
            continue
        if not m.part_group:
            out.append(m)
        elif m.part_group in merged and m.part_group not in emitted:
            emitted.add(m.part_group)
            out.append(merged[m.part_group])
    out.sort(key=lambda m: canonical_ts(m.created_at))
    return out, held_from is not None


__all__ = [
    "OVERSIZE_REMEDY_HUMAN",
    "OVERSIZE_REMEDY_PARTS",
    "OVERSIZE_REMEDY_TEAM",
    "BusMessageTooLarge",
    "MAX_BUS_MESSAGE_BYTES",
    "MAX_MESSAGE_PARTS",
    "MAX_MULTIPART_TOTAL_BYTES",
    "group_budget_reason",
    "too_many_parts_reason",
    "PART_ASSEMBLY_GRACE_SECONDS",
    "assemble",
    "oversize_fact",
    "oversize_reason",
    "split_for_bus",
]
