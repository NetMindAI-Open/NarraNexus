---
code_file: src/xyz_agent_context/schema/parsed_message.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Single canonical struct for inbound IM messages. Every IM channel
(Lark, Slack, Telegram, ...) parses its platform-specific event into
``ParsedMessage`` before entering the trigger pipeline. Without this,
each channel's dedup / debounce / worker code would have to know each
platform's event shape.

## Design decisions

- **Dataclass, not Pydantic** — internal-only, no validation needed,
  zero serialization overhead. Matches the project pattern for
  internal value types.
- **Enums inherit ``str``** — ``content_type`` and ``chat_type`` are
  JSON-serialisable without a custom encoder, same trick
  ``WorkingSource`` uses. Lets ``trigger_extra_data`` round-trip
  cleanly through audit / log writes.
- **``raw: dict`` pass-through** — channel-specific bits (Lark's
  ``sender_type``, Slack's ``thread_ts`` reply context, Telegram's
  ``message_thread_id``) live here without polluting the canonical
  struct. Subclasses read it back when they need platform context.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase.parse_event`` (subclasses build
  ParsedMessage from raw events).
- **Downstream**: ``ChannelDedupStore.classify`` (uses
  ``message_id`` + ``timestamp_ms``); ``ChannelDebounceMerger``
  (groups by ``chat_id`` + ``sender_id``);
  ``ChannelInboxWriter`` (stamps content + sender into inbox rows);
  ``ChannelTriggerBase._build_and_run_agent`` (feeds the agent).
