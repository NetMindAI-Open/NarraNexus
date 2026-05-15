---
code_file: src/xyz_agent_context/channel/channel_dedup_store.py
stub: false
last_verified: 2026-05-08
---

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
