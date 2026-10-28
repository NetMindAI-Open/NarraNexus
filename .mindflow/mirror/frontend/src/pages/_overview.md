---
code_dir: frontend/src/pages/
last_verified: 2026-04-10
stub: false
---

# pages/ — Full-screen route-level components

## Directory role

Top-level page components that correspond to React Router routes. These are full-screen views outside the main `MainLayout` shell. All pages are lazy-loaded via `React.lazy` in `App.tsx`.

## Key file index

| File | Route | Notes |
|------|-------|-------|
| `ModeSelectPage.tsx` | `/mode-select` | First-launch mode picker. Sets `runtimeStore.mode`. |
| `LoginPage.tsx` | `/login` | Dual-mode login (local: user_id only, cloud: user_id + password). |
| `RegisterPage.tsx` | `/register` | Cloud-only account creation with invite code. |
| `CreateUserDialog.tsx` | (modal on `/login`) | Local-mode user creation dialog. |
| `WelcomePage.tsx` | `/welcome` | First-run flow: wire a model → import existing agents → meet the guide agent. `/setup` redirects here. |
| `SettingsPage.tsx` | `/app/settings` | LLM provider config + embedding index management. |
| `SystemPage.tsx` | `/app/system` | Service health and log viewer. Requires Tauri bridge. |
| `index.ts` | — | Barrel export (only `LoginPage`). |

## Collaboration with other directories

All pages read from `configStore` and `runtimeStore`. Auth pages call `api.*` directly for login/register/create-user. `SystemPage` uses `lib/platform.ts` exclusively. `SettingsPage` composes `components/settings/ProviderSettings` and `components/ui/EmbeddingStatus`; `WelcomePage` composes `components/welcome/*` and reuses `components/settings/OneKeyOnboard`. Pages do not import from each other (except `LoginPage` importing `CreateUserDialog`).
