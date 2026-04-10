---
code_file: frontend/src/components/ui/KPICard.tsx
last_verified: 2026-04-10
stub: false
---

# KPICard.tsx — Shared metric tile used in dashboard grids

## 为什么存在

Extracted to avoid duplication between `AwarenessPanel` (contacts / chats / strong connections) and `AgentInboxPanel` (unread / rooms / read rate). Note: `AgentInboxPanel` still has a local inline copy — that copy should be removed and replaced with this shared component.

## 上下游关系
- **被谁用**: `AwarenessPanel`, `AgentInboxPanel` (partially via local copy).
- **依赖谁**: `cn` utility only.

## Gotcha / 边界情况

`icon` prop is `React.ElementType` (the class), not JSX. Pass `icon={Users}`, not `icon={<Users />}`. The `pulse` prop both applies an animated glow shadow to the card AND makes the icon pulse — useful for "live" counts like active streaming or unread messages.
