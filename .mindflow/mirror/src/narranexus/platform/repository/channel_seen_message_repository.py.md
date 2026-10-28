---
code_file: src/narranexus/platform/repository/channel_seen_message_repository.py
stub: false
last_verified: 2026-08-19
---

## Why it exists

Generic version of ``LarkSeenMessageRepository``: same
INSERT-or-detect-UNIQUE atomicity, but keyed on ``(channel,
dedup_key)`` so all IM channels share one ``channel_seen_messages``
table without colliding on duplicate keys.

## 2026-07-16 — the caller owns the dedup-key namespace

``mark_seen``'s param is ``dedup_key`` (was ``message_id``). The stored
value is whatever string the caller passes, NOT necessarily a bare
platform message id. ``ChannelDedupStore`` passes
``f"{agent_id}:{message_id}"`` for multi-agent channels because Matrix
fans the same room event out to EVERY member agent's client — a bare id
would let whichever agent's sync landed first mark it seen and silently
drop every other agent's copy (the 2026-07-16 group-room silent-loss
bug; see the store's mirror md). Single-tenant callers (Lark) pass a
bare id. The physical column stays ``message_id`` (schema unchanged,
铁律 #6). **Any new Layer-3 caller MUST namespace the key by agent when
more than one agent can receive the same platform id** — the rename is
the guardrail against silently re-opening the bug.

## Design decisions

- **Not a ``BaseRepository[T]`` subclass.** The row is two columns +
  a timestamp; the hot path needs only two atomic ops, not CRUD.
  ``BaseRepository`` would force inventing a CRUD shape that nothing
  uses. Same call as the Lark version it generalises.
- **Composite UNIQUE on ``(channel, message_id)``.** Lets the same
  ``om_xxx`` (or whatever the platform emits) appear in different
  channels independently.
- **Fail-open contract on non-UNIQUE errors.** UNIQUE-constraint
  violations are genuine duplicates → return ``False``. Anything else
  (connection lost, disk full, ...) MUST propagate so the trigger can
  fail-open in the dedup cascade. Returning ``False`` on transient
  errors would silently drop user messages — the OPPOSITE of intent.
- **Per-channel cleanup.** ``cleanup_older_than_days`` filters by
  ``channel`` so a Slack outage cannot drag down Lark's retention.

## Upstream / downstream

- **Upstream**: ``ChannelDedupStore`` (Layer 3 of the cascade).
- **Downstream**: ``channel_seen_messages`` table.

## Gotchas

- Unique-violation detection runs through the shared
  [[dialect_errors.py]] ``is_unique_violation`` (dual-dialect; the six
  hand-copied checks converged onto it in PR#327). To onboard a future
  backend, add its sentinel *there* — do NOT re-copy the match into this
  file. Note that predicate-ising also flipped the test from
  case-**sensitive** to case-**insensitive** (it lowercases the message),
  which only widens what counts as a duplicate and is harmless here (a
  UNIQUE hit → drop the replay, never the reverse).
