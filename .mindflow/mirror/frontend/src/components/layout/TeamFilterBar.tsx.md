---
code_file: frontend/src/components/layout/TeamFilterBar.tsx
last_verified: 2026-05-09
stub: false
---

# TeamFilterBar.tsx — Sidebar 顶部 team chips (subproject 1)

显示 `[All] [<team chips>] [Untagged]` 三类切换 + 三个工具按钮（Upload / Package / Settings2）。

工具按钮：
- **Upload**：去 `/app/bundle/import` 导入 `.nxbundle`
- **Package**（2026-05-09 新增）：去 `/app/bundle/export`。如果当前 `selectedFilter` 是某个 team_id，自动透传 `?team=<id>&agents=<member_csv>`，让 wizard 直接预选好那 team 的成员
- **Settings2**：打开 `TeamManagementModal`

`Sidebar.tsx` 包了一层 `TeamFilterAndAgents` helper，根据 chip 选择计算 `filterAgentIds` 传给 `AgentList`。collapsed 模式下三个工具按钮也都在（垂直排列），保持入口对称。
