---
code_file: src/xyz_agent_context/channel/channel_inbox_writer.py
stub: false
last_verified: 2026-07-03
---

## 2026-07-03 — turn's inbound/reply stamped 1µs apart (ordering fix)

Both rows of a turn were written at turn end with one shared
``now = utc_now()`` — identical created_at. With ``ORDER BY created_at`` and
no tie-break, two rows sharing a microsecond sort unstably, so the reply
could render above the message it answered (worst on WeChat, whose messages
carry no timestamp of their own — timestamp_ms == 0). Fix: ``inbound_at =
now``, ``reply_at = now + timedelta(microseconds=1)``; created_at alone
orders a turn, utc_now()'s advance between turns keeps turns in completion
order. Read side: inbox route adds a message_id tie-break; the frontend
compares created_at as a microsecond ISO STRING (inboxOrder.ts) rather than
via new Date() (millisecond), which would drop the 1µs gap.

## Why it exists

Generalisation of ``LarkTrigger._write_to_inbox`` +
``_ensure_inbox_entities``. Every IM channel writes the same 5-row
bundle to the MessageBus tables (pseudo-agent, channel, member,
incoming message, optional outgoing). Centralising it here makes the
shape consistent across channels for the frontend Inbox.

## Design decisions

- **Channel-prefixed synthetic IDs**. ``channel_id`` is
  ``f"{channel}_{chat_id}"``; pseudo-agent is
  ``f"{channel}_user_{sender_id}"``. Different IM channels could
  legitimately share a chat_id (some platforms emit short numeric IDs)
  so unprefixed IDs would collide.
- **Display name in the channel row.** ``bus_channels.name`` is
  ``f"{brand_display}: {display_name}"`` — the Inbox UI shows the
  human's name plus the brand badge in one glance.
- **Get-then-insert idempotency.** ``AsyncDatabaseClient`` doesn't
  expose dialect-specific upsert without hand-written SQL, so the
  writer reads first then inserts only when missing. Slow-path-only;
  the inbox bundle is N=1 per inbound message.
- **Description refresh.** When the pseudo-agent already exists with
  a placeholder name (sender previously seen with sender_name="Unknown"
  → display fell back to sender_id) and the new write has a real
  resolved name, the row is updated. Without this, the Inbox would
  forever show open IDs instead of names for the first burst of
  messages from any new sender.
- **Caller injects ``db``.** The writer never imports ``get_db_client``
  — the trigger already holds a handle. Keeps the writer pure-data and
  unit-testable.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase._process_message``.
- **Downstream**: ``bus_agent_registry``, ``bus_channels``,
  ``bus_channel_members``, ``bus_messages`` tables (read by the
  frontend Inbox UI).

## Gotchas

- Re-raises on failure so the trigger's ``_process_message`` can write
  ``EVENT_INBOX_WRITE_FAILED`` to the audit log. The trigger swallows
  the exception itself — the writer just makes the failure observable.
