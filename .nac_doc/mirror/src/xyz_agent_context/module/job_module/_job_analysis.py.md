---
code_file: src/xyz_agent_context/module/job_module/_job_analysis.py
last_verified: 2026-04-10
---

# _job_analysis.py — Job 执行结果分析提示词构建

## 为什么存在

从 `job_module.py` 提取出来，把"如何把一次 Job 执行的结果组织成 LLM 分析提示词"这件事独立维护。这是一个纯函数集合（无状态，无副作用），负责在 `_job_lifecycle.handle_job_execution_result()` 调用 LLM 之前组装好 prompt。

两个核心函数：`extract_execution_trace()` 把 `agent_loop_response` 里的工具调用和思考过程格式化成可读字符串；`build_job_analysis_prompt()` 把 Job 元数据、当前时间、执行产出、执行轨迹拼成一个结构化的分析提示词，按 `job_type` 给出不同的状态判断规则。

## 上下游关系

- **被谁用**：`_job_lifecycle.handle_job_execution_result()` 通过 `build_job_analysis_prompt()` 构建提示词；同时调用 `extract_execution_trace()` 准备 trace 字符串
- **依赖谁**：无外部依赖（纯计算）

## 设计决策

**按 `job_type` 分叉的状态判断规则**：`build_job_analysis_prompt()` 对 ONE_OFF、SCHEDULED、ONGOING 三种类型分别给出不同的提示词片段。ONE_OFF 告诉 LLM"执行成功就 completed，失败就 failed"；SCHEDULED 告诉 LLM"执行后保持 active 等下次触发"；ONGOING 告诉 LLM"判断 end_condition 是否满足，并可以在配置间隔的 ±2 倍范围内微调下次执行时间"。

**next_run_time 的调整自由度**：对 SCHEDULED 和 ONGOING 类型，提示词告诉 LLM"可以根据上下文微调执行时间，但偏差不超过配置间隔的 2 倍"。这给了 LLM 一定的弹性（比如检测到目标快要达成时缩短间隔），但避免了 LLM 把执行时间推迟到很远的未来。

**`extract_execution_trace()` 的抗噪设计**：`agent_loop_response` 里可能有大量 delta 和 progress 消息，只提取有 `title` + `details` 属性的 `ProgressMessage` 类型，并只截取工具调用前 200 字符、输出前 300 字符、思考前 200 字符。这是主动截断，防止 trace 太长撑爆分析提示词。

## Gotcha / 边界情况

- **`awareness_info` 从 `ctx_data.extra_data` 取**：`build_job_analysis_prompt()` 会从 `ctx_data.extra_data["awareness"]` 里提取 Agent 的 Awareness 信息放入提示词（前 500 字符）。这让 LLM 在分析 ONGOING 任务的 end_condition 时有业务背景。如果 Job 是由 `job_trigger` 触发执行，`ctx_data` 是运行时收集的；如果 Awareness 为空（比如 AwarenessModule 没有加载），LLM 就只能依赖提示词里的通用 example 来判断。

## 新人易踩的坑

- `extract_execution_trace()` 里 `thinking_items` 最多只取前 3 条（`thinking_items[:3]`）——如果你在调试时看到 trace 里缺少思考步骤，是这里的截断造成的，不是 LLM 没有思考。
