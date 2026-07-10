"""
@file_name: telegram_module.py
@date: 2026-05-09
@description: Telegram channel module — subclass of ChannelModuleBase.

Telegram is the simplest IM channel architecturally:
  - One Bot Token from @BotFather (no OAuth, no admin approval, no
    multi-tenant manifest).
  - Long polling (no Socket Mode, no public IP needed).
  - **Privacy mode default ON** — bot in groups only sees `/commands`
    and @-mentions. THIS IS THE RIGHT DEFAULT and we explicitly
    recommend keeping it on. (See ``_NO_BOT_INSTRUCTION`` Step 2.)
    Disabling privacy makes the bot receive every group message,
    which floods the agent with noise + token cost. Same architectural
    issue Slack has today (Slack subscribes to ``message.channels``
    by default — Phase 5 fixes it). Telegram defaults already give us
    the @-mention-only behavior; don't break the default.

Mirrors slack_module.py shape; three deltas:
  1. No App Manifest YAML — replaced by @BotFather command sequence.
  2. Single token (no Bot Token + App-Level Token pair).
  3. Owner identity via Telegram @username, not email.
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

from ._telegram_credential_manager import TelegramCredential, TelegramCredentialManager
from ._telegram_mcp_tools import register_telegram_mcp_tools
from .telegram_sdk_client import TelegramSDKClient, TelegramSDKError


TELEGRAM_MCP_PORT = 7832


# ───────────────────────────────────────────────────────────────────────────
# MessageSourceRegistry handler — see slack_module._extract_slack_reply for
# the full rationale. TL;DR: ChatModule's default extractor only knows
# about ``send_message_to_user_directly``; Telegram agents reply via
# ``tg_cli(method="sendMessage", args={"text": "..."})``, so without
# this handler every Telegram turn is persisted as
# "Background activity (telegram)" and the agent loses all multi-turn
# context. Observed 2026-05-13: all telegram rows in
# instance_json_format_memory_chat were "Background activity (telegram)".
# ───────────────────────────────────────────────────────────────────────────


def _extract_telegram_reply(tool_name: str, arguments: dict) -> Optional[str]:
    """Extract the user-visible reply text from a Telegram agent tool call.

    Recognises:
      1. ``send_message_to_user_directly(content=...)`` — generic chat path.
      2. ``tg_cli(method="sendMessage", args={"chat_id": ..., "text": ...})``
         — the canonical Telegram reply path.

    Returns the reply text on match, ``None`` when the tool call isn't
    a user reply (e.g. ``sendChatAction`` for the typing indicator,
    ``getUpdates``, or any non-Telegram tool).
    """
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:  # noqa: BLE001
            args = {}
    if not isinstance(args, dict):
        return None

    # Generic chat-style content arg
    if "send_message_to_user_directly" in (tool_name or ""):
        content = args.get("content", "")
        return content or None

    if "tg_cli" not in (tool_name or ""):
        return None

    method = args.get("method", "")
    if method != "sendMessage":
        # sendChatAction (typing) / deleteMessage / editMessageText / etc.
        # are NOT user-visible reply text.
        return None

    inner_args = args.get("args") or {}
    if isinstance(inner_args, str):
        try:
            inner_args = json.loads(inner_args)
        except Exception:  # noqa: BLE001
            return "(sent via tg_cli)"
    if not isinstance(inner_args, dict):
        return "(sent via tg_cli)"

    return inner_args.get("text") or "(sent via tg_cli)"


# Register at module-import time. Idempotent guard mirrors lark_module.
try:
    MessageSourceRegistry.register(MessageSourceHandler(
        name="telegram",
        user_reply_tool_names=("tg_cli", "send_message_to_user_directly"),
        row_prefix_template="[Telegram · {sender_name} · {sender_id} · {chat_id}]",
        extract_reply_fn=_extract_telegram_reply,
        dedicated_trigger=True,
    ))
except ValueError:
    # Re-import (test hot-reload, etc.) — handler already registered.
    pass


# ── Discovery prompt (no credential bound) ─────────────────────────────
_NO_BOT_INSTRUCTION = """\
## Telegram Integration  (no bot bound yet)

