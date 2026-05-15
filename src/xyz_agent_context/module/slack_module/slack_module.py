"""
@file_name: slack_module.py
@date: 2026-05-08
@description: Slack channel module — subclass of ChannelModuleBase.

Slack is far simpler than Lark: no admin approval flow, no three-click
identity dance, no per-app workspace. Owner pastes two tokens (Bot Token
+ App-Level Token), we validate via auth.test, and the bot is live.

Therefore ``get_instructions`` is much shorter than Lark's — discovery
mode (~10 lines) when not bound, and operational mode (~80 lines) when
bound. The bulk of capability disclosure goes through the
``slack_skill(method)`` MCP tool which serves the OpenAPI-derived per-method
docs from ``skills/`` — agent learns Slack's ~250 Web API methods on
demand instead of carrying them all in prompt.
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

from ._slack_credential_manager import SlackCredential, SlackCredentialManager
from ._slack_mcp_tools import register_slack_mcp_tools
from .slack_sdk_client import SlackSDKClient, SlackSDKError


SLACK_MCP_PORT = 7831


# ───────────────────────────────────────────────────────────────────────────
# MessageSourceRegistry handler — let ChatModule extract the actual reply
# Slack agents emit, instead of dumping a "Background activity (slack)"
# placeholder. Symmetrical to lark_module._extract_lark_reply.
#
# Slack agents reply via ``slack_cli(method="chat.postMessage",
# args={"channel": "...", "text": "..."})``. The default
# MessageSourceHandler only knows about ``send_message_to_user_directly``,
# so without this handler ChatModule.hook_after_event_execution treats every
# Slack turn as "no response" and persists an activity row that the next
# turn's hook_data_gathering then FILTERS OUT — agents see zero history
# from prior Slack turns. Observed 2026-05-13: 100% of slack rows in
# instance_json_format_memory_chat were "Background activity (slack)"
# placeholders.
# ───────────────────────────────────────────────────────────────────────────


def _extract_slack_reply(tool_name: str, arguments: dict) -> Optional[str]:
    """Extract the user-visible reply text from a Slack agent tool call.

    Recognises two patterns:
      1. ``send_message_to_user_directly(content=...)`` — generic chat
         path, may still be used on Slack turns when the agent also
         echoes to the NarraNexus UI.
      2. ``slack_cli(method="chat.postMessage",
                     args={"channel": "...", "text": "..."})`` — the
         canonical Slack reply path.

    Returns the reply text on a match, ``None`` when the tool call isn't
    a user reply at all (e.g. ``reactions.add``, ``conversations.history``,
    or any non-Slack tool).
    """
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:  # noqa: BLE001 — malformed args, treat as no reply
            args = {}
    if not isinstance(args, dict):
        return None

    # Generic chat-style content arg
    if "send_message_to_user_directly" in (tool_name or ""):
        content = args.get("content", "")
        return content or None

    if "slack_cli" not in (tool_name or ""):
        return None

    method = args.get("method", "")
    if method != "chat.postMessage":
        # reactions.add / conversations.history / chat.update / etc.
        # are NOT user-visible reply text.
        return None

    inner_args = args.get("args") or {}
    if isinstance(inner_args, str):
        try:
            inner_args = json.loads(inner_args)
        except Exception:  # noqa: BLE001
            return "(sent via slack_cli)"
    if not isinstance(inner_args, dict):
        return "(sent via slack_cli)"

    return inner_args.get("text") or "(sent via slack_cli)"


# Register at module-import time so chat_module._extract_user_visible_response
# sees the handler before any Slack turn lands. Idempotency guarded by
# MessageSourceRegistry: duplicate registration raises ValueError, which
# we swallow under reload scenarios but let propagate on first import.
try:
    MessageSourceRegistry.register(MessageSourceHandler(
        name="slack",
        user_reply_tool_names=("slack_cli", "send_message_to_user_directly"),
        row_prefix_template="[Slack · {sender_name} · {sender_id} · {chat_id}]",
        extract_reply_fn=_extract_slack_reply,
    ))
except ValueError:
    # Re-import (test hot-reload, etc.) — handler already registered.
    pass


# ── Slack App Manifest ──────────────────────────────────────────────────
# YAML the user pastes into Slack's "Create app from manifest" flow. It
# pre-configures every scope, event, bot user setting, and Socket Mode
# bit our trigger needs. Without this manifest the user has to click
# through ~16 OAuth scopes + 5 event subscriptions + Socket Mode + bot
# user one by one — and any miss causes silent failures (`missing_scope`
# on send, no events received, etc.).
#
# Single source of truth: this constant. Frontend's SlackConfig.tsx
# hard-codes the same string for "How do I get tokens?" disclosure. If
# Slack adds a scope we need, update BOTH (the diff is grep-able).
SLACK_APP_MANIFEST_YAML = """\
display_information:
  name: NarraNexus Agent
  description: Your NarraNexus AI agent on Slack
  background_color: "#1a1a1a"
