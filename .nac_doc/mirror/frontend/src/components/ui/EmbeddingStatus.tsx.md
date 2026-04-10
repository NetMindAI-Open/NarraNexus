---
code_file: frontend/src/components/ui/EmbeddingStatus.tsx
last_verified: 2026-04-10
stub: false
---

# EmbeddingStatus.tsx — Full vector index rebuild status panel

Shows per-entity-type progress bars (Narrative / Event / Job / Entity), overall percentage, error messages, and a "Rebuild" trigger button. Intended for the settings or config area, not for the chat surface (that's `EmbeddingBanner`).

Auto-polls when a rebuild is running; collapses to a compact green checkmark when `all_done`. Shares `embeddingStore` with `EmbeddingBanner` — they stay in sync automatically.
