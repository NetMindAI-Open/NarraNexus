---
code_file: src/xyz_agent_context/module/discord_module/discord_trigger.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — react_tool_ref = "react_to_user_message"

Sets the class attr `react_tool_ref` (bare tool name) so the base trigger's
`_early_feedback_prefix` injects the per-turn "ack early" directive with Discord's
react tool. (Mirror also renamed `.md` → `.py.md` for the sync bot.)

## Why it exists

Discord's ``ChannelTriggerBase`` subclass. Receives messages over the
Discord Gateway (a persistent WebSocket) via discord.py and feeds them
into the shared dedup → worker → AgentRuntime pipeline. Sibling of
``slack_trigger.py`` / ``telegram_trigger.py``.

## Design decisions

- **discord.py Gateway bridged to ``connect()`` via an asyncio.Queue.**
  discord.py is callback-driven (``@client.event on_message``); the
  exact pattern SlackTrigger uses for Socket Mode. ``client.start`` runs
  as a background task; its death (auth failure / disconnect) is
  surfaced onto the queue with the ``__discord_client_exit__`` sentinel
  so the base loop runs backoff / permanent-failure detection.
- **discord.py is used ONLY here.** All REST (send / history / auth) goes
  through ``DiscordSDKClient`` (aiohttp). Opening a full Gateway just to
  POST one message would be wasteful, so the send/bind/history paths
  never touch discord.py. See ``discord_sdk_client.md``.
- **Raw events normalized to plain dicts in ``_message_to_raw``.** The
  ``on_message`` handler converts ``discord.Message`` → dict (computing
  ``mentions_me`` against ``client.user`` while it has it), so
  ``parse_event`` operates on a dict and is unit-testable without
  discord objects.
- **Reply policy = DM always, guild only on @-mention.** ``parse_event``
  drops guild messages without ``mentions_me``; mirrors Slack's
  app_mention gating. Keeps the bot silent in busy servers until
  addressed.
- **Bot-loop guard.** ``parse_event`` drops ANY bot-authored message
  (``author_is_bot``), so two NarraNexus agents in one channel can't
  ping-pong. ``is_echo`` is the canonical own-message check the base
  also runs.
- **``_subscriber_key`` = ``agent_id``.** Discord credentials have no
  ``app_id`` (the base default); one bot per agent, agent_id unique.
- **Permanent-auth detection** covers both ``discord.LoginFailure``
  (gateway) and ``DiscordSDKError`` code ``unauthorized`` (REST).

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase`` (dedup, worker pool, credential
  watcher, audit, inbox, reconnect backoff).
- **Downstream**: ``DiscordCredentialManager`` (load_active),
  ``DiscordSDKClient`` (attachment download + sender-name fallback),
  ``DiscordContextBuilder`` (prompt), ``AgentRuntime`` (via the base's
  ``_build_and_run_agent``).

## Gotchas

- **Proxy must be passed explicitly to ``discord.Client(proxy=...)``.**
  discord.py does NOT read HTTPS_PROXY/HTTP_PROXY from the environment for
  either the REST login or the Gateway WebSocket. ``connect()`` reads the
  proxy env itself and passes it (mirrors SlackTrigger → SocketModeClient).
  Without this, a network behind a forward proxy (mainland China) times out
  at ``static_login`` (GET /users/@me) and the Gateway never connects —
  observed 2026-06-17 (`ConnectionTimeoutError to discord.com`). The proxy
  env still has to be SET in the process (run.sh launched with
  `https_proxy=...`); the code only ensures discord.py actually uses it.
- discord.py is imported guarded (``_HAS_DISCORD``); ``start`` raises if
  the dependency is missing so the module package still imports without
  it (tests mock the SDK and never import discord).
- ``message_content`` intent must be ON in the Developer Portal or every
  ``content`` arrives empty — code can't detect this, it just looks like
  silent users. Surfaced in the module instructions + frontend.
- ``extract_output`` returns ``"(stayed silent)"`` when the agent ran but
  never called a send tool — same convention as Slack/Lark; don't treat
  that sentinel as a real reply downstream.
- **``_message_to_raw`` strips the bot's OWN @-mention from ``content``**
  (``_strip_bot_mention``). A guild "@bot hi" arrives as raw markup
  ``<@BOTID> hi``; the opaque numeric token is noise the model can't map to
  "this is me" and it degraded channel replies while DMs (no prefix) worked
  — the 2026-06-24 "DM replies fine, channel @mention replies blank" report.
  Only the bot's own mention is removed (other users' mentions survive), the
  reply-policy gate reads the structured ``mentions`` list (not the markup)
  so stripping doesn't affect gating, and a bare "@bot" ping falls back to
  the original so it isn't blanked into an empty-content drop.
