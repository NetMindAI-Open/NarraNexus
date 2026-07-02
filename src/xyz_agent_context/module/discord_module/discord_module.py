"""
@file_name: discord_module.py
@date: 2026-06-16
@description: Discord channel module — subclass of ChannelModuleBase.

Discord setup is simple: create an application in the Developer Portal,
add a bot, enable the Message Content Intent, copy the token, invite the
bot to a server. One token, no OAuth scope dance at bind time.

The agent-facing surface is messaging-first: dedicated ``discord_send`` /
``discord_reply`` / ``discord_read_history`` tools plus binding
management — no generic REST dispatcher and no API-doc corpus. So
``get_instructions`` is compact: discovery mode when not bound,
operational mode (≤ 80 lines) when bound.

The single load-bearing manual step is the **Message Content Intent**:
without it Discord delivers message events with an EMPTY ``content``
field, so the bot "sees" messages but reads them as blank. The
instructions and the frontend DiscordConfig both call this out.
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
from xyz_agent_context.schema import (
    ContextData,
    ModuleConfig,
    WorkingSource,
)
from xyz_agent_context.schema.hook_schema import HookAfterExecutionParams

from ._discord_credential_manager import DiscordCredential, DiscordCredentialManager
from ._discord_mcp_tools import register_discord_mcp_tools
from .discord_sdk_client import DiscordSDKClient, DiscordSDKError


DISCORD_MCP_PORT = 7834


# ───────────────────────────────────────────────────────────────────────────
# MessageSourceRegistry handler — let ChatModule extract the actual reply a
# Discord agent emits, instead of dumping a "Background activity (discord)"
# placeholder. Symmetrical to _extract_slack_reply / _extract_lark_reply.
#
# Discord agents reply via ``discord_send(channel_id, text)`` or
# ``discord_reply(channel_id, message_id, text)``. Without this handler
# ChatModule.hook_after_event_execution treats every Discord turn as "no
# response" and persists an activity row that the next turn's
# hook_data_gathering then filters out — the agent would see zero history
# from prior Discord turns.
# ───────────────────────────────────────────────────────────────────────────


def _extract_discord_reply(tool_name: str, arguments: dict) -> Optional[str]:
    """Extract the user-visible reply text from a Discord agent tool call.

    Recognises ``discord_send`` / ``discord_reply`` / ``discord_dm`` (the
    text-sending paths) and the generic ``send_message_to_user_directly``
    (used when the agent also echoes to the NarraNexus UI). Returns
    ``None`` when the tool call isn't a user reply (e.g.
    ``discord_read_history`` / ``discord_list_channels``).
    """
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:  # noqa: BLE001 — malformed args, treat as no reply
            args = {}
    if not isinstance(args, dict):
        return None

    if "send_message_to_user_directly" in (tool_name or ""):
        content = args.get("content", "")
        return content or None

    tn = tool_name or ""
    if "discord_send" in tn or "discord_reply" in tn or "discord_dm" in tn:
        text = args.get("text", "")
        return text or "(sent via discord)"

    return None


try:
    MessageSourceRegistry.register(
        MessageSourceHandler(
            name="discord",
            user_reply_tool_names=(
                "discord_send",
                "discord_reply",
                "discord_dm",
                "send_message_to_user_directly",
            ),
            row_prefix_template="[Discord · {sender_name} · {sender_id} · {chat_id}]",
            extract_reply_fn=_extract_discord_reply,
        )
    )
except ValueError:
    # Re-import (test hot-reload, etc.) — handler already registered.
    pass


# ── Discovery prompt (no credential bound) ─────────────────────────────
_NO_BOT_INSTRUCTION = """\
## Discord Integration  (no bot bound yet)

This agent does NOT yet have a Discord bot bound. Walk the user through
this flow (~3 minutes):

### Step 1 — Create the application + bot
1. Open https://discord.com/developers/applications and click
   **New Application**. Name it, then open the **Bot** tab.
2. On the **Bot** page, scroll to **Privileged Gateway Intents** and
   turn ON **Message Content Intent**. THIS IS REQUIRED — without it the
   bot receives messages with an empty body and can't read what users say.
