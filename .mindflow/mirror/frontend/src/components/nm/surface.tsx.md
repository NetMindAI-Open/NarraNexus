---
code_file: frontend/src/components/nm/surface.tsx
last_verified: 2026-05-18
stub: false
---

# nm/surface.tsx — Surface primitives (PaperCard, RaisedPanel, SunkenWell, Divider)

## Why it exists

Implements NM Axiom #4 (lift via paper, not shadow). Provides 3 semantic
elevations within the warm paper family + 1 separator.

- `PaperCard` — base flat card on warm paper (`bg: --nm-card` + hairline)
- `RaisedPanel` — "above the page" surface for dropdowns/popovers
  (`bg: --nm-raised` + hairline + minimal soft lift shadow)
- `SunkenWell` — "depressed paper" for inputs/code/quoted regions
  (`bg: --nm-paper-warm` + inset hairline)
- `Divider` — 1px hairline (default) or 2px ink (thick), horizontal or vertical

## Upstream / Downstream

- **Upstream**: `@/lib/utils.cn`, NM CSS vars
- **Downstream**: nearly everything. PaperCard wraps almost every list row /
  card in M3-M4. RaisedPanel is the standard popover/dropdown shell.
  SunkenWell wraps inputs (M2 form primitives) and code blocks.

## Design decisions

**Three semantic surfaces, not one Card with variant.** A "raised" thing is
*semantically different* from a "card" thing (the former implies temporary
elevation for focus / interaction; the latter implies persistent content
container). Three exports make the call site read clearer than
`Card variant="raised"`.

**RaisedPanel keeps a *minimal* lift shadow** (`0 1px 0 ..04, 0 2px 6px ..05`).
This is the ONE exception to "no shadows" — without it, RaisedPanel and
PaperCard look identical on the same surface, defeating the "above" semantic.
The shadow is so faint it reads as "paper edge" not "drop shadow".

**SunkenWell uses inset hairline instead of border**. Visually identical, but
`box-shadow: inset` doesn't take layout space — important for inputs where
content alignment must not shift when the border appears.

**Divider has two variants only.** `default` = 1px hairline (table rows,
list separators); `thick` = 2px ink (major section breaks). Anything else
should be a different primitive (e.g. archive-rule).

**forwardRef on all three surfaces.** Allows callers to attach refs (for
focus management, intersection observers, etc.) without an extra wrapper.

## Gotchas

- PaperCard padding default = md (`p-4`). Use `padding="none"` when child
  needs flush layout (e.g. nested list with own padding).
- RaisedPanel's inline `style` spread happens AFTER the boxShadow assignment;
  if a caller passes `style.boxShadow`, the inline-then-spread order means
  caller wins. Keep this in mind for testing.
- Divider with `orientation="vertical"` only sizes correctly when parent is
  a flex container — uses `self-stretch` to fill cross-axis.

## Related

- `nm/bracket.tsx` BracketCornerMarks → typically wraps PaperCard for "selected"
- `nm/form.tsx` TextInput → uses SunkenWell as the input "well"
