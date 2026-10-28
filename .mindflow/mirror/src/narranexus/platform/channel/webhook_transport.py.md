---
code_file: src/narranexus/platform/channel/webhook_transport.py
last_verified: 2026-09-07
stub: false
---

# channel/webhook_transport.py — webhook channels

## Intent

`WebhookChannelTriggerBase`: a channel trigger whose `connect()` drains the inbox for its credential instead of holding a socket, and whose `load_active_credentials()` is the generic store's active rows — so a webhook channel implements only the parsing half of the trigger contract. `verify_webhook` is the endpoint's authentication: the binding's `webhook_secret` as a plain token (`X-Webhook-Token` / `?token=`) or an HMAC-SHA256 of the raw body (`X-Webhook-Signature: sha256=…`), constant-time compared. Constructed as `cls(max_workers=3)` like every channel trigger.

## 2026-09-07 — retention sweep purges claimed inbox rows

WebhookChannelTriggerBase overrides _run_cleanup (the trigger's daily retention sweep) to purge claimed inbox rows older than WEBHOOK_EVENT_RETENTION_DAYS (7). Before this purge_claimed had no caller at all and the table grew without bound.

## 2026-09-07 — token comparison on bytes

hmac.compare_digest on str raised TypeError (a 500) for a non-ASCII X-Webhook-Token; comparing UTF-8 bytes makes a malformed header a plain 401.

## 2026-09-07 — verify_webhook has no query-token arm

Header token or HMAC signature only.
