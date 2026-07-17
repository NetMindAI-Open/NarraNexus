---
code_file: frontend/src/lib/api.ts
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — Agent 实时层熔断器接入

新增 `getAgentCircuitBreaker(agentId)` / `resetAgentCircuitBreaker(agentId)`，打 `/api/agents/{id}/circuit-breaker[/reset]`（mirror getBusFailures/retryBusFailure）。


## 2026-07-13 — channel set-active methods

Added `set{Lark,Slack,Telegram,WeChat,Discord}Active(agentId, active)` → `POST /api/<ch>/set-active`, flipping a bound credential's active flag without a re-bind (used to activate bundle-imported inactive channels).

## 2026-07-10 — clearHistory gains scope flags

`clearHistory(agentId, { conversations, memory })` now sends
`?conversations=&memory=` (default both true) to
`DELETE /api/agents/{id}/history`, driving the scoped wipe. Response type
extended in [[api.ts]] (types) with per-target counts + `disk_errors`.

## 2026-07-10 — submitFeedback()

`submitFeedback(category, text)` → POST /api/feedback。返回 {ok, delivered}；
delivered=false 只代表接收端不可达或杀开关开启，UI 不据此报错。


## 2026-07-09 — per-agent LLM config methods

Added ``getAgentLlmConfig`` / ``setAgentLlmConfig`` / ``resetAgentLlmConfig``
(GET/PUT/DELETE ``/api/agents/{id}/llm-config[/{slot}]``) backing the chat-page
per-agent override surfaces. Types ``AgentSlotView`` / ``AgentSlotEffective`` in
[[api.ts]] (types). setProviderSlot / getAgentFramework stay — they now write the
user-level GLOBAL DEFAULT (see [[ProviderSettings]]).

 
## 2026-07-05 — recharge / rechargeStatus (Phase 4, module E)

`recharge(amount, currency?, successUrl?, cancelUrl?)` POSTs the top-up and returns
`{checkout_url, session_id}`; `rechargeStatus(sessionId)` GETs by-session. Both forward the
loginToken via X-Netmind-Token. Types RechargeResponse/RechargeStatusResponse added in
[[api]] (types). The panel opens checkout_url then polls rechargeStatus.



## 2026-07-02 (Phase 3) — 订阅写操作

`subscribe()` / `cancelSubscription()` / `reactivateSubscription()`——共用私有
`billingWrite()`（POST + `X-Netmind-Token`，空 token 早退）。subscribe 返
checkout_url，面板 openExternal + 轮询 `/me`。

## 2026-07-02 — NetMind billing 方法 + billing 401 不触发全局登出

新增 `getPlans()` / `getSubscription()`（[[billing]] 代理）+ 私有
`getNetmindToken()`（从 localStorage `narra-nexus-config` 读 netmindToken，经
`X-Netmind-Token` 头带上）。两个关键决策：① `getSubscription()` 空 token 直接
throw（不发空头 round-trip，安全审查 H-1）；② `request()` 的 401 自动登出处理
**跳过 `/api/billing/`**（`isBillingEndpoint`）——billing 401 是 NetMind token
失效，不是 NarraNexus 会话失效，绝不能把有效会话登出（code review HIGH）。

## 2026-07-03 — bus-failures + notices client methods (upstream #52)

`getBusFailures` / `retryBusFailure` (per-agent recovery endpoints) and
`getNotices` / `markNoticeRead` (user-scope inbox_table read side).

## 2026-06-24 — team group chat: getTeamChat / sendTeamChat + setProviderSlot

Team group-chat client surface (a team = a group chat over the message bus):

- `getTeamChat(teamId, since?)` → `GET /api/teams/{id}/chat/messages` (optional
  `?since=` cursor) returns `TeamChatHistoryResponse` ([[teams]]) — the history
  plus a `thinking` array of member agent_ids the trigger is currently
  processing (drives the "…" indicators). Polled by the team chat view.
