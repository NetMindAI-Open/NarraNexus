---
code_file: src/xyz_agent_context/channel/channel_trigger_base.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — "ack early" moved into the per-turn input (salience)

The early-feedback directive used to live in each module's `get_instructions`
(system prompt) where models deprioritized it. It now rides in the **per-turn
input** instead: `_build_and_run_agent` prepends `_early_feedback_prefix(message)`
to `tagged_prompt` (right after the channel tag), so the "ACK FIRST" line sits
inline with THIS message — higher salience. `_early_feedback_prefix` uses the
new `react_tool_ref` class attr (bare name / Lark's `mcp__…` / None for WeChat →
message-only ack) + the real room/message ids, via `render_early_feedback`.
Still a SHOULD (prompt, not a hard guarantee). Lark's `_build_and_run_agent`
override injects the same prefix.

## 2026-07-10 — surface source_message_id (agent-driven feedback enabler)

`_build_and_run_agent` adds `source_message_id` (the inbound platform message
id) to `trigger_extra_data`. It merges into `ctx_data.extra_data`, so a channel
module's get_instructions can tell the agent which message to react to /
reply in-thread (the agent-facing `react_to_user_message` tool). Kept here, not
in ChannelTag, so it stays ephemeral (not persisted into chat-history tags).

## 2026-07-08 — `pre_start(db)` hook added for the consolidated supervisor

New optional lifecycle hook `pre_start(db)` (default no-op), called by the
consolidated supervisor (`module/run_channel_triggers.py`) BEFORE `start(db)`.
Subclasses override it to run their own idempotent one-off migration inside the
channel instead of in the shared entrypoint (rule #4). First user:
`LarkTrigger.pre_start` carries the legacy `auth_status` migration that used to
live in the now-deleted `run_lark_trigger` entrypoint. The design decision
"6 abstract methods + 1 optional hook + 2 PUSH stubs" below now reads "…+ 2
optional hooks…" (`fetch_attachments` and `pre_start`). Consolidation relies on
`start()` already being non-blocking + all state being per-instance, so N
triggers coexist in one event loop.

## 2026-07-07 — error-fallback: surface run failures INTO the channel

Problem: IM delivery is "agent calls its own reply tool during the run; the
trigger only scrapes the sent text for the inbox." So if a run FAILED before
the agent reached its reply tool, nothing was sent to the channel — the user
saw silence, indistinguishable from the agent choosing not to answer. Chat had
a helper_llm fallback; IM was excluded. (slack/discord/telegram/wechat wrote
the error to the inbox ONLY; lark already sent it; matrix uses streaming
markers.)

Fix — three pieces, all in `_build_and_run_agent`:
1. New overridable hook `send_channel_reply(credential, message, text)` —
   default no-op; each IM subclass implements it with the per-subscriber SDK
   client it already holds, addressing via `message.chat_id` / `sender_id` /
   `raw`. The runtime CANNOT do this itself (it has no channel client — those
   live on the trigger), which is why the fallback lives at the trigger layer.
2. `_send_error_fallback(...)` sends the error via the hook UNLESS the agent
   already replied this turn (`already_replied` → don't double-message),
   best-effort (a send failure is logged, never masks the original error).
3. `_build_and_run_agent` now: wraps `run_and_collect` in try/except (a hard
   raise, not just a yielded ERROR, still notifies — no silent crash); on
   `result.is_error` computes `already_replied` from `extract_output` vs
   `CHANNEL_SILENT_SENTINEL` and fires the fallback.

**Key safety property**: the fallback fires ONLY on `is_error` (or a raise). A
run that stays silent by CHOICE never sets `is_error`, so intended silence
(group non-@, nothing to add — see `_build_and_run_agent_silent_batch`) is
never disturbed. This deliberately does NOT recover the "agent wrote a reply
but forgot to call the send tool" (`no_reply`) case for IM — too ambiguous vs
intended silence; only errors are surfaced.

`CHANNEL_SILENT_SENTINEL = "(stayed silent)"` is now a shared module constant
(was hard-coded identically in 5 channels) so the base can tell "agent stayed
silent" from "agent replied" when gating the fallback. Lark's bespoke error
send was consolidated onto the same hook.

## 2026-07-02 — `_build_and_run_agent_silent_batch` for group non-@ ingestion

New instance method on the base: takes a non-empty list of
`ParsedMessage` (same chat_id, chronological), merges into one
`input_content` line-per-message with `[ts] Display: body`, then calls
`get_agent_runtime_client().run_and_collect(..., silent=True,
trigger_extra_data={"batch_messages": [...]})`. Per-message metadata
(event_id / timestamp / sender_id / sender_name / attachments) rides
in `batch_messages`; ChatModule's silent-batch write path (see
[[chat_module.py]]) reads it and appends N user rows to
`instance_json_format_memory` with NO assistant row.

Why here (channel-agnostic): all IM triggers have the same "group
message that didn't @ us" shape — Slack currently drops these at the
event boundary, Lark runs the full agent on every message, both
suboptimal. Landing the silent-batch shape on the base means each
trigger only needs a classification step (dm / group_mention /
group_silent) + a debounced flush; the batch runtime plumbing is
shared. Matrix (Commit 4b) is the first consumer.

The method is fire-and-forget for output: silent runs produce no
user-facing text, so no return value; failures inside the runtime
call are logged and swallowed to keep the sync loop / debounce timer
advancing (a dropped batch is recoverable via reconnect + since_token
replay, a crashed trigger is not).

## 2026-07-03 — CONTENT_DEDUP_WINDOW_SECONDS + _content_fingerprint (X1)

New opt-in class attr (default 0) wires ChannelDedupStore's
content-fingerprint layer; `_content_fingerprint` hashes
(chat_id|sender_id|content) → sha256[:32]. Policy stays in the subclass
(NarramessengerTrigger sets 20 min to cover the platform's 15-min
re-dispatch deadline); base computes the fingerprint so every channel
shares one identity definition. Chosen over shrinking
PROCESS_MESSAGE_TIMEOUT below the platform deadline, which would cut slow
LLM turns short (铁律 #14).

## 2026-07-03 — unparsed raw events now audited (`_on_unparsed`)

`parse_event(raw) -> None` (stickers/images/voice on text-only channels)
used to hit a bare `continue` — no log, no audit row, unanswerable "why
didn't the bot reply?" tickets (lessons #3/#5; 2026-07-03 wechat incident
burned an hour proving a message was never parseable). The subscriber loop
now calls `_on_unparsed`, which writes `ingress_dropped_unparsed` with the
raw item's KEYS only (never payloads — media bytes / text stay out of the
audit table).

