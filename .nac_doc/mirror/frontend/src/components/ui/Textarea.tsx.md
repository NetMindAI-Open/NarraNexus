---
code_file: frontend/src/components/ui/Textarea.tsx
last_verified: 2026-04-10
stub: false
---

# Textarea.tsx — Multi-line input matching Input.tsx style

Same glow focus ring and error state as `Input`. `resize-none` by default (can be overridden). Has a decorative SVG corner marker (the L-shaped bracket). Used in `ChatPanel` (main message input) and `AwarenessPanel` (awareness edit modal).

The `ChatPanel` usage sets `rows={1}` with `max-h-[160px]` for auto-grow via CSS; actual grow logic is CSS-only (no JS resize observer).
