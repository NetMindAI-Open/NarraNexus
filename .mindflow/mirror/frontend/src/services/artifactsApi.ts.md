---
code_file: frontend/src/services/artifactsApi.ts
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — openUrl / setEmbedMode

Added `openUrl(agentId, url, title?)` (POST .../artifacts/url) and
`setEmbedMode(agentId, artifactId, mode)` (POST .../embed-mode) for URL tabs.
## 2026-05-26 — Absolute URLs via `getApiBaseUrl()` (fix dmg blank panel)

The whole API surface — `base()`, `userBase()`, and the `raw_url`
returned by `getRawUrl()` — now goes through `getApiBaseUrl()` from
`@/stores/runtimeStore`. The old bare-relative form (`/api/agents/...`)
worked in cloud because the page origin == backend origin, but in the
Tauri dmg the page is served from `tauri.localhost` while the backend
listens on `http://localhost:8000`. Every fetch silently 404'd → empty
artifacts list + dead clicks + the panel never opening. The bug was
present since this module was first written (2026-04) and was masked
for cloud-first users; the fix matches the rule already followed by
`lib/api.ts` (which prefixes every URL with `getApiBaseUrl()`).

`absolutiseBackendUrl()` defensively passes through fully-qualified
URLs (`https?://...`) so a future CDN-hosted artifact variant won't
get double-prefixed. Contract pinned by
`src/services/__tests__/artifactsApi.test.ts`.

The pre-fix gotcha line "**No auth headers**" below is also stale —
`authHeaders()` exists and is called by every JWT-authed method (it
sends both `Authorization: Bearer` and `X-User-Id`). The pre-fix
"`base()` helper" note about constructing per call still applies, but
now the helper also reads the runtime base URL so a mode switch
(cloud ↔ dmg) takes effect on the very next call without any
restart-the-process gymnastics.

## 2026-05-14-r3 — `deleteSource` dropped from `remove` / `bulkDelete`

Deletion is now registry-only. `remove(agentId, artifactId)` (no third arg)
and `bulkDelete(userId, ids)` (no third arg) just remove the DB rows;
workspace files are never touched. `BulkDeleteResult` no longer carries
`source_deleted`. Same change rationale as the backend (see
[[agents_artifacts.py]] / [[users_artifacts.py]] mirror md).

## 2026-05-14 — pointer model: token-based raw fetch, register from workspace, delete_source

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

- `getDetail` returns a plain `Artifact` (the old `{artifact, versions}` shape
  is gone).
- New `getRawUrl(agentId, artifactId)` mints an HMAC view token via
  `GET .../view-token` and returns the directory-style raw URL
  (`/api/public/artifacts/raw/{token}/`).
- `fetchArtifactBlobUrl` / `fetchArtifactText` no longer attach
  `authHeaders()` — the raw URL is on the JWT-bypassed public route and
  carries the token in its path.
- `remove(agentId, artifactId, deleteSource=false)` — propagates the popup
  choice to the backend (`?delete_source=`).
- New `registerFromWorkspace(agentId, params)` — backs the workspace tree
  viewer's "register as artifact" action; same validation as the MCP tool.
- `bulkDelete(userId, ids, deleteSource=false)` — bulk variant.

# services/artifactsApi.ts — Artifacts REST API client

## Why it exists

Centralizes all `fetch` calls targeting `/api/agents/{agentId}/artifacts` so nothing else in the frontend constructs those URLs by hand. Every operation (list, detail, pin toggle, delete) has a typed wrapper that throws on non-OK responses rather than returning an ambiguous `null`.

## Upstream / Downstream

Depends on `@/types/artifact` for `Artifact` and `ArtifactWithVersions`. Has no React or Zustand dependency — it is a pure async module that can be called from anywhere.

Primary consumer is `stores/artifactStore.ts`, which calls these methods inside Zustand actions. The store owns error handling above this layer; `artifactsApi` itself throws, letting callers decide whether to show a toast, retry, or silently swallow (as the WS handler does for race-condition deletes).

## Design decisions

**`base()` helper instead of a hardcoded string.** The base URL is constructed once per call rather than stored as a module-level constant, because `agentId` varies per call. This avoids accidental cross-agent leakage if the helper were reused with a stale closure.

**`scope=session` vs `scope=pinned` query params.** The backend serves both categories through the same endpoint with a `scope` discriminator, matching the REST pattern used elsewhere in the codebase (`GET /api/agents/{id}/artifacts?scope=...`). This keeps the URL surface minimal — no `/pinned` sub-resource.

**No auth headers.** The project-wide convention is that the session cookie / JWT is included automatically by the browser (cookie) or by a Vite proxy forwarding the `Authorization` header. This service does not inject tokens itself.

**`remove` returns `Promise<void>`.** A successful DELETE has no meaningful body. Consumers only need to know it succeeded or threw.

## Gotchas

- `listSession` + `listPinned` are called in parallel by `artifactStore.loadForSession`. Neither call is debounced — if called rapidly (e.g., tab switching), they will fire multiple concurrent requests. The store's `set()` call is the last-write-wins reconciliation point.
- `setPinned` sends a PATCH. If the backend changes to PUT for the full resource, update here too — the store's `upsert` call after the PATCH assumes the response shape matches `Artifact`.