- `sendTeamChat(teamId, content, mentions)` → `POST /api/teams/{id}/chat/messages`
  posts a user message; `mentions` carries agent_ids and/or the literal `"@all"`
  (backend maps it to @everyone). The mention list is what drives delivery — who
  the bus routes the message to / wakes up.

Also `setProviderSlot(slot, {provider_id, model, thinking?, reasoning_effort?})`
→ `PUT /api/providers/slots/{slot}` — the same endpoint Settings › Providers
uses, surfaced inline (e.g. the composer) so the agent's model can be switched
without leaving chat. Identity from the auth header as usual.

## 2026-06-10 — api.onboard

`onboard(apiKey, providerType?)` → POST /api/providers/onboard. providerType
is only sent when the user manually overrode the sk-ant- prefix detection;
otherwise null lets the backend decide.


## 2026-06-23 — getMyNarratives / getMyNetwork (owner-scoped)

Added `getMyNarratives(includeDefault = false)` → `GET /api/me/narratives`,
`getMyNetwork()` → `GET /api/me/network`, and `getMyWorldview()` →
`GET /api/me/worldview` (owner-level, cross-agent). Power the three "You"
workspace tabs — [[NarraMemoryTimeline]] / [[NexusNetworkGraph]] /
[[WorldviewLenses]]; types in [[you]].

## 2026-06-11 — netmindLogin (NetMind token exchange)

Added `netmindLogin(netmindToken, source?)` → `POST /api/auth/netmind-login`.
POSTs `{ netmind_token, source }` to the backend which validates the NetMind
access token and returns a self-issued JWT (`NetmindLoginResponse`). The
response type mirrors `RegisterResponse` in shape (user_id, token, role,
has_system_quota, initial_input_tokens, initial_output_tokens) plus
`is_new_user`, `display_name`, and `email` which are specific to the NetMind
identity handoff. `source` is optional — callers pass `'arena'` to indicate
origination from the Arena SSO flow.

`NetmindLoginResponse` is defined in `@/types/api.ts` immediately after
`LoginResponse`.

last_verified: 2026-06-10
stub: false
---

## 2026-06-10 — analytics methods: identity from auth header only (review fix)

PR #24 review hardening, matching the backend change in `routes/auth.py`:
`getAnalyticsOptOut()` / `setAnalyticsOptOut(optedOut)` no longer take a
`userId` parameter (no query param, no body field) and `trackFunnelEvent(event)`
no longer accepts `properties`. The server derives the user from the auth
header and stamps event properties (surface etc.) itself.

## 2026-06-09 — trackFunnelEvent (setup page UI actions)

Added `trackFunnelEvent(event)` — POSTs `{event}` to
`POST /api/auth/funnel`. Called fire-and-forget by `SetupPage` (callers
`.catch(() => {})` to suppress errors). Identity travels in the auth header
(X-User-Id / JWT) set by `getAuthHeaders`, not in the body — consistent with
every other `ApiClient` method.

This is the only `ApiClient` method that targets the `/api/auth/funnel`
endpoint. It is intentionally not typed beyond `{ success: boolean }` because
the funnel endpoint is write-only from the frontend's perspective.

## 2026-06-08 — getAnalyticsOptOut / setAnalyticsOptOut

Two new `ApiClient` methods added after `markOnboardingStep`:

- `getAnalyticsOptOut()` → `GET /api/auth/settings/analytics`
  returns `boolean` (false = opted in, true = opted out)
- `setAnalyticsOptOut(optedOut)` → `PUT /api/auth/settings/analytics`
  body `{opted_out}`, returns void

Both use the standard `this.request<T>()` fetch wrapper. Called by
`SettingsModal` when the user toggles the Privacy section switch.

## 2026-05-18 — importBundleFromUrl (one-click template install)

Added `importBundleFromUrl(url, expectedSha256?)` → `POST /api/bundle/import/from-url`.
The deep-link / website install path: instead of the user uploading a
`.nxbundle`, the backend fetches the URL itself (SSRF-guarded) and runs the
same preflight, returning a `BundlePreflightResponse`. Called by the
`/app/templates/install` page when arriving from narra.nexus.

