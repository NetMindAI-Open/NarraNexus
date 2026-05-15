---
code_file: frontend/src/components/layout/ContextPanelHeader.tsx
last_verified: 2026-05-14
stub: false
---

# ContextPanelHeader.tsx — Right-panel tab strip with notification indicators

## 2026-05-14 — "Skills" tab renamed to "Skill & MCP"

The `id: 'skills'` tab's `label` is now `"Skill & MCP"` — `[[SkillsPanel]]`
absorbed `[[MCPManager]]`. The `ContextTab` id stays `'skills'` (same as the
"Config"/`'awareness'` label↔id mismatch noted below — labels are display
strings, ids are stable).

## 为什么存在

Owns the `ContextTab` type definition (the single source of truth for tab names) and renders the tab strip with two types of notification indicators:
- Red number badge on "Inbox" tab when `agentInboxUnreadCount > 0`.
- Pulsing red dot on "Config" (Awareness) tab when the agent's awareness was updated during a run.

Also houses `CostPopover` (LLM spend display) as a utility button to the right of the tabs.

## 上下游关系
- **被谁用**: `MainLayout.ChatView`.
- **依赖谁**: `tabs.tsx` (Radix Tabs), `CostPopover`, `usePreloadStore`, `useConfigStore`.

## Gotcha / 边界情况

The "Config" tab displays with label "Config" but internally maps to `id: 'awareness'` — the tab switch renders `AwarenessPanel`. This mismatch exists because the panel was renamed from "Awareness" to "Config" in the UI but the internal type was not updated.

`ContextTab` is exported from this file — import it as `import type { ContextTab } from './ContextPanelHeader'` when needed in other layout files.
