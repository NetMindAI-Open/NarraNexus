---
code_file: frontend/src/stores/configStore.ts
last_verified: 2026-05-13
stub: false
---

## 2026-05-13 — login/logout 清掉 teamsStore（local 多用户 fix 收尾）

backend identity 那波修完后还有一个前端缓存洞：`teamsStore` 用
zustand persist 把 `teams + loaded` 持久化到 localStorage，而
`TeamFilterBar.tsx:28-30` 看到 `loaded=true` 就跳过 refresh。
两个用户在同一浏览器轮流登录会出现：bob 看到 alice 缓存的 team
chips（因为 `loaded` 在 localStorage 里活着 → 没人触发重新拉）。

修法：`login()` 检测 prevUserId !== userId 时调
`useTeamsStore.setState({ teams: [], loaded: false })`；`logout()`
对称清一遍。下次 TeamFilterBar mount 时 `loaded=false` 触发 refresh
→ 拿到新身份对应的 teams。

为什么 import teamsStore 没循环：teamsStore 只 import 自 `@/lib/api`，
而 api.ts 直接读 localStorage（不 import configStore），所以
configStore→teamsStore→api 是链式无环。

其他 persisted store 排查过：themeStore（全局）、runtimeStore（mode
state）、configStore 自己（logout 已自清）——只有 teamsStore 是
per-user persisted 且有"loaded gate"模式，所以这一次只清它。
artifactStore / chatStore 没用 persist 中间件，自然不受影响。

# configStore.ts — Auth, agent selection, and awareness notification state

## Why it exists

This is the identity and session spine of the frontend. It answers two questions on every render: "who is logged in?" and "which agent am I talking to?". It also owns the red-dot awareness notification system — tracking which agents have updated their awareness profile since the user last looked.

## Upstream / Downstream

Persisted to `localStorage` under the key `narra-nexus-config` via Zustand `persist` middleware. This means `isLoggedIn`, `userId`, `token`, and `agentId` survive page reloads without a re-login.

Consumed by almost everything: `App.tsx` (`ProtectedRoute` / `PublicRoute` routing guards), `wsManager.ts` (reads `token` from `getState()` to inject JWT into the WebSocket handshake), `api.ts` (`getAuthHeaders` reads `state.token` directly from `localStorage` to avoid a circular import with the store), `useAutoRefresh.ts` (reads `agents` and calls `refreshAgents`), `AwarenessPanel.tsx` (calls `clearAwarenessUpdate`), and `Sidebar.tsx` (reads `awarenessUpdatedAgents` for badge dots).

Depends on `api.ts` (`getAgents`, `getAwareness`) and `@/types` for `AgentInfo`.

## Design decisions

**JWT token read from localStorage by `api.ts`, not from the store.** `api.ts` cannot import `useConfigStore` without creating a circular dependency (`api.ts` ← `configStore.ts` ← `api.ts`). The workaround is that `getAuthHeaders` in `api.ts` reads the raw localStorage JSON directly at call time. Brittle but correct given the constraint.

**Awareness update tracking uses a split strategy.** `awarenessUpdatedAgents` (which agents have unseen updates) lives in Zustand (in-memory). The "last seen" timestamp lives in `localStorage` under per-agent keys `lastSeenAwarenessTime:<agentId>`. On `checkAwarenessUpdate`, if the server's `update_time` is newer than the stored timestamp, the agent is added to the set. On `clearAwarenessUpdate`, the timestamp is written and the agent is removed from the set.

**No token refresh.** The JWT is stored as-is. If it expires, `ProtectedRoute` catches the `401` from `api.getAgents` and calls `logout()`. There is no refresh token flow.

**`persist` stores everything.** The `partialize` option is not used, so `agents`, `awarenessUpdatedAgents`, and even empty strings persist. On logout, the store is reset to initial values, which overwrites the persisted entry.

## Gotchas

**Login must store token BEFORE calling `getAgents`.** `LoginPage` calls `login(userId, token)` first, then calls `api.getAgents`. If the order is reversed, `getAuthHeaders` reads no token and the cloud-mode `getAgents` call returns `401`. This was a real bug (commit `b4b58ce`).

**`cloud-web` mode never shows "Change Mode".** If `mode === 'cloud-web'` (force-deployed cloud build), `LoginPage` hides the "Change Mode" button. Calling `logout()` in this mode clears the session but leaves `mode` as `cloud-web` in `runtimeStore`, sending the user directly back to `/login` instead of `/mode-select`.

**`refreshAgents` silently ignores network errors.** Called by `useAutoRefresh` every 30 seconds. A transient backend restart will not clear the agent list — it just logs a console error. This is intentional so a brief backend restart doesn't destroy the UI.
