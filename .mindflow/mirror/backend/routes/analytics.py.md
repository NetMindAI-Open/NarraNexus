---
code_file: backend/routes/analytics.py
last_verified: 2026-08-10
stub: false
---

# analytics.py — authenticated first-party event ingestion

The browser reports only an enumerated set of lifecycle events here. Identity
comes from auth middleware; arbitrary event names and free-form properties are
rejected. The route writes operational facts only to the product database
through the shared analytics API; there is no external telemetry destination.

Client event IDs are validated, then hashed with authenticated user and event
name into an `fe:` namespace. A browser therefore cannot reserve backend IDs
such as `run_started:*`, while retries remain idempotent without storing raw
user IDs in the key. A per-user sliding window accepts normal lifecycle bursts
but returns 429 for an event firehose. Message text, credentials, email, and
other PII must never be added to this request model.