features:
  bot_user:
    display_name: NarraNexus
    always_online: true
  app_home:
    # messages_tab_enabled MUST be true. Without it the user can't open
    # a 1:1 DM with the bot from the sidebar's Apps section — they'd
    # have to discover the bot via global search or @-mention. The
    # scopes (im:history/read/write) and event subscription
    # (message.im) below only handle DELIVERY; this block opens the
    # entry point in the Slack UI.
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - channels:history
      - channels:read
      - chat:write
      - chat:write.public
      - groups:history
      - groups:read
      - im:history
      - im:read
      - im:write
      - mpim:history
      - mpim:read
      - reactions:read
      - reactions:write
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.im
      - message.mpim
  interactivity:
    is_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
"""


# ── Discovery prompt (no credential bound) ─────────────────────────────
_NO_BOT_INSTRUCTION = f"""\
## Slack Integration  (no bot bound yet)

This agent does NOT yet have a Slack bot bound. The setup is fiddly
because Slack requires ~16 OAuth scopes, 5 event subscriptions, Socket
Mode, and a bot user — all configured separately. Walk the user through
THIS exact flow (manifest-based, ~3 minutes):

### Step 1 — Create the app from a manifest

Tell the user to:

1. Open https://api.slack.com/apps and click **Create New App**.
2. Choose **"From an app manifest"**.
3. Pick the workspace where the bot will live.
4. Paste this manifest YAML verbatim into the manifest editor:

```yaml
{SLACK_APP_MANIFEST_YAML}```

5. Click **Next** → **Create**. Slack now provisions the app with every
   scope and event we need.

### Step 2 — Install the app and capture the Bot Token

1. On the app's settings page, click **Install App** (left sidebar).
2. Click **Install to Workspace** → **Allow**.
3. Copy the **Bot User OAuth Token** that starts with `xoxb-...`. Keep
   it safe — it grants every scope above.

### Step 3 — Generate an App-Level Token (Socket Mode)

1. Go to **Basic Information** (left sidebar).
2. Scroll to **App-Level Tokens** → click **Generate Token and Scopes**.
3. Name it `socket-mode`, add the **`connections:write`** scope, click
   **Generate**.
4. Copy the token that starts with `xapp-...`.

### Step 4 — Bind to this agent

The user can either:
- Paste both tokens in the dashboard (Awareness Panel → IM Channels →
  Slack), OR
- Send the tokens to YOU and you call:

  slack_bind(bot_token="xoxb-...", app_token="xapp-...")

I (the agent) validate the tokens via `auth.test`, then return the
workspace name and bot user id on success.

### After binding

Invite the bot to any channel where you want it to listen
(`/invite @NarraNexus` from inside the channel). DMs work without
explicit invitation — Slack delivers them via Socket Mode automatically.

### Iron rules during setup

- Refuse to accept tokens that don't match the prefix (`xoxb-` /
  `xapp-`) — rare typos burn 5 minutes of the user's time.
