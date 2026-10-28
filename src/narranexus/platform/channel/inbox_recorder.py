"""
@file_name: inbox_recorder.py
@author:
@date: 2026-08-17
@description: Records a conversation turn into the inbox — the platform's own
record of conversations the user was not in.

Replaces ``ChannelInboxWriter``, which wrote a five-row bundle into the
MessageBus tables (pseudo-agent, channel, membership, inbound, reply). Two
things that cost, both measured on prod 2026-08-17:

* 86% of ``bus_messages`` (28,605 of 33,164 rows) was IM inbox content. The
  bus's own table was mostly another feature's storage.
* The membership row it created had a ``last_read_at`` nothing ever advanced —
  159 of 172 IM memberships (92%) sat at NULL. The bus unread predicate is
  ``created_at > COALESCE(last_read_at, epoch)``, so 1,364 IM messages were
  permanently "unread" and rode into 90 agents' turn context, attributed to
  pseudo-agents like ``lark_user_<id>`` that no Source-Recognition rule
  describes.

The rows now live in ``inbox_threads`` / ``inbox_thread_messages``, which the
agent's unread injection does not read at all — so for everything written from
2026-08-17 on, the containment IS structural: there is no row in
``bus_messages`` to leak.

For rows written BEFORE that, it is a filter, and saying otherwise was the
comment's mistake. Every deployed database still holds the IM history the old
writer put in ``bus_messages``, and `_unread_predicate` would still hand it to the
model — so `LocalMessageBus._unread_predicate` excludes the dedicated-trigger channel
prefixes. That is honestly a prefix filter, of the same kind that drifted in
2026-07-03; what makes it survivable is that it is derived from the registry
rather than hand-maintained, and that it is temporary: it retires when the legacy
rows are purged. `reference/self_notebook/todo/2026-08-17-inbox-backfill-runbook.md`
owns that step.

WHAT THIS IS NOT: the notification store. ``InboxRepository`` / ``inbox_table``
is a different feature that shares the word — system alerts the platform pushes
at an owner. Nothing here touches it.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Optional, Sequence

from loguru import logger

from narranexus.platform.schema.entity_schema import ENTITY_NAME_MAX_LEN
from narranexus.platform.schema.parsed_message import UNKNOWN_SENDER_NAME, ChatType
from narranexus.platform.utils import utc_now

#: Thread-id family prefixes. The family comes first so the namespace says WHAT
#: a row is before it says which one — and so any residual filter is trivially
#: correct rather than a list of channel names that can go stale.
IM_THREAD_PREFIX = "im_"
AGENT_DM_THREAD_PREFIX = "nx_dm_"

#: Direction of a row within a turn.
INBOUND = "in"
OUTBOUND = "out"


def im_thread_id(channel: str, agent_id: str, chat_id: str) -> str:
    """`im_lark_<agent>_<chat_id>` — one thread per (agent, IM conversation).

    The agent id is part of the KEY, not just the row's `agent_id` column, for the
    same reason `agent_dm_thread_id` carries both: one owner can point several
    agents at the same conversation, and the panel lists per agent.

    Without it the column was set once at creation and never updated, so a second
    agent's messages appended to the first agent's thread and the second agent's
    inbox was empty. Reachable rather than theoretical: a Telegram private chat's
    `chat_id` is the USER's id, identical across bots, so one person DMing two of
    an owner's agents collided. The writer this replaced created a
    `bus_channel_members` row per agent, so both sides were visible — this was a
    regression, not an inherited gap.

    It also makes the `agent_id` column trustworthy for anything that scopes BY
    agent — `wipe_service`'s "clear this agent's conversations" would otherwise
    delete another agent's record.
    """
    return f"{IM_THREAD_PREFIX}{channel}_{agent_id}_{chat_id}"


def agent_dm_thread_id(agent_id: str, peer_agent_id: str) -> str:
    """`nx_dm_<agent>_<peer>` — one thread per (agent, peer) pair.

    Both ids, not just the peer: the same owner can have several agents talking
    to the same peer, and the panel lists per agent.
    """
    return f"{AGENT_DM_THREAD_PREFIX}{agent_id}_{peer_agent_id}"


class InboxRecorder:
    """Writes one conversational turn into the inbox record.

    Stateless apart from the source name, so a caller can hold one per channel
    or build one per call. The INBOX write uses the injected db handle — that
    write never reaches for ``get_db_client``, keeping the caller's
    transaction/handle choices its own (the same reason ``ChannelInboxWriter``
    took one). The best-effort REACH write (``_record_reach``) is the one
    exception: it goes through the ``module/data_access`` seam, which resolves
    its own handle — today the same process singleton, and after P2 flips
    ``NARRANEXUS_BACKEND_URL`` an outbound HTTP call from this path.
    """

    def __init__(self, source: str, brand_display: str = "") -> None:
        """
        Args:
            source: matches ``MessageSourceHandler.name`` for IM sources
                ("lark", "wechat", …) or "agent_dm" for peer conversation.
            brand_display: human label used in the thread title ("Feishu").
                Defaults to a title-cased source when empty.
        """
        if not source:
            raise ValueError("source must be a non-empty string")
        self._source = source
        self._brand = brand_display or source.title()

    @property
    def source(self) -> str:
        return self._source

    async def record_turn(
        self,
        *,
        db,
        thread_id: str,
        owner_user_id: str,
        agent_id: str,
        counterpart_id: str,
        counterpart_name: str,
        inbound_text: str,
        outbound_text: str = "",
        inbound_attachments: Optional[Sequence[dict]] = None,
        outbound_attachments: Optional[Sequence[dict]] = None,
        chat_id: str = "",
        chat_type: Optional[ChatType] = None,
    ) -> None:
        """Record one turn: what arrived, and what the agent said back.

        Best-effort at the caller's discretion — this re-raises so the caller
        can write its own audit row (``EVENT_INBOX_WRITE_FAILED``), which is
        the contract ``ChannelInboxWriter`` had and the triggers still rely on.

        The outbound row is written only when the agent actually said
        something: a turn that legitimately stayed silent should not leave an
        empty bubble in the user's panel.
        """
        now = utc_now()
        display = counterpart_name if counterpart_name and counterpart_name != UNKNOWN_SENDER_NAME else counterpart_id

        await self._ensure_thread(
            db,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            counterpart_id=counterpart_id,
            counterpart_name=display,
            now=now,
        )

        # Inbound and reply are ONE turn but two rows that must sort
        # inbound-then-reply. Written together, a shared `now` gave them an
        # identical created_at, and `ORDER BY created_at` with no tie-break let
        # the reply sort above the message it answered — worst on WeChat, whose
        # messages carry no timestamp of their own. One microsecond apart means
        # created_at alone orders a turn, and utc_now()'s advance between turns
        # keeps turns in completion order.
        await self._insert_message(
            db, thread_id=thread_id, direction=INBOUND,
            sender_id=counterpart_id, sender_name=display,
            content=inbound_text, attachments=inbound_attachments, at=now,
        )
        if outbound_text and outbound_text.strip():
            await self._insert_message(
                db, thread_id=thread_id, direction=OUTBOUND,
                sender_id=agent_id, sender_name="",
                content=outbound_text,
                attachments=outbound_attachments,
                at=now + timedelta(microseconds=1),
            )
            preview = outbound_text
            last_at = now + timedelta(microseconds=1)
        else:
            preview = inbound_text
            # The inbound row's own timestamp, not the reply slot's. A silent turn
            # has no row at `now + 1µs`, so stamping the thread there put its
            # "last message" one microsecond after the only message it has — and
            # the panel sorts threads on this column, so a silent turn sorted
            # ahead of a turn that actually answered at the same instant.
            last_at = now

        await db.update(
            "inbox_threads",
            {"thread_id": thread_id},
            {
                "last_message_at": last_at,
                "last_message_preview": (preview or "")[:200],
                "updated_at": now,
            },
        )
        logger.info(f"InboxRecorder[{self._source}]: recorded turn in {thread_id}")

        # Reachability, recorded automatically. An inbound turn is proof this
        # agent CAN reach this counterpart on this channel, in this exact
        # conversation — so remember it on the counterpart's social entity, the
        # single home for "who I know and how to reach them" (Owner: no parallel
        # per-surface rosters). Kept out of the inbox write's own re-raise: a
        # failure to record reach must never lose the inbox row.
        await self._record_reach(
            agent_id=agent_id,
            counterpart_id=counterpart_id,
            counterpart_name=counterpart_name,
            chat_id=chat_id,
            chat_type=chat_type,
        )

    async def _record_reach(
        self,
        *,
        agent_id: str,
        counterpart_id: str,
        counterpart_name: str,
        chat_id: str,
        chat_type: Optional[ChatType],
    ) -> None:
        """Write "agent_id reaches counterpart_id on <source> via <chat_id>" onto
        the counterpart's social entity (`contact_info.channels`), through the
        social data store — no direct module import (binding rule #3), no LLM.

        ONLY records a 1:1 (``ChatType.PRIVATE``) conversation. A group's
        ``chat_id`` is the room, not a way to reach one person: recording it as
        "reach counterpart X" and then delivering a message meant for X to that
        id would post it to the whole group. So anything not PRIVATE is skipped,
        and the parsers make that a POSITIVE decision — they whitelist the one
        literal that means 1:1 and treat a group / topic / unknown type as GROUP
        — while ``record_turn``'s own ``chat_type`` defaults to ``None`` (also
        skipped). Both directions fail safe: an unconfirmed type records nothing
        rather than leaking.

        Best-effort: an address book is not worth a turn (same posture as the
        bus address book), so failure is logged, never raised. But the store's
        seam NEVER raises — it returns an in-band ``{"success": False}`` for a
        missing instance / rejected id / (post-P2) an auth failure — so the
        return value is checked too, or every real failure passes silently.
        """
        if chat_type != ChatType.PRIVATE or not chat_id or not counterpart_id or not agent_id:
            return
        try:
            from narranexus.platform.channel.channel_contact_utils import set_channel_info
            from narranexus.platform.module_system.data_access import get_agent_data_store

            contact = set_channel_info(
                {}, self._source, {"id": counterpart_id, "rooms": {agent_id: chat_id}}
            )
            updates: dict = {"contact_info": contact}
            # Name a first-contact / still-nameless entity — otherwise it stays
            # nameless and §3b's first step (search by name) cannot find it. The
            # store's create branch consumes `entity_name_if_new`, and its merge
            # branch is fill-if-empty (names an entity another path left
            # nameless), but a non-blank existing name is NEVER overwritten — so
            # a channel display name cannot clobber a canonical one. Truncated to
            # the column width (`entity_name` is VARCHAR(255) on MySQL); an
            # over-long name would otherwise fail the whole reach write with a 1406.
            if counterpart_name and counterpart_name != UNKNOWN_SENDER_NAME:
                updates["entity_name_if_new"] = counterpart_name[:ENTITY_NAME_MAX_LEN]
            res = await get_agent_data_store().extract_entity_info(
                agent_id=agent_id,
                entity_id=counterpart_id,
                updates=updates,
                update_mode="merge",
            )
            # The seam reports failure in-band, not by raising — surface it, or
            # the whole capability can silently no-op (e.g. no social instance,
            # or a post-P2 identity 401) with nothing in the logs.
            if isinstance(res, dict) and res.get("success") is False:
                logger.warning(
                    f"InboxRecorder[{self._source}]: reach not recorded "
                    f"(agent={agent_id}, counterpart={counterpart_id}): "
                    f"{res.get('message')}"
                )
        except Exception as e:  # noqa: BLE001 — reach is never worth a turn
            logger.warning(
                f"InboxRecorder[{self._source}]: reach recording failed "
                f"(agent={agent_id}, counterpart={counterpart_id}): {e}"
            )

    async def record_peer_message(
        self,
        *,
        db,
        owner_user_id: str,
        from_agent: str,
        from_name: str,
        to_agent: str,
        to_name: str,
        content: str,
        attachments: Optional[Sequence[dict]] = None,
    ) -> None:
        """Record ONE agent-to-agent message into BOTH agents' inbox threads.

        Recorded at SEND time, from the ``message_agent`` tool — the single
        place that holds "who sent what to whom". This is deliberate: on a peer
        DM the sender's ``turn.text`` is its monologue to its OWNER, never the
        text it sent the peer (a peer is reached only by the bus send tool), so
        the recipient's turn cannot supply the outbound. The tool call can.

        A2A messaging is same-owner only, so both threads share one owner. The
        sender's thread (``nx_dm_<from>_<to>``) gets an OUTBOUND row; the
        recipient's thread (``nx_dm_<to>_<from>``) gets an INBOUND row. Each
        agent's inbox thus shows the full round-trip: its own sends plus what
        the peer sent it.

        Re-raises on failure (like ``record_turn``): the caller decides whether
        an inbox miss is worth an audit row, and must not let it invert the
        outcome of the send that already succeeded.

        ``source_message_id`` is left NULL here on purpose. The column carries a
        UNIQUE index (one inbox row per source message), which fits IM's 1:1
        inbound→row mapping but NOT A2A's shape: one bus message becomes TWO
        rows (the sender's outbound and the recipient's inbound), so stamping
        both with the same bus id violates the constraint. A join back to
        ``bus_messages`` for A2A would need a different key; that is out of scope.
        """
        if not (content and content.strip()) and not attachments:
            return
        now = utc_now()
        # Sender's own thread: what it said to the peer.
        await self._record_one_way(
            db,
            thread_id=agent_dm_thread_id(from_agent, to_agent),
            owner_user_id=owner_user_id,
            agent_id=from_agent,
            counterpart_id=to_agent,
            counterpart_name=to_name or to_agent,
            direction=OUTBOUND,
            sender_id=from_agent,
            sender_name="",
            content=content,
            attachments=attachments,
            now=now,
        )
        # Recipient's thread: the peer's message arriving.
        await self._record_one_way(
            db,
            thread_id=agent_dm_thread_id(to_agent, from_agent),
            owner_user_id=owner_user_id,
            agent_id=to_agent,
            counterpart_id=from_agent,
            counterpart_name=from_name or from_agent,
            direction=INBOUND,
            sender_id=from_agent,
            sender_name=from_name or from_agent,
            content=content,
            attachments=attachments,
            now=now,
        )

    async def _record_one_way(
        self,
        db,
        *,
        thread_id: str,
        owner_user_id: str,
        agent_id: str,
        counterpart_id: str,
        counterpart_name: str,
        direction: str,
        sender_id: str,
        sender_name: str,
        content: str,
        attachments: Optional[Sequence[dict]],
        now,
    ) -> None:
        """Ensure the thread exists and append one directional message.

        Shares ``_ensure_thread`` / ``_insert_message`` with ``record_turn``;
        the difference is that a turn writes an inbound+reply pair while this
        writes a single message (each half of a peer DM is recorded at its own
        source, not as one turn)."""
        await self._ensure_thread(
            db,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            counterpart_id=counterpart_id,
            counterpart_name=counterpart_name,
            now=now,
        )
        await self._insert_message(
            db,
            thread_id=thread_id,
            direction=direction,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            attachments=attachments,
            at=now,
        )
        await db.update(
            "inbox_threads",
            {"thread_id": thread_id},
            {
                "last_message_at": now,
                "last_message_preview": (content or "")[:200],
                "updated_at": now,
            },
        )

    async def _ensure_thread(
        self,
        db,
        *,
        thread_id: str,
        owner_user_id: str,
        agent_id: str,
        counterpart_id: str,
        counterpart_name: str,
        now,
    ) -> None:
        """Create the thread, or refresh a placeholder name on an existing one.

        The name refresh is not cosmetic. A sender first seen with
        ``sender_name="Unknown"`` falls back to the raw id, and without this the
        panel would show that id forever — for the whole first burst of messages
        from every new contact.

        **The insert may lose a race, and losing is not an error.** `thread_id`
        is the primary key, so two turns opening the same NEW thread both read
        `None` and both insert; the loser used to raise, `record_turn` re-raised,
        and the caller booked `EVENT_INBOX_WRITE_FAILED` — a message simply
        missing from the user's panel. Reachable in ordinary use: debounce
        batches and multi-agent group chats deliver concurrently. So a duplicate
        key is treated as "someone else created it", which is what it means, and
        the refresh runs against their row. Same shape as `wake_signal.bump`.
        """
        title = f"{self._brand}: {counterpart_name}"

        async def _refresh(existing: dict) -> None:
            if counterpart_name and counterpart_name != existing.get("counterpart_name"):
                await db.update(
                    "inbox_threads",
                    {"thread_id": thread_id},
                    {"counterpart_name": counterpart_name, "title": title, "updated_at": now},
                )

        existing = await db.get_one("inbox_threads", {"thread_id": thread_id})
        if existing:
            await _refresh(existing)
            return
        try:
            await db.insert("inbox_threads", {
                "thread_id": thread_id,
                "owner_user_id": owner_user_id,
                "agent_id": agent_id,
                "source": self._source,
                "title": title,
                "counterpart_id": counterpart_id,
                "counterpart_name": counterpart_name,
                "created_at": now,
                "updated_at": now,
            })
        except Exception:  # noqa: BLE001 — narrowed by the re-read below
            # Re-read rather than inspect the driver's error: the duplicate-key
            # exception type differs between aiosqlite and aiomysql, and a
            # dialect-specific `except` here would silently stop catching the
            # race on one of the two backends. If the row is there, the race is
            # what happened; if it is not, the insert failed for a real reason
            # and the original exception is the honest thing to raise.
            raced = await db.get_one("inbox_threads", {"thread_id": thread_id})
            if raced is None:
                raise
            await _refresh(raced)

    async def _insert_message(
        self,
        db,
        *,
        thread_id: str,
        direction: str,
        sender_id: str,
        sender_name: str,
        content: str,
        attachments: Optional[Sequence[dict]],
        at,
        source_message_id: Optional[str] = None,
    ) -> None:
        await db.insert("inbox_thread_messages", {
            "message_id": f"ibx_{uuid.uuid4().hex[:16]}",
            "thread_id": thread_id,
            "direction": direction,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "attachments": json.dumps(list(attachments), ensure_ascii=False) if attachments else None,
            "source_message_id": source_message_id,
            "created_at": at,
        })


async def resolve_owner_for_agent(db, agent_id: str) -> str:
    """The owner a thread belongs to.

    Threads are listed per user, so the record needs the owner even though the
    triggers only hold an agent_id. Goes through the shared repository seam
    rather than a local query, so "who owns this agent" keeps one answer.
    """
    try:
        from narranexus.platform.repository.agent_repository import AgentRepository

        return await AgentRepository(db).resolve_owner(agent_id) or ""
    except Exception as e:  # noqa: BLE001 — a missing owner degrades the panel, not the turn
        logger.warning(f"InboxRecorder: owner lookup failed for {agent_id}: {e}")
        return ""


__all__ = [
    "InboxRecorder",
    "IM_THREAD_PREFIX",
    "AGENT_DM_THREAD_PREFIX",
    "INBOUND",
    "OUTBOUND",
    "im_thread_id",
    "agent_dm_thread_id",
    "resolve_owner_for_agent",
]
