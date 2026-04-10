---
code_dir: frontend/src/stores/
last_verified: 2026-04-10
stub: false
---

# stores/ — Zustand state layer

## Directory role

All client-side state that must survive component unmounts lives here. Every store is a Zustand slice. The stores collectively answer three questions for the rest of the app: who is the user and which agent are they using (`configStore`), what mode/environment are we in (`runtimeStore`), and what is the current UI state for panels (`chatStore`, `preloadStore`, `jobComplexStore`, `embeddingStore`).

## Key file index

| File | Responsibility |
|------|---------------|
| `configStore.ts` | Auth identity, selected agent, JWT token, awareness red-dot tracking. Persisted. |
| `runtimeStore.ts` | App mode (`local`/`cloud-app`/`cloud-web`), feature flags, base URL resolution. Persisted. |
| `chatStore.ts` | Per-agent streaming state, message history, toast queue. NOT persisted. |
| `preloadStore.ts` | Parallel cache for all panel data (jobs, inbox, awareness, etc.). Silent polling support. |
| `jobComplexStore.ts` | DAG job group view state, exponential-backoff polling, JOB_TEMPLATES. |
| `embeddingStore.ts` | Embedding rebuild status, adaptive polling. |
| `index.ts` | Barrel export. |

## Collaboration with other directories

`services/wsManager.ts` writes to `chatStore` via `getState()` — bypasses React. `lib/api.ts` reads auth headers from `localStorage` (populated by `configStore` persist) to avoid a circular import. `runtimeStore` exports `getApiBaseUrl` and `getWsBaseUrl` consumed by both `api.ts` and `wsManager.ts`. `hooks/useAutoRefresh.ts` drives `preloadStore`'s individual `refresh*` methods on a timer. No store imports from another store — they are independent slices that components and hooks compose.
