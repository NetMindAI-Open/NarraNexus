---
code_file: frontend/src/stores/dashboardStore.ts
last_verified: 2026-04-13
stub: false
---

# dashboardStore.ts

## 为什么存在
Dashboard 页的状态中枢。管 agents 数据、自适应 polling 节奏（FSM）、429 退避。

## FSM（TDR-6）
3 输入：`visibility`（document.visibilityState）× `tauriFocused`（tauri blur/focus event）× `any_running`（agents.some(kind !== 'idle')）

| visibility | tauri focus | any_running | interval |
|---|---|---|---|
| hidden    | —       | —    | ∞ (pause) |
| visible   | blurred | —    | ∞ (pause) |
| visible   | focused | true | 3000ms    |
| visible   | focused | false| 30000ms   |

Web 模式 `isTauri()==false` → `tauriFocused` 视为永远 true。

## 429 退避
后端 rate limit (2 req/s per viewer) 打到 429 → `onRateLimited()` 指数退避（2s → 4s → 8s → …，上限 60s）。成功响应清零 backoff。

## 上下游
- 上游：`DashboardPage.tsx` 订阅 + driven polling 循环
- 下游：`api.ts::getDashboardStatus()` + `tauri.ts::setTrayBadge`

## Gotcha
- `lastTrayCount` 存 store，仅当 running count **变化**才 invoke tray → 减少 IPC 开销
- `computeInterval()` 返 `Infinity` 代表"不调度"，`DashboardPage` 见 Infinity 就不 `setTimeout`，等下次 visibility/focus 变化被动唤醒
