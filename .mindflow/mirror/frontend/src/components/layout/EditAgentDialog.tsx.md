---
code_file: frontend/src/components/layout/EditAgentDialog.tsx
last_verified: 2026-07-23
stub: false
---

# EditAgentDialog.tsx — 编辑 agent 名称 + 描述

## 为什么存在

`agent_description` 一直是"活字段但无 UI":它进 agent 自己的 LLM 上下文
(`basic_info_module` → 身份 prompt 的 Description 行)和 A2A Agent Card,还用在
社交网络 / message bus 里别的 agent 看到的描述,但前端从没给过编辑/展示入口——
于是超长值只能靠 bundle 导入注入(NetMindAI-Open#71)。这个对话框补上唯一的
可编辑入口(名称 + 描述)。

## 设计决策

- 两个字段都受 `AGENT_TEXT_MAX_LENGTH`(见 [[agentLimits]],镜像后端
  entity_schema 常量)约束:实时 `n/255` 计数器超限变红,超限时 Save 禁用 +
  红字提示。**不**在输入框上加 maxLength 硬截,而是允许输入(比如粘贴一大段)
  再靠计数 + 禁用来"报错",符合"给 count 并且超过报错"的要求;真正的兜底是
  后端 `UpdateAgentRequest` 的 422。
- 受控于 [[AgentList.tsx]](持有 busy / editTarget),只在打开时挂载,所以字段
  每次打开都自然重置,无需 reset effect(与 [[ClearAgentDataDialog.tsx]] 同套路)。
- 名称为空也禁用 Save;保存时 name 走 `trim()`,description 原样(允许尾随空格,
  它是自由文本)。

## 上下游

- **被谁用**:[[AgentList.tsx]] 在 `editTarget && ...` 下渲染。
- **依赖**:`@/components/ui` 的 Dialog/Input/Textarea/Button;`api.updateAgent`
  由宿主调用(本组件只回调 `onSave(name, description)`)。

测试:`__tests__/editAgentDialog.test.tsx`(计数、超限禁用、空名禁用、成功保存)。
