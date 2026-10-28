---
code_file: frontend/src/components/awareness/ChannelActiveToggle.tsx
last_verified: 2026-09-10
stub: false
---

# ChannelActiveToggle.tsx — enable/disable a bound IM channel credential

## 2026-09-09 — `reason` prop：停用时显示平台给出的原因（B-28）

新增可选 `reason`：仅在 `!active && reason` 时在状态文字下方渲染
`channelActiveToggle.disabledReason`（"Disabled automatically: {{reason}}"，10 份 locale 同步，
另 8 份此前根本没有 `channelActiveToggle` 段，一并补齐）。后端在重新启用时清空该字段，所以
用户手动 disable 不会显示。Telegram / Slack / Discord / WeChat 四个面板都传 `credential.disabled_reason`
（Lark 走自己的 `auth_status` 状态条，不接；插件频道走 GenericChannelConfig 自己渲染同一键）。
vitest `__tests__/channelActiveToggle.test.tsx` 钉住：inactive+reason 渲染 / active 不渲染 / inactive 但 reason 空不渲染。

## 为什么存在

一个凭据从 bundle 导入后落库即**停用**（防双连）。用户需要一个界面把它激活——激活这一下就是"抢占该 bot 唯一的连接槽位"。5 个频道配置组件（Lark/Slack/Telegram/WeChat/Discord）都要这个 enable/disable 开关，抽成一个共享小组件，保证各处交互一致。

## 上下游关系

- **被谁用**：`LarkConfig / SlackConfig / TelegramConfig / WeChatConfig / DiscordConfig`，各自在"已绑定"区块里渲染 `<ChannelActiveToggle active=... onToggle=... />`
- **依赖谁**：父组件传入的 `onToggle(next)` —— 内部调 `api.set{Channel}Active(agentId, next)` 后 `fetchCredential()` 刷新。后端落到 `POST /api/<ch>/set-active`（flip is_active/enabled，不重新绑定）。

## 设计决策

### 纯受控 + busy 自管

组件只接 `active` + `onToggle`，自己管 `busy`（点击时禁用、转圈）。翻转后的真实状态由父组件重新拉凭据决定，避免本地 optimistic 状态和后端不一致。

### 为什么是"人当锁"

激活/停用是有意识的人工动作，不是机器级分布式锁。若用户在本地和云端都点了"启用"，两边仍会抢同一个连接槽位——这是设计上接受的边界（见设计文档 §6）。开关默认停用防的是**误触发**的双连，不是明知故犯。

## Gotcha

- Lark 的字段是 `is_active`，其余频道是 `enabled`。父组件传 `active` prop 时要取对字段（组件本身不区分渠道）。
- NarraMessenger 频道**没有**接这个开关（本轮激活 UI 只覆盖 5 个频道）；其凭据虽可打包/导入，但导入后需另行激活。