> Concrete subclasses today: ``LarkTrigger``, ``SlackTrigger``,
> ``TelegramTrigger``, ``DiscordTrigger``. (The file docstring's old
> "Lark is NOT a subclass" line was stale; corrected when Discord landed
> — all four are subclasses now.)

## Why it exists

Phase 1's centerpiece. Direct extraction of the channel-agnostic 80%
of ``LarkTrigger`` into a base class so Slack (Phase 3) and Telegram
(Phase 4) ship without re-implementing dedup, Worker Pool, audit log,
inbox writer, or credential watcher. Lark itself stays put in Phase 1
— Phase 2 will refactor it onto this base.

This is the locus of the architecture's "Pattern C: shared subscriber"
principle (see ``.mindflow/project/references/architecture.md``).

## Design decisions

- **6 abstract methods + 1 optional hook + 2 PUSH stubs.** Subclasses
  implement ``connect``, ``parse_event``, ``is_echo``,
  ``resolve_sender_name``, ``create_context_builder``,
  ``load_active_credentials``. The optional ``fetch_attachments`` hook
  (added in Phase 1a, default returns ``[]``) lets channels with media
  ingestion override without forcing text-only channels to do anything.
  PUSH-mode stubs (``handle_webhook``, ``verify_webhook``) raise
  ``NotImplementedError`` until Phase 6.
- **``_persist_attachment`` helper.** Lives in the base because the
  download → MIME sniff → on-disk store → optional STT path is fully
  channel-agnostic. Each channel subclass downloads bytes from its own
  SDK then hands them to this helper. Workspace path resolution mirrors
  WS upload exactly (``_resolve_agent_owner(agent_id) or agent_id``)
  so the agent's Read tool finds the file at the same path the
  attachment was written to.
- **Attachment list flows via ``trigger_extra_data["attachments"]``.**
  Mirrors ``backend/routes/websocket.py:644-648`` so ChatModule's
  ``hook_data_gathering`` (which reads
  ``ctx_data.extra_data["attachments"]``) treats IM-uploaded and
  WS-uploaded files identically. The base only sets the key when the
  list is non-empty — keeps text-only audits noise-free.

- **Caption-less file uploads MUST flow** (Phase 1b regression-fix).
  The empty-content guard in ``_process_message``:
  ``if not message.content.strip(): return`` was originally written
  in Phase 1a when ParsedMessage was text-only — an empty content
  was a clear no-op. Phase 1b made files first-class, but the guard
  wasn't updated. Real-world failure mode: user drag-drops a PDF
  into Slack DM without typing anything → ``text=""`` +
  ``files=[...]``. parse_event correctly extracted ``attachment_refs``
  into ``raw``, but the base guard cut the message off BEFORE
  ``fetch_attachments`` could ever run, with NO audit row at all
  (the audit trail just stopped at ``debounce_merged``). The guard
  now keeps the early-return only when BOTH ``content`` is empty
  AND ``raw["attachment_refs"]`` is empty. Pin tested by
  ``tests/channel/test_attachment_fetch_pipeline.py::test_caption_less_file_upload_still_processed``.