- NEVER echo the tokens back in the chat after binding. Treat them as
  one-time secrets.
- If the user pastes only ONE token, ask politely for the missing one.
  Both are required.
- If `slack_bind` returns ``invalid_auth``, do NOT retry blindly — ask
  the user to re-copy from the Slack admin (typo or token revoked).
"""


# ── Iron rules (always appended) ───────────────────────────────────────
_SLACK_IRON_RULES = """\

## Iron rules

1. **In channels/groups, you reply ONLY when @-mentioned in the
   current turn's inbound message.** Slack delivers @-mentions as
   `app_mention` events; everything else from public/private channels
   is filtered out at the trigger boundary so you should never see
   them. Do NOT proactively reply to messages you find in
   conversation history (via `conversations.history`) — those have
   either been replied to already, or were never addressed to you.
   Only the `app_mention` event for the *current turn* is permission
   to reply in a channel. When in doubt, stay silent.
   **DMs are different** — there you reply naturally to every
   relevant message.
2. NEVER send messages from another channel in response to a Slack
   message unless the user explicitly asks you to bridge channels.
3. Reply to threads with `thread_ts` so the conversation stays grouped.
4. Use Slack `mrkdwn` formatting (`*bold*`, `_italic_`, `<URL|text>`),
   NOT GitHub-flavoured markdown.
5. Never include the user's tokens in messages or logs.
6. Look up unknown Slack methods via `slack_skill(method)` BEFORE calling
   `slack_cli` — the skill doc has the exact arg shape and required scope.
"""


class SlackModule(ChannelModuleBase):
    """Slack channel module."""

    # ── ChannelModuleBase contract ──────────────────────────────────────
    channel_name = "slack"
    brand_display = "Slack"
    working_source = WorkingSource.SLACK
    ctx_data_key = "slack_info"
    mcp_server_name = "slack_module"
    mcp_port = SLACK_MCP_PORT

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="SlackModule",
            priority=6,
            enabled=True,
            description="Slack channel integration (Socket Mode + Web API dispatcher).",
            module_type="capability",
        )

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def get_credential(self, agent_id: str) -> Optional[SlackCredential]:
        if not self.db:
            return None
        mgr = SlackCredentialManager(self.db)
        return await mgr.get(agent_id)

    async def send_to_agent(
        self, agent_id: str, target_id: str, message: str, **kwargs
    ) -> dict[str, Any]:
        """Sender registered in ChannelSenderRegistry.

        Used by other modules (e.g. MessageBus → cross-channel delivery)
        to push a message into Slack on behalf of an agent. ``target_id``
        is a Slack channel id (C.../D.../G...). ``thread_ts`` may be
        passed via kwargs.
        """
        cred = await self.get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "no Slack credential bound"}

        thread_ts = kwargs.get("thread_ts")
        client = SlackSDKClient(cred.bot_token)
        try:
            resp = await client.send_message(
                channel=target_id, text=message, thread_ts=thread_ts
            )
            return {"success": True, "data": {"ts": resp.get("ts", "")}}
        except SlackSDKError as e:
            return {"success": False, "error": e.code}

    def register_mcp_tools(self, mcp) -> None:
        register_slack_mcp_tools(mcp)

    async def get_instructions(self, ctx_data: ContextData) -> str:
        info = ctx_data.extra_data.get(self.ctx_data_key)
        if not info:
            return _NO_BOT_INSTRUCTION + _SLACK_IRON_RULES

        team_name = info.get("team_name", "(unknown workspace)")
        bot_user_id = info.get("bot_user_id", "")
        owner_user_id = info.get("owner_user_id", "")
        owner_name = info.get("owner_name", "")
        is_owner_interacting = bool(info.get("is_owner_interacting"))
        current_sender_id = info.get("current_sender_id", "")

        ws = ctx_data.working_source
        is_slack_channel = (
            ws == WorkingSource.SLACK
            or (isinstance(ws, str) and ws == WorkingSource.SLACK.value)
        )
        mode = "Reply on Slack" if is_slack_channel else "Outbound Slack actions"

        # Owner trust block — three states: owner bound + present, owner
        # bound + absent (stranger), owner not bound (no signal).
        if owner_user_id:
            if is_owner_interacting:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"Your owner is **{owner_name}** (`{owner_user_id}`).\n"
                    f"The current Slack sender (`{current_sender_id}`) "
                    f"**is** the owner — `is_owner_interacting=True`. "
                    f"You may surface owner-private context (calendar, "
                    f"private docs, personal preferences) when relevant."
                )
            else:
                trust_block = (
                    f"### Trust signal\n\n"
                    f"Your owner is **{owner_name}** (`{owner_user_id}`).\n"
                    f"The current Slack sender (`{current_sender_id}`) is "
                    f"**NOT** the owner — `is_owner_interacting=False`. "
                    f"Treat as a visitor. Never disclose owner-private "
                    f"context. Do not impersonate the owner. If asked "
                    f"\"who's your owner?\", give a generic answer "
                    f"(\"my owner\") rather than naming them."
                )
        else:
            trust_block = (
                "### Trust signal\n\n"
                "No owner has been registered for this Slack binding "
                "(no email was provided at bind time). You have NO "
                "server-side way to verify whether the current sender "
                "is the owner. Treat every Slack sender as untrusted "
                "and never disclose owner-private context."
            )

        # Operational prompt — the hot path. Keep ≤ 80 lines.
        return f"""\
