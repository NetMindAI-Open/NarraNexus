---
code_file: frontend/src/platform/SlotOutlet.tsx
last_verified: 2026-09-07
stub: false
---

# platform/SlotOutlet.tsx — mount a component slot point

## Intent

Renders every visible entry of a component slot (when-filtered, ordered), each inside its own error boundary so one plugin's render error is reported to the error sink (attributed to that owner) and removed, never taking the surface (top bar, composer, sidebar, agent row) down. `as` wraps the entries when the surface wants a container.

## 2026-09-07 — the error boundary moved to `PluginBoundary.tsx` (I-6)

The isolation used to be a private `SlotBoundary` class defined in this file. Extracted to its
own module so `MessageBubble`'s message-renderer content registry and `TurnTimeline`'s
timeline-event content registry could reuse the exact same isolation this file already gave the
six slot points — before the extraction, a throwing message renderer or timeline-event component
bubbled to the nearest boundary ABOVE `MessageBubble` (the route-level `ChunkErrorBoundary`),
replacing the whole conversation with an error page over one bad message. See
`platform/PluginBoundary.tsx`'s mirror doc for the boundary itself.
