"""
@file_name: wechat_module.py
@author:
@date: 2026-06-24
@description: WeChat channel module — subclass of ChannelModuleBase.

Personal-account WeChat via the iLink ("ClawBot") gateway. Architecturally
closest to Telegram (long-poll, single token), so this mirrors
``telegram_module.py``. Deltas:
  1. Binding is a QR-scan flow (Channels panel → backend/routes/wechat.py), not
     a token paste — so there is no ``wechat_bind`` MCP tool.
  2. The agent replies via ``wechat_send(to_user_id, context_token, text)``.
  3. Owner identity is the peer's wxid, claimed on first DM (opaque at bind).
  4. v1 is DM-only (personal account, 1:1).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.channel import ChannelModuleBase
from xyz_agent_context.channel.message_source_handler import (
    MessageSourceHandler,
    MessageSourceRegistry,
)
from xyz_agent_context.schema import ContextData, ModuleConfig, WorkingSource

from ._wechat_credential_manager import WeChatCredential, WeChatCredentialManager
from ._wechat_mcp_tools import register_wechat_mcp_tools
from .wechat_sdk_client import send_text_once

# MCP port. 7833=NarraMessenger, 7834=Discord (moved there on dev to clear a
# NarraMessenger clash), so WeChat takes 7835.
WECHAT_MCP_PORT = 7835


def _extract_wechat_reply(tool_name: str, arguments: dict) -> Optional[str]:
    """Extract the user-visible reply text from a WeChat agent tool call.

    Recognises ``wechat_send(text=...)`` (the canonical reply path) and the
    generic ``send_message_to_user_directly(content=...)``. Returns None when
    the call isn't a user reply (so it isn't logged as "Background activity").
    """
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:  # noqa: BLE001
            args = {}
    if not isinstance(args, dict):
        return None
    if "send_message_to_user_directly" in (tool_name or ""):
        return args.get("content", "") or None
    if "wechat_send" in (tool_name or ""):
        return args.get("text", "") or "(sent via wechat_send)"
    return None


# Register at import time (idempotent — mirrors telegram_module).
try:
    MessageSourceRegistry.register(MessageSourceHandler(
        name="wechat",
        user_reply_tool_names=("wechat_send", "send_message_to_user_directly"),
        row_prefix_template="[WeChat · {sender_name} · {sender_id}]",
        extract_reply_fn=_extract_wechat_reply,
    ))
except ValueError:
    pass


_NO_BIND_INSTRUCTION = """\
## WeChat Integration  (no account bound yet)

To connect a personal WeChat account, the user binds it from the **Channels**
panel: click **WeChat → Connect**, a login QR appears, the user scans it in
WeChat (Me → scan). Once confirmed, this agent receives WeChat DMs and can
reply. There is nothing for you to configure here — direct the user to the
Channels panel if they ask how to connect WeChat.
"""

_WECHAT_IRON_RULES = """\
### Iron rules

1. Reply with **plain text only** — WeChat renders no markdown, so `*`,
   backticks and `#` show up literally. No code fences, no bold markers.
2. Send **exactly one** message per turn via `wechat_send`. Don't spam.
3. v1 is **1:1 DM only** (a personal account). There are no groups here.
"""


class WeChatModule(ChannelModuleBase):
    """WeChat (iLink) channel module."""

    channel_name = "wechat"
    brand_display = "WeChat"
    working_source = WorkingSource.WECHAT
    ctx_data_key = "wechat_info"
    mcp_server_name = "wechat_module"
    mcp_port = WECHAT_MCP_PORT

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="WeChatModule",
            priority=7,
            enabled=True,
            description="WeChat channel integration (iLink long-poll, personal account).",
            module_type="capability",
        )

    async def get_credential(self, agent_id: str) -> Optional[WeChatCredential]:
        if not self.db:
            return None
        return await WeChatCredentialManager(self.db).get(agent_id)

    async def send_to_agent(
        self, agent_id: str, target_id: str, message: str, **kwargs
    ) -> dict[str, Any]:
        """Sender registered in ChannelSenderRegistry. ``target_id`` is the
        peer's WeChat user id. iLink needs a ``context_token`` to address the
        conversation; pass it via kwargs (the reply path always has it)."""
        cred = await self.get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "no WeChat credential bound"}
        context_token = kwargs.get("context_token", "") or ""
        ok = await send_text_once(
            cred.bot_token, cred.base_url, target_id, context_token, message
        )
        return {"success": True} if ok else {"success": False, "error": "send_failed"}

    def register_mcp_tools(self, mcp) -> None:
        register_wechat_mcp_tools(mcp)

    async def get_instructions(self, ctx_data: ContextData) -> str:
        info = ctx_data.extra_data.get(self.ctx_data_key)
        if not info:
            return _NO_BIND_INSTRUCTION + _WECHAT_IRON_RULES

        owner_wx_id = info.get("owner_wx_id", "")
        current_sender_id = info.get("current_sender_id", "")
        is_owner_interacting = bool(info.get("is_owner_interacting"))

        if owner_wx_id:
            if is_owner_interacting:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"The current WeChat sender (`{current_sender_id}`) **is** your "
                    f"owner. You may surface owner-private context when relevant."
                )
            else:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"Your owner's WeChat id is `{owner_wx_id}`. The current sender "
                    f"(`{current_sender_id}`) is **NOT** the owner — treat as a "
                    f"visitor. Never disclose owner-private context or impersonate "
                    f"the owner."
                )
        else:
            trust_block = (
                "### Trust signal\n\n"
                "No owner has been claimed yet for this WeChat binding (the owner "
                "claims it by sending the first DM). Treat the current sender as "
                "untrusted until then."
            )

        return f"""\
## WeChat Integration  (Reply on WeChat)

You are connected to a personal WeChat account (via the iLink gateway).

{trust_block}

### Replying

To reply, call `wechat_send(to_user_id, context_token, text)`:
  - `to_user_id` + `context_token`: from the inbound message context (room_id /
    the message's context_token). Use them verbatim.
  - `text`: your reply.

{_WECHAT_IRON_RULES.strip()}
"""

    async def build_extra_data(
        self, cred: WeChatCredential, ctx_data: ContextData
    ) -> dict[str, Any]:
        current_sender_id = ""
        ct = ctx_data.extra_data.get("channel_tag") or {}
        if isinstance(ct, dict):
            current_sender_id = ct.get("sender_id", "") or ""
        is_owner_interacting = bool(
            cred.owner_wx_id and current_sender_id and current_sender_id == cred.owner_wx_id
        )
        return {
            "bot_wx_id": cred.bot_wx_id,
            "owner_wx_id": cred.owner_wx_id,
            "owner_user_id": cred.owner_user_id,
            "current_sender_id": current_sender_id,
            "is_owner_interacting": is_owner_interacting,
            "enabled": cred.enabled,
        }


logger.debug(f"WeChatModule defined (MCP port {WECHAT_MCP_PORT})")
