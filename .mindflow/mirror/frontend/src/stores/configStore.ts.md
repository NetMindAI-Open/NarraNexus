---
code_file: frontend/src/stores/configStore.ts
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — `refreshAgents` 被服务端拒绝时不再一声不吭

`refreshAgents` 原来是 `if (res.success) set({agents})`，**没有 else**：服务端
答 200 + `{success:false}` 时既不更新也不出声。而 `agents` 是 persist 的、
没有 `partialize`（见下方 2026-05-13 条与 "persist stores everything"），所以
它留在 store 里的那份就是**下一次页面加载会显示**的那份 —— UI 会继续自信地
渲染可能已经过期的名字。

「拒绝时保留旧列表」是**故意**的（后端抖一下不该把侧栏清空，见 Gotchas 里
`refreshAgents` 那条），这次没有改这个决定；改掉的是**静默**：多一个 else 记
`console.error('Agent list refresh refused by server:', res.error)`。
`/api/auth/agents` 对 handler 里任何未处理异常都答 200 + `{success:false}`
（[[App.tsx]] 里 ProtectedRoute 的注释写过同一件事），所以这个分支正是
「UI 明知可能不准还在照显示」的那个分支——铁律侧的「不许静默吞错」。

背景工单：深圳线下第二轮 P1「改名后前端显示回退旧名」。该单的**根因在后端**
（[[auth.py]] 2026-08-17 条：rowcount 方言差异把已落库的改名报成失败），本条
是同族的第二个洞——真正的刷新失败会以「显示旧名」的形态出现，与「没保存成」
在用户眼里无法区分。

测试：`frontend/src/stores/__tests__/configStore.refreshAgents.test.ts`
（成功替换 / 拒绝保留且报错 / 网络失败保留且报错 / 无身份不发请求）。

## 2026-08-06 — `login()` 重置 session guard

`login()` 首行调 `resetSessionGuard()`（[[sessionGuard.ts]]）。该模块用一个
`confirmed` 闩锁保证一批并发 401 只登出一次；不在拿到新凭证时清掉它，新
会话就永远不会再被探测了。

另外记一笔（本次核对确认，无需改代码）：`logout()` **不**清聊天草稿。草稿
存在 `narra-nexus-chat-drafts` 这个独立 key 里，Composer 在卸载时 flush，
所以强制登出后半句话还在。`chatDrafts.logout.test.ts` 把这个性质钉住了，
防止将来某次"登出时清干净"的清理顺手带走它。

## 2026-06-11 — NetMind dual-token + display profile fields

Added three new persisted fields for the NetMind auth integration:

- `netmindToken: string` — the raw NetMind `loginToken` retained after OAuth
  handshake. Not used for our own JWT API calls; reserved for Phase 2/3 NetMind
  Power actions (credits exchange, api-key generation). Stored separately from
  `token` (our own JWT) so the two auth systems stay independent.
- `displayName: string` — human-readable nickname from NetMind profile. Needed
  because `userId` is now the opaque 32-hex `userSystemCode` from NetMind, which
  is meaningless to show in UI.
- `email: string` — NetMind account email, stored for profile display and
  potential future identity matching.

`login()` signature extended: fourth optional parameter `profile?: { displayName?: string; email?: string }`. Callers that only pass `(userId)` or `(userId, token, role)` are unaffected — profile fields default to empty strings. This keeps local mode working without any call-site changes.

New action `setNetmindToken(token: string)` — called after the Phase 1 NetMind
OAuth callback to store the NetMind token separately from the standard login
flow. Separation is intentional: `login()` is called with our own JWT once the
backend verifies the NetMind identity; `setNetmindToken` is called right after
with the original NetMind token for later use.

`logout()` now clears all three new fields in addition to the existing reset.

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

**No token refresh.** The JWT is stored as-is and there is no refresh flow — expiry means re-login. Expiry is now detected two ways: proactively, by reading `exp` client-side for the pre-expiry banner ([[tokenExpiry.ts]]), and reactively, when a session-death 401 survives the [[sessionGuard.ts]] probe. (Until 2026-08-06 it was "any 401 logs you out", which is how one stale NetMind token could end a valid session.)

**`persist` stores everything.** The `partialize` option is not used, so `agents`, `awarenessUpdatedAgents`, and even empty strings persist. On logout, the store is reset to initial values, which overwrites the persisted entry.

## Gotchas

**Login must store token BEFORE calling `getAgents`.** `LoginPage` calls `login(userId, token)` first, then calls `api.getAgents`. If the order is reversed, `getAuthHeaders` reads no token and the cloud-mode `getAgents` call returns `401`. This was a real bug (commit `b4b58ce`).

**`cloud-web` mode never shows "Change Mode".** If `mode === 'cloud-web'` (force-deployed cloud build), `LoginPage` hides the "Change Mode" button. Calling `logout()` in this mode clears the session but leaves `mode` as `cloud-web` in `runtimeStore`, sending the user directly back to `/login` instead of `/mode-select`.

**`refreshAgents` silently ignores network errors.** Called by `useAutoRefresh` every 30 seconds. A transient backend restart will not clear the agent list — it just logs a console error. This is intentional so a brief backend restart doesn't destroy the UI.
