---
code_file: backend/routes/openai_compat.py
last_verified: 2026-06-10
stub: false
---

## 2026-06-10 — `complete` 加入 `_TERMINAL_TYPES`

`BackgroundRun._finalize` 现在会向 live 订阅者广播终结
`{"type":"complete","state":...}` 帧（见 background_run.py.md 同日条
目）。本路由的 `_TERMINAL_TYPES` 把它加进去，让 SSE 流在收到该帧时立即
发 finish_reason="stop" + `[DONE]`，而不是等 broadcaster 关闭、迭代器
耗尽才结束。行为等价，只是更及时。

# openai_compat.py — OpenAI 兼容 chat completions（Manyfold 接入）

## 为什么存在

Manyfold 平台的 `ApiChatAdapter` 只会说标准 OpenAI 协议。本路由暴露
`POST /v1/chat/completions`，把一次 NarraNexus agent run 翻译成 OpenAI
SSE chunk 流，让外部平台无侵入地驱动 agent。

## 关键 Owner 决策（2026-05-25）

- 请求里的 `model` 字段 = **agent_id**（不是模型名）；所有 chunk /
  error 响应原样回显。
- 鉴权：Bearer `MANYFOLD_GATEWAY_TOKEN`（auth middleware 先行过滤）。
- 仅当 `ENABLE_MANYFOLD_API=1` 时注册（backend/main.py 条件 include）。

## 上下游

复用 BackgroundRun + Broadcaster：创建 run 后 subscribe broadcaster，
把事件按 `_classify_event` 映射到四个 OpenAI 通道——agent_thinking /
agent_response → `delta.reasoning_content`；
`send_message_to_user_directly` 的 args.content → `delta.content`；
其他 tool → `delta.tool_calls`；tool output → 非标准
`delta.tool_results` 扩展（Manyfold 端 openclaw.adapter.ts 配对消费）。
终结帧（`_TERMINAL_TYPES`）→ finish_reason="stop" + `data: [DONE]`。

## Gotcha

- 不映射的事件类型返回 None 静默跳过——新增广播帧类型对本路由默认
  无害，但若它是终结语义必须加进 `_TERMINAL_TYPES`。
- subscribe 必须发生在 run 启动后尽快完成，否则可能错过早期事件
  （见 L373 注释）。
