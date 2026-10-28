---
code_file: src/narranexus/platform/browser/_browser_impl/control.py
last_verified: 2026-09-23
stub: false
---

# Exclusive control arbitration

A takeover belongs to a connection identifier, not to an undifferentiated user.
Only that connection can provide input or release control; disconnecting a
watcher cannot release another panel. Subscribers receive state changes so all
panels agree on holder, owner_connection_id and whether agent work is waiting.

Queued agent actions have no time limit. Waking a waiter does not prove it still
owns control: it rechecks after each wake because another panel may already have
taken over. Cancellation removes waiters and session closure fails them instead
of leaving promises unresolved. BrowserSession adds an operation lock around
actual actions and handovers so an already-running action finishes before a
takeover becomes effective.
