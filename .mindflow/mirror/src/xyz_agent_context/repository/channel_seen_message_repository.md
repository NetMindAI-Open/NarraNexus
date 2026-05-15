---
code_file: src/xyz_agent_context/repository/channel_seen_message_repository.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Generic version of ``LarkSeenMessageRepository``: same
INSERT-or-detect-UNIQUE atomicity, but keyed on ``(channel,
message_id)`` so all IM channels share one ``channel_seen_messages``
table without colliding on duplicate ``message_id`` values.

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

- Error-string matching covers sqlite ("UNIQUE constraint failed"),
  mysql ("Duplicate entry"), and the mysql error-code form ("1062").
  If a future backend lands, add its sentinel here — silently
  returning ``True`` on a UNIQUE violation would double-process
  every replay.
