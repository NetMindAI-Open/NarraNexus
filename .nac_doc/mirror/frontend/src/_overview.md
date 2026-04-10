---
code_dir: frontend/src/
last_verified: 2026-04-10
stub: false
---

# src/ — React frontend root

## Directory role

The root of the frontend application. Contains the bootstrap entry point (`main.tsx`), the root routing component (`App.tsx`), and the first-level subdirectories that organize the app by concern.

## Key file index

| File/Dir | Responsibility |
|----------|---------------|
| `main.tsx` | Vite entry point — mounts React tree with `StrictMode`, `QueryClientProvider`, `BrowserRouter`. |
| `App.tsx` | Route tree, `ProtectedRoute`/`PublicRoute` guards, `RootRedirect`, global hooks (`useTheme`, `useTimezoneSync`). |
| `stores/` | Zustand state (auth, mode, chat sessions, preloaded panel data). |
| `hooks/` | Cross-cutting React hooks (polling, theme, WebSocket adapter, timezone sync). |
| `services/` | Singleton non-React services (`wsManager`). |
| `lib/` | Pure utilities and infrastructure (`api`, `platform`, `utils`). |
| `pages/` | Full-screen route-level components (auth, setup, system, settings). |
| `components/` | All reusable and panel-specific UI components. |
| `types/` | TypeScript type definitions shared across the app. |

## Architecture flow

User action → Component → Hook (useAgentWebSocket / useAutoRefresh) → Service (wsManager) / Store (chatStore, preloadStore) → lib/api.ts → Backend.

`runtimeStore` is the single source of truth for API base URL. All HTTP and WebSocket callers resolve their target host from `getApiBaseUrl()` / `getWsBaseUrl()` on every call, so mode switches take effect immediately.

## Platform variants

The same codebase runs in three configurations: `bash run.sh` (local, Vite proxy), Tauri dmg (local, direct to `localhost:8000`), and cloud Nginx deployment (cloud-web, same origin or user-configured URL). `runtimeStore` and `lib/platform.ts` abstract the differences so components do not need platform checks.
