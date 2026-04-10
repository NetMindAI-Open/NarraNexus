---
code_file: src/xyz_agent_context/module/job_module/prompts.py
last_verified: 2026-04-10
---

# prompts.py — Job 执行提示词模板与 ONGOING 分析提示词

## 为什么存在

集中管理所有与 Job 执行相关的提示词模板，避免把长字符串散落在多个 py 文件里。这里有两类内容：
1. 组成 Job 执行提示词的五个分段模板（`JOB_TASK_INFO_TEMPLATE`、`JOB_ENTITIES_SECTION_TEMPLATE` 等），由 `_job_context_builder.build_execution_prompt()` 拼接使用
2. `ONGOING_CHAT_ANALYSIS_PROMPT`，由 `_job_lifecycle.update_ongoing_jobs_from_chat()` 在 CHAT 触发路径下判断 ONGOING Job 是否满足 `end_condition` 时使用

## 上下游关系

- **被谁用**：`_job_context_builder.py`（五个执行模板）；`_job_lifecycle.py`（`ONGOING_CHAT_ANALYSIS_PROMPT`）
- **依赖谁**：无（纯字符串常量，没有 Python 依赖）

## 设计决策

**`JOB_EXECUTION_PROMPT_TEMPLATE` 的关键指令**：提示词对 Agent 有三条硬性要求——必须调用 `send_message_to_user_directly` 发送最终报告，只发一条，不发中间进度。这三条是 Job 执行后"用户能看到 Job 结果"的前提条件。`send_message_to_user_directly` 的输出是 ChatModule 里 `_extract_user_visible_response()` 能识别的唯一信号——Agent 所有其他输出对用户都不可见。

**执行身份说明**：模板里明确告诉 Agent"你的 Narrative、记忆、对话历史是为这个实体加载的"，以及"调用 `send_message_to_user_directly` 时消息会出现在这个实体的对话历史里"。这是关键上下文——JobTrigger 在执行时切换了 user_id（用 `related_entity_id or user_id`），但 Agent 自身不知道自己在"扮演另一个用户的助手"。这段说明消除了这种认知歧义。

**`ONGOING_CHAT_ANALYSIS_PROMPT` 要求 LLM 返回结构化字段**：`job_id`、`is_end_condition_met`、`end_condition_reason`、`should_continue`、`progress_summary`、`process` 六个字段，与 `_job_lifecycle.py` 里 `OngoingExecutionResult` Pydantic 模型对应。提示词里有举例说明（"客户说'我买了' → 满足"、"客户问'价格是多少' → 未满足"），但这些是通用示例，不是写死的销售场景——Agent 的 Awareness 里定义的具体业务场景会覆盖这类判断。

## Gotcha / 边界情况

- **`extra_requirement` 占位符**：`JOB_EXECUTION_PROMPT_TEMPLATE` 末尾有 `{extra_requirement}` 占位符，内容由 `_job_context_builder.py` 动态填充（有上下文时加第 6 条要求，无上下文时传空字符串）。如果直接 `str.format()` 调用而不传这个占位符，会 `KeyError`。

## 新人易踩的坑

- 改 `ONGOING_CHAT_ANALYSIS_PROMPT` 时要同步检查 `_job_lifecycle.py` 里解析返回结果的代码——提示词里要求的返回字段名和 `OngoingExecutionResult` Pydantic 模型的字段名必须一一对应。
- 五个执行模板用 `JOB_` 前缀命名，ONGOING 分析提示词用 `ONGOING_CHAT_ANALYSIS_PROMPT` 命名——风格不统一，是历史原因造成的。
