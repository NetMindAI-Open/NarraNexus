---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/prompts.py
last_verified: 2026-08-18
---

## 2026-08-17 — 房间版 delivery 段改写：不再承诺自动上墙

`JOB_DELIVERY_TO_ROOM` 原文是「你下面的正文就是报告，运行结束时自动发到房间——不要通过
任何函数投递」。这句话只在 team 房间收纯文本的那段时间里为真，而那正是全平台唯一
「纯文本谁也到不了」为假的表面。改造后若原样不动，等于告诉 job 写完报告就停，房间一个字
也收不到。

现在：**用 `message_team` 把报告发到房间；你的纯文本谁也到不了，房间也不例外；运行结束
时不会有人替你发。** 平台侧仍有兜底，但那是兜底不是契约，见 [[job_trigger]]。

`JOB_DELIVERY_TO_OWNER` 里的工具名同步改为 `notify_owner`——job 的桌上就是它。


## 2026-08-17 — 注释里的函数名修正

`job_trigger._deliver_job_result` → `_deliver_to_origin`。按图索骥的人 grep 不到
就得重读整个文件；属于铁律 #10 关心的「文档指向不存在的东西」，只是发生在
Tier-1。

## 2026-08-14 — 投递指令按来源拆成两份

`JOB_EXECUTION_PROMPT_TEMPLATE` 里写死 owner 私聊的那段拆出来，成为
`JOB_DELIVERY_TO_OWNER` / `JOB_DELIVERY_TO_ROOM`，由
`job_delivery_instructions(origin_source)` 选。

写死那段就是「@Leader 明早提醒我们」投进一个人 DM 的直接原因。两个方向都不能
错：让房间来源的 job 去调 `send_message_to_user_directly` 会投错地方；让私聊 job
以为「明文自动上墙」则会**整个丢掉**答案——没有任何东西会去搬运它。

提示词和投递代码读**同一个字段**（`jobs.origin_source`，投递侧见
[[job_trigger]]），所以两者不可能描述不同的面。未知来源退回 owner 私聊：那是唯
一永远存在的面，而「不认识的值」意味着我们不知道该往哪投，最安全的答案是一定有
人会看的那个地方。

## 2026-06-12 — JOB_TASK_INFO_TEMPLATE renders identities by human name

The task-info section now has `Execution identity: {execution_identity}` and
`Task creator: {task_creator}` placeholders (human names) instead of raw
user_id. The names are resolved in [[_job_context_builder.py]] via
[[user_repository.py]] `get_display_name`, so the opaque NetMind userSystemCode
is no longer shown as a person in a job's execution prompt.

# prompts.py — Job 执行提示词模板与 ONGOING 分析提示词

## 为什么存在

集中管理所有与 Job 执行相关的提示词模板，避免把长字符串散落在多个 py 文件里。这里有两类内容：
1. 组成 Job 执行提示词的五个分段模板（`JOB_TASK_INFO_TEMPLATE`、`JOB_ENTITIES_SECTION_TEMPLATE` 等），由 `_job_context_builder.build_execution_prompt()` 拼接使用
2. `ONGOING_CHAT_ANALYSIS_PROMPT`，由 `_job_lifecycle.update_ongoing_jobs_from_chat()` 在 CHAT 触发路径下判断 ONGOING Job 是否满足 `end_condition` 时使用

## 上下游关系

- **被谁用**：`_job_context_builder.py`（五个执行模板）；`_job_lifecycle.py`（`ONGOING_CHAT_ANALYSIS_PROMPT`）
- **依赖谁**：无（纯字符串常量，没有 Python 依赖）

## 设计决策

**`JOB_EXECUTION_PROMPT_TEMPLATE` 的关键指令**：提示词对 Agent 有三条硬性要求——必须调用 `notify_owner` 发送最终报告，只发一条，不发中间进度。这三条是 Job 执行后"用户能看到 Job 结果"的前提条件。`notify_owner` 的输出是 ChatModule 里 `_extract_user_visible_response()` 能识别的唯一信号——Agent 所有其他输出对用户都不可见。

**执行身份说明**：模板里明确告诉 Agent"你的 Narrative、记忆、对话历史是为这个实体加载的"，以及"调用 `notify_owner` 时消息会出现在这个实体的对话历史里"。这是关键上下文——JobTrigger 在执行时切换了 user_id（用 `related_entity_id or user_id`），但 Agent 自身不知道自己在"扮演另一个用户的助手"。这段说明消除了这种认知歧义。

**`ONGOING_CHAT_ANALYSIS_PROMPT` 要求 LLM 返回结构化字段**：`job_id`、`is_end_condition_met`、`end_condition_reason`、`should_continue`、`progress_summary`、`process` 六个字段，与 `_job_lifecycle.py` 里 `OngoingExecutionResult` Pydantic 模型对应。提示词里有举例说明（"客户说'我买了' → 满足"、"客户问'价格是多少' → 未满足"），但这些是通用示例，不是写死的销售场景——Agent 的 Awareness 里定义的具体业务场景会覆盖这类判断。

## Gotcha / 边界情况

- **`extra_requirement` 占位符**：`JOB_EXECUTION_PROMPT_TEMPLATE` 末尾有 `{extra_requirement}` 占位符，内容由 `_job_context_builder.py` 动态填充（有上下文时加第 6 条要求，无上下文时传空字符串）。如果直接 `str.format()` 调用而不传这个占位符，会 `KeyError`。

## 新人易踩的坑

- 改 `ONGOING_CHAT_ANALYSIS_PROMPT` 时要同步检查 `_job_lifecycle.py` 里解析返回结果的代码——提示词里要求的返回字段名和 `OngoingExecutionResult` Pydantic 模型的字段名必须一一对应。
- 五个执行模板用 `JOB_` 前缀命名，ONGOING 分析提示词用 `ONGOING_CHAT_ANALYSIS_PROMPT` 命名——风格不统一，是历史原因造成的。

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
