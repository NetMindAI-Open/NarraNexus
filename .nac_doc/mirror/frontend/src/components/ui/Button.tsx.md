---
code_file: frontend/src/components/ui/Button.tsx
last_verified: 2026-04-10
stub: false
---

# Button.tsx — Custom button matching the Bioluminescent Terminal design

## 为什么存在

Not imported from shadcn. Every button in the app uses this so the glow effects, focus rings, and disabled styles are consistent with the CSS-variable-based theme.

## 上下游关系
- **被谁用**: All domain components and `Dialog.tsx` (close button).
- **依赖谁**: `cn` utility, CSS variables only.

## 设计决策

Five variants cover every use-case without relying on external class overrides. `accent` hardcodes `text-[#0a0a12]` (dark) because it sits on a luminous gradient and must have contrast in both light and dark themes.

## Gotcha / 边界情况

- `size="icon"` is a square 40×40px. Always provide a `title` prop when using icon-only buttons.
- The `glow` prop is declared but currently unused in the rendered output — no-op at this time.
