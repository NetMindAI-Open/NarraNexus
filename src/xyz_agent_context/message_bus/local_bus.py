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

from xyz_agent_context.message_bus.message_bus_service import MessageBusService
from xyz_agent_context.message_bus.schemas import BusAgentInfo, BusChannelMember, BusMessage
from xyz_agent_context.utils.db_backend import DatabaseBackend


def _generate_id(prefix: str) -> str:
    """Generate a short random ID with the given prefix."""
    return f"{prefix}_{secrets.token_hex(4)}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


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
        return BusMessage(
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            from_agent=row["from_agent"],
            content=row["content"],
            msg_type=row.get("msg_type", "text"),
            mentions=mentions,
            attachments=attachments,
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
    ) -> str:
        """Send a message to a channel and return the generated message_id."""
        msg_id = _generate_id("msg")
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
            "created_at": _now_iso(),
        })
        # Index the message into the unified search layer (memory_bus), under the
        # sender, pointing back to the message. Append-only — bus is objective
        # message history (like chat); no update/dedup (design §10-C). Recipient-
        # side recall of INBOUND messages is largely covered by the per-turn
        # interaction index (a bus message that triggers a turn becomes that
        # turn's input); per-recipient fan-out is a possible follow-up.
        try:
            from loguru import logger
            from xyz_agent_context.memory import MemoryEngine
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

    async def get_unread(self, agent_id: str) -> List[BusMessage]:
        """Get all unread messages for an agent across all channels."""
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT m.* FROM bus_messages m "
            f"JOIN bus_channel_members cm ON m.channel_id = cm.channel_id "
            f"WHERE cm.agent_id = {ph} "
            f"AND m.created_at > COALESCE(cm.last_read_at, '1970-01-01') "
            f"ORDER BY m.created_at ASC",
            (agent_id,),
        )
        return [self._row_to_message(row) for row in rows]

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

    async def send_to_agent(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        msg_type: str = "text",
        attachments: Optional[List[dict]] = None,
    ) -> str:
        """Send a direct message to another agent, auto-creating a DM channel if needed."""
        ph = self._db.placeholder

        # Same-user boundary: an agent may only DM agents owned by the same
        # user. Cross-user direct messaging is intentionally disabled — never
        # let an agent message another user's agent.
        from_owner = await self._agent_owner(from_agent)
        to_owner = await self._agent_owner(to_agent)
        if from_owner and to_owner and from_owner != to_owner:
            raise PermissionError(
                f"cross-user messaging is not allowed: {from_agent} cannot "
                f"message {to_agent} (different owners)"
            )

        # Find existing direct channel between these two agents
        rows = await self._db.execute(
            f"SELECT c.channel_id FROM bus_channels c "
            f"JOIN bus_channel_members m1 ON c.channel_id = m1.channel_id AND m1.agent_id = {ph} "
            f"JOIN bus_channel_members m2 ON c.channel_id = m2.channel_id AND m2.agent_id = {ph} "
            f"WHERE c.channel_type = 'direct'",
            (from_agent, to_agent),
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
            from_agent, channel_id, content, msg_type, attachments=attachments
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
        limit: int = 50,
    ) -> List[BusMessage]:
        """
        Get messages that have not been processed by the agent.

        Uses the cursor model and filters out self-sent messages
        and poison messages (failure_count >= 3).
        """
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT m.* FROM bus_messages m "
            f"JOIN bus_channel_members cm ON m.channel_id = cm.channel_id "
            f"WHERE cm.agent_id = {ph} "
            f"AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01') "
            f"AND m.from_agent != {ph} "
            f"ORDER BY m.created_at ASC "
            f"LIMIT {int(limit)}",
            (agent_id, agent_id),
        )

        # Filter out poison messages (failure_count >= 3)
        result = []
        for row in rows:
            failure_count = await self.get_failure_count(row["message_id"], agent_id)
            if failure_count < 3:
                result.append(self._row_to_message(row))
        return result

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
        """
        if hasattr(up_to_timestamp, "isoformat"):
            up_to_timestamp = up_to_timestamp.isoformat()
        await self._db.update(
            "bus_channel_members",
            {"agent_id": agent_id, "channel_id": channel_id},
            {"last_processed_at": up_to_timestamp},
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
