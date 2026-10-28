"""
@file_name: wechat_context_builder.py
@author:
@date: 2026-06-24
@description: Build execution context for WeChat (iLink) triggered messages.

Mirrors ``telegram_module/telegram_context_builder.py``. iLink, like Telegram's
Bot API, exposes no server-side history endpoint, so history falls back to the
local inbox record that ``InboxRecorder`` populates under
``thread_id = im_thread_id("wechat", agent_id, to_user_id)``.

Note this is the ONE place the inbox record is also OPERATIONAL: for channels
with no history API it is the agent's conversation memory, not just something a
person reads. The 2026-08-17 decoupling therefore moved this reader with the
writer — leaving it on ``bus_messages`` would have made WeChat forget every
conversation.

The reply contract: the agent replies by calling the ``wechat_send`` MCP tool
with the inbound ``to_user_id`` + ``context_token`` (surfaced here in
``reply_instruction``). v1 is DM-only (personal account, 1:1).
"""
from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from narranexus.platform.channel.channel_prompts import (
    ROOM_TYPE_DIRECT,
)
from narranexus.platform.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from narranexus.platform.schema.parsed_message import ParsedMessage

from ._wechat_credential_manager import WeChatCredential


class WeChatContextBuilder(ChannelContextBuilderBase):
    """WeChat-specific context builder."""

    def __init__(
        self,
        message: ParsedMessage,
        credential: WeChatCredential,
        agent_id: str,
        db_client: Any = None,
    ):
        self._message = message
        self._credential = credential
        self._agent_id = agent_id
        self._db = db_client

    async def get_message_info(self) -> Dict[str, Any]:
        to_user_id = self._message.chat_id
        context_token = (self._message.raw or {}).get("context_token", "") or ""
        reply_instruction = (
            f'call `wechat_send(to_user_id="{to_user_id}", '
            f'context_token="{context_token}", text="YOUR_REPLY")`. Send exactly '
            f"ONE message. Use plain text — WeChat has no markdown rendering, so "
            f"asterisks / backticks show up literally."
        )
        return {
            "agent_id": self._agent_id,
            "channel_display_name": "WeChat",
            "channel_key": "wechat",
            "room_name": "",
            "room_id": to_user_id,
            "room_type": ROOM_TYPE_DIRECT,  # v1: personal-account DM only
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.bot_wx_id,
            "message_body": self._message.content,
            "send_tool_name": "wechat_send",
            "reply_instruction": reply_instruction,
        }

    def reply_kwargs(self) -> Dict[str, Any]:
        """iLink addresses a conversation by ``to_user_id`` + a per-inbound
        ``context_token``; the registered sender needs the token as a kwarg.

        Surfaced so the platform-side no-reply fallback can deliver this
        turn's reply without a tool call from the model (see
        ``step_3_agent_loop`` IM DM fallback). Empty token = no fallback
        delivery, which is the honest degradation: iLink would reject the
        send anyway.
        """
        return {"context_token": (self._message.raw or {}).get("context_token", "") or ""}

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Read recent turns from the local inbox record (no iLink history API)."""
        if not self._db or not self._message.chat_id:
            return []

        from narranexus.platform.channel.inbox_recorder import (
            OUTBOUND,
            im_thread_id,
        )

        thread_id = im_thread_id("wechat", self._agent_id, self._message.chat_id)
        fetch_n = max(limit + 5, 10)
        try:
            rows = await self._db.get(
                "inbox_thread_messages",
                {"thread_id": thread_id},
                limit=fetch_n,
                order_by="created_at DESC",
            )
        except Exception as e:  # noqa: BLE001 — history is best-effort
            logger.warning(
                f"[wechat:{self._agent_id}] history fetch failed "
                f"(thread={thread_id}): {type(e).__name__}: {e}"
            )
            return []

        if not rows:
            # DEPLOY-WINDOW FALLBACK — remove after the inbox backfill runs
            # (reference/self_notebook/todo/2026-08-17-inbox-backfill-runbook.md).
            # iLink has no history API, so the record IS the agent's memory, and
            # pre-decouple turns still live in `bus_messages`. Without this every
            # existing WeChat bot forgets its whole history on cutover.
            return await self._legacy_bus_history(limit)

        current_id = self._message.message_id
        normalized: List[Dict[str, Any]] = []
        for row in reversed(rows):
            content = row.get("content", "") or ""
            row_msg_id = row.get("message_id", "") or ""
            if current_id and row_msg_id == current_id:
                continue
            # `direction` says whose line this is directly — the old form
            # compared `from_agent` against the agent id, which the record
            # layer no longer stores as a bus sender.
            is_bot = row.get("direction") == OUTBOUND
            sender = "Me (bot)" if is_bot else (
                self._message.sender_name or row.get("sender_name") or ""
            )
            normalized.append({
                "timestamp": str(row.get("created_at", "")),
                "sender": sender,
                "body": content,
            })

        if len(normalized) > limit:
            normalized = normalized[-limit:]
        return normalized

    async def _legacy_bus_history(self, limit: int) -> List[Dict[str, Any]]:
        """Deploy-window fallback: read pre-decouple turns from ``bus_messages``.

        Remove once the inbox backfill has run (see
        reference/self_notebook/todo/2026-08-17-inbox-backfill-runbook.md — the
        Telegram builder has an IDENTICAL copy; the runbook's delete step must
        remove BOTH). The old writer stored turns under
        ``channel_id = f"wechat_{chat_id}"`` with
        ``from_agent`` = ``wechat_user_<sender_id>`` (inbound) or this agent's id
        (outbound). AGENT-ISOLATED: only this bot's own replies and user messages
        are read; another bot's reply in the same shared ``wechat_{chat_id}``
        channel (``from_agent`` = a different agent id) is excluded — feeding it
        to this agent would be worse than forgetting. ``wipe_service`` deletes
        these rows for sole-member channels (a WeChat DM is one — only the agent
        is a member), so a cleared agent reads empty here too.
        """
        channel_id = f"wechat_{self._message.chat_id}"
        fetch_n = max(limit + 5, 10)
        try:
            rows = await self._db.get(
                "bus_messages",
                {"channel_id": channel_id},
                limit=fetch_n,
                order_by="created_at DESC",
            )
        except Exception as e:  # noqa: BLE001 — history is best-effort
            logger.warning(
                f"[wechat:{self._agent_id}] legacy history fetch failed "
                f"(channel={channel_id}): {type(e).__name__}: {e}"
            )
            return []

        current_id = self._message.message_id
        out: List[Dict[str, Any]] = []
        for row in reversed(rows):
            from_agent = row.get("from_agent", "") or ""
            if from_agent == self._agent_id:
                is_bot = True
            elif from_agent.startswith("wechat_user_"):
                is_bot = False
            else:
                continue  # another agent's reply in a shared chat — not ours
            row_msg_id = row.get("message_id", "") or ""
            if current_id and row_msg_id == current_id:
                continue
            content = row.get("content", "") or ""
            sender = "Me (bot)" if is_bot else (
                self._message.sender_name or ""
            )
            out.append({
                "timestamp": str(row.get("created_at", "")),
                "sender": sender,
                "body": content,
            })
        if len(out) > limit:
            out = out[-limit:]
        return out

    async def get_room_members(self) -> List[Dict[str, Any]]:
        # v1 is 1:1 DM — no member enumeration.
        return []
