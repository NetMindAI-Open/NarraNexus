---
code_file: frontend/src/lib/api.ts
last_verified: 2026-05-15
stub: false
---

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
