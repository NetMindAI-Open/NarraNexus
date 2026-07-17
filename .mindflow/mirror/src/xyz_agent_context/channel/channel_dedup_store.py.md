---
code_file: src/xyz_agent_context/channel/channel_dedup_store.py
stub: false
last_verified: 2026-07-16
---

## 2026-07-16 — Layer 3 (durable DB) partitions by agent_id too

Symptom: in a Matrix group room with N agents, only ONE agent's
`_process_message` fired per user message; the other N-1 silently
lost it. DM was unaffected. Which agent "won" flipped run-to-run.
Silent-batch memory therefore corrupted (only one witness), and if
the winner was an unrelated agent, the intended `@mentioned` agent
never even ran mention detection.

Root cause: Layer 2 (memory) already partitioned by `agent_id`
(`cache_key = f"{agent_id}:{message_id}"`, line 180) but Layer 3
called `repo.mark_seen(message_id)` with the bare id. Matrix fanout
delivers the SAME event_id to every room member's nio client; whichever
`sync_forever` loop tick lost the microsecond race INSERT-committed
first, and every other agent's copy came back `newly_inserted=False`
→ `accept=False, layer="db_dedup"` → audit-log-drop then return.
`_process_message` never fired, so from that agent's point of view
the message did not exist (verified 2026-07-16 via
`channel_trigger_audit` rows for events `$ckUJ2u...`, `$JdpVdu...`,
`$IKPm3V...` — always one `db_new` + one `db_dedup` from the same
room).

Fix: reuse the Layer 2 `cache_key` (already agent-partitioned) as
the argument to `repo.mark_seen`. Schema (`channel_seen_messages`,
`message_id VARCHAR(128)`) accommodates the composite string —
`f"{agent_id}:{message_id}"` is ~70 chars — so no migration and no
new UNIQUE index. Fail-open on Layer 3 I/O is preserved.

`ChannelSeenMessageRepository.mark_seen`'s param was renamed
`message_id` → `dedup_key` and its docstrings now state **the caller
owns the key namespace**: multi-agent channels MUST pass
`f"{agent_id}:{message_id}"`, single-tenant callers (Lark, via
`ChannelDedupStore` with empty `agent_id`) pass a bare id. So the
physical `message_id` column now legitimately holds two shapes —
composite for Matrix/base, bare for Lark — partitioned harmlessly by
the `channel` column. The rename is the guardrail: a future Layer-3
caller reading the old "keyed on (channel, message_id)" contract
would have passed a bare id and re-opened this exact bug.

Regression test note: the same-agent-replay guard MUST use a FRESH
store (cold Layer 2) reusing the same repo — the first draft asserted
on the same store instance, so Layer 2's `memory_dedup` short-circuited
before Layer 3 was ever reached and the assertion would have passed
even against the un-fixed bare-id code. `test_classify_db_layer_
partitions_by_agent_id` now spins up `store_restarted` to actually
exercise the durable composite-key dedup.

Session log: `drafts/logs/narra_guide_v12_incident_2026_07_14.md`
covers the wider Matrix-side incident; this fix is the client-side
group-room half.

## 2026-07-03 — Layer 4: opt-in content-fingerprint window (X1)

Platforms that re-dispatch the same message under a NEW message_id
(NarraMessenger re-issues an invocation when its 15-min server deadline
expires mid-processing) defeat every id-keyed layer — the agent ran and
replied twice (X1). `content_window_seconds` (constructor, default 0=off)
plus a caller-supplied `content_fingerprint` in classify() adds a
memory-window layer that drops a fresh id carrying a known fingerprint.
The window SLIDES — a hit refreshes the stamp (review of PR #51 caught the
fixed-window hole: 20-min window < 30-min worker timeout meant the second
re-dispatch of a long turn was accepted; with sliding, any-length turns
stay covered while re-dispatch intervals < window).
Deliberately in-memory: the re-dispatch lands in the same subscriber
process within the window; a durable fingerprint would keep blocking a
user's genuinely repeated text long after. Mechanism only — WHAT
identifies a message and HOW LONG lives with the trigger
(ChannelTriggerBase._content_fingerprint / CONTENT_DEDUP_WINDOW_SECONDS).

## Why it exists

The three-layer dedup cascade currently inline in
``LarkTrigger._check_and_classify_event`` is generalised here so every
IM channel gets the same correctness guarantees for free. Lark's own
implementation stays unchanged in Phase 1 — Phase 2 will switch the
trigger over.

## Design decisions

- **Three layers, cheapest first**: timestamp baseline (no I/O) →
  in-memory cache (lock-only) → durable DB (one round-trip).
- **``threading.Lock`` not ``asyncio.Lock`` on Layer 2.** SDK callbacks
  (Lark today; pattern preserved for any future channel using a
  thread-based SDK) reach this layer from non-async threads.
  ``classify`` is `async` only because Layer 3 calls into the repo.
- **Baseline is caller-controlled.** ``__init__`` does not default to
  ``time.time()`` because every channel has its own definition of
  "session start". The trigger calls ``update_baseline`` at process
  startup and on every transport reconnect.
- **Fail-open on Layer 3 I/O.** When the DB raises a non-UNIQUE error
  the store accepts the event and stamps ``layer="db_fail_open"`` so
  the audit row records DB-driven double-processing for review. This
  matches the documented "silent loss is worse than rare double-reply"
  contract from Lark.
- **Returns a dict, not just a bool.** Callers (the trigger) want to
  audit which layer rejected each event; a dict carries both ``accept``
  and ``layer`` for that.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase._dedup_and_handle``.
- **Downstream**: ``ChannelSeenMessageRepository.mark_seen``.

## Gotchas

- Layer 2 cleanup (``self._memory_cache = {...}``) replaces the dict
  reference under the lock. Other callers reading the dict in parallel
  see a stable snapshot but lose any concurrent insertion they made
  before the cleanup completed. This is acceptable because the dict is
  only consulted as part of the ``with self._memory_lock:`` block.
- ``baseline_ms`` is monotonic — calls with smaller values are ignored.
  Don't assume "I just received an old reconnect timestamp" will reset
  the filter; the trigger only advances baseline forward.
