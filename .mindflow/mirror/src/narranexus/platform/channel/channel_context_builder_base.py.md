---
code_file: src/narranexus/platform/channel/channel_context_builder_base.py
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 — 按 room_type 选通讯协议 + 新增轮次信封

两件事，都因为 `room_type` 从展示标签变成了行为开关（缘由见
`channel_prompts.py.md` 同日条目）：

1. **`build_prompt()` 多填一个占位符**：`communication_protocol=
   communication_protocol_for(info.get("room_type"))`。1:1 私聊拿到「默认回复」那份，
   群聊拿到原有的沉默纪律那份。子类不需要改任何东西——只要 `get_message_info()`
   照旧返回 `room_type` 就自动生效。
2. **`turn_envelope()` + `reply_kwargs()`**：给 trigger 层取用的通用轮次事实。
   `build_prompt()` 把 `get_message_info()` 的结果缓存进 `_message_info_cache`
   （**别再调一次** `get_message_info()`——Slack/Lark 那几个会打平台 API），
   `turn_envelope()` 从缓存产出 `{channel_room_type, channel_reply_kwargs}`，
   由 `ChannelTriggerBase` 并进 `trigger_extra_data`。
   `reply_kwargs()` 默认空 dict；只有寻址一个会话还需要额外参数的渠道才重写
   （目前只有微信 iLink 要 `context_token`）。

   **为什么放在信封里而不是让 step_3 去问渠道**：`step_3` 要在「1:1 私聊、模型
   一个表达工具都没调」时替 agent 把回复发出去（见 `step_3_agent_loop.py.md`
   同日条目）。信封是通用键 + `ChannelSenderRegistry`，所以编排层不 import 任何
   渠道模块（铁律 #3）。

   `build_prompt()` 没跑过时 `turn_envelope()` 返回 `{}`，调用方按「不是私聊」处理
   ——即不兜底。

# channel_context_builder_base.py — 渠道消息 Prompt 组装的抽象基类

## 为什么存在

每个 IM 渠道（Matrix、Slack 等）的消息 prompt 结构是相同的：消息元数据 → 发件人档案 → 历史记录 → 当前消息 → 群成员 → 操作指令。但获取这些数据的方式各渠道不同（Matrix 通过 SDK 查房间，Slack 通过 API 查频道）。

`ChannelContextBuilderBase` 用 Template Method 模式固定组装顺序，只让子类实现数据获取的三个抽象方法，避免每个渠道 Module 重复实现一遍相同的 prompt 拼接逻辑。

## 上下游关系

**被谁继承**：`module/matrix_module/` 里的 `MatrixContextBuilder`（具体名称以代码为准）继承它并实现抽象方法。未来的 Slack Module 也应继承它。

**依赖谁**：`channel_prompts.py` 里的五个模板字符串（`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 等）；`SocialNetworkRepository`（通过 `get_sender_entity()` 查发件人档案，默认实现返回 None，子类可重写）；`ChannelHistoryConfig` dataclass 控制历史记录行为。

**下游**：`build_prompt()` 的返回值是 **执行 prompt**，作为 AgentRuntime 的 `input_content`。另有 `build_retrieval_anchor()`（2026-06-01 新增）产出**干净检索锚点** `[From <name>] <body>`，由 trigger 放进 `trigger_extra_data["retrieval_anchor"]`，narrative 检索/continuity 只 embed 这个锚点（不再解析执行 prompt）。

## 设计决策

`get_sender_entity()` 在基类里默认返回 `None`——基类不直接依赖 `SocialNetworkRepository`，由子类决定是否查社交图谱。这避免了基类与 SocialNetworkModule 的强绑定（遵循模块独立原则）。

群成员列表（`get_room_members()`）只在成员超过 2 人时才渲染到 prompt 里，1:1 DM 不需要显示成员列表。

`build_retrieval_anchor()`（2026-06-01）用 `get_message_info()` 的结构化字段（`sender_display_name` + `message_body`）直接组锚点，**不解析** execution 模板——因此 build_prompt 的模板格式与 narrative 检索解耦了（旧的 `_extract_core_content` 正则耦合已删除，它在 prod 早已因模板漂移而失效）。

历史记录截断策略是从最旧的消息开始删，最后一条消息（待回复的那条，用 ▶ 标记）永远不被截断。

## Gotcha / 边界情况

`_format_messages()` 的时间戳格式曾被 `continuity._extract_core_content()` 的正则依赖；该函数 2026-06-01 已删除（continuity 改用结构化锚点），所以这层格式耦合不复存在。`_format_messages` 现在只服务于 execution prompt 的历史记录段。

`ChannelHistoryConfig.history_max_chars` 默认 3000 字符，超出后旧消息被截断。截断时会在开头插入 `"  ... (earlier messages truncated)"` 提示，但这个提示本身会占用 chars 计数，极端情况下可能导致即使截断了还是超出，陷入循环——这个 bug 目前未修复。

## 新人易踩的坑

Chat Module 和 Job Module 的 prompt **不经过**这个基类——它们有自己的 prompt 逻辑（文件开头注释里有明确说明）。只有外部 IM 渠道 Module 才用这个基类。别把 ChatModule 的 prompt 构建也改到这里来。
