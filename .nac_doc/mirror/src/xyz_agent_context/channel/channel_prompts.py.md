---
code_file: src/xyz_agent_context/channel/channel_prompts.py
last_verified: 2026-04-10
stub: false
---

# channel_prompts.py — 所有 IM 渠道共用的 Prompt 模板库

## 为什么存在

渠道消息 prompt 的结构性文字（"你收到了一条来自 X 的消息"、"发件人档案"、"历史记录"等段落头）在所有渠道间是一样的，变化的只是填入的数据（渠道名、消息体等）。集中管理这些模板有两个好处：调整措辞时一处修改全渠道生效；方便审查和迭代 prompt 效果。

`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 是最关键的——它定义了整个渠道消息的框架，包含"通讯协议"章节，里面详细规定了 Agent 何时应该回复、何时应该保持沉默。这些规则是防止 Agent 陷入"自说自话"死循环的核心护栏。

## 上下游关系

**被谁用**：`ChannelContextBuilderBase.build_prompt()` 用 `.format(**info, ...)` 填充 `CHANNEL_MESSAGE_EXECUTION_TEMPLATE`；`_build_sender_profile()` 用 `SENDER_PROFILE_FROM_ENTITY_TEMPLATE` 或 `SENDER_PROFILE_UNKNOWN_TEMPLATE`；`_build_history_section()` 用 `CONVERSATION_HISTORY_TEMPLATE`；`_build_members_section()` 用 `ROOM_MEMBERS_TEMPLATE`。

**无其他依赖**：这个文件只有字符串常量，不导入任何其他模块。

**隐式消费者**：`narrative/_narrative_impl/continuity.py` 的 `_extract_core_content()` 依赖 `CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 生成的输出**以 `[Matrix · ...]` 开头**。这不是对这个文件本身的依赖，而是对整个 prompt 渲染链的依赖，但修改模板格式会破坏那里的假设。

## 设计决策

"通讯协议"章节（"## Communication Protocol"）是 2026-03 经历多轮调优后写入的规则集，解决了三个核心问题：
1. Agent 之间的对话容易陷入"收到→好的→明白了→好的"的无效确认循环
2. 群聊里每条消息都会触发所有成员的 AgentRuntime，但大多数消息不需要每个人回复
3. @mention 被滥用导致每个人都被强制处理不相关消息

这些规则是通用的，不应写入具体 Agent 的 Awareness——Awareness 处理的是"这个 Agent 是做什么的"，通讯纪律是基础设施层面的规范。

## Gotcha / 边界情况

`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 里的 `{channel_key}` 占位符出现在 Instructions 第 5 条里（`contact_info.channels.{channel_key}`），这是 `get_message_info()` 返回的字段之一。如果子类的 `get_message_info()` 没有返回 `channel_key`，`.format()` 会抛 `KeyError`。

模板里有中英文混合的示例（"好的"、"谢谢"等），这是刻意的——系统主要面向中文用户，给 LLM 提供中文表达的反例让它更好地识别无效确认语。

## 新人易踩的坑

模板里有两个"消息目标"的说明：`matrix_send_message` 回复渠道房间，`send_message_to_user_directly` 发送给 owner。这两个工具名是硬编码在模板里的。如果渠道的 MCP 工具名改了，必须同步更新这里的说明，否则 Agent 会用错工具。
