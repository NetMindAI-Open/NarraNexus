---
code_dir: frontend/src/components/ui/
last_verified: 2026-04-10
stub: false
---

# ui/ — Bioluminescent Terminal design system

## 目录角色

Hand-rolled design-system primitives for the "Bioluminescent Terminal" aesthetic. **Not shadcn/ui** — all the styled wrappers are written from scratch using CSS custom properties (`--accent-primary`, `--bg-elevated`, etc.). The three Radix-based wrappers (`popover.tsx`, `scroll-area.tsx`, `tabs.tsx`, `tooltip.tsx`) are the only files that import from `@radix-ui/`.

Only these four Radix files should be in this directory. Everything else is a pure custom component.

## 关键文件索引

| File | Notes |
|------|-------|
| `Button.tsx` | 5 variants (default, ghost, outline, accent, danger) + 4 sizes. The `accent` variant uses the gradient primary; `danger` is for destructive actions. |
| `Card.tsx` | 4 variants (default, glass, elevated, sunken) + sub-components: `CardHeader`, `CardContent`, `CardTitle`, `CardFooter`. |
| `Badge.tsx` | Has a `pulse` prop that renders a live dot to the left of the text — used for unread counts. |
| `Dialog.tsx` | Custom portal-based modal. Uses `createPortal(document.body)` to escape transform-offset stacking contexts. Handles `Escape` key and body scroll lock. |
| `Markdown.tsx` | Wraps `react-markdown` with GFM + raw HTML (`rehype-raw`). External links open `_blank`. Exports `MarkdownPreview` for truncated inline previews. |
| `KPICard.tsx` | Extracted shared component used by `AwarenessPanel` and `AgentInboxPanel`. Takes an icon component as a prop (not JSX). |
| `EmbeddingBanner.tsx` | Chat-area top banner shown when vector index rebuild is pending. Drives itself from `embeddingStore` — no props needed. |
| `EmbeddingStatus.tsx` | Detailed rebuild progress panel (for settings). Same store, different presentation. |
| `AgentCompletionToast.tsx` | Fixed bottom-right toast queue for background agent completions. Reads from `chatStore.toastQueue`. Auto-dismisses after 5s. |
| `ThemeToggle.tsx` | Cycles light/dark/system via `useTheme` hook. |
| `popover.tsx` | Thin Radix wrapper. Styled to match the design system. |
| `scroll-area.tsx` | Thin Radix wrapper. |
| `tabs.tsx` | Thin Radix wrapper. Used in `ContextPanelHeader`. |
| `tooltip.tsx` | Thin Radix wrapper. |

## 和外部目录的协作

- `index.ts` re-exports everything except the four Radix wrappers (they are imported directly from their file paths by consumers that need them). This keeps the barrel import clean.
- Consumers: virtually every domain component imports at minimum `Button`, `Card`, `Badge` from `@/components/ui`.
- `embeddingStore` is the only store imported directly from this directory; all other components are pure display primitives.

## Gotcha

- `Dialog` renders into `document.body` via `createPortal`. If you render a Dialog inside a parent with `transform`, without the portal you would see z-index / positioning bugs. This is the reason the portal exists.
- `KPICard` accepts `icon: React.ElementType` (the component class), not `icon: ReactNode`. Pass `icon={Users}`, not `icon={<Users />}`.
- `AgentCompletionToast` has no props — it subscribes to the store directly. Mount it once in `MainLayout`.