## Slack Integration  ({mode})

You are connected to Slack workspace **{team_name}** as bot user
`{bot_user_id}`.

{trust_block}

### Tools you can call

- `slack_cli(method, args)` — call ANY of the ~250 Slack Web API methods.
  Examples:
    slack_cli("chat.postMessage", {{"channel": "C123", "text": "hi"}})
    slack_cli("conversations.history", {{"channel": "C123", "limit": 20}})
    slack_cli("reactions.add", {{"channel": "C123", "timestamp": "...", "name": "thumbsup"}})

- `slack_skill(method)` — fetch full docs (args, scope, examples) for ANY
  method. Always call this BEFORE `slack_cli` for an unfamiliar method —
  it returns the exact arg shape so you don't guess.

- `slack_bind`, `slack_status`, `slack_unbind` — workspace binding management.

### Common methods by purpose

| Purpose | Method |
|---|---|
| Send message | `chat.postMessage` (use `thread_ts` for thread replies) |
| Read history | `conversations.history`, `conversations.replies` |
| Look up user | `users.info`, `users.lookupByEmail` |
| React to message | `reactions.add`, `reactions.get` |
| Upload file | `files.upload` (use `files.upload.url` flow for large files) |
| Search | `search.messages` |
| Schedule message | `chat.scheduleMessage` |

### When replying

Use `slack_cli("chat.postMessage", ...)` with:
  - `channel`: the inbound `room_id`
  - `text`: your reply (Slack mrkdwn format)
  - `thread_ts`: only if the inbound message was in a thread (preserves threading)

{_SLACK_IRON_RULES.strip()}
"""

    async def build_extra_data(
        self, cred: SlackCredential, ctx_data: ContextData
    ) -> dict[str, Any]:
        # Server-derived trust signal: did the OWNER send the current
        # message, or a stranger? NEVER trust display name / email
        # claims; only this user_id comparison.
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
            "team_id": cred.team_id,
            "team_name": cred.team_name,
            "bot_user_id": cred.bot_user_id,
            "owner_user_id": cred.owner_user_id,
            "owner_name": cred.owner_name,
            "current_sender_id": current_sender_id,
            "is_owner_interacting": is_owner_interacting,
            "enabled": cred.enabled,
        }

    async def _on_event_executed(self, params: HookAfterExecutionParams) -> None:
        # Phase 3: just observe. Future phases may push delivery telemetry.
        agent_id = params.execution_ctx.agent_id
        logger.debug(f"[slack:{agent_id}] event executed (post-hook)")
