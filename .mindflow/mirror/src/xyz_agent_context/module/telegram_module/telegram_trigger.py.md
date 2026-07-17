---
code_file: src/xyz_agent_context/module/telegram_module/telegram_trigger.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — react_tool_ref = "react_to_user_message"

Sets the class attr `react_tool_ref` (bare tool name) so the base trigger's
`_early_feedback_prefix` injects the per-turn "ack early" directive with
Telegram's react tool. (Mirror also renamed `.md` → `.py.md` for the sync bot.)

## Why it exists

Phase 4 long-poll trigger built on the Phase 1 ``ChannelTriggerBase``.
Each active ``channel_telegram_credentials`` row gets one ``getUpdates``
loop; the base class owns dedup, debounce, worker pool, audit log, and
inbox writing. This subclass only fills the abstract surface
(connect/parse/echo/sender-name/builder) plus an ``extract_output``
override.

Telegram-specific reasons it differs from the Slack trigger:
- No webhook (no public IP needed). Pure long-poll via ``getUpdates``.
- ``getUpdates`` and webhook are mutually exclusive — collisions
  surface as 409 Conflict, handled inline.
- No ``conversation_history`` API on the Bot API (handled in the
  context builder, not here).

## Design decisions

- **Long-poll with ``timeout=30s`` server-side.** Default Bot API
  pattern. Idle wake-up sleep of 0.5s lets ``self.running`` be checked
  promptly when the bot is quiet so ``stop()`` returns within ~1s.
- **Client total timeout 35s > poll timeout 30s.** Anything less and
  aiohttp aborts the long-poll mid-flight; documented in
  ``TelegramSDKClient.__init__`` and again here for symmetry.
- **409 Conflict recovery.** If ``getUpdates`` returns "terminated by
  setWebhook", the bind flow's defensive ``deleteWebhook`` failed (or
  the user re-set a webhook out of band). We call ``deleteWebhook``
  again and retry without bubbling the error to the base's reconnect
  backoff — would otherwise mark the credential unhealthy on a fully
  recoverable error.
- **``extract_output`` scrapes ``tg_cli`` tool-call args, NOT
  ``output_text``.** This is the **load-bearing Phase 3 regression
  prevention.** Slack v1 used ``result.output_text`` which contained
  the agent's reasoning ("My thought process: ...") and leaked
  chain-of-thought into the inbox. Telegram is built right from the
  start — pull the ``text`` arg out of the ``method=sendMessage``
  ``tg_cli`` call. There is a unit test pinning this; if you "simplify"
  to ``output_text``, the test fails.
- **``_extract_tg_reply`` only returns text for ``method=sendMessage``.**
  Other methods (``sendChatAction``, ``deleteMessage``,
  ``editMessageText``, ``setMessageReaction``) are not user-visible
  reply text. They legitimately fire during a turn — including them
  would clutter the inbox.
- **Empty-replies show as ``"(stayed silent)"``.** Distinguishes "agent
  ran but produced no message" from "agent crashed" in the inbox.
- **Phase 1a — multimodal ingestion enabled.** ``parse_event`` now
  extracts ``document`` / ``photo`` / ``voice`` / ``audio`` / ``video``
  payloads into ``raw["attachment_refs"]`` and falls back to ``caption``
  for ``content`` when ``text`` is empty. Stickers / locations /
  contacts / polls still return ``None`` (out of scope). ``photo`` is a
  ``PhotoSize[]`` — the trigger picks ``[-1]`` (largest).
  ``caption_entities`` are merged with ``entities`` so @-mentions
  inside captioned media are still detected.
- **``fetch_attachments`` (Phase 1a override).** Iterates
  ``raw["attachment_refs"]``, calls ``TelegramSDKClient.download_file``
  (two-step ``getFile`` → binary GET) per ref, then hands bytes to the
  base's ``_persist_attachment``. Never-raises: failures audit and skip
  while remaining refs still flow. Three failure modes get distinct
  audit events: backend ``max_upload_bytes`` exceeded
  (``EVENT_INGRESS_DROPPED_OVERSIZED``), Telegram 20 MB platform cap
  hit (same event, different ``reason``), network/getFile errors
  (``EVENT_ATTACHMENT_FETCH_FAILED``). Success →
  ``EVENT_ATTACHMENT_PERSISTED`` with ``has_transcript`` so ops can
  spot STT regressions for audio.