Walk the user through this @BotFather sequence (~3 minutes, all in
Telegram itself — no manifest, no OAuth):

### Step 1 — Create the bot via @BotFather
1. Open Telegram, search for ``@BotFather``, start a chat.
2. Send ``/newbot``. BotFather asks for a display name, then a username
   (must end in ``bot``, e.g. ``narranexus_demo_bot``).
3. BotFather replies with a token like ``7981632450:AAH2kx9LmPq...``.
   This is the Bot Token. Copy it.

### Step 2 — Privacy mode (KEEP DEFAULT ON)
By default, Telegram bots in groups ONLY see ``/commands`` and
@-mentions of the bot. **This is the RIGHT default for almost every
use case** — keep it.

Why:
- DMs work normally regardless of privacy mode (1:1 conversations are
  unaffected).
- In groups the bot only "wakes up" when explicitly addressed. This
  saves tokens, prevents the bot from spam-replying to every chat
  message, and matches the standard Slack/Lark group-bot UX.

Do NOT run ``/setprivacy -> Disable`` unless the user explicitly wants
the bot to passively listen to ALL group messages — a rare research /
note-taking / summarisation use case. If they ask "why isn't the bot
replying in the group?", the answer is "@-mention it" — NOT "disable
privacy".

### Step 3 — Optional: enable group joining
If the user wants to add the bot to groups (most do):
1. ``/setjoingroups`` -> bot -> ``Enable``.

### Step 4 — Optional: tell me your @username for the trust signal
Ask the user for their Telegram @username (e.g. ``@bin_liang``). Bind
with it so the agent can recognise "this is the owner DM-ing me" vs
"stranger DM-ing me":

    tg_bind(bot_token="7981...", owner_username="@bin_liang")

Or paste both into the dashboard.

**Important — owner trust signal activates on FIRST DM, not at bind:**
Telegram's API does not let bots look up users by @username at bind
time (only supergroups/channels are looked up that way). So the bind
stores the @username as a LOCK but cannot yet resolve it to a numeric
user_id. The trust signal activates when the **owner sends the FIRST
DM** to the bot — the inbound message carries their numeric user_id
and current @username, and the trigger auto-matches against the lock
to populate ``owner_user_id``.

Tell the user: "After binding, open Telegram and send any message
(``/start`` or 'hi') to the bot. That activates the owner trust signal."

