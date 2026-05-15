---
code_file: src/xyz_agent_context/repository/channel_trigger_audit_repository.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Generic multi-channel version of ``LarkTriggerAuditRepository``. The
trigger runs in its own EC2 container, where pulling logs out post-
incident is hard. This repository is the trigger's black-box recorder
— every interesting lifecycle event lands in one row.

Phase 1 ships this alongside the existing Lark-specific repo (no
behavioural change to Lark). Phase 2 will redirect Lark writes here
and drop the old repo.

## Design decisions

- **Best-effort writes that NEVER raise.** ``append`` swallows every
  exception and logs to loguru. Losing an audit row is always
  preferable to stalling real user traffic.
- **JSON ``details`` column.** Adding new fields to an event type
  doesn't require a migration — just stash into ``details`` and
  the new field flows into the JSON blob.
- **Per-channel cleanup + filtering.** Every query / cleanup adds
  ``channel = self._channel`` so one channel's bursty volume doesn't
  swamp another's queries.
- **String constants, re-exported.** Event types live in
  ``xyz_agent_context.channel.channel_audit_events``. We re-export
  the common ones from this module so callers don't need a second
  import — same UX as the Lark version.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase._audit`` (the only writer).
- **Downstream**: ``channel_trigger_audit`` table; consumed by future
  /healthz endpoints and admin UIs.
