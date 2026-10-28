---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/event_adapter.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — `output_truncated` 映成 `OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE`

TYPE_ERROR 的 error_type 若是 loop 自己的 `ErrorType.OUTPUT_TRUNCATED`，不再折成 `invalid_request`，而是映成
平台常量 `"output_budget_exhausted"`（[[runtime_message]]）；其余不在 `LEGACY_SAFE_ERROR_TYPES` 的类型照旧折叠。
这是 [[circuit_breaker]] 豁免的结构化信号：只有 loop 自己构造 OUTPUT_TRUNCATED（provider 错误分类器不会产出它），
所以调用方/provider 回显的文本无法伪造它。下游核过：`classify_self_serviceable` 对该类型返回 None、
`_is_auth_failure` 不命中、`_fallback_skip_decision` 与原 `invalid_request` 同路径、前端无按 `invalid_request` 分支。

## 2026-09-10（B-05/#127）— TYPE_ERROR 翻译透传框架自报的 `fatal`

`response.error` 的 data 带 `"fatal": bool(payload.get("fatal", True))`，原样透传 loop.py `_fail()`
的判定（[[loop]] 同日条目），本文件不做判断，契约正文见 [[response_processor]]。缺省 `True` 只是防御：
`_fail` 今天总是显式设这个键。

不带这个标记的后果：B-03 截断重试耗尽后的真失败，run 落 `state=completed` + 空回复 + 无 fatal 标记。

## 2026-09-03（插件平台批 1）— 事件常量改从 `narranexus.contracts.agent_events` import

`agent_framework/loop/events.py` 已删除（无兼容垫片，铁律 #2），本文件对事件字典常量/构造器的
引用全部指向契约包；语义与线上值不变（`tests/snapshots/golden/agent_events.json` 钉住）。

## 2026-08-31（二）— 映射表的理由加上例外

同 [[harness/expression]]：「delivered to no one」改成「**框架**不投递」，
并补一句无表达工具轮次的例外。

**映射本身没变，这是重点**：那种轮次上仍然映到 `thinking_item`。平台之后拿这段
文字做什么是平台的事；本表决定的只有一件事——**我们从不把它冒充成 reply**。
把这两件事分开写，是为了下一个人不会因为「原来它会被投递」就去改映射。

## 2026-08-31 — 语义映射表的措辞随宪法改口（无行为变化）

`text/thinking deltas -> thinking_item` 那条的理由原写「plain text is PRIVATE
reasoning」。改为「working narration：owner 可以看，但不送达给任何人」。

**映射一条都没动。** 它仍然不能映到 legacy「assistant text」通道——那样等于
把叙述当答案交给用户。变的是理由的准确性：挡住的是**冒充答案**，不是「藏起来」。

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
