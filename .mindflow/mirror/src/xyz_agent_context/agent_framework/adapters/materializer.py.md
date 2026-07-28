---
code_file: src/xyz_agent_context/agent_framework/adapters/materializer.py
last_verified: 2026-07-27
stub: false
---
# adapters/materializer.py — 共享的「结构化消息 → CLI 提示文本」物化步骤

## 为什么存在

两个 CLI driver 都在门口把 context_runtime 给的结构化 role messages 压
平成「system prompt 文本 + 单条 user 输入」（CLI 只收这两样）。此前是
两份平行私有实现（claude 内联在 agent_loop 里；codex 在 cli_sdk 的
`_build_system_prompt_and_user_msg`）。本模块把两种策略并排收进一个显
式共享接缝：

- `flatten_for_argv`（claude 策略）：argv 承载 → 字符+字节双上限
  （Linux MAX_ARG_STRLEN）、source-aware 驱逐、截断标注；**会 pop 调用
  方列表的最后一条**——load-bearing quirk：step_3 的 fallback 之后读的
  是同一个 list。
- `flatten_for_file`（codex 策略）：文件承载（instructions.md）→ 无字
  节上限、宽预算、同款驱逐；操作副本不变异调用方。

## 刻意不合并成一个函数

两者可观测输出不同（标注文案、上限、变异语义），与历史行为的字节级等
价才是本次上提的契约。自研 loop（自己投影上下文的 driver）根本不调本
模块——这正是 driver 契约「签名一致、消费深度自由」的一半。

## 坑

- 金样测试 `tests/agent_framework/test_materializer.py` 钉住 header/
  footer 字节、驱逐次序（先最旧非 chat 再最旧 chat）、UTF-8 边界截断、
  变异/拷贝语义。改任何输出细节先过金样。
- `_source` 由 context_runtime.build_input_for_framework 从行的
  meta_data.working_source 写入，未知默认 "chat"。
