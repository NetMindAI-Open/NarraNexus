---
code_file: src/xyz_agent_context/module/job_module/_job_lifecycle.py
last_verified: 2026-04-10
---

# _job_lifecycle.py — Job 执行后生命周期处理

## 为什么存在

从 `job_module.py` 分离出来（2026-03-06），把 `hook_after_event_execution` 里的 LLM 分析逻辑独立维护。这个文件集中了"Job 执行完之后应该发生什么"的核心决策——用 LLM 分析执行结果并决定 Job 的下一个状态，以及检测 ONGOING Job 的结束条件。

## 上下游关系

- **被谁用**：`JobModule.hook_after_event_execution()` 通过 `handle_job_execution_result` 和 `update_ongoing_jobs_from_chat` 委托调用
- **依赖谁**：`OpenAIAgentsSDK.llm_function()`（LLM 分析）；`JobRepository`（DB 更新）；`_job_analysis`（构建分析提示词）；`prompts.ONGOING_CHAT_ANALYSIS_PROMPT`

## 两条处理路径

**`handle_job_execution_result`（JOB 触发路径）**：
1. 从 `HookAfterExecutionParams` 提取执行上下文（trace、output、工具调用列表）
2. 通过 `instance_id` 找到对应的 `JobModel`
3. 调用 `_job_analysis.build_job_analysis_prompt()` 按 Job 类型（ONE_OFF/SCHEDULED/ONGOING）构建分析提示词
4. LLM 返回 `JobExecutionResult` 或 `OngoingExecutionResult`（Pydantic 结构化输出）
5. 更新 DB 字段（status、next_run_time、output、description 等）
6. 如果是终止状态（COMPLETED/FAILED），返回 `HookCallbackResult` 触发依赖链

**`update_ongoing_jobs_from_chat`（CHAT 触发路径）**：
当 CHAT 对话里有活跃的 ONGOING Job 实例时，用 `ONGOING_CHAT_ANALYSIS_PROMPT` 分析本次对话内容是否满足 Job 的 `end_condition`，满足则把 Job 标记为 `COMPLETED`。

## 设计决策

**LLM 结构化输出**：用 Pydantic 模型（`JobExecutionResult`、`OngoingExecutionResult`）作为 LLM 的输出 schema，通过 `OpenAIAgentsSDK.llm_function(output_type=...)` 确保 JSON 格式正确。避免了手工解析 LLM 返回的自由文本。

**失败时不抛异常**：如果 LLM 调用失败或 Job 记录找不到，函数记录 warning 日志并返回 `None`（不触发回调）。`job_trigger` 会作为 fallback 机械更新状态。这是容错设计，不是 bug 隐藏。

## Gotcha / 边界情况

- **`get_job_by_instance_id` 是注入的 callable**：这个参数让 `_job_lifecycle.py` 不直接依赖 `JobModule`（避免循环引用），而是通过闭包或 lambda 注入查询函数。调试时注意这个 callable 实际指向什么。
- **ONGOING 路径里的 `instance_id` 是 `job_xxx` 格式**：`active_job_instance_ids` 通过 `inst_id.startswith("job_")` 过滤，只处理 Job 实例。新增其他以 `job_` 开头的前缀类型（如果有的话）会被误匹配。
