---
code_file: frontend/src/components/settings/__tests__/SettingsModal.browser.test.tsx
last_verified: 2026-09-23
stub: false
---

# Browser feature gating in the legacy settings modal

Disabling `builtin.browser` must hide the modal's Browser entry even though its
navigation is a local array rather than the full-page settings registry. Disabling
it while selected must unmount browser settings and show providers immediately.
The tests use the real registry and plugin disable path, with only expensive
settings panes stubbed, and restore the browser contribution after each test.
