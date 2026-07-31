---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/prompts/assembler.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`PromptInputs` 新增 `default_reply_tool`(平台声明名单的首位,装配时冻结)——
constitution 的例子槽位吃它;mute 回合为空串,例子整句消失。

# prompts/assembler — 纯函数装配 + 稳定前缀/动态尾部切分

PromptMode 三档派生(不维护两份 prompt);同输入逐字节同输出(不读时钟/环境/随机),字节稳定测试对着它写(C2)。装配器吃类引用:换 NexusPowerPrompts 子类=整套 prompt pack。
