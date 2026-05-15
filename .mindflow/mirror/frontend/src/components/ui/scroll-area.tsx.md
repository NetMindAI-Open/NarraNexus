---
code_file: frontend/src/components/ui/scroll-area.tsx
last_verified: 2026-05-15
stub: false
---

# scroll-area.tsx — Radix ScrollArea with themed scrollbar

Thin wrapper over `@radix-ui/react-scroll-area`. Renders a styled scrollbar thumb using `--border-default` / `--text-tertiary` tokens. Re-exported from `index.ts` (`ScrollArea`, `ScrollBar`). Used wherever a custom scrollbar is needed alongside overflow control.

## 2026-05-15 — nested-scroll correctness

Default Viewport classes now include `overscroll-contain`. Background: AwarenessPanel mounts its outer `<ScrollArea h-full>` over two inner ScrollAreas (the awareness markdown thesis block and the FileUpload workspace tree). Without `overscroll-behavior: contain`, the moment the inner viewport reached the bottom of its `max-h` clip, the wheel event chained to the outer panel — and because the inner had hidden (hover-only) scrollbars, users perceived this as "the inner box won't scroll at all". `overscroll-contain` keeps the wheel inside the inner viewport until its boundary, full stop. For top-level ScrollAreas where no parent scroller exists the property is a no-op.

Callers that need an always-visible scrollbar (because the inner-scroll feature must be discoverable) pass `type="auto"`; that prop forwards into the Radix Root via `{...props}` and overrides the default hover-only reveal.
