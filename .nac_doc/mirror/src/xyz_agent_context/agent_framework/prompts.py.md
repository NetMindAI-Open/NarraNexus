---
code_file: src/xyz_agent_context/agent_framework/prompts.py
last_verified: 2026-04-10
stub: false
---
# prompts.py — Claude Agent SDK system prompt 的格式常量

## 为什么存在

`xyz_claude_agent_sdk.py` 在构建 system prompt 时需要把多轮对话历史拼接进去（因为 Claude Code CLI 不原生支持多轮对话，历史必须手动嵌入到 system prompt 中）。这些分隔符文本（chat history 的开始/结束标记、截断警告）如果直接硬编码在 `agent_loop()` 里，会让那个方法更难读，也更难在测试中替换。这个文件把它们提取为具名常量。

## 上下游关系

只被 `xyz_claude_agent_sdk.py` 的 `agent_loop()` 方法使用。未来如果添加其他需要拼接历史的地方，可以复用这些常量保持格式一致。

这是一个纯数据文件，没有任何依赖，也没有其他下游。

## 设计决策

常量而非配置：这些 prompt 格式是系统的一部分，不应该由用户配置。把它们放在独立文件里是为了可见性和可维护性，而不是为了可配置性。

历史结束指令（`CHAT_HISTORY_END_INSTRUCTION`）包含了明确的行为引导（"This time please make the response by user input in this turn"），这是为了防止 Claude 混淆历史对话和当前 turn 的任务。

## Gotcha / 边界情况

- `CHAT_HISTORY_TRUNCATED_HEADER` 和 `CHAT_HISTORY_HEADER` 的区别只在于标题文字，实际截断逻辑在 `xyz_claude_agent_sdk.py` 里处理。修改截断行为不需要改这个文件。

## 新人易踩的坑

- 这些常量目前只在 `xyz_claude_agent_sdk.py` 里用到，但如果实现了其他 agent backend（如直接用 Anthropic API 而非 Claude CLI），也需要同样的 prompt 格式，应该复用这些常量而不是再硬编码一份。
