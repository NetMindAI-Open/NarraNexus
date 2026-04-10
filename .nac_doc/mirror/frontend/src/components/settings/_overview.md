---
code_dir: frontend/src/components/settings/
last_verified: 2026-04-10
---

# settings/ — LLM provider configuration and model assignment

Two files cover everything:

- `SettingsModal.tsx` — the full-screen modal shell (sidebar nav, backdrop,
  ESC handler, portal rendering)
- `ProviderSettings.tsx` — the actual provider CRUD and model-slot assignment
  UI embedded inside the modal

## Why a full-screen modal instead of a popover

The original design used a small popover, but provider config requires
multiple sections (provider list, model slots, embedding status) and
explanatory copy for non-technical users. A popover was too cramped.

## Interaction with other settings

The Embedding Index section inside `SettingsModal` renders `EmbeddingStatus`
from `@/components/ui/EmbeddingStatus`. This is the only settings concern
outside this directory.

## Consumed by

The settings gear button in the top navbar / header bar. `SettingsModal` is
rendered with `createPortal` directly to `document.body` so it floats above
all other layers.
