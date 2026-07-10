---
code_file: src/xyz_agent_context/channel/channel_audit_events.py
stub: false
last_verified: 2026-07-03
---

## 2026-07-03 — `EVENT_INGRESS_DROPPED_UNPARSED`

New ingress constant for raw events rejected by parse_event (unsupported
message types). Emitted by ChannelTriggerBase._on_unparsed.

## 2026-07-02 — `EVENT_TRANSPORT_SEND_FAILED` (Matrix Commit 4b)

New shared audit type for reply-side transport failures. Added when
MatrixTrigger's `_send_matrix_reply` exhausts retries or hits a
permanent auth error on `client.room_send`. Kept generic so any
channel whose reply path can fail out-of-band after the agent
finished can emit it — Slack / Telegram might too once their reply
tools land on this pattern. Distinct from `inbox_write_failed`:
that one is our own DB row; this one is the platform refusing our
outbound message. Details carry `error_code` (M_LIMIT_EXCEEDED /
M_UNKNOWN_TOKEN / transport_exception / no_active_client / …),
`attempts`, and a truncated `body_preview` for post-mortem.

## Why it exists

Single source of truth for the event-type strings that flow into
``channel_trigger_audit.event_type``. Module-level string constants
(NOT an enum) so callers can grep for them and the DB column stays a
plain VARCHAR — adding a new event type does not require a schema
migration.

## Design decisions

- **``transport_*`` instead of ``ws_*``**. The Lark trigger's audit
  uses ``ws_connected`` etc. because Lark is exclusively WebSocket.
  Telegram long-polls and Slack offers Socket Mode + Event API, so
  the abstraction layer uses the more general ``transport_*`` prefix.
  Phase 2 will redirect Lark writes here too — until then the two
  vocabularies coexist.
- **``EVENT_DEBOUNCE_MERGED`` is new** — Lark today has no debounce.
  Phase 1 ships the merger and the audit type together so post-incident
  reviewers can correlate "user sent 3 in a row" with "agent ran once".
- **Phase 1a attachment-ingestion trio**:
  ``EVENT_INGRESS_DROPPED_OVERSIZED``,
  ``EVENT_ATTACHMENT_FETCH_FAILED``, ``EVENT_ATTACHMENT_PERSISTED``.
  Emitted by ``ChannelTriggerBase`` + per-channel
  ``fetch_attachments``. Kept distinct so ops can tell platform-cap
  refusals apart from network failures apart from happy-path persists.
  All three strings are ≤ 32 chars to keep the ``event_type`` index
  lean (see ``tests/channel/test_audit_events_attachment.py``).

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase`` (writes most events);
  ``ChannelTriggerAuditRepository`` (re-exports for caller convenience).
- **Downstream**: any /healthz endpoint or admin UI that surfaces
  audit data.
