---
code_file: frontend/src/lib/mock/index.ts
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

mock 事件日志加一条 `browser_look`（Claude Code 形状：元数据 JSON + 图片描述），开发演示与视觉验收用。

## 2026-08-27 — `createAgent` 的返回体补 `bound_channels: []`

纯粹是跟着 `AgentInfo.bound_channels` 变必填走。新建的 agent 本来也没有任何
渠道绑定,`[]` 就是真值,不是占位符。

# mock/index.ts — 前端 mock API（demo / 离线模式）

## 为什么存在

与 [[api]] 同形的桩实现：mock 模式下（demo 部署 / 无后端本地开发）替换真实
ApiClient，让 UI 全链路可渲染。**契约**：方法集必须与 api.ts 的公开方法保持
同步——api.ts 增删方法时这里同步增删（本文件历史上缺 mirror，2026-07-18
删除 `setQuotaPreference` 时补建）。

## 变更史

- 2026-07-20 — 补 `useSubscription` 桩（api.useSubscription 有了第一个真实
  调用方——[[NetmindAccountPanel]] 的 Link it now 按钮——按"方法集同步"
  契约补齐）。
- 2026-07-18 — 删 `setQuotaPreference` 桩（随免费额度偏好端点移除，见
  [[api]] 同日条目）。

## 坑

- 桩返回值多为最小合法形态（如 `{ enabled: false }`），面板测试不要依赖
  mock 层的数据真实性——组件测试自带 vi.mock 的 api 层。

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