- **Lazy AgentRuntime import.** Eager top-level import causes a
  circular load: ``channel/__init__.py`` re-exports
  ``ChannelTriggerBase`` for ergonomic use, but
  ``module/__init__.py`` (loading LarkModule) reaches the channel
  package first, so importing AgentRuntime here would re-enter the
  partially-initialised module package. Lazy-loading inside
  ``_build_and_run_agent`` breaks the cycle without forcing callers
  to import from the longer ``channel.channel_trigger_base`` path.
- **Tunable class attributes, not constructor args.** ``MIN_WORKERS``,
  ``MAX_WORKERS``, ``PROCESS_MESSAGE_TIMEOUT_SECONDS`` etc. are class
  attributes so subclasses can override them in 1 line; constructor
  args reserved for instance state (``base_workers``,
  ``history_config``).
- **``_subscriber_key`` override hook.** Defaults to
  ``credential.app_id`` (Lark today). Slack workspace install can
  serve multiple agents per ``team_id``; that channel will override
  to a compound key without changing the base.
- **Owner resolution via ``agents.created_by``.** AgentRuntime needs
  the agent's OWNER user_id to map provider quotas, NEVER the IM
  sender_id. This bug bit Lark previously and is fixed once here.
- **Per-message timeout, not just stream timeout.** The 30-min
  ``PROCESS_MESSAGE_TIMEOUT_SECONDS`` cap exists because
  ``collect_run`` only times out on stream silence, not total
  wall-clock — without this a stuck LLM call could occupy a worker
  forever.
- **``_prune_dead_workers`` between sizing decisions.** A worker that
  silently dies (cancellation leak, async-for oddity) would otherwise
  keep ``_adjust_workers`` from spawning a replacement, leaving the
  queue to grow unbounded. Lark's H-4 fix preserved here.
- **Credential cache is refreshed every poll, not just on subscriber
  start.** The credential is a DB snapshot whose ``permission_state`` /
  ``auth_status`` change mid-session — most importantly when the owner
  completes the three-click user authorization, which is what flips
  ``LarkCredential.user_oauth_ok()`` and lets ``resolve_sender_name``
  read names via the user token. The transport (``connect``) captures
  its credential once and the long-lived stream never re-reads it, so
  ``_credential_watcher`` overwrites ``_subscriber_creds[key]`` with the
  fresh DB snapshot on every poll, and ``_worker`` re-resolves the
  credential from that cache at dequeue time. Net effect: a mid-session
  auth completion (or any credential change) takes effect on the next
  message without restarting the subscriber or dropping the connection.
  Found on EC2: every Lark sender stayed "Unknown" because the running
  subscriber was started before the owner finished authorizing.

## Upstream / downstream

- **Upstream**: subclasses (Phase 2 will rebase ``LarkTrigger``;
  Phase 3 adds ``SlackTrigger``; Phase 4 adds ``TelegramTrigger``).
- **Downstream**:
  - ``ChannelDedupStore`` (3-layer dedup)
  - ``ChannelDebounceMerger`` (optional)
  - ``ChannelInboxWriter`` (5-row bundle)
  - ``ChannelTriggerAuditRepository`` (audit log)
  - ``ChannelSeenMessageRepository`` (durable dedup, owned by the
    dedup store)
  - ``AgentRuntime`` + ``collect_run`` (lazy import)
  - ``ChannelTag`` (prompt injection)

## Gotchas

- ``working_source`` defaults to ``WorkingSource.CHAT`` because every
  enum value must exist before the subclass picks one — Phase 1
  doesn't add ``WorkingSource.SLACK`` etc. Subclasses MUST set this
  to a meaningful value before ``start()``.
- ``handle_webhook`` raises ``NotImplementedError`` with a Phase-6
  reference — do NOT remove the references; webhooks need both
  HTTP routing AND a per-channel parser, neither of which is built.
- ``stop()`` flushes any debounce-buffered messages then drops them
  with a log line. We don't try to enqueue them on shutdown because
  the credential context (set per-flush in
  ``_enqueue_or_debounce``'s closure) isn't reachable from
  ``flush_all``. If a future channel really needs strict shutdown
  drain semantics, encode the credential into ParsedMessage.raw and
  reconstruct in ``_enqueue_debounced``.
