---
code_file: frontend/src/lib/productAnalytics.ts
last_verified: 2026-08-27
stub: false
---

# productAnalytics.ts — non-blocking first-party browser capture

Centralizes authenticated event ingestion, assigning a per-tab session ID and
an idempotency ID per fact. Capture is fire-and-forget so measurement cannot
delay login, chat, rendering, or checkout. Payloads contain only controlled
identifiers and timings, never product content.

Setup page actions use this same capture path; the older ApiClient funnel
method and `/api/auth/funnel` route no longer form a second ingestion contract.

Requests use Fetch `keepalive` because `/pay` records checkout-open immediately
before replacing the page with Stripe; without it navigation can cancel the
only conversion signal.

## 2026-08-27 — funnel events renamed

`setup_entered / setup_completed / setup_skipped` became
`welcome_entered / welcome_completed / welcome_skipped` when the `/setup`
provider page became step 1 of [[WelcomePage]]. Same funnel shape, one screen
earlier: `welcome_completed` now means the user reached the end of the whole
first-run flow (or had nothing left to do), not just that they pasted a key.
