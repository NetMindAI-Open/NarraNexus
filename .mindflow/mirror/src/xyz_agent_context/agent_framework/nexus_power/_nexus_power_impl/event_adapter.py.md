---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/event_adapter.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — TYPE_TOOL_USE_START → pending tool_call

映射为与完整调用**同形状**的 legacy `tool_call_item`（同用 `arguments` 键，
空 dict）+ `pending: True`。消费端按 tool_call_id 原地替换即可，不必学习
新消息型别；不支持名字先行的框架只发一次完整事件，消费端无需分支。

# event_adapter — 遗留 dict 契约唯一翻译点

## 2026-07-29 — text_delta 的 thinking_item 打 monologue 标

独白(text_delta)与 CoT(thinking_delta)都映射成 thinking_item 展示,但只有独白带
`monologue: true`:它是本框架的"assistant text"等价物,平台的 reasoning 链
(final_output → meta_data.reasoning → 下轮 <my_reasoning>)靠这个标志接上。
CoT 不打标——任何驱动的 CoT 都不进 final_output。

灰度共存的护城河:六种遗留形状只在这里产出,旧消费链(ResponseProcessor/前端/计费/审计)零改动。step_done→response.usage(每步用量,代理模型记账兜底同款通道);turn_done→response.done(双词汇 usage)。tool_arg_delta/compaction 无遗留形状,有意丢弃(新协议消费方读类型化流)。
