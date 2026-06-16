---
code_file: frontend/src/components/bookmarks/BookmarkPanelHost.tsx
last_verified: 2026-06-11
stub: false
---

# BookmarkPanelHost.tsx — One lazy panel per atomic tab

## 为什么存在

Renders the single panel behind an atomic tab. Every panel is
React.lazy'd (the retired ContextPanelContent pattern): clicking a tab
mounts exactly one light chunk — the direct fix for the "small tabs
respond slowly" feedback (the first drawer iteration statically
mounted Jobs+Inbox / a whole accordion).

## 设计决策

- awareness/workspace/channels/social → AwarenessPanel `section` prop;
  skills/mcp → SkillsPanel `section` prop — atomic rendering reuses the
  existing panels' state and logic, nothing forked.
- Mount-time markTabOpened clears the tab's info highlights.
- JobsPanel's onJobResolved wires into resolveJobAttention.

## 新人易踩的坑

ActivityPanel / AgentProfilePanel (the multi-section first iteration)
were deleted 2026-06-11 — don't resurrect stacked sections; Owner rule
is one tab = one content.