3. Click **Reset Token** → **Copy**. This is the bot token (keep it
   secret — it grants full bot access).

### Step 2 — Invite the bot to a server
1. Open the **OAuth2 → URL Generator** tab.
2. Under **Scopes** tick `bot`. Under **Bot Permissions** tick at least
   *View Channels*, *Send Messages*, *Read Message History*.
3. Open the generated URL, pick a server you manage, and authorize.

### Step 3 — Bind to this agent
The user can either paste the token in the dashboard (Awareness Panel →
IM Channels → Discord), OR send it to YOU and you call:

  discord_bind(bot_token="<token>", owner_user_id="<your numeric id>")

`owner_user_id` is optional but recommended: Discord → Settings →
Advanced → enable **Developer Mode**, then right-click your name → **Copy
User ID**. It lets the agent tell owner from stranger. I validate the
token via Discord's API and return the bot's identity on success.

### Iron rules during setup
- If the user reports "the bot sees messages but they're blank", the
  diagnosis is **Message Content Intent is OFF** — send them back to the
  Bot page to enable it. Do NOT debug the trigger or network.
- NEVER echo the token back in chat after binding. Treat it as a secret.
- If `discord_bind` returns ``unauthorized``, the token is wrong or was
  reset — ask the user to copy a fresh one. Don't retry blindly.
"""


# ── Iron rules (always appended) ───────────────────────────────────────
_DISCORD_IRON_RULES = """\

## Iron rules

1. **In servers you reply ONLY when @-mentioned in the current turn's
   inbound message.** Non-mention server messages are filtered out at the
   trigger boundary, so you should never see them. Do NOT proactively
   reply to messages you find in history via `discord_read_history` —
   those were either already handled or never addressed to you. **DMs are
   different** — there you reply naturally to every relevant message.
2. To reply, call `discord_reply(channel_id, message_id, text)` (inline
   reply, preferred) or `discord_send(channel_id, text)` (plain message).
   Send exactly ONE reply per turn.
3. Use standard markdown (`**bold**`, `*italic*`, `` `code` ``, ``` ```blocks``` ```).
   Discord renders it natively. Note: plain `[text](url)` links do NOT
   render in normal messages — paste the raw URL instead.
4. Replies over 2000 characters are split into multiple messages
   automatically; prefer concise replies anyway.
5. Never include the bot token in messages or logs.
6. Never bridge content from another channel into Discord unless the user
   explicitly asks you to.