If the user doesn't supply @username, the bot still works but with NO
trust signal — every Telegram sender is treated as untrusted (no
auto-resolve will ever fire because there's no lock to match against).

### Iron rules during setup
1. Refuse tokens that don't match ``<digits>:<base64>`` shape.
2. NEVER echo the token back in the chat after binding. Treat it as a
   one-time secret.
3. If ``tg_bind`` returns ``Unauthorized``, ask the user to re-copy from
   BotFather (token typo or @BotFather revoked it via ``/revoke``).
4. **DO NOT recommend ``/setprivacy -> Disable``** as a "fix" when the
   bot seems quiet in a group. The fix is to @-mention the bot.
   Disabling privacy is for advanced / research bots only.
"""


# ── Iron rules (always appended) ───────────────────────────────────────
_TELEGRAM_IRON_RULES = """\

## Iron rules

1. **In groups/supergroups, you reply ONLY when @-mentioned.** Telegram
   privacy mode (default ON) already filters group events down to
   @-mentions and ``/commands`` before they reach you, so this is
   usually enforced at the source. Do not try to engage with a group
   message that wasn't directed at you, even if you can technically
   see it.
2. NEVER send messages on another channel in response to a Telegram
   message unless the user explicitly asks you to bridge channels.
3. Reply in-thread (use ``message_thread_id``) ONLY if the inbound
   message had one — supergroup forum topics carry it; regular groups
   and DMs do not.
4. Use plain text (no ``parse_mode``). Telegram MarkdownV2 escape
   rules are aggressive (``_*[]()~>#+-=|{}.!\\``); wrong escaping
   produces 400 Bad Request.
5. Never include the user's bot token in messages or logs.
6. Look up unknown Telegram methods via ``tg_skill(method)`` BEFORE
   calling ``tg_cli`` — the skill doc has the exact arg shape.
7. **Inbound attachments are SUPPORTED.** The bot RECEIVES documents
   (PDF / DOCX / TXT / CSV / ...), photos (JPG / PNG / ...), audio /
   voice memos, and videos. Each file is downloaded to the agent's
   workspace and surfaced in the chat history as a
   ``[User uploaded <kind>: name=..., path=/.../att_XXXXXXXX.<ext>,
   mime=..., — use Read tool to view]`` marker.

   - To VIEW an uploaded file, call your built-in ``Read`` tool against
     the absolute ``path=`` shown in the marker. Read is multimodal:
     PDFs and images return native content blocks; text / code / data
     files return their text contents directly.
   - For audio / voice memos the marker carries an extra
     ``transcript=...`` field if Whisper transcription succeeded.
     **Use the transcript directly** — it IS the spoken content.
     If ``transcript=`` is absent or empty the file is still on disk
     but transcription was unavailable (no OpenAI-compatible provider
     configured) — say so, do NOT fabricate spoken content.
   - Stickers, locations, contacts, polls and game messages are still
     ignored at the trigger layer (out of scope).
   - Telegram bots cannot download files larger than 20 MiB. The user
     will see a friendly refusal for oversized uploads — you do not
     need to apologise further.

   To SEND media back to the user, call ``sendPhoto`` / ``sendDocument``
   / ``sendVoice`` etc. via ``tg_cli`` (no curated skill md but the
   methods work).
"""


class TelegramModule(ChannelModuleBase):
    """Telegram channel module."""

    # ── ChannelModuleBase contract ──────────────────────────────────────
    channel_name = "telegram"
    brand_display = "Telegram"
    working_source = WorkingSource.TELEGRAM
    ctx_data_key = "telegram_info"
    mcp_server_name = "telegram_module"
    mcp_port = TELEGRAM_MCP_PORT

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="TelegramModule",
            priority=7,
            enabled=True,
            description="Telegram channel integration (long-poll Bot API).",
            module_type="capability",
        )

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def get_credential(self, agent_id: str) -> Optional[TelegramCredential]:
        if not self.db:
            return None
        mgr = TelegramCredentialManager(self.db)
        return await mgr.get(agent_id)

    async def send_to_agent(
        self, agent_id: str, target_id: str, message: str, **kwargs
    ) -> dict[str, Any]:
        """Sender registered in ChannelSenderRegistry.

        ``target_id`` is a Telegram chat_id (numeric int64 as string).
        ``message_thread_id`` may be passed via kwargs for forum-topic threads.
        """
        cred = await self.get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "no Telegram credential bound"}

        thread_id = kwargs.get("message_thread_id") or kwargs.get("thread_ts")
        client = TelegramSDKClient(cred.bot_token)
        try:
            resp = await client.send_message(
                chat_id=target_id,
                text=message,
                message_thread_id=thread_id,
            )
            return {"success": True, "data": {"message_id": resp.get("message_id")}}
        except TelegramSDKError as e:
            return {"success": False, "error": e.code}
        finally:
            await client.close()

    def register_mcp_tools(self, mcp) -> None:
        register_telegram_mcp_tools(mcp)

    async def get_instructions(self, ctx_data: ContextData) -> str:
        info = ctx_data.extra_data.get(self.ctx_data_key)
        if not info:
            return _NO_BOT_INSTRUCTION + _TELEGRAM_IRON_RULES

        bot_username = info.get("bot_username", "(unknown)")
        bot_user_id = info.get("bot_user_id", "")
        owner_user_id = info.get("owner_user_id", "")
        owner_name = info.get("owner_name", "")
        is_owner_interacting = bool(info.get("is_owner_interacting"))
        current_sender_id = info.get("current_sender_id", "")

        ws = ctx_data.working_source
        is_telegram_channel = (
            ws == WorkingSource.TELEGRAM
            or (isinstance(ws, str) and ws == WorkingSource.TELEGRAM.value)
        )
        mode = "Reply on Telegram" if is_telegram_channel else "Outbound Telegram actions"

        if owner_user_id:
            if is_owner_interacting:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"Your owner is **{owner_name}** (`{owner_user_id}`).\n"
                    f"The current Telegram sender (`{current_sender_id}`) "
                    f"**is** the owner — `is_owner_interacting=True`. "
                    f"You may surface owner-private context when relevant."
                )
            else:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"Your owner is **{owner_name}** (`{owner_user_id}`).\n"
                    f"The current Telegram sender (`{current_sender_id}`) is "
                    f"**NOT** the owner — `is_owner_interacting=False`. "
                    f"Treat as a visitor. Never disclose owner-private context. "
                    f"Do not impersonate the owner. If asked \"who's your owner?\", "
                    f"give a generic answer (\"my owner\")."
                )
        else:
            trust_block = (
                "### Trust signal\n\n"
                "No owner has been registered for this Telegram binding "
                "(no @username was provided at bind time). You have NO "
                "server-side way to verify whether the current sender is the "
                "owner. Treat every Telegram sender as untrusted and never "
                "disclose owner-private context."
            )

        return f"""\
## Telegram Integration  ({mode})

You are connected to Telegram as bot **@{bot_username}** (`{bot_user_id}`).

{trust_block}

### Tools you can call

- `tg_cli(method, args)` — call ANY of Telegram's ~100 Bot API methods.
  Examples:
    tg_cli("sendMessage", {{"chat_id": "123", "text": "hi"}})
    tg_cli("editMessageText", {{"chat_id": "123", "message_id": 7, "text": "edited"}})
    tg_cli("setMessageReaction", {{"chat_id": "123", "message_id": 7, "reaction": [{{"type": "emoji", "emoji": "👍"}}]}})

- `tg_skill(method)` — fetch full docs (args, examples) for a method.
  Always call this BEFORE `tg_cli` for an unfamiliar method.

- `tg_bind`, `tg_status`, `tg_unbind` — binding management.

### Common methods by purpose

| Purpose | Method |
|---|---|
| Send message | `sendMessage` (use `message_thread_id` for forum threads) |
| Edit message | `editMessageText` |
| Delete message | `deleteMessage` |
| Reply (in DM) | `sendMessage` with `reply_to_message_id` |
| React | `setMessageReaction` |
| Pin | `pinChatMessage` (bot must be admin) |
| Look up user | `getChatMember` (needs chat_id context) |

### When replying

Use `tg_cli("sendMessage", ...)` with:
  - `chat_id`: the inbound `room_id`
  - `text`: your reply, **plain text** (no parse_mode — see iron rule 3)
  - `message_thread_id`: only if inbound message had one
  - `reply_to_message_id`: optional, for explicit reply-to-message threading

### Receiving in groups

In groups you only see ``/commands`` and @-mentions of the bot — by
design (Telegram privacy mode default ON, which we recommend keeping).
If a user asks "why isn't the bot replying in our group?", the answer
is **"@-mention it"**, not "turn off privacy mode". Privacy mode is
intentionally on; flipping it would flood you with every message in
the group and is reserved for rare passive-listener bots.

{_TELEGRAM_IRON_RULES.strip()}
"""

    async def build_extra_data(
        self, cred: TelegramCredential, ctx_data: ContextData
    ) -> dict[str, Any]:
        # Server-derived trust signal — same model as Slack/Lark.
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
            "owner_username": cred.owner_username,
            "current_sender_id": current_sender_id,
            "is_owner_interacting": is_owner_interacting,
            "enabled": cred.enabled,
        }

    async def _on_event_executed(self, params: HookAfterExecutionParams) -> None:
        agent_id = params.execution_ctx.agent_id
        logger.debug(f"[telegram:{agent_id}] event executed (post-hook)")
