---
code_file: src/xyz_agent_context/channel/channel_context_builder_base.py
last_verified: 2026-04-10
stub: false
---

# channel_context_builder_base.py — 渠道消息 Prompt 组装的抽象基类

## 为什么存在

每个 IM 渠道（Matrix、Slack 等）的消息 prompt 结构是相同的：消息元数据 → 发件人档案 → 历史记录 → 当前消息 → 群成员 → 操作指令。但获取这些数据的方式各渠道不同（Matrix 通过 SDK 查房间，Slack 通过 API 查频道）。

`ChannelContextBuilderBase` 用 Template Method 模式固定组装顺序，只让子类实现数据获取的三个抽象方法，避免每个渠道 Module 重复实现一遍相同的 prompt 拼接逻辑。

## 上下游关系

**被谁继承**：`module/matrix_module/` 里的 `MatrixContextBuilder`（具体名称以代码为准）继承它并实现抽象方法。未来的 Slack Module 也应继承它。

**依赖谁**：`channel_prompts.py` 里的五个模板字符串（`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 等）；`SocialNetworkRepository`（通过 `get_sender_entity()` 查发件人档案，默认实现返回 None，子类可重写）；`ChannelHistoryConfig` dataclass 控制历史记录行为。

**下游**：`build_prompt()` 的返回值会作为 AgentRuntime 的 `input_content` 传入，并被存到 `session.last_query`。因此输出格式会被 `narrative/_narrative_impl/continuity.py` 的 `_extract_core_content()` 解析——那里有硬编码的 `[Matrix · ...]` 模式匹配。

## 设计决策

`get_sender_entity()` 在基类里默认返回 `None`——基类不直接依赖 `SocialNetworkRepository`，由子类决定是否查社交图谱。这避免了基类与 SocialNetworkModule 的强绑定（遵循模块独立原则）。

群成员列表（`get_room_members()`）只在成员超过 2 人时才渲染到 prompt 里，1:1 DM 不需要显示成员列表。

`build_prompt()` 方法上有一段 TODO 注释，明确标注了它与 `continuity.py/_extract_core_content()` 的耦合点。这是刻意保留的"耦合警告"，提醒任何修改这里格式的人必须同步检查那边。

历史记录截断策略是从最旧的消息开始删，最后一条消息（待回复的那条，用 ▶ 标记）永远不被截断。

## Gotcha / 边界情况

`_format_messages()` 里的时间戳格式 `[{ts}] {sender}:\n    {body}` 是被 `_extract_core_content()` 里的正则表达式 `_MATRIX_LAST_MSG_RE` 所依赖的格式。如果修改了这里的格式（比如去掉方括号或换行），正则表达式会失效，连续性检测会收到未剥离的完整 prompt，导致 LLM 被渠道元信息干扰，判断质量下降。

`ChannelHistoryConfig.history_max_chars` 默认 3000 字符，超出后旧消息被截断。截断时会在开头插入 `"  ... (earlier messages truncated)"` 提示，但这个提示本身会占用 chars 计数，极端情况下可能导致即使截断了还是超出，陷入循环——这个 bug 目前未修复。

## 新人易踩的坑

Chat Module 和 Job Module 的 prompt **不经过**这个基类——它们有自己的 prompt 逻辑（文件开头注释里有明确说明）。只有外部 IM 渠道 Module 才用这个基类。别把 ChatModule 的 prompt 构建也改到这里来。
