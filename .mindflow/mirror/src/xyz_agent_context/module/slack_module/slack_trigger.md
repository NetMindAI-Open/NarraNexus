---
code_file: src/xyz_agent_context/module/slack_module/slack_trigger.py
stub: false
last_verified: 2026-05-12
---

## Why it exists

Slack's concrete ``ChannelTriggerBase`` subclass for Phase 3. The base
owns dedup, worker pool, credential watcher, audit log, inbox writer,
reconnect backoff. This file fills in only the Slack-specific abstract
surface (connect / parse / echo / sender / builder / load).

Slack's wire model is **Socket Mode**: a persistent WebSocket from the
bot to Slack. No public URL, no ngrok, no inbound HTTP. The trigger
opens one socket per credential and bridges the slack_sdk
callback-driven listener into the base's async-generator pattern via
an ``asyncio.Queue``.

## Design decisions

- **Bridge callback API to async generator via queue.** ``slack_sdk``'s
  ``SocketModeClient`` calls a registered listener; the base wants to
  iterate. We register a listener that ack's the envelope first (keeps
  Slack from retrying the event), filters early, then enqueues. The
  ``connect`` async generator yields off the queue with a 30s timeout
  so the base can observe ``self.running`` and shut down cleanly.
- **Ack BEFORE doing anything else.** If we fail to enqueue, Slack
  must still see the ack — otherwise it retries the same event up to
  3 times, blowing through dedup.
- **Bot self-message detection uses ``bot_user_id``, not ``bot_id``.**
  ``event.bot_id`` is the App-level ``B...`` id; ``event.user`` is the
  bot's user-level ``U...`` id. ``auth.test`` returns ``user_id`` (the
  ``U...``) which we stored on the credential. They are different
  identifiers — getting it wrong silently causes the bot to reply to
  itself.
- **Subtype filter (``_IGNORED_SUBTYPES``) is conservative for Phase
  3.** ``message_changed``/``deleted`` skipped (no edit-react UX yet),
  ``file_share`` skipped (file ingestion is a future phase),
  ``thread_broadcast`` skipped (would otherwise produce duplicate
  events for one user message). Re-enable methodically as features
  land.
- **Phase 5 channel-type allow-list
  (``_ACCEPTED_MESSAGE_CHANNEL_TYPES = {"im", "mpim"}``).** Reply
  policy in channels is "only when @-mentioned". Slack delivers
  @-mentions as ``app_mention`` events (filter-exempt — those still
  pass), while regular ``message`` events from public/private channels
  are dropped at the trigger boundary so the agent never sees them.
  Defense in depth: the filter is applied both in ``_listener`` (the
  production path) and ``parse_event`` (so tests, future webhook
  callers, anything that bypasses ``_listener`` still gets the
  policy). ``channel_type`` is absent on ``app_mention`` events — the
  filter only runs for ``event_type == "message"``.
- **``DEBOUNCE_WINDOW_MS = 1500``.** Slack users frequently fire
  rapid follow-ups ("hi", "actually one more thing"). The base's
  debounce window collapses these into one agent run.
- **``HISTORY_BUFFER_MS = 5 * 60 * 1000``.** Slack's
  ``conversations.replies`` returns full thread history but the
  context builder caps it at ``history_limit=20`` — the buffer just
  ensures the base's "recent message" window comfortably covers a
  typical Slack burst.
- **One Socket Mode client per credential, tracked in
  ``_socket_clients``.** Needed so ``stop()`` can disconnect cleanly
  before the base tears down workers — otherwise the WS keeps yielding
  into a dead queue.
- **users.info cache TTL = 5 minutes.** Slack rate-limits ``users.info``
  pretty hard (Tier 4). Cache keyed on ``(agent_id, user_id)`` because
  display name resolution must respect agent boundaries.
- **``client_msg_id`` preferred over ``ts`` as message_id.** It's a
  stable UUID for user-submitted messages; ``ts`` is good as a
  fallback for system-emitted events that lack ``client_msg_id``.
- **``extract_output`` scrapes ``slack_cli`` tool-call args, NOT
  ``result.output_text``.** Slack agents reply by calling
  ``slack_cli(method="chat.postMessage", args={"text": ...})`` —
  ``output_text`` only contains the agent's reasoning ("My thought
  process: ..."), which would leak chain-of-thought into the inbox.
  Mirror of Lark's pattern. Filters by ``method=="chat.postMessage"``;
  other Slack methods (``reactions.add``, ``chat.update``) are not
  user-visible reply text and are skipped.

## Upstream / downstream

- **Upstream**: ``run_slack_trigger.py`` (entry point) constructs and
  starts this; ``ChannelTriggerBase`` provides the lifecycle scaffold.
- **Downstream**:
  - ``slack_sdk.socket_mode.aiohttp.SocketModeClient`` — lazy-imported
    so a stripped image without ``slack-sdk`` boots far enough to
    show a friendly error in ``start()``.
  - ``SlackContextBuilder`` — created per inbound message.
  - ``SlackCredentialManager.list_active`` — feeds the base's watcher.

## Gotchas

- ``connect`` is an async generator. Returning early or raising inside
  ``finally`` will leak a socket — always wrap disconnect in
  ``asyncio.wait_for`` with a small timeout.
- ``ChatType.GROUP if chat_id.startswith(("C", "G"))`` — Slack DMs
  start with ``D``. Keep the prefix logic; ``app_mention`` events
  always come from ``C`` (public channels).
- Phase 3 maps every accepted event to ``MessageContentType.TEXT``,
  even when there are file attachments. Attachment handling is a
  later phase — bumping the type without the rest of the pipeline
  would break the agent's reply path.
