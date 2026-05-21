---
code_file: frontend/src/components/nm/bracket.tsx
last_verified: 2026-05-18
stub: false
---

# nm/bracket.tsx — Bracket vocabulary (logo, edge, label, corners, empty, dropzone, loading)

## Why it exists

Implements NM Axiom #6 — the bracket `[ ]` motif as a universal "this is a
container / quote / piece of context" syntax mark. The brand logo `[ • • ]`
is the seed; the rest of the file extends that vocabulary to every place a
container needs to be visually marked without using a heavy box outline.

Seven exports:

- `BracketMarkLogo` — `[ • • ] narra` brand wordmark
- `BracketEdge` — small (8-12px) corner bracket for message bubbles, cards
  (per-corner, species-colorable)
- `BracketSectionLabel` — `[ ACTIVE AGENTS ]` uppercase mono section header
- `BracketCornerMarks` — wrapper that adds 4-corner BracketEdges (used as
  selection state on cards/items — replaces "whole-card highlight" pattern)
- `BracketEmptyState` — `[ 暂无对话 ]` large bracketed empty state + optional
  hint + CTA
- `BracketDropzone` — file-upload zone with diagonal bracket corners (tl + br
  dashed) and species-colored active state
- `BracketLoading` — `[ Loading · · · ]` placeholder with typing-blink dots

## Upstream / Downstream

- **Upstream**: `@/lib/utils.cn`. NM tokens via CSS vars.
- **Downstream**:
  - `chat/MessageBubble` uses `BracketEdge` (M3) for per-bubble species mark
  - `nm/identity` `AvatarStack` selection state (M3) wraps with `BracketCornerMarks`
  - `layout/Sidebar` section headers use `BracketSectionLabel` (M3)
  - `nm/bubble` MessageBubble (M3)
  - `pages/BundleImportPage` `BracketDropzone` (M4)
  - Any empty list → `BracketEmptyState`
  - All async load placeholders → `BracketLoading`

## Design decisions

**BracketMarkLogo built with absolutely positioned div borders, no SVG.**
NM design稿 uses `border-left + border-right + border-top + border-bottom`
slices to draw the brackets. This:
- Renders crisp at any size (border 1.5px is pixel-aligned)
- Inherits color from `currentColor` for theming
- Doesn't require an SVG file in the bundle

Trade-off: not vector — can't be exported as standalone svg for marketing.
For marketing assets we'd render a separate SVG.

**BracketEdge offset by `-1px`.** The bracket sits *just outside* the
parent border so it looks like a label peeking from the corner, not a chip
attached inside the corner. Negative offsets require the parent to have
`position: relative` (which `BracketCornerMarks` provides).

**BracketSectionLabel composed at the markup level**, not via :before/:after
pseudo-elements. Pseudo-elements would prevent the trailing slot from
sitting between the closing `]` and the right edge.

**BracketCornerMarks is a composition**, not a new primitive — it's just
four `BracketEdge`s. This deliberately keeps `BracketEdge` as the
fundamental building block and `BracketCornerMarks` as the convenience.

**BracketEmptyState is centered + restraint-first.** No icon at top by
default (icons in the bracket compete for attention); add via children if
needed.

**BracketDropzone is two diagonal corners (tl + br), not a complete
border.** Per NM spec — the "container is implied, not drawn". Active state
adds carbon-soft tint.

**BracketLoading uses three independent typing-blink dots with stagger.**
Three dots of `animate-typing-cursor` with `animationDelay: 0/200/400ms`
produces a sequential dot-wave effect that reads as "thinking" without
spinner geometry.

## Gotchas

- `BracketEdge` REQUIRES the parent to be `position: relative`. Without
  that, the corner offsets snap to the viewport. `BracketCornerMarks`
  handles this; if you place `BracketEdge` directly, set
  `style={{ position: 'relative' }}` on the parent.
- `BracketEmptyState` font-size is fixed at 22px display font. For very
  small empty states (in-card), override via className with text-base.
- `BracketLoading` triple dots' `animation-delay` causes the second/third
  dot to start invisible. If reduced-motion is preferred, the
  `prefers-reduced-motion` media query in index.css collapses the animation
  to 0.01ms, making all 3 dots visible immediately — that's the intended
  fallback.

## Related

- `nm/identity.tsx` — RingAvatar etc. typically used together with
  BracketEdge / BracketCornerMarks
- `nm/surface.tsx` — PaperCard often hosts BracketCornerMarks for selected state
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.2
