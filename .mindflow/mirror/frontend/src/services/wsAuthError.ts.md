---
code_file: frontend/src/services/wsAuthError.ts
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 — `dispatchAuthExpired` → `reportWsAuthFailure`（改走探针）

不再直接派发 `narranexus:auth-expired`，改为把帧交给
[[sessionGuard.ts]] 的 `confirmSessionDeath()`，由 `GET /api/auth/session`
裁决。

原因：`error_type: 'AuthError'` 底下混着并非会话问题的帧——例如 local 模式
"URL 的 x_user_id 与 payload 不符"，那是前端状态 bug。旧实现一律登出。
后端现在给每帧打了 `error_code`（[[websocket]]），本函数把它透传给探针作为
诊断信息，但**判决权在探针**：分类错了最多损失这一次运行，而不是整个会话。

`isAuthErrorMessage` 未改（两通道匹配逻辑照旧），其单测继续有效。

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
