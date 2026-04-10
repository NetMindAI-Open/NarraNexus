---
code_dir: frontend/src/lib/
last_verified: 2026-04-10
stub: false
---

# lib/ — Utility and infrastructure layer

## Directory role

Pure-logic utilities and thin infrastructure wrappers. No React, no Zustand, no component logic. Files here are imported by stores, hooks, services, and components alike. The three files cover HTTP (api.ts), platform abstraction (platform.ts), and general utilities (utils.ts).

## Key file index

| File | Responsibility |
|------|---------------|
| `api.ts` | HTTP client singleton. Resolves base URL dynamically, injects JWT, wraps all backend endpoints. |
| `platform.ts` | `PlatformBridge` abstraction — detects Tauri vs web browser and returns the appropriate bridge. |
| `utils.ts` | `cn` (Tailwind class merge), `generateId`, `formatTime`, `formatDate`, `formatRelativeTime`, `truncate`, `parseUTCTimestamp`. |

## Collaboration with other directories

`api.ts` imports `getApiBaseUrl` from `stores/runtimeStore` to resolve the backend URL at call time. `platform.ts` is used only by `pages/SystemPage.tsx`. `utils.ts` is used broadly across all layers. No file in `lib/` imports from `components/`, `hooks/`, or `stores/` (except `api.ts`'s one-directional import from `stores/runtimeStore`).
