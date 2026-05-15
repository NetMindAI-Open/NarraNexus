"""
@file_name: channel_inbox_writer.py
@date: 2026-05-08
@description: Generic Inbox writer for IM channel triggers.

Generalisation of ``LarkTrigger._write_to_inbox`` + ``_ensure_inbox_entities``.
Every IM channel writes inbound + outbound messages to the same
MessageBus tables (``bus_agent_registry``, ``bus_channels``,
``bus_channel_members``, ``bus_messages``) so the frontend Inbox surfaces
a unified history regardless of source platform.

Synthetic IDs are channel-prefixed so different IM channels do not collide:

    channel_id        = f"{channel}_{chat_id}"            # "lark_oc_abc"
    pseudo_agent_id   = f"{channel}_user_{sender_id}"     # "slack_user_U123"
    bus_channels.name = f"{brand_display}: {display_name}"  # "Slack: Alice"

Idempotency: pseudo-agent / channel / member rows use a get-then-insert
pattern (not INSERT IGNORE) because the underlying
``AsyncDatabaseClient`` API doesn't expose dialect-specific upsert
without hand-written SQL.
"""
from __future__ import annotations

import uuid

from loguru import logger

from xyz_agent_context.utils.timezone import utc_now


class ChannelInboxWriter:
    """Writes IM events to the MessageBus tables."""

    def __init__(self, channel: str, brand_display: str):
        """
        Args:
            channel: lowercase key, e.g. "lark", "slack", "telegram".
                Used to prefix synthetic channel_id and pseudo agent_id.
            brand_display: human-readable label, e.g. "Feishu", "Slack".
                Used in bus_channels.name and bus_agent_registry.capabilities.
        """
        if not channel:
            raise ValueError("channel must be a non-empty string")
        if not brand_display:
            raise ValueError("brand_display must be a non-empty string")
        self._channel = channel
        self._brand_display = brand_display

    @property
    def channel(self) -> str:
        return self._channel

    async def write(
        self,
        *,
        db,
        agent_id: str,
        sender_id: str,
        sender_name: str,
        original_message: str,
        agent_response: str,
        chat_id: str,
    ) -> None:
        """
        Write the standard 5-row inbox bundle.

        1. Ensure pseudo-agent (the human sender) exists.
        2. Ensure channel exists.
        3. Ensure agent membership exists.
        4. Insert incoming message (from pseudo-agent).
        5. Insert agent response — only if non-empty.

        Failures are caught and logged; the trigger's hot path must
        continue serving even if Inbox write fails. The trigger's
        audit repo will record EVENT_INBOX_WRITE_FAILED on top.

        Caller injects ``db`` (an ``AsyncDatabaseClient`` handle). The
        writer never imports ``get_db_client`` — the trigger already
        holds a handle.
        """
        try:
            now = utc_now()
            channel_id = f"{self._channel}_{chat_id}"
            display_name = sender_name if sender_name and sender_name != "Unknown" else sender_id
            channel_name = f"{self._brand_display}: {display_name}"

            await self._ensure_entities(
                db,
                agent_id=agent_id,
                sender_id=sender_id,
                sender_name=sender_name,
                display_name=display_name,
                channel_id=channel_id,
                channel_name=channel_name,
                now=now,
            )

            # 4. Inbound — from the pseudo-agent representing the IM sender.
            await db.insert("bus_messages", {
                "message_id": f"{self._channel}_in_{uuid.uuid4().hex[:12]}",
                "channel_id": channel_id,
                "from_agent": f"{self._channel}_user_{sender_id}",
                "content": original_message,
                "msg_type": "text",
                "created_at": now,
            })

            # 5. Outbound — only when the agent actually replied.
            if agent_response and agent_response.strip():
                await db.insert("bus_messages", {
                    "message_id": f"{self._channel}_out_{uuid.uuid4().hex[:12]}",
                    "channel_id": channel_id,
                    "from_agent": agent_id,
                    "content": agent_response,
                    "msg_type": "text",
                    "created_at": now,
                })

            logger.info(
                f"ChannelInboxWriter[{self._channel}]: wrote to inbox channel {channel_id}"
            )
        except Exception as e:  # noqa: BLE001 — caller handles audit
            logger.warning(
                f"ChannelInboxWriter[{self._channel}].write failed: "
                f"{type(e).__name__}: {e}"
            )
            raise

    async def _ensure_entities(
        self,
        db,
        *,
        agent_id: str,
        sender_id: str,
        sender_name: str,
        display_name: str,
        channel_id: str,
        channel_name: str,
        now: str,
    ) -> None:
        """Idempotent upsert of pseudo-agent, channel, membership rows."""
        pseudo_agent_id = f"{self._channel}_user_{sender_id}"

        # Pseudo-agent row representing the human IM sender.
        existing_agent = await db.get_one(
            "bus_agent_registry", {"agent_id": pseudo_agent_id}
        )
        if not existing_agent:
            await db.insert("bus_agent_registry", {
                "agent_id": pseudo_agent_id,
                "owner_user_id": "",
                "capabilities": f"{self._brand_display} user",
                "description": display_name,
                "visibility": "public",
                "registered_at": now,
            })
        elif (
            sender_name
            and sender_name != "Unknown"
            and existing_agent.get("description") != sender_name
        ):
            # Sender previously seen with a placeholder name; refresh it.
            await db.update(
                "bus_agent_registry",
                {"agent_id": pseudo_agent_id},
                {"description": sender_name},
            )

        existing_channel = await db.get_one("bus_channels", {"channel_id": channel_id})
        if not existing_channel:
            await db.insert("bus_channels", {
                "channel_id": channel_id,
                "name": channel_name,
                "channel_type": "direct",
                "created_by": agent_id,
                "created_at": now,
            })

        existing_member = await db.get_one(
            "bus_channel_members",
            {"channel_id": channel_id, "agent_id": agent_id},
        )
        if not existing_member:
            await db.insert("bus_channel_members", {
                "channel_id": channel_id,
                "agent_id": agent_id,
                "joined_at": now,
            })
