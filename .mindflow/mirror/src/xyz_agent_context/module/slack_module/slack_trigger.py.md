---
code_file: src/xyz_agent_context/module/slack_module/slack_trigger.py
stub: false
last_verified: 2026-07-23
---

## 2026-07-23 — a dead app_token must be forced to ESCAPE connect(), then classified

Symptom: a dead **app-level token** (`xapp-…`) makes the workers spam
`apps.connections.open … {'ok': false, 'error': 'invalid_auth'} … Retrying…`
forever, and the credential is never disabled. **Two independent bugs had to be
fixed together** — fixing only one leaves the symptom intact:

**Bug A — the error never escaped `connect()` (the load-bearing one).**
slack_sdk's `SocketModeClient.connect()` (`aiohttp/__init__.py` 352-409) wraps the
WSS-URL fetch in `while True: try/except Exception:` — on failure it calls
`logger.exception(...)` (line 408) and `sleep`s, then retries indefinitely. So the
raw `SlackApiError` from `apps.connections.open` is **swallowed inside connect()**;
it never reaches our `_subscribe_loop`, `is_permanent_auth_failure` is never called,
and the credential is never disabled. (The multi-line "traceback" in the workers
log is `logger.exception`'s output from that swallow point — NOT a propagation to
our code. Names lie; read the vendored source — CLAUDE.md engineering lesson #1.)
Fix: `connect()` now calls `socket_client.issue_new_wss_url()` **itself** before
handing off to `socket_client.connect()`. `issue_new_wss_url()` (`async_client.py`
43-58) retries `ratelimited` internally but **re-raises every other SlackApiError**,
so a dead token propagates out of our code up to the base loop. `connect()` then
skips its own fetch because `wss_uri` is already set (`aiohttp/__init__.py` 372).
This is the whole reason we don't just call `connect()` directly — regression-pinned
by `test_slack_trigger.py::test_connect_propagates_permanent_auth_error` (goes red
if the pre-fetch line is removed).

**Bug B — even once it escaped, the classifier didn't recognise it.**
`is_permanent_auth_failure` only checked `isinstance(exc, SlackSDKError)` (our
wrapper). But Socket Mode is the one path NOT routed through `SlackSDKClient`, so
the error is a *raw* `slack_sdk.errors.SlackApiError`, not our `SlackSDKError`.
Fix: the classifier now also matches the raw error, reading the code from
`exc.response` (a dict or `AsyncSlackResponse` — both expose `.get`; `None` →
`False`) and comparing against `_SLACK_PERMANENT_AUTH_CODES`. Transient codes
(`ratelimited`, …) stay non-permanent so a healthy credential is never disabled on
a blip. Raw import is guarded (`_SlackApiError`) because slack-sdk is optional.

Contrast: DiscordTrigger already whitelists the raw `discord.LoginFailure` for
the classifier half — Slack was the odd one out.

**Cleanup corollary — `close()` not `disconnect()`.** Making Bug A's exception
escape means non-permanent connect failures (network blip, `apps.connections.open`
unreachable — `issue_new_wss_url` only swallows `ratelimited`, re-raises the rest)
now re-enter `_subscribe_loop` on backoff (≤120 s), and **each round constructs a
fresh `SocketModeClient`**. But `SocketModeClient.__init__` (`aiohttp/__init__.py`
130,137) already opens an `aiohttp.ClientSession` AND fires a `process_messages()`
task at construction — and only `close()` (446-457) cancels that task + shuts the
session; `disconnect()` (411) merely drops the ws. So (1) the pre-fetch/connect
handoff is wrapped in `try/except BaseException: await close(); raise`, and (2) the
subscribe-loop `finally` and `stop()` were switched from `disconnect()` → `close()`
(the latter a pre-existing leak on the normal reconnect path, swept per binding
rule #8). Without this, a half-hour upstream outage leaks ~20 zombie tasks +
sessions and spams aiohttp "Unclosed client session" (CLAUDE.md lesson #2).
Regression-pinned by `test_connect_propagates_permanent_auth_error` asserting
`close()` awaited once / `disconnect()` never.

**Known follow-up (NOT covered here).** This fixes the INITIAL-connect case (dead
token at subscribe time). A token revoked *mid-session* fails on slack_sdk's
background reconnect path (`connect_to_new_endpoint`), whose exceptions are
swallowed the same way but run in a fire-and-forget task we don't drive — catching
that needs an independent L2/L3 health check (CLAUDE.md lesson #4), tracked
separately, not in this change.

## 2026-07-10 — react_tool_ref = "react_to_user_message"

Sets the class attr `react_tool_ref` (bare tool name) so the base trigger's
`_early_feedback_prefix` injects the per-turn "ack early" directive with Slack's
react tool. (Mirror also renamed `.md` → `.py.md` for the sync bot.)

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
- **Subtype filter (``_IGNORED_SUBTYPES``) — INITIAL ASSUMPTION WRONG,
  CORRECTED IN-PHASE.** ``message_changed``/``deleted`` skipped (no
  edit-react UX yet), ``thread_broadcast`` skipped (would otherwise
  produce duplicate events for one user message). **``file_share`` is
  NOT in the ignore list** — the original Phase 1b commit included it
  based on the assumption that modern Slack delivers DM files as a
  regular ``message.im`` event with ``files[]`` populated and that
  ``file_share`` was a legacy duplicate. Real CN-dev manual smoke
  proved this wrong: text-only "hi" went through, but text + PDF
  produced ZERO audit rows. The truth is **``file_share`` IS the
  canonical delivery envelope** for DM file uploads — Slack sends one
  envelope per upload with ``subtype="file_share"`` AND ``files[...]``
  populated. Filtering it eats every inbound file event. Pin tested in
  ``test_slack_attachment_ingest.py::test_parse_event_file_share_subtype_now_processed``.
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
- Phase 1b now derives ``content_type`` from the primary attachment's
  mime: ``image/*`` → IMAGE, ``audio/*`` → AUDIO, ``video/*`` → VIDEO,
  anything else → FILE. Text-only messages still get TEXT. The
  Anthropic-style ``Read`` tool in the agent SDK uses this hint as a
  rendering signal; the rest of the pipeline doesn't branch on it,
  so the historical "everything is TEXT" choice is no longer needed.

## Phase 1b additions (attachment ingestion)

- **``parse_event`` extracts ``files[]`` into
  ``raw["attachment_refs"]``**. Each file entry produces one ref
  dict carrying ``platform_ref`` (Slack ``file.id``),
  ``original_name``, ``mime_hint``, ``size_hint``, and the
  ``url_private`` if Slack delivered it inline. Multi-file uploads
  produce multiple refs; malformed entries (string, missing id) are
  skipped without breaking the whole event. A message with no text
  AND no refs returns ``None`` (sticker / system event analogue).

- **``fetch_attachments`` override**. Per-ref pipeline:
  1. Pre-check ``size_hint > max_upload_bytes`` → audit
     ``EVENT_INGRESS_DROPPED_OVERSIZED`` (``reason: "backend_max_upload_bytes"``).
  2. If ``url_private`` is missing from the event, call
     ``files.info`` to hydrate it. Slack occasionally ships file
     events with only ``file.id`` populated during high-traffic
     windows; ``files_info`` recovery is the canonical workaround.
  3. ``download_url`` with stream-cap = ``max_upload_bytes``. Streaming
     cap raises ``SlackSDKError("oversized")`` mid-stream — audited
     as ``EVENT_INGRESS_DROPPED_OVERSIZED`` with
     ``reason: "stream_cap_exceeded"`` so ops can tell platform-cap
     refusals from network failures.
  4. Hand bytes to ``ChannelTriggerBase._persist_attachment`` (MIME
     sniff + on-disk store + Whisper STT for audio/*).

  Never-raises: per-ref failures audit and skip; partial successes
  still flow to the agent. Pin tested in
  ``test_slack_attachment_ingest.py::test_fetch_attachments_partial_success``.

- **``SocketModeClient`` proxy plumbing (Phase 1b follow-up).**
  ``connect`` now reads ``HTTPS_PROXY`` / ``HTTP_PROXY`` env vars at
  subscriber-start time and passes ``proxy=`` to ``SocketModeClient``.
  Why: slack_sdk's SocketModeClient builds its own aiohttp
  ClientSession internally and **does NOT honour the ``trust_env``
  flag** the way our SDK clients do. Without an explicit ``proxy=``,
  the wss to ``wss-primary.slack.com`` bypasses any local proxy. In
  restrictive networks (mainland China is the canonical case) the
  TCP/TLS handshake to ``wss-primary.slack.com`` usually succeeds —
  Slack's edge IPs aren't blanket-blocked — but the ongoing event
  frames are dropped / reordered by middleboxes. The result is a
  "connected but no events" zombie: ``transport_connected`` fires,
  ``socket mode connected, team=X`` logs once, then ``stale.
  Reconnecting... reason: disconnected for 182+ seconds`` repeats
  indefinitely with zero ingress_processed audit rows in between.
  Outbound ``chat.postMessage`` keeps working because the
  ``SlackSDKClient.web`` aiohttp session is independent and honours
  the env vars. Symptom signature: agent self-test message succeeds
  but inbound is silent.

## Known Slack-server quirks observed during smoke (2026-05-21)

These are **server-side** behaviors with no code remediation — recorded
so the next operator doesn't waste time hunting our pipeline when the
symptom is actually Slack drop-on-send.

1. **No buffering during "subscription off" windows.** Socket Mode is
   live-only. Messages sent while Event Subscriptions are disabled (or
   while the socket is between sessions) are dropped at Slack's server
   and never replayed on reconnect. ``conversations.history`` still
   returns them because it's a Web API — but the event bus is gone.
   Symptom: missing audit row + present Slack-side message.
   Reproduced during 1b smoke: ~10 messages sent before the operator
   completed reinstall + Event-Subscriptions toggle → 0 ingress rows.

2. **File-event warm-up after fresh ``files:read`` grant.** Even when
   text-only ``message.im`` events arrive instantly post-reinstall,
   file-bearing events (``message`` + ``files[]``) silently drop for
   ~1–5 minutes. The socket is alive, the scope is present in
   ``auth.test``, and ``conversations.history`` shows the upload — but
   Socket Mode never fires for the event during the window.
   Reproduced 2026-05-21 in Protagolabs workspace: ``Transcript.pdf``
   sent ~3 minutes after reinstall produced no audit; the next PDF
   sent ~3 minutes later succeeded with no other change. Mitigation:
   re-send. No code fix; the symptom self-resolves.