- **``allowed_updates=["message"]``.** Phase 4 ignores
  ``edited_message`` / ``callback_query`` / ``inline_query``.
- **``chat_type`` collapses supergroup/channel into ``GROUP``** —
  reply path is identical. ``private`` → ``ChatType.PRIVATE``.
- **``thread_id`` carries supergroup forum topic id** so the
  Module's ``send_to_agent`` reply-in-thread path works.
- **``mentions`` parsed from ``entities``** — both ``mention`` (plain
  ``@username``) and ``text_mention`` (inline user object) shapes.
- **``resolve_sender_name`` returns ``sender_id`` rather than burning
  an API call** — Telegram has no general user-by-id API outside a
  chat context, and ``parse_event`` already extracts first/last from
  the message payload.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase`` (Phase 1 base).
- **Calls**: ``TelegramSDKClient.get_updates`` / ``delete_webhook``,
  ``TelegramCredentialManager.list_active``,
  ``TelegramContextBuilder``.
- **Schemas read**: ``ParsedMessage``, ``ChatType``, ``MessageContentType``.

## Gotchas

- Removing the 409 inline retry will make Telegram credentials look
  unhealthy after any bind flow that didn't run ``deleteWebhook``.
- Reverting ``extract_output`` to ``result.output_text`` re-creates
  the Phase 3 chain-of-thought leak. Unit test pins this.
- ``_poll_offsets`` is per-credential and process-local. A restart
  starts the loop from offset 0; Telegram only retains updates for 24h
  so the worst case is replaying the last day's messages, which the
  ``DEDUP_TTL_SECONDS=600`` window catches for recent ones. Stale
  >10-minute-old messages may double-process across restart — accept.
- ``re`` is imported but currently unused at module scope (kept for
  future entity parsing). Don't strip it without checking.

## Late owner resolution (`_process_message` override)

Telegram's ``getChat`` Bot API **does not accept @username for regular
user accounts** — only supergroups, channels, and bots can be looked up
that way. (The doc explicitly says "username of the target supergroup or
channel".) So at bind time we cannot resolve ``owner_username`` to a
numeric ``user_id`` for a normal Telegram user. That call almost always
returns ``Bad Request: chat_not_found`` for user @handles.

The canonical resolution path moved INTO the trigger:

1. Bind stores ``owner_username`` (the lock) + leaves ``owner_user_id``
   empty.
2. ``_process_message`` override checks: if ``owner_username`` is set
   AND ``owner_user_id`` is still empty, call ``_maybe_resolve_owner``
   BEFORE the base's processing.
3. ``_maybe_resolve_owner`` extracts ``message.raw.message.from.username``,
   compares case-insensitively to ``credential.owner_username``. On
   match, writes ``owner_user_id`` and ``owner_name`` via
   ``TelegramCredentialManager.update_owner``, plus mutates the
   in-memory credential so the rest of THIS turn sees the resolved
   owner.

**Security model**: this is **NOT** "first DM wins". A stranger DM'ing
the bot first cannot claim ownership because their ``from.username``
won't match the stored lock. Telegram @username ownership is globally
unique and stable — matching the handle on first contact functionally
proves "you control this handle on Telegram". The lock is the same
strength as Slack's bind-time ``users.lookupByEmail`` result, just
deferred to first-DM-time because Telegram's API forces the deferral.

Edge cases:
- User without a public @username: ``message.raw.from.username`` is
  empty → match always fails → owner stays unresolved. Phase 4 doesn't
  support these users; they'd need a numeric-user_id binding path (not
  built).
- Username changed since bind: stored ``owner_username`` no longer
  matches the new value → resolution never fires. User must rebind.
- Already resolved: no-op (idempotent guard on ``owner_user_id`` being
  empty).