## 2026-05-15 — bundle Artifacts + MCP preview

Added `previewArtifacts(agentIds)` and `previewMcps(agentIds)` next to the
existing `previewBusChannels` helper. Used by the redesigned BundleExportPage
to populate the Artifacts tab and the MCP section of the renamed
`Skills & MCP` tab.

## 2026-05-14 — workspace tree + nested delete + raw helpers

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

`listFiles` now returns a recursive tree (`FileListResponse.tree`) rather
than a flat array. `deleteFile(agentId, userId, path)` accepts a
workspace-relative path (potentially nested, e.g. `report/index.html` or
`report`) — `encodeURI` preserves slashes so the backend `{path:path}`
route receives the whole sub-path. Added `workspaceFileRawUrl(...)` for
`<a href download>` and `fetchWorkspaceFileBlob(...)` for JWT-authed
inline preview (Tauri `<a download>` can't carry headers, so the preview
modal goes through fetch+blob).

## 2026-05-13 — getAuthHeaders 同时注入 X-User-Id

之前只发 `Authorization: Bearer <jwt>`——只覆盖 cloud 模式。local 模式
没 JWT 所以这个 header 是空的，后端 auth_middleware 在 local 分支
无法识别请求者是谁，统一 fallback 到 users 表第一行 → 多用户串号
（teams / dashboard / agents_cost / bundle 都被影响）。

修复：`getAuthHeaders()` 同时读 `userId`（configStore），存在就
注入 `X-User-Id`。两个 header 并存、互不干扰：

- cloud 模式：后端只信 JWT，`X-User-Id` 完全忽略（defence-in-depth）
- local 模式：后端只信 `X-User-Id`，JWT 在 local 模式没有签名秘钥
  本来就是无效的

ApiClient 保持 mode-agnostic——切换 cloud / local 的判断完全在
后端 auth_middleware 内做，前端无需感知。详见
`backend/auth.py.md` 2026-05-13 section。

# api.ts — HTTP client singleton

## Why it exists

Every panel and store needs to talk to the backend. Without a centralized HTTP client, each call would need to re-implement base URL resolution, auth header injection, and error handling. `api.ts` provides a typed singleton `api` object where all endpoints are methods, and cross-cutting concerns are handled in one place.

## Upstream / Downstream

Imports `getApiBaseUrl` from `stores/runtimeStore` — the single source of truth for base URL across the app. `getApiBaseUrl` is re-exported from `api.ts` as `getBaseUrl` for backward compatibility (some older call sites use `getBaseUrl`).

Consumed by virtually every store (`preloadStore`, `configStore`, `jobComplexStore`, `embeddingStore`) and several hooks (`useAutoRefresh`, `useSkills`, `useTimezoneSync`) and pages (`SetupPage`, `LoginPage`, `RegisterPage`, `CreateUserDialog`).

## Design decisions

**Dynamic base URL on every call.** `getApiBaseUrl()` is called inside `request<T>()` on every request rather than cached at construction time. This means a mode switch (local → cloud) takes effect on the very next API call without a page reload.

**JWT injection via localStorage, not store import.** `getAuthHeaders` reads `localStorage.getItem('narra-nexus-config')` directly rather than importing `useConfigStore`. This breaks the circular dependency: `configStore → api → configStore`. The downside is brittleness to the Zustand persist key name (`narra-nexus-config`) and the state shape (`state.token`). If either changes, `getAuthHeaders` must be updated manually.

**`FormData` calls bypass `request<T>`.** `uploadFile`, `uploadRAGFile`, `installSkillFromGithub`, and `installSkillFromZip` call `fetch` directly because `Content-Type` must be omitted for `FormData` (the browser sets the boundary automatically). These calls use `getApiBaseUrl()` directly and call `this.getAuthHeaders()` for auth injection.

