---
code_file: frontend/src/pages/index.ts
last_verified: 2026-04-10
stub: false
---

# index.ts — Pages barrel export (partial)

## Why it exists

Provides a `@/pages` import path for `LoginPage`. Only `LoginPage` is exported here — all other pages are lazy-loaded directly in `App.tsx` via `React.lazy(() => import('@/pages/FooPage'))`.

## Notes

The barrel is intentionally sparse. `App.tsx` uses `React.lazy` for all pages to enable route-level code splitting. Adding pages to this barrel would cause them to be eagerly bundled into the main chunk. Only `LoginPage` is exported here; `App.tsx` also lazy-imports it, meaning the barrel export is currently unused in production. It exists for cases where `LoginPage` needs to be imported without lazy-loading (e.g., in tests or Storybook).
