---
code_file: frontend/src/lib/api.ts
last_verified: 2026-04-29
stub: false
---

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

**Typed return types imported from `@/types`.** All response types live in `@/types` (the TypeScript layer). `api.ts` does not define any types itself. Adding a new endpoint requires adding the corresponding response type to `@/types` first.

## Gotchas

**`getAuthHeaders` reads stale localStorage if token is updated in memory but not yet persisted.** Zustand `persist` is synchronous for writes, so in practice the token is in localStorage by the time the next request fires. But if something modifies `configStore.token` without going through Zustand (e.g., direct `localStorage.setItem`), `getAuthHeaders` would read the wrong value.

**`register` and `createUser` are different endpoints with different semantics.** `register` (`POST /api/auth/register`) requires an invite code, returns a JWT, and creates an account in cloud mode. `createUser` (`POST /api/auth/create-user`) is a no-auth admin endpoint for local mode that creates a user without a password. Using the wrong one silently succeeds on some backends and fails on others.

**`searchSocialNetwork` uses `URLSearchParams` while most other calls build URLs manually with template literals.** Both approaches work, but mixing them makes the code harder to scan. Future endpoint additions should use `URLSearchParams` for consistency.