**Binary-response calls bypass `request<T>`.** `fetchAttachmentBlob` returns `response.blob()` instead of `response.json()`. Used by `useAttachmentBlobUrl` to feed `<img>` / `<a>` elements that can't carry an `Authorization` header themselves. There is no longer a public `attachmentRawUrl` builder — issuing the URL without doing the authed fetch in the same step would invite the 401-loop bug that motivated the hook.

**`request<T>` throws on non-2xx.** The error message is `"API error: ${status} ${statusText}"`. Callers that need to distinguish error types must do so via the returned `success: false` payload rather than via exception. Exceptions only happen for network failures or non-2xx responses — not for business logic errors.

**Side effects on 401 (stale JWT) and 402 (quota).** Before throwing, `request<T>` dispatches global `CustomEvent`s for two specific statuses: `narranexus:auth-expired` on 401 when an `Authorization` header was actually attached and the endpoint is not `/api/auth/login` or `/api/auth/register` (top-level `App` listens and calls `configStore.logout()` so `ProtectedRoute` redirects to `/login`); `narranexus:quota-exceeded` on 402 with `error_code=QUOTA_EXCEEDED_NO_USER_PROVIDER`. The 401 guard skips anonymous probes and login attempts so wrong-credentials surfaces in the form rather than logging the user out. Decoupled via events to avoid a circular import on `@/stores/configStore`.

**Typed return types imported from `@/types`.** All response types live in `@/types` (the TypeScript layer). `api.ts` does not define any types itself. Adding a new endpoint requires adding the corresponding response type to `@/types` first.

## Gotchas

**`getAuthHeaders` reads stale localStorage if token is updated in memory but not yet persisted.** Zustand `persist` is synchronous for writes, so in practice the token is in localStorage by the time the next request fires. But if something modifies `configStore.token` without going through Zustand (e.g., direct `localStorage.setItem`), `getAuthHeaders` would read the wrong value.

**`register` and `createUser` are different endpoints with different semantics.** `register` (`POST /api/auth/register`) requires an invite code, returns a JWT, and creates an account in cloud mode. `createUser` (`POST /api/auth/create-user`) is a no-auth admin endpoint for local mode that creates a user without a password. Using the wrong one silently succeeds on some backends and fails on others.

**`searchSocialNetwork` uses `URLSearchParams` while most other calls build URLs manually with template literals.** Both approaches work, but mixing them makes the code harder to scan. Future endpoint additions should use `URLSearchParams` for consistency.

**`provisionArena(userToken?)` (2026-06-16; body added 2026-06-23).** `POST /api/arena/provision` — identity comes from the session headers. When `userToken` (the user's NetMind JWT) is passed it goes in the body as `{user_token}` so the backend can bind the agent's owner email via Arena's platform-only endpoint (optional; omitted → bind skipped; the token is forwarded to Arena, never persisted). Idempotent server-side (one Arena agent per user). Called by `lib/arenaLanding.ts` after login when the entry source is Arena.

**`createAgent()` 4th arg (2026-06-16, #43).** Optional `opts?: { teamId }` adds `team_id` to the `POST /api/auth/agents` body, so an agent created from a team's sidebar "+" is attached to that team server-side.

## 2026-07-02 (Phase 2) — getFeeInfo

`getFeeInfo()`（GET /api/billing/fee-info，X-Netmind-Token，空 token 早退）。余额
数据源，模块 B。

## 2026-07-02 (Phase 5) — useSubscription

`useSubscription()`（POST /api/providers/use-subscription，X-Netmind-Token）。触发后端
生成 key→建 netmind provider→绑槽（模块 F）。

## 2026-07-03 (G1) — getRecords

`getRecords(direction?)`（GET /api/billing/records，X-Netmind-Token）。消费/充值流水。

## 2026-07-07 — onboard() 加 replace 参数 + needs_replace 返回

`onboard(apiKey, providerType, replace?)` 新增可选 `replace`，请求体带 `replace`；返回
类型加 `needs_replace` / `existing_masked`，供 OneKeyOnboard 的换 key 确认流程使用。
