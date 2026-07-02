---
code_file: src/xyz_agent_context/module/_module_impl/instance_decision.py
last_verified: 2026-06-17
stub: false
---
# instance_decision.py — LLM 驱动的 Module Instance 智能决策

## 为什么存在

这是 Module 系统的"调度大脑"：给定用户输入 + 当前活跃的 Task Module 实例 + Narrative
摘要 + awareness + 已加载的 Capability 模块，调一次 LLM 产出
`InstanceDecisionOutput`——决定走 `agent_loop` 还是 `direct_trigger`，以及要创建 /
保留哪些 Task Module 实例（主要是 JobModule）。

关键边界：**Capability 模块（Chat / Awareness / Social / BasicInfo…）由 loader 规则
自动加载，不进 LLM 决策**；LLM 只管 Task 模块（JobModule）的创建与生命周期。这与
铁律 #4 一致——通用调度逻辑在这里，具体场景由各 Agent 的 Awareness 提供。

输出用 Pydantic structured output（`InstanceDecisionOutput` / `InstanceDict` /
`JobConfig`）强约束 LLM 的 JSON 形状，避免解析飘移。`_get_default_decision` 是 LLM
调用失败时的兜底：原样保留当前 Task 实例，不丢任务。

上游：`_module_impl` 的 loader / instance 管理逻辑调用 `llm_decide_instances`。
下游：`dict_to_module_instance` 把 LLM 输出的 dict 转成 `ModuleInstance` 落库。

## 2026-06-17 — helper LLM 调用切到 protocol-agnostic 的 get_helper_sdk()

PR #25 把 `OpenAIAgentsSDK()` 直接实例化改成 `get_helper_sdk()`。意图是**别把决策
用的 helper LLM 绑死在 OpenAI Agents SDK 上**（铁律 #9）：`get_helper_sdk()` 做
protocol-agnostic 分发，底层框架 / provider 可换而本文件不动。行为契约不变——仍是
`sdk.llm_function(instructions=..., user_input=..., output_type=InstanceDecisionOutput)`，
拿 `result.final_output`。这是一次纯依赖收敛，无决策逻辑改动。
