---
code_file: src/xyz_agent_context/module/chat_module/prompts.py
last_verified: 2026-04-10
---

# prompts.py — ChatModule 指令定义

## 为什么存在

`CHAT_MODULE_INSTRUCTIONS` 的核心使命是建立 Agent 的"思考 vs 说话"认知模型，并给出消息发送纪律。这是整个系统里最关键的 prompt 约束之一——如果 Agent 没有正确理解"不调用 `send_message_to_user_directly` 就等于没有说话"，用户将永远看不到 Agent 的回复。

## 上下游关系

- **被谁用**：`ChatModule.__init__` 把它赋给 `self.instructions`；每轮对话时注入系统提示
- **依赖谁**：无外部依赖，纯文本常量

## 设计决策

**用表格和类比强化核心概念**："你的文本输出用户看不到"是反直觉的（大多数 LLM 训练数据里 final_output 就是用户可见回复）。Prompt 用了声音隔离室类比（"your text output = thinking in soundproof room"）和明确的对比表格来强化这个概念。这些重复强调是必要的，不是冗余。

**消息发送纪律（Message Delivery Discipline）**：明确列出四种场景和对应规则，防止 Agent 在 IM 渠道（Matrix）收到大量消息时把所有中间消息都转发给用户主聊。"IM 频道消息只有在用户被@、需要紧急决策、或关键信息时才发"是防噪音的核心约束。

**Anti-Patterns 列表**：明确列出错误做法（转发每条 IM 消息、发送进度更新、重复确认收到任务）比只说"什么时候发"更有效。负面示例对 LLM 行为约束效果更强。

## Gotcha / 边界情况

- **最后那条"MUST send a FINAL conclusive response"约束**是为了防止另一个极端——Agent 做了大量工具调用和研究，但最后忘记调用 `send_message_to_user_directly` 总结结果。这个约束和"不要发没必要的消息"之间存在张力，Agent 需要判断平衡点。

## 新人易踩的坑

- 给 Agent 添加新的触发场景（如新 IM 渠道）时，需要在这个 prompt 的"消息发送纪律"表格里明确说明新场景的发送规则，否则 Agent 会 fallback 到"不知道该不该发"而随机发送。
