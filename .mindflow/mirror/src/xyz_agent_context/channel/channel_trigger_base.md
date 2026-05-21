---
code_file: src/xyz_agent_context/channel/channel_trigger_base.py
stub: false
last_verified: 2026-05-21
---

## Why it exists

Phase 1's centerpiece. Direct extraction of the channel-agnostic 80%
of ``LarkTrigger`` into a base class so Slack (Phase 3) and Telegram
(Phase 4) ship without re-implementing dedup, Worker Pool, audit log,
inbox writer, or credential watcher. Lark itself stays put in Phase 1
— Phase 2 will refactor it onto this base.

This is the locus of the architecture's "Pattern C: shared subscriber"
principle (see ``.mindflow/project/references/architecture.md``).

## Design decisions

- **6 abstract methods + 2 PUSH stubs.** Subclasses implement
  ``connect``, ``parse_event``, ``is_echo``, ``resolve_sender_name``,
  ``create_context_builder``, ``load_active_credentials`` and nothing
  else. PUSH-mode stubs (``handle_webhook``, ``verify_webhook``)
  raise ``NotImplementedError`` until Phase 6 — they exist now only so
  the future webhook routes can locate them via the base contract.
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
