---
code_file: frontend/src/components/ui/scroll-area.tsx
last_verified: 2026-06-02
stub: false
---

## 2026-06-02 — Root is `flex flex-col` so `max-h-*` ScrollAreas actually scroll

The Root base classes are now `relative overflow-hidden flex flex-col`. Why
it was broken: Radix sizes the Viewport `height: 100%` (our `h-full w-full`).
CSS resolves `height: 100%` to `auto` when the Root's own height is `auto` —
exactly the case for a Root constrained ONLY by `max-height` (`max-h-[55vh]`
on the FileUpload workspace tree, `max-h-[40vh]` on the awareness thesis,
plus 8 more sites: MCPManager, EventCard ×3, SkillCard, JobDetailPanel,
JobExpandedDetail, RAGUpload). The Viewport then grew to full content height
and the Root just CLIPPED it via overflow:hidden — no scrollbar, no wheel.
The `2026-05-15` overscroll-contain note assumed those inner viewports
scrolled; they never did (verified in headless Chrome — `SCROLLS=false`,
clientHeight == full content). Making the Root a flex column gives the single
in-flow child (the Viewport) a definite height bounded by the Root's
max-height. **Safe for all usages**: Scrollbar and Corner are Radix
`position: absolute` (out of flow), so the Viewport is the only flex item;
`h-full` / `flex-1` / horizontal Roots were re-verified to still scroll. This
is the real fix for the "right-panel workspace won't scroll" report that the
v1.7.14 ContextPanelContent `flex flex-col` change (a proven no-op) did not
address.

# scroll-area.tsx — Radix ScrollArea with themed scrollbar

Thin wrapper over `@radix-ui/react-scroll-area`. Renders a styled scrollbar thumb using `--border-default` / `--text-tertiary` tokens. Re-exported from `index.ts` (`ScrollArea`, `ScrollBar`). Used wherever a custom scrollbar is needed alongside overflow control.

## 2026-05-15 — nested-scroll correctness

Default Viewport classes now include `overscroll-contain`. Background: AwarenessPanel mounts its outer `<ScrollArea h-full>` over two inner ScrollAreas (the awareness markdown thesis block and the FileUpload workspace tree). Without `overscroll-behavior: contain`, the moment the inner viewport reached the bottom of its `max-h` clip, the wheel event chained to the outer panel — and because the inner had hidden (hover-only) scrollbars, users perceived this as "the inner box won't scroll at all". `overscroll-contain` keeps the wheel inside the inner viewport until its boundary, full stop. For top-level ScrollAreas where no parent scroller exists the property is a no-op.

Callers that need an always-visible scrollbar (because the inner-scroll feature must be discoverable) pass `type="auto"`; that prop forwards into the Radix Root via `{...props}` and overrides the default hover-only reveal.
