---
code_file: frontend/src/components/nm/index.ts
last_verified: 2026-05-18
stub: false
---

# nm/index.ts — Barrel export for NM primitives

## Why it exists

Single import path for the entire NM primitive library:

```ts
import { RingAvatar, MessageBubble, Button, PaperCard } from '@/components/nm';
```

instead of:

```ts
import { RingAvatar } from '@/components/nm/identity';
import { MessageBubble } from '@/components/nm/bubble';
import { Button } from '@/components/nm/button';
import { PaperCard } from '@/components/nm/surface';
```

## Re-exports

12 category files, all primitives re-exported via `export * from`:

- identity (5 components + types)
- bracket (7)
- surface (4)
- bubble (3 + TurnBreak helper)
- button (4)
- status (4 + ConnectionState type)
- feedback (3)
- form (9)
- nav (5 + types)
- modal (4)
- viz (3)
- misc (6)

Total: 58 NM primitives exported.

Radix-wrap primitives (Tooltip, Popover) live in `components/ui/` (existing
files restyled in M2) — kept separate because they have a different import
pattern (require Provider, etc.). Import as:
`import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/tooltip';`

## Related

- `pages/NMPlaygroundPage.tsx` — visual gallery
