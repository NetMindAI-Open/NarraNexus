---
code_file: frontend/src/components/nm/misc.tsx
last_verified: 2026-05-18
stub: false
---

# nm/misc.tsx — Chip, Tag, Badge, CodeBlock, Kbd, Link

## Why it exists

6 small primitives that don't fit other categories but are needed throughout.

- `Chip`: hairline pill with species color text + dismissible variant
- `Tag`: smaller data tag (no border, paper-warm bg, mono uppercase ink-50)
- `Badge`: numeric count indicator on icons; dot variant for "unread" hint
- `CodeBlock`: SunkenWell-style code with language header + copy button +
  optional line numbers
- `Kbd`: keyboard shortcut hint `[ Cmd ][ K ]` bracket-cap visual
- `Link`: ink-underlined inline link with hover-invert; external icon when `external`

## Upstream / Downstream

- **Upstream**: `cn`, NM CSS vars
- **Downstream**:
  - Chip — agent module list (M3), filter selections (M3), file attachments (M3)
  - Tag — small role markers ("BETA", "INTERNAL")
  - Badge — sidebar nav item counts, inbox unread, agent in-flight
  - CodeBlock — Markdown rendering pre tag, agent reply with code, tool result
  - Kbd — hint text in command palettes, tooltip help
  - Link — every inline anchor

## Design decisions

**Chip vs Tag distinction.** Chip is interactive-ish (often dismissible,
species-colored, used for selections); Tag is purely informational, smaller,
no color (used for inline data markers like "DRAFT" or "v1.2"). Calling
them out separately stops people from making "small chip" or "big tag"
custom variants.

**Badge dot variant uses `dot` boolean, not a separate component.** The
count and dot variants share the "I'm on top of an icon" position, so
sharing the props is cleaner.

**CodeBlock copy button uses optimistic UI:** clicking shows "✓ Copied"
for 1.5s then reverts. If clipboard API fails (some sandboxed iframes
block it), the catch silently no-ops — no error message, since copy
failure isn't actionable for the user.

**Kbd uses real `<kbd>` element** (semantic HTML for keyboard input). The
2px bottom border creates a subtle "key cap" 3D effect without an actual
3D shadow.

**Link hover inverts** (bg = ink, color = paper) — matches the existing
`.markdown-content a` Archive pattern, preserved in NM index.css.

## Gotchas

- `Chip species="ink"` is the default and a valid value (TypeScript union
  expands NMSpecies + 'ink'). Useful for "generic chip" without species.
- `Badge count={0}` renders `null` unless `dot=true`. This is intentional —
  empty badges shouldn't visually exist.
- `CodeBlock showLineNumbers` uses a CSS grid `grid-cols-[auto_1fr]` so
  line numbers and code align without per-line manual width calculation.
- `Link external` automatically sets `target=_blank` and `rel=noopener
  noreferrer` (security best practice — prevents tabnabbing).

## Related

- `nm/surface.tsx` SunkenWell — CodeBlock visually matches it
- `nm/identity.tsx` NMSpecies type imported for Chip/Badge species prop
