---
code_file: frontend/src/pages/DashboardPage.tsx
last_verified: 2026-04-13
stub: false
---

# DashboardPage.tsx

## 为什么存在
Dashboard v2 主页面组件。挂在 `/app/dashboard` 路由（App.tsx）。

## 协作
- 订阅 `dashboardStore` 的 agents / error / FSM 输入
- `visibilitychange` → `setVisibility`
- Tauri `tauri://blur` / `tauri://focus` 事件 → `setTauriFocused`（通过 `lib/tauri.ts::listenTauri`）
- 轮询循环：`tick()` 拿数据 → `setTrayBadge(runningCount)` 仅当变化 → 下次 `setTimeout(tick, computeInterval())`

## 渲染
- 卡片列表：`AgentCard`（3+1 主视图）+ 展开时 `AgentCardExpanded`（owner-only 详情）
- error 态 + 空态文案

## Gotcha
- 清理函数必须把 `active=false` + clearTimeout，否则卸载后 tick 继续发请求
- Tauri event listener 不存在时 `listenTauri` 返 null，调用方需 `unlistenFn?.()`
- DO NOT 把 `action_line` 传给 `dangerouslySetInnerHTML`（eslint `no-restricted-syntax` 会挡）
