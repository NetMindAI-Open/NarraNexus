---
code_file: src/narranexus/platform/analytics/__init__.py
last_verified: 2026-08-12
stub: false
---

# __init__.py (analytics)

## 2026-08-12 — reverse pointer to the frontend GTM loader (doc-only)

Docstring now notes that third-party WEB analytics on the cloud site is a
SEPARATE, client-side system ([[webAnalytics.ts]], GTM only, event-only, gated
on the same per-user opt-out). This backend sink stays first-party / DB-only /
no-vendor. The note exists so a future reader does not "enable a cloud vendor
sink" here believing cloud has no product analytics. Comment-only; intent below
unchanged.

## Why it exists

This module is the single first-party product-event write surface. Capture
sites call `track()`; the module applies the user's analytics opt-out, stamps
the process surface, and persists the event to `product_analytics_events`.

There is intentionally no vendor sink, SDK, network client, identify call, or
shutdown flush. Cloud writes to its configured RDS database. Local and desktop
write to their local SQLite database, so their product facts never leave the
machine.

## Upstream / downstream

- **Consumed by**: routes and runtime services that record activation,
  messaging, failure, and payment facts.
- **Depends on**: `analytics/surface.py`, `UserSettingsRepository`,
  `ProductAnalyticsRepository`, and the shared database client.
- **Consumed downstream by**: `narranexus-data`, which has read-only access to
  the cloud RDS table. It cannot see local SQLite rows.

## Design decisions

**Database-only persistence**: removing the old sink seam makes the privacy
boundary structural rather than configuration-dependent. Legacy analytics
environment variables cannot reactivate telemetry because no external SDK or
adapter exists in the runtime or desktop bundle.

**Opt-out precedes persistence**: opted-out users produce no product-event
rows. The lookup remains best-effort so analytics never interrupts product
flow; insert failures are logged and swallowed independently.

**Known dimensions plus JSON**: query-critical dimensions are duplicated into
indexed columns. The compact JSON copy is retained for low-volume diagnosis,
but capture sites must never send message text, credentials, email addresses,
or other free-form PII.

**Idempotent event IDs**: persistence delegates to the repository's atomic
insert-first deduplication. The first fact wins and concurrent replays cannot
overwrite its dimensions. Calls without a stable ID receive a UUID.

## Gotchas

- `surface` describes where the database write happened; it is not a routing
  instruction and never sends an event elsewhere.
- The local analytics opt-out still matters because it controls local SQLite
  persistence even though external telemetry no longer exists.
- `track()` is observational and must never break login, chat, or payment.
