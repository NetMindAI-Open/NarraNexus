"""
@file_name: local_bus.py
@author: NarraNexus
@date: 2026-04-02
@description: Local SQLite-backed implementation of the MessageBus service

Implements MessageBusService using a DatabaseBackend (typically SQLiteBackend).
Designed for single-node / desktop use. All state lives in the local database.

Key design decisions:
- Cursor-based delivery model via last_processed_at per channel member
- Poison message filtering: messages with >= 3 failures are skipped
- Agent capabilities stored as JSON-serialized list in the registry
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger

from narranexus.platform.channel.message_source_handler import (
    im_channel_prefixes,
)
from narranexus.platform.message_bus.message_bus_service import MessageBusService
from narranexus.platform.message_bus.multipart import (
    MAX_BUS_MESSAGE_BYTES,
    MAX_MESSAGE_PARTS,
    MAX_MULTIPART_TOTAL_BYTES,
    BusMessageTooLarge,
    group_budget_reason,
    too_many_parts_reason,
)
from narranexus.platform.message_bus.schemas import (
    BusAgentInfo,
    BusChannelMember,
    BusMessage,
    canonical_ts,
)
from narranexus.platform.utils.db.db_backend import DatabaseBackend


def _generate_id(prefix: str) -> str:
    """Generate a short random ID with the given prefix."""
    return f"{prefix}_{secrets.token_hex(4)}"


#: One lane's pending batch. A LIMIT, not a page: the poll loop takes the
#: oldest N unprocessed rows of ONE channel, acks what it delivered, and comes
#: back for the rest. A batch that fills the limit may therefore be cut in the
#: middle of a multipart group — the trigger asks `multipart.assemble` to hold
#: such a group rather than judge it stale on partial evidence, and widens to
#: `PENDING_BATCH_LIMIT_WIDE` when nothing before the group can be delivered.
PENDING_BATCH_LIMIT = 50
PENDING_BATCH_LIMIT_WIDE = 500


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# Once a (message, agent) pair reaches this many delivery failures the message
# is permanently dropped from the pending queue with no further retries. Owned
# here because ``get_pending_messages`` is the filter that enforces it;
# ``MessageBusTrigger`` imports it for the owner-facing "permanently dropped"
# notice so the two can never drift apart.
POISON_FAILURE_THRESHOLD = 3


def _as_utc(value) -> Optional[datetime]:
    """Normalise a stored timestamp to an aware UTC datetime, or None.

    Backends disagree on the wire type: MySQL hands back naive ``datetime``
    objects, SQLite hands back the ISO strings ``_now_iso`` wrote. Anything
    comparing two timestamps in Python must go through here first.
    """
    if value is None:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def direct_channel_sql(ph: str) -> str:
    """The one definition of "the DM channel between these two agents".

    Two callers with two different placeholder dialects — `send_to_agent` holds
    the RAW backend (`self._db.placeholder`), while the `read_history` resolver
    holds an `AsyncDatabaseClient` (`%s`) — so what is shared is the SQL TEXT and
    each caller supplies its own `ph`. Sharing an execute() instead would force
    one of them onto the wrong placeholder, which is the bug that made
    `_room_labels` silently return nothing on SQLite.

    It is shared at all because it was copied: the same three-way join lived in
    two files, and "what identifies a DM" is exactly the kind of fact that then
    gets changed in one of them (archived flags, dedupe, cross-owner rules). Four
    copies of the team-room lookup is how `team_rooms.py` came to exist.

    **Callers MUST reject `a == b` before calling.** Both joins are satisfied by
    the same member row when the two parameters are equal, so every direct channel
    the caller belongs to matches and `rows[0]` is arbitrary. The invariant lives
    here rather than only in each caller because there are two callers now and the
    third will otherwise get it wrong too — `send_to_agent` was the one that did.

    `ORDER BY created_at ASC` is not cosmetic. `send_to_agent` creates a channel
    when the lookup misses, so two concurrent first-sends to the same peer can
    both miss and both create — after which an unordered `rows[0]` is
    engine-dependent, and the sender and the history reader can disagree about
    which channel the conversation is. Ordered, they at least agree on the older
    one.
    """
    return (
        f"SELECT c.channel_id FROM bus_channels c "
        f"JOIN bus_channel_members m1 ON c.channel_id = m1.channel_id "
        f"AND m1.agent_id = {ph} "
        f"JOIN bus_channel_members m2 ON c.channel_id = m2.channel_id "
        f"AND m2.agent_id = {ph} "
        f"WHERE c.channel_type = 'direct' "
        # Both callers take `rows[0]`; fetching the rest to discard them is waste.
        f"ORDER BY c.created_at ASC LIMIT 1"
    )


class LocalMessageBus(MessageBusService):
    """
    SQLite-backed MessageBus implementation.

    Uses a DatabaseBackend instance for all persistence. Suitable for
    local/desktop deployments where all agents run on the same machine.

    Args:
        backend: An initialized DatabaseBackend (e.g., SQLiteBackend).
    """

    def __init__(self, backend: DatabaseBackend) -> None:
        self._db = backend

    # ===== Helpers =====

    @staticmethod
    def _row_to_message(row: dict) -> BusMessage:
        """Convert a DB row to a BusMessage, deserializing mentions JSON."""
        mentions_raw = row.get("mentions")
        mentions = json.loads(mentions_raw) if mentions_raw else None
        attachments_raw = row.get("attachments")
        attachments = json.loads(attachments_raw) if attachments_raw else None
        # A half-written or hand-edited row must not take the whole transcript
        # down: losing one message's layout is a degradation, losing the room
        # is an outage.
        segments = None
        segments_raw = row.get("segments")
        if segments_raw:
            try:
                segments = json.loads(segments_raw)
            except (ValueError, TypeError):
                logger.warning(
                    f"[bus] message {row.get('message_id')} has unreadable "
                    f"segments; rendering as one block"
                )
        return BusMessage(
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            from_agent=row["from_agent"],
            content=row["content"],
            msg_type=row.get("msg_type", "text"),
            mentions=mentions,
            attachments=attachments,
            segments=segments,
            event_id=row.get("event_id"),
            sender_turn_source=row.get("sender_turn_source"),
            routed_by=row.get("routed_by"),
            root_run_id=row.get("root_run_id"),
            part_index=row.get("part_index") or None,
            part_count=row.get("part_count") or None,
            part_group=row.get("part_group") or None,
            created_at=row.get("created_at"),
        )

    # ===== Messaging =====

    async def send_message(
        self,
        from_agent: str,
        to_channel: str,
        content: str,
        msg_type: str = "text",
        mentions: Optional[List[str]] = None,
        attachments: Optional[List[dict]] = None,
        event_id: Optional[str] = None,
        sender_turn_source: Optional[str] = None,
        root_run_id: Optional[str] = None,
        routed_by: Optional[str] = None,
        # Appended LAST and kept there: `send_message` has positional callers,
        # and a parameter added in the middle silently rebinds every one of
        # them. Pinned by test_team_message_segments.
        segments: Optional[List[dict]] = None,
        *,
        part_index: int = 0,
        part_count: int = 0,
    ) -> str:
        """Send a message to a channel and return the generated message_id.

        ``part_index`` / ``part_count`` (both 0 = an ordinary message) mark ONE
        part of a long message sent in order. Part 1 opens a group whose id is
        its own message_id; every later part must follow the previous index
        from the same sender in the same channel with the same count, or the
        send is refused (a part that cannot be placed would be delivered as a
        fragment). The recipient's trigger holds the group back until the last
        part lands and hands the turn one reassembled message — see multipart.py.

        ``sender_turn_source`` records WHICH KIND of turn produced this
        message ("chat"/"job"/… = the sender was running an errand for its
        owner, so this is a question; "message_bus" = the sender was already
        answering a peer, so this is a reply). MessageBusTrigger reads it to
        decide whether the recipient should answer the peer or relay to its
        owner — see the column comment in schema_registry.

        ``routed_by`` records WHY ``mentions`` holds what it does: ``None`` when
        the sender wrote them, ``"default_responder"`` when a team room had none
        and the route picked the fallback agent. Downstream cannot reconstruct
        this — a single mention naming the lead is exactly what a user
        deliberately naming the lead looks like.

        ``root_run_id`` records WHICH TRIGGER TREE the sending run belonged to,
        so the run this message wakes up inherits it. Without it the lineage
        breaks at every agent→agent hop and a cascade stop leaves the branch
        beyond the hop running.
        """
        size = len((content or "").encode("utf-8"))
        if size > MAX_BUS_MESSAGE_BYTES:
            # Every writer, not one tool: `message_team` and the platform's own
            # lines reach this insert too (review I4). Refused, never cut. The
            # exception carries the FACT; the remedy is the caller's (a peer
            # tool has parts, a room tool and a person do not — #389 I1).
            raise BusMessageTooLarge(size)
        msg_id = _generate_id("msg")
        part_group = await self._resolve_part_group(
            from_agent, to_channel, msg_id, part_index, part_count, size
        )
        # A message carrying files is tagged "multimodal" so UI / search can
        # distinguish it; pure text stays "text".
        if attachments and msg_type == "text":
            msg_type = "multimodal"
        await self._db.insert("bus_messages", {
            "message_id": msg_id,
            "channel_id": to_channel,
            "from_agent": from_agent,
            "content": content,
            "msg_type": msg_type,
            "mentions": json.dumps(mentions) if mentions else None,
            "attachments": json.dumps(attachments) if attachments else None,
            # Empty list stores as NULL: it carries nothing a reader could act
            # on, and a value that looks like data invites the reader to trust
            # it as "this turn genuinely had no segments".
            "segments": json.dumps(segments) if segments else None,
            "event_id": event_id,
            "sender_turn_source": sender_turn_source,
            "root_run_id": root_run_id,
            "routed_by": routed_by,
            "part_index": part_index or None,
            "part_count": part_count or None,
            "part_group": part_group,
            "created_at": _now_iso(),
        })
        # Nudge the poll loop — the ONE place this can live.
        #
        # `MessageBusTrigger._wake` only reaches posts made by the trigger's own
        # process, and a team reply is now a tool call made on the MCP server.
        # Bumping here, right after the insert, makes "posted without waking"
        # impossible instead of merely discouraged: there is no second insert
        # site to forget (this is the only `bus_messages` insert in the repo).
        #
        # AFTER the insert, deliberately: the signal means work exists, so a
        # send that raised must leave it untouched.
        from narranexus.platform.message_bus import wake_signal

        await wake_signal.bump(self._db)

        # Index the message into the unified search layer (memory_bus), under the
        # sender, pointing back to the message. Append-only — bus is objective
        # message history (like chat); no update/dedup (design §10-C). Recipient-
        # side recall of INBOUND messages is largely covered by the per-turn
        # interaction index (a bus message that triggers a turn becomes that
        # turn's input); per-recipient fan-out is a possible follow-up.
        try:
            from loguru import logger
            from narranexus.platform.memory import MemoryEngine
            if (content or "").strip():
                await MemoryEngine(self._db, from_agent).index(
                    "bus", msg_id, content, scope_type="agent",
                    tags=[f"channel:{to_channel}"],
                )
        except Exception as e:  # noqa: BLE001 — index is best-effort enrichment
            logger.warning(f"bus index failed (non-fatal): {e}")
        return msg_id

    async def get_messages(
        self,
        channel_id: str,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> List[BusMessage]:
        """Get messages from a channel, optionally filtered by timestamp."""
        ph = self._db.placeholder
        if since:
            rows = await self._db.execute(
                f"SELECT * FROM bus_messages WHERE channel_id = {ph} "
                f"AND created_at > {ph} ORDER BY created_at ASC LIMIT {int(limit)}",
                (channel_id, since),
            )
        else:
            rows = await self._db.execute(
                f"SELECT * FROM bus_messages WHERE channel_id = {ph} "
                f"ORDER BY created_at ASC LIMIT {int(limit)}",
                (channel_id,),
            )
        return [self._row_to_message(row) for row in rows]

    async def get_recent_messages(self, channel_id: str, limit: int = 20) -> List[BusMessage]:
        """Get the MOST RECENT ``limit`` messages, returned oldest→newest.

        ``get_messages`` is ``ORDER BY created_at ASC LIMIT n`` (the oldest n),
        which is wrong for "recent scrollback". This selects the newest n
        (``DESC``) and reverses so the caller reads them in chat order. Used to
        give a team-room agent the recent conversation (incl. attachments) as
        context, not just the message that @mentioned it.
        """
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT * FROM bus_messages WHERE channel_id = {ph} "
            f"ORDER BY created_at DESC LIMIT {int(limit)}",
            (channel_id,),
        )
        return [self._row_to_message(row) for row in reversed(rows)]

    async def get_message(self, message_id: str) -> Optional[BusMessage]:
        row = await self._db.get_one("bus_messages", {"message_id": message_id})
        return self._row_to_message(row) if row else None

    async def get_messages_before(
        self, channel_id: str, before: str, limit: int = 50
    ) -> List[BusMessage]:
        """The page immediately ABOVE ``before``, returned oldest→newest.

        The mirror image of ``get_messages(since=…)``, and the two directions
        are deliberately not symmetrical:

          * ``since`` returns the OLDEST after the cursor — catching up must
            never skip a message, so it walks forward from where the reader is.
          * ``before`` returns the NEWEST before it — scrolling up wants the
            page directly above what is on screen, not the start of the room's
            history.

        Either one implemented in the other direction produces a transcript
        with a silent hole in it rather than an error.

        EXCLUSIVE on the cursor: the caller passes the timestamp of the oldest
        message it already has. Inclusive would re-send that message on every
        page, which the frontend's merge would then dedup away — leaving each
        page one message shorter than asked for, for no visible reason.

        Args:
            channel_id: Channel to read.
            before: ISO timestamp string, as handed out by the API.
            limit: Page size.

        Returns:
            Up to ``limit`` messages older than ``before``, oldest first. Empty
            when the top of the history has been reached — which is how a caller
            knows to stop offering "load more" without inferring it from a short
            page (a short page also just means a sparse window).
        """
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT * FROM bus_messages WHERE channel_id = {ph} "
            f"AND created_at < {ph} ORDER BY created_at DESC LIMIT {int(limit)}",
            (channel_id, before),
        )
        return [self._row_to_message(row) for row in reversed(rows)]

    def _unread_predicate(self, ph: str) -> tuple[str, tuple]:
        """The unread predicate AND its parameters, from ONE construction.

        Shared by the fetch, the probe and the count deliberately: `get_unread`,
        `has_unread_before` and `count_unread` must agree, or "N unread (showing
        M)" starts lying about its own list.

        ``from_agent != agent`` matches ``get_pending_messages``, which has always
        had it. Its absence here meant an agent read its own posts back as
        unanswered items — loudest exactly where it hurts, a room the agent talks
        in a lot.

        **Legacy IM channels are excluded.** Until 2026-08-17 `ChannelInboxWriter`
        mirrored every IM turn into `bus_messages` for the Inbox to display, under
        a channel nobody ever marked read — so 1,364 messages were permanently
        unread and rode into 90 agents' context every turn, attributed to
        pseudo-agents like `lark_user_<id>`. Moving the inbox to its own tables
        stops NEW rows being written; the deployed rows are still there and this
        predicate is what feeds them to the model. Filtering the read is what ends
        the injection, on the deploy rather than on the day someone runs the purge.

        Not "structural containment" — honestly a filter over rows a retired writer
        left behind. It retires when those rows are purged;
        `reference/self_notebook/todo/2026-08-17-inbox-backfill-runbook.md` owns
        that step.

        SUBSTR, not LIKE. `_` is a single-character LIKE wildcard and every one of
        these prefixes ENDS in `_`, so `NOT LIKE 'lark_%'` also excluded
        `larkX_…` and `larky_…` — any channel whose id starts with "lark" plus any
        one character. Verified on SQLite: with ids (lark_oc_1, larkX_oc_2,
        larky_room, ch_team_1, lark), the unescaped pattern kept only (ch_team_1,
        lark). Current id formats make the over-match unreachable, which is exactly
        why it would have survived to bite whoever changes an id format later — and
        this filter decides what reaches the model, so over-matching is silent
        context loss, not an error. (A literal `%` would also have been fatal on
        MySQL: pymysql mogrifies with `query % args`, so every unread query would
        have raised `TypeError` and been swallowed by the caller's `except`.)

        SUBSTR also lets the prefix be a BOUND parameter rather than interpolated
        text; only its LENGTH goes into the SQL, and that is an int derived from a
        registry key. Two verified properties, on SQLite: an id SHORTER than the
        prefix yields the whole shorter string, which correctly compares unequal
        and is kept; an exact-prefix id is excluded. One recorded dialect
        difference: our MySQL DDL uses a `_ci` collation, so `<>` there is
        case-INSENSITIVE while SQLite's is not — unreachable, since ids are built
        from lowercase registry names, and both readings only ever exclude a
        channel that does not exist. `test_unread_cursor_mysql.py` exercises this
        predicate on real MySQL.

        **Returns the SQL and the params together, and that is the point.** They
        were two methods reading `im_channel_prefixes()` separately, with nothing
        tying the number of emitted placeholders to the length of the returned
        tuple — the exact shape `message_bus_module._room_labels`' own comment
        condemns ("two independent constructions feeding a count and its tuple is
        the shape that breaks when someone edits one line"), built two files away
        from that comment. Add any condition to the generator — skip a prefix,
        dedupe, `if len(pfx) > 1` — and `has_unread_before` shifts by one:
        `m.channel_id = ?` receives a prefix string and `m.created_at < ?` receives
        a channel id. The query returns nothing, `has_unread_before` says False,
        and `ack_read` discards unread messages. No exception, and no test failure
        unless the fixture happens to hold the right number of prefixes.
        """
        # Clause and parameter appended TOGETHER, in one loop. Returning
        # `tuple(prefixes)` alongside a generator over the same list is not enough:
        # a condition added to the generator (skip a prefix, dedupe,
        # `if len(pfx) > 1`) would leave the params unfiltered and shift every
        # subsequent placeholder. Verified — that mutation passed the suite when
        # the two were separate expressions. Paired, a `continue` here drops both
        # and they cannot desynchronise.
        clauses: list[str] = []
        params: list[str] = []
        for pfx in im_channel_prefixes():
            clauses.append(f" AND SUBSTR(m.channel_id, 1, {len(pfx)}) <> {ph}")
            params.append(pfx)

        sql = (
            f"FROM bus_messages m "
            f"JOIN bus_channel_members cm ON m.channel_id = cm.channel_id "
            f"WHERE cm.agent_id = {ph} "
            f"AND m.from_agent != {ph} "
            f"AND m.created_at > COALESCE(cm.last_read_at, '1970-01-01')"
            f"{''.join(clauses)}"
        )
        # Documentation, not the guard: the paired loop above already makes this
        # unfalsifiable, and `assert` is stripped under `-O`. The real guard is
        # `test_legacy_im_rows_not_injected.py`'s placeholder-vs-param count, which
        # runs with assertions on and fails on a mutation. Left in because it states
        # the invariant where someone editing the loop will read it.
        assert len(clauses) == len(params)
        return sql, tuple(params)

    async def get_unread(
        self, agent_id: str, limit: Optional[int] = None
    ) -> List[BusMessage]:
        """Unread messages across all channels, oldest first.

        ``limit`` selects the NEWEST ``limit`` messages and returns them in
        reading order — the same DESC-then-reverse shape ``get_recent_messages``
        documents, and for the same reason: ``ORDER BY created_at ASC`` with a
        cap hands back the OLDEST rows, which is the opposite of what every
        caller wants. This query feeds the per-turn "what is going on" block, so
        an ancient window is worse than none: it reads as current.

        ``limit=None`` returns the whole backlog, and one caller depends on
        that. The module's post-turn hook asks for the full set to work out
        which messages a reply covers; cap it and every older answered message
        stays unread forever.
        """
        ph = self._db.placeholder
        where, pfx = self._unread_predicate(ph)
        if limit is None:
            rows = await self._db.execute(
                f"SELECT m.* {where} ORDER BY m.created_at ASC",
                (agent_id, agent_id, *pfx),
            )
            return [self._row_to_message(row) for row in rows]
        rows = await self._db.execute(
            f"SELECT m.* {where} ORDER BY m.created_at DESC LIMIT {int(limit)}",
            (agent_id, agent_id, *pfx),
        )
        return [self._row_to_message(row) for row in reversed(rows)]

    async def has_unread_before(
        self, agent_id: str, channel_id: str, before: str
    ) -> bool:
        """Is anything in this channel still unread and older than ``before``?

        A boolean, answered by the database. The caller wants to know whether a
        turn's rendered window reached the bottom of what the agent still owes,
        and doing that by pulling every unread message across every channel and
        filtering in Python is the exact shape this module's unread work just
        removed: the whole backlog crossing the wire to be thrown away.

        Comparing in SQL also keeps one ordering authority. The Python version
        had to re-derive the cursor's lexicographic comparison by hand, which is
        a second implementation of a rule that has already bitten this codebase
        once.
        """
        if not agent_id or not channel_id or not before:
            return False
        ph = self._db.placeholder
        where, pfx = self._unread_predicate(ph)
        rows = await self._db.execute(
            f"SELECT 1 AS hit {where} "
            f"AND m.channel_id = {ph} AND m.created_at < {ph} LIMIT 1",
            # Prefix params sit BETWEEN the predicate's own and this query's
            # extras, because that is the order the placeholders appear in. This
            # is the one caller that interleaves, so it is the one a shifted
            # parameter count breaks first.
            (agent_id, agent_id, *pfx, channel_id, canonical_ts(before)),
        )
        return bool(rows)

    async def has_message_from_turn(
        self, channel_id: str, from_agent: str, event_id: str
    ) -> bool:
        """Did this agent put anything into this channel during that turn?

        The turn's own id is the join: both the platform's team-room post and
        the agent's own ``message_team`` stamp ``event_id``, so one query
        covers a reply however it was sent. A timestamp window would answer a
        near-enough question with a comparison this codebase has already been
        bitten by once; an identity is exact.

        Existence only — the caller is deciding whether to announce a delivery
        failure, and the message body has no bearing on that.

        No dedicated index, on purpose: ``idx_bus_msg_channel_time`` already
        makes this one channel's rows the scan. Two callers, both cold: the bus
        trigger runs it on a branch that needs a provider failure to be reached
        at all, and the job trigger once per room-origin job run. A third index
        on the busiest table in the bus would be paid for by every insert,
        forever, to speed up a query that rarely runs. Revisit if a HOT caller
        appears — per-turn on the delivery path would be one.
        """
        if not channel_id or not from_agent or not event_id:
            return False
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT 1 AS hit FROM bus_messages WHERE channel_id = {ph} "
            f"AND from_agent = {ph} AND event_id = {ph} LIMIT 1",
            (channel_id, from_agent, event_id),
        )
        return bool(rows)

    async def count_unread(self, agent_id: str) -> int:
        """How many unread messages exist, independent of any window.

        The prompt renders "N unread (showing M)". Once the fetch is capped, N
        can no longer be ``len()`` of the result — it would always equal M and
        the reader would never learn there was a backlog at all.
        """
        ph = self._db.placeholder
        where, pfx = self._unread_predicate(ph)
        rows = await self._db.execute(
            f"SELECT COUNT(*) AS n {where}",
            (agent_id, agent_id, *pfx),
        )
        return int(rows[0].get("n") or 0) if rows else 0

    async def mark_read(self, agent_id: str, message_ids: List[str]) -> None:
        """Mark messages as read by advancing the read cursor per channel."""
        if not message_ids:
            return

        # Fetch the messages to find their channel_id and created_at
        messages = await self._db.get_by_ids("bus_messages", "message_id", message_ids)

        # Group by channel_id and find the latest created_at per channel
        channel_latest: dict[str, str] = {}
        for msg in messages:
            if msg is None:
                continue
            ch = msg["channel_id"]
            ts = msg["created_at"]
            if ch not in channel_latest or ts > channel_latest[ch]:
                channel_latest[ch] = ts

        # Update last_read_at for each channel
        for ch_id, latest_ts in channel_latest.items():
            await self._db.update(
                "bus_channel_members",
                {"agent_id": agent_id, "channel_id": ch_id},
                {"last_read_at": latest_ts},
            )

    async def _resolve_part_group(
        self, from_agent: str, channel_id: str, msg_id: str,
        part_index: int, part_count: int, size: int,
    ) -> Optional[str]:
        """The group id a part belongs to, or None for an ordinary message.

        Validates the part contract at the write edge so a fragment can never
        be stored unplaceable: 1 <= index <= count, and a
        part > 1 must find the sender's most recent part in this channel to be
        exactly index-1 of the same count (the group is then that part's).
        Two independent bounds on a group, each with its own refusal:
        ``part_count`` may not exceed ``MAX_MESSAGE_PARTS`` (checked on every
        part, so part 1 already fails), and the parts already stored plus this
        one (``size`` bytes) may not exceed ``MAX_MULTIPART_TOTAL_BYTES`` (review
        I7) — measured in bytes in Python, not with SQL LENGTH(), which counts
        characters on SQLite and bytes on MySQL. Raises ValueError with an
        agent-readable reason otherwise.
        """
        if not part_index and not part_count:
            return None
        if part_count < 1 or part_index < 1 or part_index > part_count:
            raise ValueError(
                f"invalid part {part_index}/{part_count}: parts are numbered "
                f"1..count, count >= 1"
            )
        if part_count > MAX_MESSAGE_PARTS:
            # Refused on part 1 already — the model must not send 40 parts
            # before learning the 41st is impossible.
            raise ValueError(too_many_parts_reason(part_count))
        if part_index == 1:
            if size > MAX_MULTIPART_TOTAL_BYTES:
                raise ValueError(group_budget_reason(0, size))
            return msg_id
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT part_index, part_count, part_group FROM bus_messages "
            f"WHERE channel_id = {ph} AND from_agent = {ph} "
            f"AND part_group IS NOT NULL "
            f"ORDER BY created_at DESC LIMIT 1",
            (channel_id, from_agent),
        )
        prev = rows[0] if rows else None
        if (
            prev is None
            or int(prev.get("part_count") or 0) != part_count
            or int(prev.get("part_index") or 0) != part_index - 1
        ):
            have = (
                f"{prev.get('part_index')}/{prev.get('part_count')}" if prev else "none"
            )
            raise ValueError(
                f"part {part_index}/{part_count} does not follow the previous "
                f"part (last stored: {have}); send parts in order, starting at "
                f"1/{part_count}"
            )
        stored = await self._db.execute(
            f"SELECT content FROM bus_messages WHERE channel_id = {ph} "
            f"AND part_group = {ph}",
            (channel_id, prev["part_group"]),
        )
        stored_bytes = sum(len((r.get("content") or "").encode("utf-8")) for r in stored)
        if stored_bytes + size > MAX_MULTIPART_TOTAL_BYTES:
            raise ValueError(group_budget_reason(stored_bytes, size))
        return prev["part_group"]

    async def send_to_agent(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        msg_type: str = "text",
        attachments: Optional[List[dict]] = None,
        sender_turn_source: Optional[str] = None,
        root_run_id: Optional[str] = None,
        event_id: Optional[str] = None,
        *,
        part_index: int = 0,
        part_count: int = 0,
    ) -> str:
        """Send a direct message to another agent, auto-creating a DM channel if needed."""
        ph = self._db.placeholder

        # Same-user boundary: an agent may only DM agents owned by the same
        # user. Cross-user direct messaging is intentionally disabled — never
        # let an agent message another user's agent.
        if from_agent == to_agent:
            # `direct_channel_sql` joins the channel against two member rows; with
            # the same id twice BOTH joins are satisfied by the SAME row, so any
            # direct channel this agent belongs to matches and `rows[0]` is an
            # arbitrary unrelated conversation. Proven: with a seeded `a↔b`
            # channel, `send_to_agent(from="a", to="a")` landed the row in a↔b.
            #
            # So a note-to-self reached peer b AND — via the wake signal — started
            # a full LLM turn for b. An agent naming itself is an ordinary model
            # error (self-note, "reply to myself", a copied id), and `message_agent`
            # now advertises "the same action whether you are answering someone or
            # starting a conversation of your own", which invites it.
            #
            # The read path (`_resolve_conversation`) already rejected this. The
            # write path shares the SQL and did not, which is what sharing SQL
            # without sharing its invariant looks like.
            raise ValueError(
                f"cannot message yourself: {from_agent} — name the peer you mean"
            )

        from_owner = await self._agent_owner(from_agent)
        to_owner = await self._agent_owner(to_agent)
        if from_owner and to_owner and from_owner != to_owner:
            raise PermissionError(
                f"cross-user messaging is not allowed: {from_agent} cannot "
                f"message {to_agent} (different owners)"
            )

        # Find existing direct channel between these two agents. Shared text with
        # the `read_history` resolver — see `direct_channel_sql`.
        rows = await self._db.execute(
            direct_channel_sql(ph), (from_agent, to_agent)
        )

        if rows:
            channel_id = rows[0]["channel_id"]
        else:
            # Auto-create direct channel
            channel_id = await self.create_channel(
                name=f"dm_{from_agent}_{to_agent}",
                members=[from_agent, to_agent],
                channel_type="direct",
            )

        return await self.send_message(
            from_agent, channel_id, content, msg_type, attachments=attachments,
            sender_turn_source=sender_turn_source,
            root_run_id=root_run_id,
            event_id=event_id,
            part_index=part_index,
            part_count=part_count,
        )

    # ===== Channel Management =====

    async def create_channel(
        self,
        name: str,
        members: List[str],
        channel_type: str = "group",
    ) -> str:
        """Create a new channel with the given members."""
        ch_id = _generate_id("ch")
        now = _now_iso()
        created_by = members[0] if members else "system"

        # Same-user boundary: a channel may only contain agents owned by the
        # creator's user. Cross-user channels are intentionally disabled so an
        # agent cannot pull another user's agent into a conversation.
        creator_owner = await self._agent_owner(created_by)
        if creator_owner:
            for member in members:
                if member == created_by:
                    continue
                member_owner = await self._agent_owner(member)
                if member_owner and member_owner != creator_owner:
                    raise PermissionError(
                        f"cross-user channel is not allowed: {member} has a "
                        f"different owner than {created_by}"
                    )

        await self._db.insert("bus_channels", {
            "channel_id": ch_id,
            "name": name,
            "channel_type": channel_type,
            "created_by": created_by,
            "created_at": now,
        })

        for agent_id in members:
            await self._db.insert("bus_channel_members", {
                "channel_id": ch_id,
                "agent_id": agent_id,
                "joined_at": now,
                "last_read_at": now,
            })

        return ch_id

    async def join_channel(self, agent_id: str, channel_id: str) -> None:
        """Add an agent to a channel."""
        now = _now_iso()
        await self._db.insert("bus_channel_members", {
            "channel_id": channel_id,
            "agent_id": agent_id,
            "joined_at": now,
            "last_read_at": now,
        })

    async def leave_channel(self, agent_id: str, channel_id: str) -> None:
        """Remove an agent from a channel."""
        await self._db.delete("bus_channel_members", {
            "channel_id": channel_id,
            "agent_id": agent_id,
        })

    # ===== Agent Discovery =====

    async def register_agent(
        self,
        agent_id: str,
        owner_user_id: str,
        capabilities: List[str],
        description: str,
        visibility: str = "private",
    ) -> None:
        """Register or update an agent in the discovery registry."""
        now = _now_iso()
        await self._db.upsert(
            "bus_agent_registry",
            {
                "agent_id": agent_id,
                "owner_user_id": owner_user_id,
                "capabilities": json.dumps(capabilities),
                "description": description,
                "visibility": visibility,
                "registered_at": now,
                "last_seen_at": now,
            },
            id_field="agent_id",
        )

    async def _agent_owner(self, agent_id: str) -> str:
        """Owning user_id of an agent (authoritative: agents.created_by).

        Returns "" if unknown. Used to enforce the same-user boundary on bus
        discovery and direct messaging.
        """
        ph = self._db.placeholder
        try:
            rows = await self._db.execute(
                f"SELECT created_by FROM agents WHERE agent_id = {ph}", (agent_id,)
            )
            return (rows[0]["created_by"] if rows else "") or ""
        except Exception:  # noqa: BLE001 — owner lookup must never crash discovery
            return ""

    async def search_agents(
        self,
        query: str,
        requester_agent_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[BusAgentInfo]:
        """Search for agents by capability or description.

        Scoped to the requester's own account: when ``requester_agent_id`` is
        given, only agents owned by the same user are returned. Cross-user
        discovery via the bus is intentionally disabled — an agent must never
        be able to find another user's agents.
        """
        ph = self._db.placeholder
        search_pattern = f"%{query}%"
        where = f"WHERE (capabilities LIKE {ph} OR description LIKE {ph})"
        params: list = [search_pattern, search_pattern]
        if requester_agent_id is not None:
            owner = await self._agent_owner(requester_agent_id)
            if not owner:
                # Unknown requester owner → return nothing rather than leak all.
                return []
            where += f" AND owner_user_id = {ph}"
            params.append(owner)
        rows = await self._db.execute(
            f"SELECT * FROM bus_agent_registry {where} LIMIT {int(limit)}",
            tuple(params),
        )
        results = []
        for row in rows:
            caps = row.get("capabilities", "[]")
            if isinstance(caps, str):
                caps = json.loads(caps)
            results.append(BusAgentInfo(
                agent_id=row["agent_id"],
                owner_user_id=row["owner_user_id"],
                capabilities=caps,
                description=row.get("description", ""),
                visibility=row.get("visibility", "private"),
                registered_at=row.get("registered_at", ""),
                last_seen_at=row.get("last_seen_at", ""),
            ))
        return results

    # ===== Delivery =====

    async def get_pending_messages(
        self,
        agent_id: str,
        limit: int = PENDING_BATCH_LIMIT,
        channel_id: Optional[str] = None,
    ) -> List[BusMessage]:
        """
        Get messages that have not been processed by the agent.

        Uses the cursor model and filters out self-sent messages,
        poison messages (failure_count >= 3), and messages belonging to a
        trigger tree whose owner asked to stop.

        ``channel_id`` scopes the query to ONE channel so the ``LIMIT`` lands on
        that room's backlog, not the agent's whole cross-channel backlog. The
        per-lane trigger is the only caller that wants a single room's next
        batch; passing None keeps the old cross-channel behaviour. Without this,
        an agent with >``limit`` pending in a busy room A would have every one of
        its OTHER rooms' polls return A's rows, filter to empty in Python, and
        starve — the per-lane concurrency's whole point defeated (has_unread_before
        carries the same "scope the LIMIT, don't filter in Python" rule).

        The stopped-tree filter is what makes a cascade stop actually stick:
        stopping the running turns is not enough while their queued follow-ups
        are still waiting, because the next poll would start those and the owner
        would watch new work appear right after pressing stop. It is a SQL
        predicate rather than a per-row check on purpose — the poison filter
        below already costs one query per row, and this must not add a second.
        """
        ph = self._db.placeholder
        # channel scope goes FIRST after the agent match so the LIMIT applies to
        # the single room; params are ordered to match. None → no clause, exactly
        # the old cross-channel query and its (agent_id, agent_id) params.
        channel_clause = f"AND m.channel_id = {ph} " if channel_id is not None else ""
        params = (
            (agent_id, channel_id, agent_id)
            if channel_id is not None
            else (agent_id, agent_id)
        )
        rows = await self._db.execute(
            f"SELECT m.* FROM bus_messages m "
            f"JOIN bus_channel_members cm ON m.channel_id = cm.channel_id "
            f"WHERE cm.agent_id = {ph} "
            f"{channel_clause}"
            f"AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01') "
            f"AND m.from_agent != {ph} "
            # "Was ANYONE in this tree stopped" — deliberately not "was the
            # ROOT stopped". The root row is often already `completed` when the
            # owner presses stop (an agent that delegates typically ends its own
            # turn right after sending), and settled rows are never flagged, so
            # keying off the root alone reads as "nothing was stopped" in the
            # most common delegating shape and keeps waking new runs.
            #
            # NULL root = not part of any tree (user messages, legacy rows):
            # never suppressed, otherwise one stop would mute the whole table.
            f"AND (m.root_run_id IS NULL OR NOT EXISTS ("
            f"  SELECT 1 FROM events e"
            f"  WHERE e.root_run_id = m.root_run_id"
            f"    AND e.cancel_requested_at IS NOT NULL"
            f")) "
            f"ORDER BY m.created_at ASC "
            f"LIMIT {int(limit)}",
            params,
        )

        # Filter out poison messages (see POISON_FAILURE_THRESHOLD)
        result = []
        for row in rows:
            failure_count = await self.get_failure_count(row["message_id"], agent_id)
            if failure_count < POISON_FAILURE_THRESHOLD:
                result.append(self._row_to_message(row))
        return result

    async def get_room_pending_summary(
        self,
        channel_id: str,
        agent_ids: List[str],
        limit: int = 200,
    ) -> dict:
        """Per-agent "what is still waiting for you in THIS room" summary.

        Returns ``{agent_id: {"count": int, "oldest_at": datetime}}``, with an
        entry only for agents that actually have something pending. "Pending"
        matches ``get_pending_messages`` semantics restricted to one channel and
        to @-addressed messages: past the agent's cursor, not self-sent, the
        agent (or ``@everyone``) is mentioned, and not poisoned.

        Exists because the team-chat GET polls every few seconds for every
        member: doing this through ``get_pending_messages`` cost one query per
        member plus one ``get_failure_count`` per pending row (a 6-member room
        polled at 3s ran ~30 queries per tick). This is three queries total,
        independent of member count.
        """
        if not agent_ids:
            return {}
        ph = self._db.placeholder

        member_rows = await self._db.execute(
            f"SELECT agent_id, last_processed_at FROM bus_channel_members "
            f"WHERE channel_id = {ph}",
            (channel_id,),
        ) or []
        cursors = {
            r["agent_id"]: _as_utc(r.get("last_processed_at"))
            for r in member_rows
            if r["agent_id"] in set(agent_ids)
        }
        if not cursors:
            return {}

        # One scan from the EARLIEST member cursor covers every member; the
        # per-agent cutoff is then applied in Python. A member that has never
        # processed anything (None cursor) pulls the window back to the start.
        floor = None if any(c is None for c in cursors.values()) else min(cursors.values())
        params: tuple = (channel_id,) if floor is None else (channel_id, floor.isoformat())
        where_ts = "" if floor is None else f"AND m.created_at > {ph} "
        rows = await self._db.execute(
            f"SELECT m.message_id, m.from_agent, m.mentions, m.created_at "
            f"FROM bus_messages m WHERE m.channel_id = {ph} {where_ts}"
            f"ORDER BY m.created_at ASC LIMIT {int(limit)}",
            params,
        ) or []
        if not rows:
            return {}

        # Poison lookup for the whole candidate set in one statement. Bare
        # identifiers + generated placeholders keep it dialect-portable.
        ids = [r["message_id"] for r in rows]
        placeholders = ", ".join([ph] * len(ids))
        failure_rows = await self._db.execute(
            f"SELECT message_id, agent_id, retry_count FROM bus_message_failures "
            f"WHERE message_id IN ({placeholders})",
            tuple(ids),
        ) or []
        poisoned = {
            (r["message_id"], r["agent_id"])
            for r in failure_rows
            if (r.get("retry_count") or 0) >= POISON_FAILURE_THRESHOLD
        }

        summary: dict = {}
        for row in rows:
            created = _as_utc(row.get("created_at"))
            if created is None:
                continue
            mentions_raw = row.get("mentions")
            try:
                mentions = json.loads(mentions_raw) if mentions_raw else []
            except (ValueError, TypeError):
                mentions = []
            if not mentions:
                continue
            broadcast = "@everyone" in mentions
            for agent_id, cursor in cursors.items():
                if row["from_agent"] == agent_id:
                    continue
                if cursor is not None and created <= cursor:
                    continue
                if not broadcast and agent_id not in mentions:
                    continue
                if (row["message_id"], agent_id) in poisoned:
                    continue
                entry = summary.setdefault(agent_id, {"count": 0, "oldest_at": created})
                entry["count"] += 1
                if created < entry["oldest_at"]:
                    entry["oldest_at"] = created
        return summary

    async def ack_processed(
        self,
        agent_id: str,
        channel_id: str,
        up_to_timestamp: str,
    ) -> None:
        """Acknowledge messages up to a timestamp as processed.

        The cursor and ``bus_messages.created_at`` are both TEXT and compared
        lexicographically in ``get_pending_messages``. ``up_to_timestamp`` may
        arrive as a string OR as an auto-parsed ``datetime`` (db_backend_sqlite
        converts ``*_at`` columns on read). A ``datetime`` serialised via
        ``str()`` becomes ``"YYYY-MM-DD HH:MM:SS"`` (space, no 'T') while
        ``created_at`` is ``_now_iso()`` ``"YYYY-MM-DDTHH:MM:SS+00:00"`` ('T').
        Since 'T' (0x54) > ' ' (0x20), a space-format cursor makes EVERY newer
        message look unprocessed → the agent is re-triggered forever (capped
        only by the rate limiter). Canonicalise to ISO-8601 so both sides match.

        Only ever moves FORWARD (the guarded UPDATE below, twin of ``ack_read``).
        This used to be an unconditional write, safe only while every caller
        passed the batch's own high-water mark. Live steering broke that: while a
        turn runs, ``_route_steer`` acks the cursor forward to a steered message
        NEWER than the turn's trigger batch, and the turn's own ack at its end
        passes the OLDER trigger high-water. An unconditional write there would
        pull the cursor BACK behind the steered messages, resurfacing them as a
        fresh turn (double delivery). The two acks now race harmlessly: whoever
        writes, the cursor only advances.
        """
        up_to_timestamp = canonical_ts(up_to_timestamp)
        ph = self._db.placeholder
        await self._db.execute_write(
            f"UPDATE bus_channel_members SET last_processed_at = {ph} "
            f"WHERE agent_id = {ph} AND channel_id = {ph} "
            f"AND (last_processed_at IS NULL OR last_processed_at < {ph})",
            (up_to_timestamp, agent_id, channel_id, up_to_timestamp),
        )

    async def ack_read(
        self,
        agent_id: str,
        channel_id: str,
        up_to_timestamp: str,
    ) -> None:
        """Mark everything up to a timestamp as SEEN by this agent.

        The twin of ``ack_processed``, on the other cursor. ``last_processed_at``
        says the trigger drove this agent past a point; ``last_read_at`` says the
        agent was actually shown what was there. Only the second one gates the
        unread list that rides every turn's context, which is why they cannot be
        merged (``inbox.py`` merged them once and the result was a room that
        showed 0 unread while accumulating hundreds).

        Timestamp canonicalisation goes through ``canonical_ts``, which both
        cursors share — see its docstring for the hazard it exists to close.

        Only ever moves forward — as does ``ack_processed`` now (see its
        docstring): both are guarded because live steering made ``ack_processed``
        callable with a value behind the current cursor, and a cursor that can be
        pulled backwards would resurface messages the agent has already read.
        """
        up_to_timestamp = canonical_ts(up_to_timestamp)
        ph = self._db.placeholder
        await self._db.execute_write(
            f"UPDATE bus_channel_members SET last_read_at = {ph} "
            f"WHERE agent_id = {ph} AND channel_id = {ph} "
            f"AND (last_read_at IS NULL OR last_read_at < {ph})",
            (up_to_timestamp, agent_id, channel_id, up_to_timestamp),
        )

    async def record_failure(
        self,
        message_id: str,
        agent_id: str,
        error: str,
    ) -> None:
        """Record a delivery failure, incrementing retry_count."""
        now = _now_iso()
        existing = await self._db.get_one("bus_message_failures", {
            "message_id": message_id,
            "agent_id": agent_id,
        })
        if existing:
            await self._db.update(
                "bus_message_failures",
                {"message_id": message_id, "agent_id": agent_id},
                {
                    "retry_count": existing["retry_count"] + 1,
                    "last_error": error,
                    "last_retry_at": now,
                },
            )
        else:
            await self._db.insert("bus_message_failures", {
                "message_id": message_id,
                "agent_id": agent_id,
                "retry_count": 1,
                "last_error": error,
                "last_retry_at": now,
            })

    async def get_failure_count(
        self,
        message_id: str,
        agent_id: str,
    ) -> int:
        """Get the number of delivery failures for a message/agent pair."""
        row = await self._db.get_one("bus_message_failures", {
            "message_id": message_id,
            "agent_id": agent_id,
        })
        if row is None:
            return 0
        return row["retry_count"]

    # ===== Channel Membership & Agent Profile =====

    async def get_channel_members(self, channel_id: str) -> List[BusChannelMember]:
        """Get all members of a channel."""
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT * FROM bus_channel_members WHERE channel_id = {ph}",
            (channel_id,),
        )
        return [BusChannelMember(
            channel_id=row["channel_id"],
            agent_id=row["agent_id"],
            joined_at=row.get("joined_at"),
            last_read_at=row.get("last_read_at"),
            last_processed_at=row.get("last_processed_at"),
        ) for row in rows]

    async def kick_member(self, channel_id: str, agent_id: str) -> None:
        """Remove a member from a channel."""
        await self._db.delete("bus_channel_members", {
            "channel_id": channel_id,
            "agent_id": agent_id,
        })

    async def get_agent_profile(self, agent_id: str) -> Optional[BusAgentInfo]:
        """Get a single agent's profile from the registry."""
        row = await self._db.get_one("bus_agent_registry", {"agent_id": agent_id})
        if row is None:
            return None
        caps_raw = row.get("capabilities", "[]")
        caps = json.loads(caps_raw) if isinstance(caps_raw, str) else (caps_raw or [])
        return BusAgentInfo(
            agent_id=row["agent_id"],
            owner_user_id=row.get("owner_user_id", ""),
            capabilities=caps,
            description=row.get("description", ""),
            visibility=row.get("visibility", "private"),
            registered_at=row.get("registered_at"),
            last_seen_at=row.get("last_seen_at"),
        )
