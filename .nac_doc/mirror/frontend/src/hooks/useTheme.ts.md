---
code_file: frontend/src/hooks/useTheme.ts
last_verified: 2026-04-10
stub: false
---

# useTheme.ts — Light/dark/system theme toggle

## Why it exists

The app supports three theme choices: `light`, `dark`, and `system` (follows OS preference). Theme state must persist across sessions without requiring a Zustand store or backend round-trip. `useTheme` handles this with a simple localStorage key and a media query listener, keeping theme logic completely isolated from the rest of the state management system.

## Upstream / Downstream

Self-contained — reads and writes `localStorage` under `narra-nexus-theme`. Applies theme by toggling the `dark` class on `document.documentElement`.

Used by `App.tsx` (`effectiveTheme` drives the `dark` class toggle in a `useEffect`) and `ThemeToggle.tsx` (displays current theme, calls `toggleTheme`).

## Design decisions

**Three-way toggle.** `toggleTheme` cycles `light → dark → system`. This differs from most apps that only toggle light/dark. The `system` option is included so users who rely on OS night mode don't have to manually switch twice a day.

**`effectiveTheme` vs `theme`.** `theme` can be `'system'`, which is not a renderable value. `effectiveTheme` is always `'light'` or `'dark'` — the resolved actual appearance. Components that need to know which variant to render use `isDark` (derived from `effectiveTheme`) rather than comparing `theme`.

**Not in Zustand.** Theme is purely a local UI preference with no backend relevance. Putting it in Zustand would add serialization overhead for a value that is simpler to keep in a hook + localStorage.

**Media query listener.** When `theme === 'system'`, the hook subscribes to `prefers-color-scheme: dark` changes. If the user changes their OS theme at runtime, the app immediately follows. The listener is cleaned up on unmount.

## Gotchas

**`useTheme` is called once in `App.tsx` and once in `ThemeToggle.tsx`.** React hook calls are independent — they each read from and write to the same `localStorage` key, but they hold separate React state. A theme change triggered by `ThemeToggle` updates its own state and writes to `localStorage`. `App.tsx`'s `useTheme` instance does NOT automatically re-render unless something forces it. In practice the `document.documentElement.classList` is the real source of truth (applied in a `useEffect`), so the visual result is correct; but the `App.tsx` instance's `effectiveTheme` state may lag by one render cycle.

**SSR / non-browser environments.** Both `getSystemTheme` and `getStoredTheme` guard against `window === undefined`. The hook is safe to call in a test environment without a DOM, though the theme will always be `'dark'` (system fallback) and `'system'` (stored fallback) respectively.
