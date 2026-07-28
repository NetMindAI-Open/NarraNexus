---
code_file: frontend/src/components/settings/SettingsModal.tsx
last_verified: 2026-07-23
---

## 2026-07-23 — Desktop section (Locked Use)

New Tauri-only nav section `desktop` (filtered out on web via `isTauri()`)
with the Locked Use prevent-sleep toggle, wired to [[powerStore.ts]] —
same row/switch treatment as the Privacy analytics toggle. i18n keys
`settings.modal.navDesktop / desktopHeading / desktopIntro /
lockedUseTitle / lockedUseDesc` in all 10 locales.

## 2026-06-10 — review fix: English-only label, identity via auth header

The toggle row title was "产品遥测 / Product analytics" — binding rule #1
(code is English-only) — now just "Product analytics". The API calls dropped
their `userId` argument (`api.getAnalyticsOptOut()` /
`api.setAnalyticsOptOut(!nextEnabled)`): identity travels in the auth header;
the local `userId` from configStore is kept only as an "is someone logged in"
gate for enabling the toggle.

## 2026-06-08 — Privacy section + analytics toggle

Added a third sidebar entry `{ id: 'privacy', label: 'Privacy', icon: Shield }`
to `NAV_SECTIONS`. The Privacy content panel contains a single toggle row for
"Product analytics".

`userId` is obtained from `useConfigStore((s) => s.userId)` — same pattern used
by `embeddingStore` and `ProviderSettings`. `api.getAnalyticsOptOut()` is
called when the section is first opened (lazy load on `activeSection === 'privacy'`).
`api.setAnalyticsOptOut(!nextEnabled)` is called on toggle with
optimistic UI update and revert on error.

The toggle is implemented as an inline `<button role="switch">` with a
sliding white circle — no external Switch component needed. `analyticsEnabled`
reflects the UI state: `true` means tracking ON (opted_out = false), `false`
means tracking OFF (opted_out = true). The default is `true` because `UserSettingsRepository.is_analytics_opted_out` returns false for new users.

# SettingsModal.tsx — Full-screen settings modal (ChatGPT-style layout)

Shell component. Handles the backdrop, ESC-to-close, body scroll lock, portal
rendering, and the left sidebar navigation. Delegates all content to
sub-components (`ProviderSettings`, `EmbeddingStatus`).

## Why it exists

The old small popover was replaced because LLM provider settings require
substantial real estate: provider list, model slot assignments with
explanations, and embedding index status.

## Upstream / downstream

- **Upstream:** `isOpen / onClose` from whichever header/nav component
  renders the settings gear button
- **Downstream:** `ProviderSettings` (LLM Providers section),
  `EmbeddingStatus` from `@/components/ui` (Embedding Index section)

## Design decisions

**`createPortal` to `document.body`:** Ensures the modal renders above all
other layers regardless of the stacking context of the caller.

**Slot explanation cards:** Static copy explaining Agent / Embedding / Helper
LLM slots in plain language. Intentional — these concepts confuse non-
technical users. The protocol labels (Anthropic protocol, OpenAI protocol)
tell developers which API format each slot expects.

**`NAV_SECTIONS` array:** Adding new settings sections is a one-liner here
(add to the array and add a content block in the render). The sidebar renders
from this array automatically.

## Gotchas

`document.body.style.overflow = 'hidden'` is set on open and cleared on
unmount. If the modal is unmounted without calling `onClose` (e.g., route
navigation), the scroll lock could leak. The `useEffect` cleanup handles the
event listener but depends on the component unmounting cleanly.
