---
code_file: frontend/src/components/ui/Dialog.tsx
last_verified: 2026-08-27
stub: false
---

# Dialog.tsx — Custom portal modal with Escape key and scroll lock

## 为什么存在

Renders via `createPortal(document.body)` to escape any parent `transform` that would break `position: fixed` stacking. This is the concrete reason it's custom rather than using a shadcn Dialog.

## 上下游关系
- **被谁用**: `AwarenessPanel` (edit awareness), `SettingsModal`, `InstallDialog`.
- **依赖谁**: `Button` (close button), `createPortal`.

## 设计决策

Handles `document.body.style.overflow = 'hidden'` on open to prevent background scroll. Cleans up on unmount. The nine size presets (`sm` through `6xl`) cover all use-cases without needing `className` overrides.

## Gotcha / 边界情况

- Does not use Radix `Dialog` primitive — no focus-trap or ARIA dialog role. If accessibility is required, this needs an upgrade.
- Sub-components `DialogContent` and `DialogFooter` are separate named exports — they are not `Dialog.Content` sub-components. Import them explicitly.
- **The title is a `div role="heading" aria-level={2}`, not an `<h2>`** (fixed
  2026-08-27). `index.css` styles bare `h1`-`h6` **outside any cascade layer**,
  and unlayered rules beat Tailwind v4's `@layer utilities` — so the intended
  `text-[12px]` archive label lost to `h2 { font-size: clamp(1.5rem,3vw,2rem) }`
  and every dialog title in the app rendered at 24-32px, wrapping onto two
  lines. The real culprit is the global rule; moving index.css typography into
  `@layer base` would fix it app-wide and is a separate change (it moves every
  heading in every page).
