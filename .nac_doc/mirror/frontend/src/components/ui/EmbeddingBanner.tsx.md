---
code_file: frontend/src/components/ui/EmbeddingBanner.tsx
last_verified: 2026-04-10
stub: false
---

# EmbeddingBanner.tsx — Thin warning strip for incomplete vector index

Sits at the top of `ChatPanel`, just below the header. Renders nothing when `status.all_done === true` or when there are no missing vectors. Starts polling `embeddingStore` on mount, stops on unmount.

The companion component `EmbeddingStatus.tsx` provides the full rebuild-progress panel for the settings area. This banner is just the lightweight "heads up" version.

Gotcha: calls `useEmbeddingStore.getState().stopPolling()` directly in the cleanup function (not via a hook reference) to avoid stale-closure issues.
