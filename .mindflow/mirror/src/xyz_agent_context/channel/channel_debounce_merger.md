---
code_file: src/xyz_agent_context/channel/channel_debounce_merger.py
stub: false
last_verified: 2026-05-20
---

## Why it exists

Users in chat apps frequently send 3 messages within a few hundred
milliseconds ("hi", "are you there", "btw, about X"). Running the
agent 3 separate times burns tokens and produces incoherent
half-replies. The merger debounces messages keyed on
``(chat_id, sender_id)`` and flushes once after a quiet window —
inspired by OpenClaw's pattern, missing from NarraNexus today.

## Design decisions

- **Window-based, not count-based.** Each new submit cancels the
  pending timer and rearms it. The flush only fires after the user
  has been quiet for the full window — no "merge after N messages"
  heuristic that would be hard to tune.
- **Last message wins on metadata.** ``message_id`` and
  ``timestamp_ms`` come from the latest submission; content bodies,
  media URLs, and ``raw["attachment_refs"]`` lists all concatenate.
  Reasoning: if the user wrote three messages, the last one is the
  most current view of the conversation, and stale earlier
  message_ids are not useful for ack tracking.
- **Attachment refs are concatenated, NOT replaced** (Phase 1a). When
  a user sends three messages within the window, each carrying a
  different attachment, all three downloads happen in the merged
  ``fetch_attachments`` pass. ``_merge`` deep-copies ``raw`` from the
  latest message before appending so input messages stay immutable
  (regression from an earlier merger version that mutated in place).
- **``asyncio.get_running_loop()``, not ``get_event_loop()``.** The
  merger is created inside the trigger's main loop and must crash
  loudly if a caller forgets to wire it up correctly. ``get_event_loop``
  has a confusing fallback (creates a new loop if none is running)
  that hides the real bug.
- **``flush_all`` for graceful shutdown.** The trigger's ``stop()``
  calls this so messages buffered in the merger are not silently lost
  when the process exits.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase._enqueue_or_debounce`` (only when
  ``DEBOUNCE_WINDOW_MS > 0`` — Lark today has it disabled).
- **Downstream**: the flush callback enqueues into the worker pool's
  task queue.

## Gotchas

- The flush callback is invoked inside ``call_later``'s timer
  scheduling. If the callback raises, we log a warning and swallow —
  losing the buffered message is preferable to crashing the merger
  for every other key.
- ``submit`` uses an ``asyncio.Lock``; flush runs as a coroutine via
  ``ensure_future`` from inside ``call_later``. Tests must use
  ``asyncio.sleep`` (not ``time.sleep``) to advance time so the timer
  actually fires.
