---
code_file: src/xyz_agent_context/agent_framework/nexus_power/assembly.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

ExpressionContract 前移到 expander 之前构建并挂 `add_expressive` 缝;
LoopAssembly.expression 直接用它(不再 frozenset 二次构造)。reminder 从装配时
算死的字符串变 `_tail()` 闭包:每步 `NexusPowerPrompts.reply_reminder(
expression.names())`——中途展开出的投递工具进 reminder,稳定前缀不动。
`PromptInputs.default_reply_tool` 取 `expression.names()` 首位(initial
expansions 之后取值,起跑展开授予的回复工具也算),装配时冻结。

# assembly — 唯一装配点:TurnRequest 进、类型化事件流出

LoopAssembly 是循环的全部依赖(硬组件无默认、策略缝带默认,R1:装配复杂度有意集中于此文件);run_turn_events 是框架顶层入口:装配→初始展开→跑循环。TurnRequest 整包可 JSON 序列化,这是 runner 跨进程传输的前提。测试用 dataclasses.replace 换件,永不 patch。坑:harness system 消息插在平台前导 system 段末尾(_insert_harness),不能追加在 user 之后;output_schema v1 显式 fail loud(schema 诚实)。
