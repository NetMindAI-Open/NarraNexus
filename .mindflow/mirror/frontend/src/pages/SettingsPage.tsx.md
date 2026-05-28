---
code_file: frontend/src/pages/SettingsPage.tsx
last_verified: 2026-05-27
stub: false
---

## 2026-05-27 — UpdatesSection rewrite: full state-machine UI

`UpdatesSection` was rewritten to drive off [[updaterStore.ts]]
(the Zustand mirror of the unified Rust state machine
[[updater.rs]]) instead of the old single-call IPC. It now renders
every state explicitly:
- `idle` / `failed` / `up_to_date` → "Check for updates" button
- `checking` / `available` → button shows spinner + status label
- `downloading` → progress bar with `12.3 MB / 412.5 MB (3%)`
- `installing` → spinner + "Installing X.Y.Z…"
- `ready` → "Restart to apply X.Y.Z" button → `restartForUpdate()`

Removed local `busy` / `msg` state. The store IS the state; the
component is pure render. This means clicking "Check" in tray,
Settings, or having the startup auto-check fire all converge on
the same UI — the v1.7.5 issue of "Settings spinner spins forever
with no progress" is structurally impossible now (the spinner
either reflects `checking` (1–30 s) → next state, OR
`downloading` with a real percentage).

`formatBytes` helper for the progress label. Local to this file
because it has no other consumer yet; promote to a shared util
if a third caller appears.

## 2026-05-22 — desktop-only "App updates" section (initial wiring)

Original implementation of `<UpdatesSection />` — a single "Check for
updates" button calling `checkForUpdates()` (deprecated). Replaced by
the state-machine rewrite above.

# SettingsPage.tsx — LLM provider and embedding configuration

## Why it exists

Provides a persistent settings surface within the `/app/settings` route. Currently composes two existing components: `ProviderSettings` (LLM API key and model configuration) and `EmbeddingStatus` (embedding index rebuild management). Neither component is exclusive to this page — `SetupPage` also uses `ProviderSettings`.

## Upstream / Downstream

Route: `/app/settings`, rendered inside `MainLayout` as a child route. No store reads of its own — delegates entirely to its child components.

`ProviderSettings` calls `GET/POST /api/providers`. `EmbeddingStatus` uses `useEmbeddingStore` which calls `/api/providers/embeddings/*`.

## Design decisions

**Thin wrapper.** This page is deliberately a layout shell. All logic lives in the components it composes. If a new settings category is added (e.g., notification preferences), a new `<section>` with the relevant component is added here.

**`EmbeddingStatus` is a settings concern, not a system concern.** Embedding rebuilds are triggered by the user when they add RAG documents. Placing this in Settings (rather than the RAG panel) reflects that it is a global index operation, not per-document.

## Gotchas

**`EmbeddingStatus` starts polling on mount.** If the user navigates to Settings while a rebuild is running, `EmbeddingStatus` picks up the live status. But if they navigate away before polling stops, the `useEmbeddingStore._pollTimer` continues running. The component itself calls `stopPolling` in its cleanup, so this is handled — but only if `EmbeddingStatus` properly calls `stopPolling` on unmount. Verify this if embedding polling behavior seems wrong after a settings navigation.
