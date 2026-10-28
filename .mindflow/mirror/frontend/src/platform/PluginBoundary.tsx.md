---
code_file: frontend/src/platform/PluginBoundary.tsx
last_verified: 2026-09-07
stub: false
---

# PluginBoundary.tsx — the one error boundary every plugin-rendered surface uses

## Why it exists

Every surface a plugin can render into needs the same guarantee: a plugin's render crash never
takes the host surface down with it. This was already true for the six component slot points via
a private `SlotBoundary` class inside `SlotOutlet.tsx`. It was NOT true for the two content
registries — `MessageBubble`'s message renderer and `TurnTimeline`'s timeline-event component —
which rendered the plugin's component bare. A throwing renderer bubbled to the nearest boundary
ABOVE `MessageBubble` (the route-level `ChunkErrorBoundary`), replacing the entire conversation
with an error page over one bad message (I-6). Extracting the boundary out of `SlotOutlet.tsx`
into its own module let both content registries reuse the identical isolation instead of each
growing their own copy.

## Upstream / Downstream

- **Used by**: `SlotOutlet.tsx` (the six component slot points), `MessageBubble.tsx` (wraps the
  matched message renderer, falling back to the shell's own bubble on crash), `TurnTimeline.tsx`
  (wraps each plugin-provided timeline-event component).
- **Depends on**: `errorSink.ts`'s `reportUiError`, attributing the caught error to `owner` (the
  plugin id, or `'shell'`).

## Design decisions

- **Fallback is a function, not a node.** `fallback?: () => ReactNode` is evaluated lazily, only
  on the render that follows a crash — a fallback that reconstructs a whole shell component (e.g.
  `MessageBubble`'s own bubble JSX) is not built on every normal render, only the rare one after
  a plugin throws.
- **One boundary per owner-attributable unit**, not one boundary around a whole list. `SlotOutlet`
  wraps each entry individually (so one plugin's crash doesn't blank the whole slot);
  `TurnTimeline` wraps each timeline event individually for the same reason.

## Gotcha / edge cases

- **Trigger**: a plugin's message renderer or timeline-event component throws during render →
  **Symptom** (before this fix): the ENTIRE conversation view is replaced by
  `ChunkErrorBoundary`'s route-level error page → **Root cause**: React error boundaries only
  catch errors from their own subtree; without a boundary directly around the plugin's component,
  the nearest ancestor boundary (route-level) is the one that catches it, and its fallback
  replaces far more than the one bad message.
