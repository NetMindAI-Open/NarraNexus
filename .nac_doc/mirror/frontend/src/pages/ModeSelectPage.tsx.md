---
code_file: frontend/src/pages/ModeSelectPage.tsx
last_verified: 2026-04-10
stub: false
---

# ModeSelectPage.tsx — First-launch mode selection

## Why it exists

On first launch (or after a mode reset), the user must choose between local and cloud deployment. This page presents two large clickable cards. Local mode immediately sets `mode = 'local'` and navigates to `/login`. Cloud mode reveals a URL input before proceeding, since the cloud server address must be configured before any API call can be made.

## Upstream / Downstream

Route: `/mode-select`. Shown by `App.tsx` when `runtimeStore.mode === null`. Not wrapped by `ProtectedRoute` or `PublicRoute` — it is fully public.

Writes to `runtimeStore`: calls `setMode('local')` or `setMode('cloud-app')` + `setCloudApiUrl(url)` before navigating to `/login`.

## Design decisions

**Cloud mode shows URL input inline.** Rather than navigating to a separate config page, the URL input slides in below the mode cards. This keeps the flow on a single page and avoids a route proliferation.

**Trailing slash is stripped on submit.** `apiUrl.replace(/\/+$/, '')` ensures downstream callers can always append `/api/...` without worrying about double slashes. This matches the normalization in `runtimeStore.setCloudApiUrl`.

**`cloud-web` mode is never shown here.** `cloud-web` is set programmatically in `RootRedirect` when `VITE_FORCE_CLOUD=true`. `ModeSelectPage` only knows about `local` and `cloud-app`.

## Gotchas

**No validation of the cloud URL beyond "not empty / not default".** The button is disabled if `apiUrl === 'https://'` (the default value) or empty. There is no ping check — if the user enters a wrong URL, they will not discover the error until the first API call on `LoginPage`.

**Mode cards have no keyboard navigation.** The cards are `<button>` elements with `onClick`, so they are focusable and activatable via Enter/Space. But there is no arrow-key navigation between them. For accessibility, the focus order is Local → Cloud.
