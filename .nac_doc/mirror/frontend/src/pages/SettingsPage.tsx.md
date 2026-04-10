---
code_file: frontend/src/pages/SettingsPage.tsx
last_verified: 2026-04-10
stub: false
---

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
