---
code_file: frontend/src/types/messages.ts
last_verified: 2026-07-14
stub: false
---
# messages.ts — 前端运行时消息 + ChatMessage 类型契约

## 为什么存在

后端 `AgentRuntime` 以流式 `yield` 各类 RuntimeMessage（progress /
agent_response / agent_thinking / tool_call / error / complete …），WS 层把它们
`to_dict()` 后推给前端。本文件是这些 wire 消息的 **TypeScript 镜像**（对齐
`schema/runtime_message.py`），外加前端自己的 `ChatMessage`（会话/历史里持久化
的一条消息，由 chatStore 从 wire 消息组装而成）。它是 producer/consumer 的
稳定契约:字段漂了这里就报错。

## 关键类型

- `ErrorMessage`:wire 错误帧。`severity`（fatal/recoverable/recovered/
  recovered_after_reply）决定前端如何渲染。
- `ChatMessage`:UI 层一条消息，带 `isError` / `warnings` / `timeline` 等
  展示派生字段。MessageBubble 直接读它，不读 live session。

## 2026-07-14 — 确定性自助类错误字段（"黑盒" P1）

- `ErrorMessage.action_reason?`:仅当 `error_type === 'config_actionable'`
  时设置，取值 `context_window` / `insufficient_balance` / `model_not_found`
  （开放字符串，向后兼容新原因）。对应后端 `SELF_SERVICEABLE_ERROR_TYPE`。
- `ChatMessage.actionReason?`:chatStore 在 `stopStreaming` 时从
  `currentActionReason` 盖上（仅 `isError` 时），供 [[MessageBubble.tsx]] 渲染
  "你可以做什么"面板而非笼统失败。

这类失败（上下文太小/余额/模型 ID）确定性、可自助修复，后端不再让 helper
兜底掩盖，前端据此给可操作引导。
