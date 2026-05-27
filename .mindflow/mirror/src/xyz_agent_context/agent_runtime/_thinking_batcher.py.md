---
code_file: src/xyz_agent_context/agent_runtime/_thinking_batcher.py
last_verified: 2026-05-13
stub: false
---

# _thinking_batcher.py — WS-tier thinking-delta 合并器

## 为什么存在

Claude Code CLI 把 LLM thinking 按 token 粒度往外推。Anthropic-protocol
转发 DeepSeek-V4-Pro 时（NetMind 等聚合商），一个 ZH 字符一个 token —
一个 13 分钟 run 会产生几千个单字符 thinking_item 事件。每个事件原样推
WS 就会让前端 main thread 在 setState 风暴下卡顿（Xiong "30 多次工具调用
然后 not response" 的核心症状之一）。

铁律 #16 禁止"丢内容 / 限流 / 截断"那类对用户可感知的优化。这里采用
**合并 frame 但不动 content** 的方案——4408 个 1-char chunks 合并成
~50 个 ~50-char chunks，content 完全相同、顺序完全相同、用户只感觉
"thinking 节奏从字符级变成了短语级"反而更接近自然语言。

## 设计点

- **per-run 实例**：`ResponseProcessor` 在每个 agent turn 都新建一个
  自己，batcher 也跟随一起新建。无 cross-turn 状态，无 cleanup hook
- **三个 trigger**：≥500 chars / 距上次 flush ≥100ms / caller 显式 `flush_ws()`
- **push 驱动而非 timer 驱动**：每次 `append_thinking` 顺手检查时间窗口。
  没有 asyncio timer，没有协程间状态共享，更易测试也更可预测
- **caller 必须在 stream 结束 / type 切换时显式 flush**：100ms 窗口
  从最近一次 append 起算；如果 LLM 停了不出新 chunk，buffer 里残留的
  内容不会自己飘出去——必须 caller 调 `flush_ws()`

## 不做的事

- Phase B 只做 **WS-tier 合并**，不做 DB-tier。DB 持久化（每段 thinking
  作为一个 event_stream row 落库）是 Phase C 配合 event_stream 表一起做
- 不做 token / 字节 / 时间维度的"内容截断 / 丢弃"——任何情况下 100% 保留
  原始字符
- 不感知 LLM 协议或 model：对 Claude 那种粗粒度 chunks（一个 chunk 就
  ≥500 chars），append 立刻触发 chars 阈值 flush，等效于"不合并"

## 调用方契约

`ResponseProcessor` 是唯一 caller。Caller 必须：

1. `_handle_run_item_stream_event` 收到 thinking_item 时 → `append_thinking()`
2. 收到任意非 thinking_item 时 → 先 `flush_ws()`，把残留发出，再处理新事件
3. `agent_loop` 退出时（无论正常 / 异常 / cancel）→ `flush_ws()` 收尾

违反这个契约会导致 thinking 内容延迟到下次 append 才出（在 long-idle
场景下用户可能看到 thinking 停 5 秒后才一段冒出来）。

## 测试

`tests/agent_runtime/test_thinking_batcher.py` 单测 + `test_response_processor_thinking_coalesce.py`
集成测试覆盖：单 chunk / 大 chunk / 累积达阈值 / 时间触发 / 显式 flush /
空输入 / 5000 chunk 端到端 verbatim 保留 + 帧数下降验证。
