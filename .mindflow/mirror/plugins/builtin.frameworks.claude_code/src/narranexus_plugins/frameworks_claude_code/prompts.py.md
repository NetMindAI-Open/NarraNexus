---
code_file: plugins/builtin.frameworks.claude_code/src/narranexus_plugins/frameworks_claude_code/prompts.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-09 — `task_list_tools_notice`（跨 run 工作的去向；2026-09-10 改口径）

`TASK_LIST_TOOLS_NOTICE_TEMPLATE` + `task_list_tools_notice(tools)`：渲染一段通用规则——
CLI 自带的任务清单工具（列表由 [[sdk]] 的 `TASK_LIST_TOOLS` 传入，模板与钉死集合不会各写一份）
在本平台**不可用**（二轮 review I2：headless spawn 下 CLI 本来就不提供这四个工具，初版写「被禁用」是在
描述一个模型从未拥有的能力；现在措辞是 "not available"，测试断言不含 "disabled"）；模型真正持有的
**TodoWrite** 清单同样只活在本 run 内、平台不读——文案点名说明但**不禁用**它；**本 run 内后台命令
（run_in_background + TaskOutput/TaskStop）照常可用**——必须明说，否则是在对模型抹掉一个可用能力；
必须跨 run 的工作（延迟/定时/周期）走平台 Job module 工具（可用时），否则本轮做完。空列表 → 空串。
只提 Job module 与 `job_create`（Job MCP server 注册的真名；PR#392 复审 I1：初版写成不存在的
`create_job`）的存在，不写场景（铁律 #4）。`test_the_job_tool_named_in_the_notice_is_a_real_job_mcp_tool`
拿 notice 里点名的工具去对 `create_job_mcp_server()` 的工具表，改名一边另一边立刻红。

## 2026-08-17 — 来源声明排在回复规则前面

`append_reply_reminder` 多收一个 `origin_declaration`，拼在提醒之前：「我在哪、在跟谁说话」
必须落在「你该怎么回答他们」之前，否则规则到达时没有可依附的对象。它是**已经渲染好的**
字符串（见 [[turn_input]]），本函数只负责拼装、不负责措辞。

docstring 里「team 房间的回复面故意为空」那条例外删掉了：team 回复现在和其他表面一样是
工具调用，所以「纯文本谁也到不了」在任何地方都不再有豁免。剩下的空声明只有一种情况
——**回复面未知**，那时编一个工具名比沉默更糟。


## 2026-08-04 — 新增 append_reply_reminder（不再是纯常量文件）

`REPLY_REMINDER_TEMPLATE` + `append_reply_reminder(user_message, tools)`：
把平台声明的本轮回复面（TurnInput.expressive_tools，与 NexusPower 尾部
同一数据源）渲染成 user message 末尾的一段通用规则（只有 reply 工具送达/
纯文本不送达/消息自带指令优先）。无声明 → 原样返回（不为未知来源
编造回复面）。规则固定、数据逐轮变化——general 指令 + 声明式数据，
不做 per-surface hardcode。
# adapters/claude/prompts.py — Claude Agent SDK system prompt 的格式常量

## 为什么存在

`adapters/claude/sdk.py` 在构建 system prompt 时需要把多轮对话历史拼接进去（因为 Claude Code CLI 不原生支持多轮对话，历史必须手动嵌入到 system prompt 中）。这些分隔符文本（chat history 的开始/结束标记、截断警告）如果直接硬编码在 `agent_loop()` 里，会让那个方法更难读，也更难在测试中替换。这个文件把它们提取为具名常量。

## 上下游关系

只被 `adapters/claude/sdk.py` 的 `agent_loop()` 方法使用。未来如果添加其他需要拼接历史的地方，可以复用这些常量保持格式一致。

这是一个纯数据文件，没有任何依赖，也没有其他下游。

## 设计决策

常量而非配置：这些 prompt 格式是系统的一部分，不应该由用户配置。把它们放在独立文件里是为了可见性和可维护性，而不是为了可配置性。

历史结束指令（`CHAT_HISTORY_END_INSTRUCTION`）包含了明确的行为引导（"This time please make the response by user input in this turn"），这是为了防止 Claude 混淆历史对话和当前 turn 的任务。

## Gotcha / 边界情况

- `CHAT_HISTORY_TRUNCATED_HEADER` 和 `CHAT_HISTORY_HEADER` 的区别只在于标题文字，实际截断逻辑在 `adapters/claude/sdk.py` 里处理。修改截断行为不需要改这个文件。

## 新人易踩的坑

- 这些常量目前只在 `adapters/claude/sdk.py` 里用到，但如果实现了其他 agent backend（如直接用 Anthropic API 而非 Claude CLI），也需要同样的 prompt 格式，应该复用这些常量而不是再硬编码一份。
