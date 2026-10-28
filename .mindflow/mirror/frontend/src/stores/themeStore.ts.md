---
code_file: frontend/src/stores/themeStore.ts
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — `pluginTheme`（持久化）

叠在基础主题之上的插件主题 id；应用由 `usePluginTheme` 负责，store 只记选择。

# themeStore.ts — Shared theme state (light / dark / system)

## Why it exists

Single source of truth for theme selection. Replaces a former `useState`-in-a-hook implementation whose state was per-component, so a toggle in one component did not propagate `isDark` changes to others. Image assets that swap by theme (logos, icons) require a shared reactive value, not just a CSS class on `<html>`.

## Upstream / Downstream

- **Upstream**: `localStorage` (key `narra-nexus-theme`, managed by Zustand `persist`), OS `prefers-color-scheme` media query.
- **Downstream**: `useTheme` hook wraps it and exposes the same shape the app already used. `App.tsx` reads `effectiveTheme` to toggle the `dark` class on `<html>`.

## Design decisions

**Persist only `theme`, not `effectiveTheme`.** `effectiveTheme` is derived from `theme` + OS state. On rehydrate we recompute it (`onRehydrateStorage` hook) so stale `effectiveTheme` from a previous session's OS setting can't leak in.

**Module-scope media query listener, not per-component.** One `matchMedia('(prefers-color-scheme: dark)')` listener at module load updates the store when the user is on `'system'` and the OS flips. Per-component listeners (the old design) scaled linearly with mount count and could miss updates if the listening component was unmounted.

**Three-way toggle cycle.** `toggleTheme` cycles `light → dark → system → light`. Matches the pre-existing UX — users who rely on OS night mode can park on `system` without double-toggling twice a day.

**`name: 'narra-nexus-theme'` intentionally reuses the old hook's localStorage key.** Zustand persist wraps the value in `{ state: ..., version: 0 }`, so any pre-existing raw string value fails to rehydrate and falls back to `'system'`. Per 铁律 #2 we accept the one-time reset instead of writing a migration.

## Gotchas

**Do not touch the DOM here.** Applying the `dark` class is `App.tsx`'s job. If both this store and a component mutate `documentElement.classList`, ordering becomes fragile. Keep this store DOM-agnostic.

**SSR safety.** `getSystemTheme` and the module-level `matchMedia` listener both guard `typeof window === 'undefined'`. Safe to import in non-browser test setups.
