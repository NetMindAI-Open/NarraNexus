---
code_file: frontend/src/components/ui/Card.tsx
last_verified: 2026-04-10
stub: false
---

# Card.tsx — Themed container with four surface variants

Four variants map to the layered background system: `default` (standard surface), `glass` (backdrop-blur for overlays), `elevated` (popped above default), `sunken` (inset/recessed). Sub-components `CardHeader`, `CardContent`, `CardTitle`, `CardFooter` handle consistent internal spacing and borders.

Consumed everywhere. The `glass` variant is used by `AwarenessPanel` and `AgentInboxPanel`.
