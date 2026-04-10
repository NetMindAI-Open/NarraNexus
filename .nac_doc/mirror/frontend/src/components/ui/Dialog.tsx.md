---
code_file: frontend/src/components/ui/Dialog.tsx
last_verified: 2026-04-10
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
