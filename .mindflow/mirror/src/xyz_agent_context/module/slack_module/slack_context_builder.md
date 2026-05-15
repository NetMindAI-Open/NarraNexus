---
code_file: src/xyz_agent_context/module/slack_module/slack_context_builder.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Per-inbound-message context builder for Slack. Subclass of
``ChannelContextBuilderBase``. Translates a Slack ``ParsedMessage`` +
the bound ``SlackCredential`` into the canonical context shape that the
agent runtime feeds into prompts.

Slack has a real conversation-history API (``conversations.history``
and ``conversations.replies``), so unlike Telegram (which has none)
we can populate ``get_conversation_history`` from the platform itself
rather than reconstructing from our own inbox.

## Design decisions

- **Thread-aware history fetch.** When the inbound carries a
  ``thread_id`` (Slack ``thread_ts``), we hit ``conversations.replies``
  for the thread — otherwise ``conversations.history`` for the
  channel. Replying outside a thread when the user clearly meant to
  thread feels wrong, so the agent must always see the right context.
- **Slack returns newest-first; we reverse before yielding.** Every
  prompt assembler in this codebase expects chronological order. Doing
  the reverse here keeps the convention out of the higher layers.
- **``room_type`` hard-coded to "Group Room".** Slack DMs (``D...``
  channels) are technically 2-person but the surface is identical to
  group channels — same API endpoints, same threading model. Calling
  them all "Group Room" simplifies the prompt without losing fidelity.
- **``send_tool_name = "slack_cli"``.** The prompt's reply
  instructions include the exact callable so the agent doesn't have
  to guess between ``slack_send`` / ``slack_post`` / ``slack_cli``.
- **``reply_instruction`` carries thread_ts only when present.**
  Threading is sticky — sending without ``thread_ts`` to a thread
  surfaces the reply at the channel root, which is jarring to the
  user. The conditional ensures the LLM gets the exact arg shape.
- **``get_room_members`` returns ``[]``.** Slack channels can hold
  thousands of members; resolving them eats rate-limit budget and the
  agent rarely benefits. Phase 3 deliberately punts. Re-enable with
  caching + on-demand fetch in a later phase.
- **``room_name`` left blank.** Could resolve via
  ``conversations.info`` but Phase 3 chose to skip the extra API hop.
  The prompt copes — the channel id is sufficient routing.

## Upstream / downstream

- **Upstream**: ``SlackTrigger.create_context_builder`` — instantiates
  one per inbound message.
- **Downstream**:
  - ``SlackSDKClient.get_conversation_history`` /
    ``get_conversation_replies`` — the only API hits this builder
    makes per turn.
  - ``ChannelContextBuilderBase`` — caps history at
    ``ChannelHistoryConfig.history_limit`` (currently 20) and
    ``history_max_chars`` (3000) before the agent sees it.

## Gotchas

- ``timestamp`` returned as ``str(self._message.timestamp_ms)`` for
  consistency with Lark's builder. Downstream consumers parse to int
  if they need to math on it.
- The ``reply_instruction`` f-string embeds the channel id directly
  into LLM context — fine because it's a public Slack id, but the
  same pattern would leak if we ever accepted user-controllable
  channel ids without validation.
- ``get_user_info`` resolution for sender name happens on the trigger
  side (with caching). The builder receives the already-resolved
  ``ParsedMessage.sender_name``; we don't redo the lookup here.
