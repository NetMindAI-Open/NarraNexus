---
code_file: frontend/src/components/settings/SettingsModal.tsx
last_verified: 2026-04-10
---

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
