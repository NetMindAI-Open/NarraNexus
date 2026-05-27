---
code_file: frontend/src/services/wsAuthError.ts
last_verified: 2026-05-27
stub: false
---

# wsAuthError.ts — WS auth-error detection helper

## Why it exists

`wsManager.ts` has two onmessage handlers (one in `run()` for a fresh
agent run, one in `reconnect()` for resuming an in-flight run). Both
need the same logic: if the backend sent an AuthError frame, fire the
app-wide `narranexus:auth-expired` event so App.tsx can logout the
user and surface the "session expired" banner. Extracting the
detection + dispatch into this module keeps the two handlers in sync
and makes the logic unit-testable without spinning up a real
WebSocket.

## Upstream / Downstream

- **Used by**: [[wsManager]] (both `run()` and `reconnect()` onmessage).
- **Listened by**: [[App]] via `window.addEventListener('narranexus:auth-expired', ...)`.

## Design decisions

**Two-channel match.** Backend sends `error_type: 'AuthError'` on
every auth-rejection frame (see `backend/routes/websocket.py:426-499`),
so that field is the primary signal. The fallback substring match
on `error_message` (`token expired` / `invalid token` /
`authentication required`) is belt-and-braces — if a future code
path produces an auth frame without `error_type`, the message text
still trips the bridge.

**No retry / no backoff.** The hook is fire-and-forget. logout()
clears local auth state; if the user re-logs in, the next WS open
will use a fresh JWT. There's nothing to retry at the WS layer.

## Gotchas

The dispatcher uses a `CustomEvent` with no payload. App.tsx's
handler is idempotent — it bails on `!isLoggedIn` — so duplicate
fires (e.g., REST 401 and WS AuthError in the same second) don't
re-toast or re-logout.
