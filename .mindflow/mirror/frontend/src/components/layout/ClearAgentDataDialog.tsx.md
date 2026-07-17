---
code_file: frontend/src/components/layout/ClearAgentDataDialog.tsx
last_verified: 2026-07-10
stub: false
---

# ClearAgentDataDialog.tsx — scoped "clear data" confirm dialog

## 为什么存在

用户要能选择性地清理一个 agent 的数据:删聊天记录 / 删记忆 / 两者都删。这个多选弹窗替代了
[[Sidebar.tsx]] 原来那个"清空历史"按钮(单一确认、且后端清不干净)。入口现在在每个 agent 行的
⋮ 菜单([[AgentRowMenu.tsx]] 的 "Clear data…"),宿主是 [[AgentList.tsx]]。

## 上下游关系

**被谁用**:仅由 [[AgentList.tsx]] 条件挂载(`{clearTarget && <ClearAgentDataDialog/>}`),
确认后回调 `onConfirm({conversations, memory})` → `api.clearHistory(id, scopes)`。

**依赖谁**:`ui/Dialog`(Radix 基元)、`ui/Button`、`components/nm/form` 的 `Checkbox`、i18n
`layout.clearAgentData.*`。

## 设计决策

**条件挂载,不用 reset effect**:复选框默认都勾选(`useState(true)`)。因为宿主每次打开都是
全新挂载,状态天然回到默认,无需 `useEffect` 重置——这也规避了 `react-hooks/set-state-in-effect`
lint 规则。所以本组件不接受 `open` prop,挂载即代表打开。

**至少勾一个**:两个都不勾时"清理"按钮禁用;确认按钮是 `danger` 变体。文案强调不可恢复,并
列出保留项(人设、渠道绑定、账号)。

## Gotcha / 边界情况

`busy` 由宿主在请求期间置真,禁用全部交互防重复提交。磁盘删除失败(`disk_errors`)由宿主用
alert 提示,不在本组件内处理。
