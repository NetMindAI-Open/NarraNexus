---
code_file: frontend/src/components/builder/ProviderPickerModal.tsx
last_verified: 2026-09-03
stub: false
---

# ProviderPickerModal.tsx — 创建工作室的 provider 准入闸门

## 为什么存在

没配 provider 的用户走进对话，第一条消息会死在「未配置 provider」上，得自己
回头去设置页诊断。闸门把这件事提前到进入之前。

Owner 在 2026-09-03 明确选了**方案 B**（设计稿里那张 `CREATE AGENT` 弹窗），
而不是 `/setup` 的一键配置卡。这是 v0 里唯一一块纯新增 UI；它仍然是纯前端，
不破坏「零后端改动」。

## 两个状态，常见的是空的那个

闸门只在用户**一个 provider 都没有**时才打开，所以首次进入时列表必然是空的。
这一态**不渲染**空列表和「Choose a provider」段 —— 一个标题压在空白上面比
直接说「连接一个 provider 就能开始」更差。添加成功后原地刷新并自动选中刚加的
那个，用户接着只需点一次 `Next`。

## 关键决策

**API Key 入口内嵌 `OneKeyOnboard`，不自己写表单。** 那个组件已经处理了
provider 类型识别、没有可辨前缀的聚合器 key、key 探活、以及已有 key 的轮换
确认。第二份 key 表单一定会和它漂移。

**CLI 入口刻意不是统一按钮。** `claude auth login` 只能通过 Tauri IPC 驱动，
所以网页端、以及没打进 CLI 的包，这个入口降级成提示文字。
`ProviderSettings.tsx` 画的是同一条界线 —— 一个看起来能点却点不动的控件比
一句「去终端跑这条命令」更糟。**这条约束与版本无关**，任何时候做 provider
弹窗都成立。

**不写 per-agent LLM override。** provider 和 slot 在本项目是 per-user 的，
`Next` 只是确认用户指的是哪个已有 provider。所以 `onReady` **没有参数**（09-03
评审修订：原先传 providerId 但唯一调用方从不接，删掉免得读者去找不存在的消费者）。副文案里那句 "This decides which
engine runs the agent" 描述的是关系，不是要落 `setAgentLlmConfig`。

## 上游 / 下游

- 由 [[ChooseCreateMethodPage.tsx]] 在探测到零 provider 时挂载（探测失败也
  挂载 —— fail closed）。
- 行数据经 [[providerRows.ts]] 映射；状态点用服务端的 `is_active`，不是我们
  自己猜的健康度。
- CLI 态读既有的 `api.getClaudeStatus()`（`providersApi.CliStatusPayload`）。

## Gotcha

每一次 setState 都在 promise 回调里，effect 体本身不写 state（eslint
`react-hooks/set-state-in-effect`），刷新用 nonce 驱动而不是直接调探测函数 ——
卸载后落地的刷新不会写进已死的组件。