"""


class DiscordModule(ChannelModuleBase):
    """Discord channel module."""

    # ── ChannelModuleBase contract ──────────────────────────────────────
    channel_name = "discord"
    brand_display = "Discord"
    working_source = WorkingSource.DISCORD
    ctx_data_key = "discord_info"
    mcp_server_name = "discord_module"
    mcp_port = DISCORD_MCP_PORT

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="DiscordModule",
            priority=6,
            enabled=True,
            description="Discord channel integration (Gateway receive + REST send).",
            module_type="capability",
        )

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def get_credential(self, agent_id: str) -> Optional[DiscordCredential]:
        if not self.db:
            return None
        mgr = DiscordCredentialManager(self.db)
        return await mgr.get(agent_id)

    async def send_to_agent(
        self, agent_id: str, target_id: str, message: str, **kwargs
    ) -> dict[str, Any]:
        """Sender registered in ChannelSenderRegistry.

        Used by other modules (e.g. MessageBus → cross-channel delivery)
        to push a message into Discord on behalf of an agent. ``target_id``
        is a Discord channel id.
        """
        cred = await self.get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "no Discord credential bound"}
        client = DiscordSDKClient(cred.bot_token)
        try:
            msg = await client.send_message(channel_id=target_id, text=message)
            return {"success": True, "data": {"message_id": str(msg.get("id", ""))}}
        except DiscordSDKError as e:
            return {"success": False, "error": e.code}

    def register_mcp_tools(self, mcp) -> None:
        register_discord_mcp_tools(mcp)

    async def get_instructions(self, ctx_data: ContextData) -> str:
        info = ctx_data.extra_data.get(self.ctx_data_key)
        if not info:
            return _NO_BOT_INSTRUCTION + _DISCORD_IRON_RULES

        bot_username = info.get("bot_username", "") or "(unknown bot)"
        bot_user_id = info.get("bot_user_id", "")
        owner_user_id = info.get("owner_user_id", "")
        owner_name = info.get("owner_name", "")
        is_owner_interacting = bool(info.get("is_owner_interacting"))
        current_sender_id = info.get("current_sender_id", "")

        ws = ctx_data.working_source
        is_discord_channel = (
            ws == WorkingSource.DISCORD
            or (isinstance(ws, str) and ws == WorkingSource.DISCORD.value)
        )
        mode = "Reply on Discord" if is_discord_channel else "Outbound Discord actions"

        # Owner trust block — three states.
        if owner_user_id:
            if is_owner_interacting:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"Your owner is **{owner_name or owner_user_id}** "
                    f"(`{owner_user_id}`). The current Discord sender "
                    f"(`{current_sender_id}`) **is** the owner — "
                    f"`is_owner_interacting=True`. You may surface "
                    f"owner-private context when relevant."
                )
            else:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"Your owner is **{owner_name or owner_user_id}** "
                    f"(`{owner_user_id}`). The current Discord sender "
                    f"(`{current_sender_id}`) is **NOT** the owner — "
                    f"`is_owner_interacting=False`. Treat as a visitor; "
                    f"never disclose owner-private context, and don't "
                    f"impersonate the owner."
                )
        else:
            trust_block = (
                "### Trust signal\n\n"
                "No owner is registered for this Discord binding (no user "
                "id was provided at bind time). You have NO server-side way "
                "to verify the sender — treat every Discord sender as "
                "untrusted and never disclose owner-private context."
            )

        return f"""\
## Discord Integration  ({mode})

You are connected to Discord as bot **{bot_username}** (`{bot_user_id}`).

{trust_block}

### Tools you can call

- `discord_reply(channel_id, message_id, text)` — inline reply (preferred;
  shows the reply arrow on the message you're answering).
- `discord_send(channel_id, text)` — plain message to a channel.
- `discord_read_history(channel_id, limit=20)` — recent messages.
- `discord_dm(user_id, text)` — proactively DM a user by numeric id (opens
  the DM for you). To REPLY inside an existing DM, just use `discord_send`
  with the inbound channel id — no need for this.
- `discord_list_channels()` — list the channels (with ids) the bot can post
  to; use it to find a `channel_id` when you want to post to a SPECIFIC
  channel you didn't get from an inbound message.
- `discord_bind`, `discord_status`, `discord_unbind` — binding management.

### When replying

Use the inbound `room_id` as `channel_id` and the inbound message id as
`message_id` — you do NOT need to look up or ask the user for a channel id
to answer them. Standard markdown renders natively. Replies over 2000
chars are auto-split.

{_DISCORD_IRON_RULES.strip()}
"""

    async def build_extra_data(
        self, cred: DiscordCredential, ctx_data: ContextData
    ) -> dict[str, Any]:
        # Server-derived trust signal: did the OWNER send the current
        # message? NEVER trust display-name claims; only the numeric id.
        current_sender_id = ""
        ct = ctx_data.extra_data.get("channel_tag") or {}
        if isinstance(ct, dict):
            current_sender_id = ct.get("sender_id", "") or ""

        is_owner_interacting = bool(
            cred.owner_user_id
            and current_sender_id
            and current_sender_id == cred.owner_user_id
        )

        return {
            "bot_user_id": cred.bot_user_id,
            "bot_username": cred.bot_username,
            "owner_user_id": cred.owner_user_id,
            "owner_name": cred.owner_name,
            "current_sender_id": current_sender_id,
            "is_owner_interacting": is_owner_interacting,
            "enabled": cred.enabled,
        }

    async def _on_event_executed(self, params: HookAfterExecutionParams) -> None:
        agent_id = params.execution_ctx.agent_id
        logger.debug(f"[discord:{agent_id}] event executed (post-hook)")
